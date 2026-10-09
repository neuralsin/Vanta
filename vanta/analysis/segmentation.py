"""Mask engine (manual 13.5): prompt -> SAM video segmentation -> temporal smoothing
-> hole filling -> feathering -> flicker suppression -> confidence -> matte sequence.

SAM 2.1 (Apache-2.0) is the default; gated SAM 3 is used when the user has
access; GrabCut is the last-resort fallback and always lands in the review
queue. Mattes are cached by shot + source hash + prompt.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from vanta.config import get_settings
from vanta.models.scheduler import best_device, scheduler

_cache: dict[str, object] = {}
_lock = threading.Lock()
CHUNK = 48  # frames per propagation chunk (bounds RAM/VRAM on the 6 GB tier)


class SegmentationError(RuntimeError):
    pass


@dataclass
class MattePrompt:
    box: tuple[float, float, float, float] | None = None  # normalised x0,y0,x1,y1 on key frame
    points: list[tuple[float, float, int]] = field(default_factory=list)  # normalised x,y,label
    key_frame: int = 0  # relative to the shot start
    text: str | None = None

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:12]


@dataclass
class MatteResult:
    directory: str
    pattern: str  # printf pattern, e.g. matte_%05d.png
    frame_count: int
    width: int
    height: int
    method: str
    confidence: float
    temporal_iou: float
    area_cv: float
    empty_fraction: float
    review_required: bool
    prompt: dict
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def frame_path(self, index: int) -> Path:
        return Path(self.directory) / (self.pattern % index)


def _read_frames(video: str | Path, start: int, end: int, long_edge: int) -> tuple[list[np.ndarray], tuple[int, int]]:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SegmentationError(f"cannot open {video}")
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    frames = []
    size = (0, 0)
    for _ in range(end - start):
        ok, frame = cap.read()
        if not ok:
            break
        size = (frame.shape[1], frame.shape[0])
        if long_edge and max(frame.shape[:2]) > long_edge:
            scale = long_edge / max(frame.shape[:2])
            frame = cv2.resize(frame, (int(frame.shape[1] * scale), int(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    cap.release()
    if not frames:
        raise SegmentationError("no frames decoded for matte generation")
    return frames, size


def _sam2_available(repo: str) -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache

        return isinstance(try_to_load_from_cache(repo, "config.json"), str)
    except Exception:
        return False


def _sam2_masks(frames: list[np.ndarray], prompt: MattePrompt) -> tuple[list[np.ndarray], list[float]]:
    """Chunked SAM2 video propagation; each chunk is re-seeded with the previous mask."""
    import torch
    from transformers import Sam2VideoModel, Sam2VideoProcessor

    repo = get_settings().models.segmentation_model
    device = best_device()
    dtype = torch.bfloat16 if device == "cuda" else torch.float32

    def loader():
        processor = Sam2VideoProcessor.from_pretrained(repo)
        model = Sam2VideoModel.from_pretrained(repo).to(device, dtype=dtype).eval()
        return processor, model

    def unload() -> None:
        with _lock:
            _cache.pop(repo, None)

    h, w = frames[0].shape[:2]
    soft: list[np.ndarray | None] = [None] * len(frames)
    scores: list[float] = [0.0] * len(frames)
    with scheduler.lease(repo):
        with _lock:
            if repo not in _cache:
                _cache[repo] = loader()
                scheduler.register_unloader(repo, unload)
            processor, model = _cache[repo]

        def run(indices: list[int], seed_mask: np.ndarray | None, seed_local: int) -> None:
            chunk = [frames[i] for i in indices]
            session = processor.init_video_session(
                video=chunk, inference_device=device, video_storage_device="cpu", dtype=dtype
            )
            if seed_mask is not None:
                processor.add_inputs_to_inference_session(
                    inference_session=session, frame_idx=seed_local, obj_ids=1, input_masks=[seed_mask.astype(np.float32)]
                )
            else:
                kwargs: dict = {}
                if prompt.box:
                    x0, y0, x1, y1 = prompt.box
                    kwargs["input_boxes"] = [[[x0 * w, y0 * h, x1 * w, y1 * h]]]
                if prompt.points:
                    kwargs["input_points"] = [[[[px * w, py * h] for px, py, _ in prompt.points]]]
                    kwargs["input_labels"] = [[[int(label) for _, _, label in prompt.points]]]
                if not kwargs:
                    raise SegmentationError("SAM2 needs a box, points or a seed mask")
                processor.add_inputs_to_inference_session(
                    inference_session=session, frame_idx=seed_local, obj_ids=1, **kwargs
                )
            with torch.inference_mode():
                model(inference_session=session, frame_idx=seed_local)
                for reverse in (False, True):
                    if reverse and seed_local == 0:
                        continue
                    for out in model.propagate_in_video_iterator(
                        session, start_frame_idx=seed_local, reverse=reverse
                    ):
                        masks = processor.post_process_masks(
                            [out.pred_masks], original_sizes=[[h, w]], binarize=False
                        )[0]
                        prob = torch.sigmoid(masks[0, 0].float()).cpu().numpy()
                        global_idx = indices[out.frame_idx]
                        soft[global_idx] = prob
                        logit = out.object_score_logits
                        scores[global_idx] = float(torch.sigmoid(logit.float()).mean()) if logit is not None else 1.0
            del session

        key = min(max(prompt.key_frame, 0), len(frames) - 1)
        first = list(range(max(0, key - CHUNK // 2), min(len(frames), key + CHUNK // 2)))
        run(first, None, key - first[0])
        # forward chunks
        last = first[-1]
        while last < len(frames) - 1:
            idx = list(range(last, min(len(frames), last + CHUNK)))
            run(idx, (soft[last] > 0.5), 0)
            last = idx[-1]
        # backward chunks
        head = first[0]
        while head > 0:
            idx = list(range(max(0, head - CHUNK + 1), head + 1))
            run(idx, (soft[head] > 0.5), len(idx) - 1)
            head = idx[0]
    return [m if m is not None else np.zeros((h, w), np.float32) for m in soft], scores


def _grabcut_masks(frames: list[np.ndarray], prompt: MattePrompt) -> tuple[list[np.ndarray], list[float]]:
    h, w = frames[0].shape[:2]
    if prompt.box:
        x0, y0, x1, y1 = prompt.box
    else:
        x0, y0, x1, y1 = 0.25, 0.15, 0.75, 0.95
    rect = (int(x0 * w), int(y0 * h), max(2, int((x1 - x0) * w)), max(2, int((y1 - y0) * h)))
    masks, scores = [], []
    prev: np.ndarray | None = None
    for frame in frames:
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        mask = np.zeros((h, w), np.uint8)
        bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        try:
            if prev is not None and prev.sum() > 100:
                mask[:] = cv2.GC_PR_BGD
                mask[cv2.dilate(prev, np.ones((15, 15), np.uint8)) > 0] = cv2.GC_PR_FGD
                mask[cv2.erode(prev, np.ones((15, 15), np.uint8)) > 0] = cv2.GC_FGD
                cv2.grabCut(bgr, mask, None, bgd, fgd, 2, cv2.GC_INIT_WITH_MASK)
            else:
                cv2.grabCut(bgr, mask, rect, bgd, fgd, 3, cv2.GC_INIT_WITH_RECT)
        except cv2.error:
            mask[:] = 0
        binary = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 1, 0).astype(np.uint8)
        prev = binary
        masks.append(binary.astype(np.float32))
        scores.append(0.5)
    return masks, scores


def _fill_holes(binary: np.ndarray) -> np.ndarray:
    # pad with background so the flood always starts outside the subject
    padded = np.pad(binary, 1, constant_values=0)
    h, w = padded.shape
    canvas = np.zeros((h + 2, w + 2), np.uint8)
    flood = padded.copy()
    cv2.floodFill(flood, canvas, (0, 0), 1)
    holes = flood[1:-1, 1:-1] == 0
    filled = binary.copy()
    filled[holes] = 1
    return filled


def postprocess(soft: list[np.ndarray], *, feather_px: float = 2.0) -> tuple[list[np.ndarray], dict[str, float]]:
    """Temporal smoothing, hole filling, feathering and flicker suppression."""
    n = len(soft)
    smoothed: list[np.ndarray] = []
    for i in range(n):
        window = [soft[j] for j in range(max(0, i - 1), min(n, i + 2))]
        smoothed.append(np.median(np.stack(window), axis=0) if len(window) == 3 else soft[i])
    out: list[np.ndarray] = []
    ious: list[float] = []
    areas: list[float] = []
    prev_bin: np.ndarray | None = None
    empty = 0
    kernel = np.ones((3, 3), np.uint8)
    for prob in smoothed:
        binary = (prob > 0.5).astype(np.uint8)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        binary = _fill_holes(binary)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
        if count > 2:
            keep = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            min_area = stats[keep, cv2.CC_STAT_AREA] * 0.05
            binary = np.isin(labels, [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= min_area]).astype(np.uint8)
        if prev_bin is not None:
            inter = np.logical_and(binary, prev_bin).sum()
            union = np.logical_or(binary, prev_bin).sum()
            iou = inter / union if union else 1.0
            ious.append(float(iou))
            if iou < 0.6 and prev_bin.sum() > 0:
                # flicker: blend toward the previous matte instead of jumping
                binary = ((binary.astype(np.float32) + prev_bin.astype(np.float32)) / 2 > 0.5).astype(np.uint8)
        if binary.sum() == 0:
            empty += 1
        areas.append(float(binary.mean()))
        prev_bin = binary
        alpha = cv2.GaussianBlur(binary.astype(np.float32), (0, 0), feather_px) if feather_px > 0 else binary.astype(np.float32)
        out.append(np.clip(alpha, 0, 1))
    area = np.asarray(areas)
    metrics = {
        "temporal_iou": float(np.mean(ious)) if ious else 1.0,
        "area_cv": float(area.std() / max(area.mean(), 1e-6)) if len(area) else 0.0,
        "empty_fraction": empty / max(1, n),
    }
    return out, metrics


def segment_subject(
    video: str | Path,
    start_frame: int,
    end_frame: int,
    out_dir: str | Path,
    prompt: MattePrompt,
    *,
    analysis_long_edge: int = 1024,
    write_full_resolution: bool = True,
) -> MatteResult:
    out_dir = Path(out_dir)
    manifest = out_dir / "matte.json"
    if manifest.is_file():
        return MatteResult(**json.loads(manifest.read_text(encoding="utf-8")))
    frames, (src_w, src_h) = _read_frames(video, start_frame, end_frame, analysis_long_edge)
    notes: list[str] = []
    method = "grabcut"
    repo = get_settings().models.segmentation_model
    soft: list[np.ndarray]
    scores: list[float]
    if _sam2_available(repo) and (prompt.box or prompt.points):
        try:
            soft, scores = _sam2_masks(frames, prompt)
            method = "sam2"
        except Exception as exc:
            notes.append(f"SAM2 failed ({type(exc).__name__}: {exc}); GrabCut fallback")
            soft, scores = _grabcut_masks(frames, prompt)
    else:
        notes.append("SAM2 not installed or no spatial prompt; GrabCut fallback")
        soft, scores = _grabcut_masks(frames, prompt)
    alphas, metrics = postprocess(soft)
    object_score = float(np.mean(scores)) if scores else 0.0
    confidence = float(
        np.clip(
            0.45 * metrics["temporal_iou"]
            + 0.25 * object_score
            + 0.2 * (1 - min(1.0, metrics["area_cv"]))
            + 0.1 * (1 - metrics["empty_fraction"]),
            0,
            1,
        )
    )
    if method == "grabcut":
        confidence = min(confidence, 0.55)
    out_dir.mkdir(parents=True, exist_ok=True)
    width, height = (src_w, src_h) if write_full_resolution else (frames[0].shape[1], frames[0].shape[0])
    pattern = "matte_%05d.png"
    for i, alpha in enumerate(alphas):
        img = (alpha * 255).astype(np.uint8)
        if img.shape[1] != width or img.shape[0] != height:
            img = cv2.resize(img, (width, height), interpolation=cv2.INTER_LINEAR)
        # RGBA with every channel = matte: Fusion EffectMask reads alpha, the Colour page
        # external matte reads luminance; one file serves both routes.
        cv2.imwrite(str(out_dir / (pattern % i)), cv2.merge([img, img, img, img]))
    result = MatteResult(
        directory=str(out_dir),
        pattern=pattern,
        frame_count=len(alphas),
        width=width,
        height=height,
        method=method,
        confidence=round(confidence, 4),
        temporal_iou=round(metrics["temporal_iou"], 4),
        area_cv=round(metrics["area_cv"], 4),
        empty_fraction=round(metrics["empty_fraction"], 4),
        review_required=confidence < 0.7 or method == "grabcut",
        prompt=asdict(prompt),
        notes=notes,
    )
    manifest.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return result


def auto_prompt(rgb: np.ndarray, faces: list[dict] | None = None) -> MattePrompt:
    """Derive a subject box from faces (body extrapolated) or saliency when no prompt is given."""
    from vanta.analysis.frame_vision import detect_faces, saliency_map

    faces = detect_faces(rgb) if faces is None else faces
    if faces:
        face = max(faces, key=lambda f: f["w"] * f["h"])
        cx = face["x"] + face["w"] / 2
        x0 = max(0.0, cx - face["w"] * 1.8)
        x1 = min(1.0, cx + face["w"] * 1.8)
        y0 = max(0.0, face["y"] - face["h"] * 0.4)
        y1 = min(1.0, face["y"] + face["h"] * 7.0)
        points = [(cx, face["y"] + face["h"] / 2, 1)]
        return MattePrompt(box=(x0, y0, x1, y1), points=points)
    sal = saliency_map(rgb)
    mask = sal > np.percentile(sal, 85)
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return MattePrompt(box=(0.2, 0.1, 0.8, 0.95))
    h, w = sal.shape
    return MattePrompt(
        box=(
            float(np.percentile(xs, 3) / w),
            float(np.percentile(ys, 3) / h),
            float(np.percentile(xs, 97) / w),
            float(np.percentile(ys, 97) / h),
        )
    )
