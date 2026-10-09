"""Shot boundary detection and transition classification.

Hard cuts use an adaptive histogram-difference detector (the AdaptiveDetector
idea from PySceneDetect) on the shared frame signals; PySceneDetect itself is
run as an independent cross-check so boundary confidence comes from agreement,
not from either detector's say-so (manual 8.5).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from vanta.analysis.signals import FrameSignals


@dataclass
class Boundary:
    frame: int  # first frame of the incoming shot (for gradual: centre)
    kind: str  # hard_cut | dissolve | fade_black | fade_white | flash | whip | zoom | glitch
    length_frames: int
    confidence: float
    evidence: dict[str, float] = field(default_factory=dict)


@dataclass
class ShotSpan:
    index: int
    start_frame: int
    end_frame: int  # exclusive
    fps: float
    transition_in: str = "start"
    transition_out: str = "end"
    boundary_confidence: float = 1.0
    take: int | None = None  # index of the original continuous take when split into moments

    @property
    def start_s(self) -> float:
        return self.start_frame / self.fps

    @property
    def end_s(self) -> float:
        return self.end_frame / self.fps

    @property
    def duration_s(self) -> float:
        return (self.end_frame - self.start_frame) / self.fps

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload.update(start_s=self.start_s, end_s=self.end_s, duration_s=self.duration_s)
        return payload


def _rolling_median(values: np.ndarray, radius: int) -> np.ndarray:
    out = np.empty_like(values)
    n = len(values)
    for i in range(n):
        lo, hi = max(0, i - radius), min(n, i + radius + 1)
        window = np.concatenate([values[lo:i], values[i + 1 : hi]])
        out[i] = np.median(window) if len(window) else 0.0
    return out


def detect_hard_cuts(
    sig: FrameSignals, *, min_shot_s: float = 0.2, threshold: float = 0.22, ratio: float = 3.0
) -> list[tuple[int, float, dict[str, float]]]:
    diff = sig.hist_diff
    pix = sig.pixel_diff
    radius = max(2, int(round(sig.fps * 0.3)))
    base = _rolling_median(diff, radius) + 0.01
    pix_base = _rolling_median(pix, radius) + 0.004
    min_gap = max(1, int(round(min_shot_s * sig.fps)))
    cuts: list[tuple[int, float, dict[str, float]]] = []
    last = -min_gap
    for i in range(1, len(diff)):
        score = diff[i] / base[i]
        pscore = pix[i] / pix_base[i]
        strong = diff[i] >= threshold and score >= ratio
        pixel_strong = pix[i] >= 0.12 and pscore >= ratio * 1.5 and diff[i] >= threshold * 0.6
        if (strong or pixel_strong) and i - last >= min_gap:
            confidence = float(np.clip(0.55 + 0.1 * min(score, 8) / 2 + 0.3 * min(diff[i], 1), 0, 0.99))
            cuts.append((i, confidence, {"hist_diff": float(diff[i]), "ratio": float(score)}))
            last = i
    return cuts


def detect_gradual(
    sig: FrameSignals, hard: set[int], *, min_len_s: float = 0.2, max_len_s: float = 2.0
) -> list[Boundary]:
    """Fades and dissolves: sustained moderate change with no single hard spike."""
    fps = sig.fps
    out: list[Boundary] = []
    luma = sig.luma
    n = len(luma)
    # fades through black / white
    for kind, mask in (("fade_black", luma < 0.04), ("fade_white", luma > 0.94)):
        i = 0
        while i < n:
            if mask[i]:
                j = i
                while j < n and mask[j]:
                    j += 1
                ramp_in = luma[max(0, i - int(0.5 * fps)) : i]
                if 0 < i and j < n and len(ramp_in):
                    centre = (i + j) // 2
                    out.append(
                        Boundary(
                            frame=centre,
                            kind=kind,
                            length_frames=max(1, j - i + int(0.3 * fps)),
                            confidence=0.8 if j - i >= 2 else 0.6,
                            evidence={"dark_frames" if kind == "fade_black" else "white_frames": j - i},
                        )
                    )
                i = j
            i += 1
    # dissolves: windowed accumulated change with linear-blend signature
    win = max(3, int(round(min_len_s * fps)))
    acc = np.convolve(sig.hist_diff, np.ones(win), mode="same")
    peak_free = sig.hist_diff < 0.2
    candidate = (acc > 0.35) & peak_free
    i = 0
    max_len = int(max_len_s * fps)
    while i < n:
        if candidate[i]:
            j = i
            while j < n and candidate[j]:
                j += 1
            length = j - i
            near_hard = any(abs(h - (i + j) // 2) < win * 2 for h in hard)
            dark = luma[i:j].min() < 0.05 if j > i else False
            if win <= length <= max_len and not near_hard and not dark:
                motion = float(np.mean(sig.flow_mag[i:j]))
                if motion < 0.02:  # cross-fades have little coherent motion
                    out.append(
                        Boundary(
                            frame=(i + j) // 2,
                            kind="dissolve",
                            length_frames=length,
                            confidence=0.6,
                            evidence={"accumulated_change": float(acc[i:j].max()), "motion": motion},
                        )
                    )
            i = j
        i += 1
    return out


def classify_hard_cut(sig: FrameSignals, frame: int) -> tuple[str, float, dict[str, float]]:
    """Look at frames around a cut to label stylised transitions (flash/whip/zoom/glitch)."""
    fps = sig.fps
    w = max(2, int(round(0.15 * fps)))
    lo, hi = max(1, frame - w), min(len(sig.luma), frame + w)
    around = slice(lo, hi)
    luma_peak = float(sig.luma[around].max())
    outside = np.concatenate([sig.luma[max(0, lo - w) : lo], sig.luma[hi : hi + w]])
    luma_base = float(np.median(outside)) if len(outside) else 0.0
    flow_peak = float(sig.flow_mag[around].max())
    sharp = sig.sharpness[around]
    sharp_ref = float(np.median(sig.sharpness[max(0, lo - 3 * w) : lo])) + 1e-6
    blur_dip = float(sharp.min() / sharp_ref) if len(sharp) else 1.0
    radial = float(np.abs(sig.radial[around]).max())
    evidence = {
        "luma_peak": luma_peak,
        "luma_base": luma_base,
        "flow_peak": flow_peak,
        "blur_ratio": blur_dip,
        "radial_peak": radial,
    }
    if luma_peak > 0.85 and luma_peak - luma_base > 0.3:
        return "flash", 0.7, evidence
    if flow_peak > 0.06 and blur_dip < 0.35:
        if radial > 0.6 * flow_peak:
            return "zoom", 0.6, evidence
        return "whip", 0.62, evidence
    jitter = float(np.std(sig.pixel_diff[around]))
    if jitter > 0.12 and float(np.mean(sig.hist_diff[around])) > 0.25:
        return "glitch", 0.5, evidence
    return "hard_cut", 0.9, evidence


def scenedetect_cuts(proxy_path: str | Path) -> list[int] | None:
    """Independent PySceneDetect AdaptiveDetector pass (None if unavailable)."""
    try:
        from scenedetect import AdaptiveDetector, detect
    except Exception:
        return None
    try:
        scenes = detect(str(proxy_path), AdaptiveDetector(), show_progress=False)
    except Exception:
        return None
    return [int(getattr(start, "frame_num", None) if getattr(start, "frame_num", None) is not None else start.get_frames()) for start, _ in scenes[1:]]


def segment_shots(
    sig: FrameSignals,
    *,
    cross_check: list[int] | None = None,
    min_shot_s: float = 0.2,
) -> tuple[list[ShotSpan], list[Boundary]]:
    hard = detect_hard_cuts(sig, min_shot_s=min_shot_s)
    tolerance = max(1, int(round(sig.fps * 0.1)))
    boundaries: list[Boundary] = []
    for frame, conf, evidence in hard:
        kind, kind_conf, extra = classify_hard_cut(sig, frame)
        if cross_check is not None:
            agreed = any(abs(frame - other) <= tolerance for other in cross_check)
            conf = min(0.99, conf + 0.08) if agreed else conf * 0.8
            evidence = {**evidence, "cross_check_agrees": float(agreed)}
        boundaries.append(
            Boundary(
                frame=frame,
                kind=kind,
                length_frames=1 if kind == "hard_cut" else 4,
                confidence=float(min(conf, kind_conf if kind != "hard_cut" else conf)),
                evidence={**evidence, **extra},
            )
        )
    hard_frames = {b.frame for b in boundaries}
    for gradual in detect_gradual(sig, hard_frames):
        if all(abs(gradual.frame - b.frame) > gradual.length_frames for b in boundaries):
            boundaries.append(gradual)
    if cross_check:
        for other in cross_check:
            if all(abs(other - b.frame) > tolerance * 2 for b in boundaries):
                boundaries.append(
                    Boundary(other, "hard_cut", 1, 0.45, {"cross_check_only": 1.0})
                )
    boundaries.sort(key=lambda b: b.frame)
    # drop low-confidence boundaries that create sub-minimum shots
    min_len = max(1, int(round(min_shot_s * sig.fps)))
    kept: list[Boundary] = []
    for b in boundaries:
        if kept and b.frame - kept[-1].frame < min_len:
            if b.confidence > kept[-1].confidence:
                kept[-1] = b
            continue
        if b.frame < min_len or sig.frame_count - b.frame < min_len:
            continue
        kept.append(b)
    shots: list[ShotSpan] = []
    start = 0
    prev_kind = "start"
    for idx, b in enumerate(kept + [None]):
        end = b.frame if b else sig.frame_count
        if end - start >= 1:
            shots.append(
                ShotSpan(
                    index=len(shots),
                    start_frame=start,
                    end_frame=end,
                    fps=sig.fps,
                    transition_in=prev_kind,
                    transition_out=b.kind if b else "end",
                    boundary_confidence=b.confidence if b else 1.0,
                )
            )
        if b:
            start = b.frame
            prev_kind = b.kind
        del idx
    return shots, kept


def split_long_takes(
    shots: list[ShotSpan],
    sig: FrameSignals,
    *,
    max_len_s: float = 8.0,
    target_s: float = 4.0,
    min_s: float = 1.6,
) -> list[ShotSpan]:
    """Split long continuous takes (camera footage) into editable moments.

    Split points are local maxima of a novelty curve (content change + change in
    motion + subject motion), so a moment usually spans one action or framing.
    Moments keep ``take`` so the cut solver knows they are continuous material.
    """
    fps = sig.fps
    out: list[ShotSpan] = []
    for shot in shots:
        length = shot.end_frame - shot.start_frame
        if length / fps <= max_len_s:
            out.append(ShotSpan(len(out), shot.start_frame, shot.end_frame, fps, shot.transition_in, shot.transition_out, shot.boundary_confidence, shot.take))
            continue
        a, b = shot.start_frame, shot.end_frame
        win = max(3, int(fps * 0.5))
        kernel = np.ones(win) / win
        hist = np.convolve(sig.hist_diff[a:b], kernel, mode="same")
        flow = np.convolve(sig.flow_mag[a:b], kernel, mode="same")
        dflow = np.abs(np.gradient(flow))
        resid = np.convolve(sig.residual[a:b], kernel, mode="same")
        dres = np.abs(np.gradient(resid))

        def norm(x: np.ndarray) -> np.ndarray:
            span = float(np.percentile(x, 98) - np.percentile(x, 2)) or 1.0
            return np.clip((x - np.percentile(x, 2)) / span, 0, 1)

        novelty = 0.5 * norm(hist) + 0.3 * norm(dflow) + 0.2 * norm(dres)
        min_len = int(min_s * fps)
        target = int(target_s * fps)
        cuts = [0]
        pos = 0
        while length - pos > target + min_len:
            lo, hi = pos + min_len, min(length - min_len, pos + int(target * 1.6))
            if hi <= lo:
                break
            window = novelty[lo:hi]
            # prefer change points near the target length
            bias = 1 - 0.35 * np.abs(np.arange(lo, hi) - (pos + target)) / max(1, target)
            nxt = lo + int(np.argmax(window * bias))
            cuts.append(nxt)
            pos = nxt
        cuts.append(length)
        take = shot.take if shot.take is not None else shot.index
        for i in range(len(cuts) - 1):
            out.append(
                ShotSpan(
                    len(out),
                    a + cuts[i],
                    a + cuts[i + 1],
                    fps,
                    shot.transition_in if i == 0 else "continuous",
                    shot.transition_out if i == len(cuts) - 2 else "continuous",
                    shot.boundary_confidence if i == 0 else 0.5,
                    take,
                )
            )
    return out


def pacing_stats(shots: list[ShotSpan], duration_s: float) -> dict[str, float]:
    """Real pacing statistics (manual 12.2): labels are views over these numbers."""
    durations = np.asarray([s.duration_s for s in shots], dtype=np.float64)
    if len(durations) == 0:
        return {}
    minutes = max(duration_s, 1e-6) / 60
    return {
        "shot_count": float(len(durations)),
        "median_s": float(np.median(durations)),
        "mean_s": float(np.mean(durations)),
        "p10_s": float(np.percentile(durations, 10)),
        "p90_s": float(np.percentile(durations, 90)),
        "cuts_per_minute": float((len(durations) - 1) / minutes),
        "long_shot_fraction": float(np.mean(durations > 3.0)),
        "rapid_cut_fraction": float(np.mean(durations < 0.6)),
        "first_shot_s": float(durations[0]),
    }


def pacing_curve(shots: list[ShotSpan], duration_s: float, bins: int = 10) -> list[float]:
    """Cuts per second across normalised time (escalation / payoff shape)."""
    if duration_s <= 0:
        return [0.0] * bins
    counts = np.zeros(bins)
    for shot in shots[1:]:
        position = min(bins - 1, int(shot.start_s / duration_s * bins))
        counts[position] += 1
    return (counts / (duration_s / bins)).round(3).tolist()
