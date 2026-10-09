"""Footage Knowledge Graph (manual 15): every analysed shot with stable IDs, descriptors,
usable ranges, transcript words, embeddings and continuity relations, queryable
without reprocessing footage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from vanta.analysis.pipeline import load_analysis
from vanta.analysis.signals import FrameSignals
from vanta.config import Settings, get_settings
from vanta.solvers.cut import FootageCandidate
from vanta.storage.library import Library

LOOK_KEYS = ("luma_p50", "contrast", "shadow_b", "high_b", "chroma_mean")


@dataclass
class FootageGraph:
    candidates: list[FootageCandidate]
    shots: dict[str, dict]  # shot id -> analysis shot record (+ media fields)
    media: dict[str, dict]
    analyses: dict[str, dict]
    transcripts: dict[str, dict] = field(default_factory=dict)
    continuity: dict[str, list[str]] = field(default_factory=dict)

    def candidate(self, shot_id: str) -> FootageCandidate:
        return next(c for c in self.candidates if c.shot_id == shot_id)

    def labels(self) -> dict[str, str]:
        """Short, unique, model-friendly shot labels: clip letter + moment number (A1, A2, B1...)."""
        letters = {}
        for i, media_id in enumerate(dict.fromkeys(c.media_id for c in self.candidates)):
            letters[media_id] = _letters(i)
        counters: dict[str, int] = {}
        out = {}
        for cand in self.candidates:
            counters[cand.media_id] = counters.get(cand.media_id, 0) + 1
            out[cand.shot_id] = f"{letters[cand.media_id]}{counters[cand.media_id]}"
        return out

    def resolve_label(self, value: str, inverse: dict[str, str] | None = None) -> str | None:
        """Model reply (label or full id) -> shot id, or None when unknown."""
        inverse = inverse if inverse is not None else {v: k for k, v in self.labels().items()}
        value = str(value).strip()
        if value in self.shots:
            return value
        return inverse.get(value) or inverse.get(value.upper())

    # ------------------------------------------------------------- queries
    def query(
        self,
        *,
        text: str | None = None,
        min_quality: float = 0.0,
        motion: str | None = None,  # low | high
        shot_size: list[str] | None = None,
        has_person: bool | None = None,
        min_duration_s: float = 0.0,
        lighting: str | None = None,
        after_shot: str | None = None,
        limit: int = 10,
    ) -> list[tuple[str, float, list[str]]]:
        """Rank shots with reasons, e.g. a hard cut on the beat after the reveal:
        ``query(text="reveal", motion="high", min_duration_s=0.6, after_shot=reveal_id)``."""
        words = {w for w in re.findall(r"[a-z0-9]+", (text or "").casefold()) if len(w) > 2}
        out = []
        for cand in self.candidates:
            reasons: list[str] = []
            if cand.quality < min_quality or cand.longest_range() < min_duration_s:
                continue
            if has_person is not None and bool(cand.face_count) != has_person:
                continue
            if shot_size and cand.shot_size not in shot_size:
                continue
            if lighting and (cand.lighting or "") != lighting:
                continue
            score = 0.4 * cand.quality
            reasons.append(f"quality {cand.quality:.2f}")
            if motion:
                fit = cand.motion_intensity if motion == "high" else 1 - cand.motion_intensity
                score += 0.3 * fit
                reasons.append(f"motion {cand.motion_intensity:.2f}")
            if words:
                have = set(t.casefold() for t in cand.tags) | set(re.findall(r"[a-z0-9]+", cand.description.casefold()))
                overlap = len(words & have)
                score += 0.4 * overlap / max(1, len(words))
                if overlap:
                    reasons.append(f"matches {', '.join(sorted(words & have))}")
            if after_shot and after_shot in self.shots:
                prev = self.shots[after_shot]
                if prev.get("media_id") == self.shots[cand.shot_id].get("media_id") and self.shots[cand.shot_id]["idx"] == prev["idx"] + 1:
                    score += 0.15
                    reasons.append("continues the action")
                if (prev.get("composition") or {}).get("shot_size") != cand.shot_size:
                    score += 0.1
                    reasons.append("changes shot size (clean cut)")
            out.append((cand.shot_id, round(score, 4), reasons))
        out.sort(key=lambda x: -x[1])
        return out[:limit]

    def showcase(self, max_shots: int) -> list[FootageCandidate]:
        """Best moments with coverage: round-robin over (clip, take) groups by score.

        A long camera roll yields hundreds of moments; the director must see the
        whole shoot, not sixty near-duplicates of the sharpest take.
        """
        groups: dict[tuple[str, int | None], list[FootageCandidate]] = {}
        for cand in self.candidates:
            if not cand.rejected:
                groups.setdefault((cand.media_id, cand.take), []).append(cand)
        for members in groups.values():
            members.sort(key=lambda c: -(0.7 * c.quality + 0.3 * c.hook_potential))
        ordered = sorted(groups.values(), key=lambda m: -(0.7 * m[0].quality + 0.3 * m[0].hook_potential))
        picked: list[FootageCandidate] = []
        depth = 0
        while len(picked) < max_shots and any(depth < len(m) for m in ordered):
            for members in ordered:
                if depth < len(members) and len(picked) < max_shots:
                    picked.append(members[depth])
            depth += 1
        return picked

    def summary_lines(self, max_shots: int = 80) -> list[str]:
        lines = []
        labels = self.labels()
        for cand in self.showcase(max_shots):
            lines.append(
                f"{labels[cand.shot_id]}: {cand.description or '(no description)'} [{', '.join(cand.tags[:6])}] "
                f"q={cand.quality:.2f} motion={cand.motion_intensity:.2f} size={cand.shot_size} hook={cand.hook_potential:.2f} "
                f"usable={cand.longest_range():.1f}s" + (f" take={cand.take}" if cand.take is not None else "")
            )
        return lines


def _letters(index: int) -> str:
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def _motion_curve(analysis: dict, settings: Settings) -> tuple[np.ndarray | None, float]:
    path = Path(analysis.get("proxy", "")).with_name("signals.npz")
    if not path.is_file():
        return None, 0.0
    try:
        sig = FrameSignals.load(path)
    except Exception:
        return None, 0.0
    return sig.flow_mag.astype(np.float32), sig.fps


def build_graph(library: Library, footage_ids: list[str], settings: Settings | None = None) -> FootageGraph:
    settings = settings or get_settings()
    candidates: list[FootageCandidate] = []
    shots: dict[str, dict] = {}
    media_map: dict[str, dict] = {}
    analyses: dict[str, dict] = {}
    transcripts: dict[str, dict] = {}
    continuity: dict[str, list[str]] = {}
    for media_id in footage_ids:
        media = library.get_media(media_id)
        if not media or media.get("analysis_status") != "done" or media.get("rejected"):
            continue
        analysis = load_analysis(media, settings)
        if not analysis:
            continue
        media_map[media_id] = media
        analyses[media_id] = analysis
        info = analysis.get("info", {})
        src_fps = float(info.get("fps") or analysis.get("analysis_fps") or 30.0)
        src_frames = int(info.get("frame_count") or round(analysis["duration_s"] * src_fps))
        curve, curve_fps = _motion_curve(analysis, settings)
        transcript = analysis.get("transcript") if analysis.get("transcript") and not analysis["transcript"].get("error") else None
        if transcript:
            transcripts[media_id] = transcript
        words = [(w["start_s"], w["end_s"]) for seg in (transcript or {}).get("segments", []) for w in seg.get("words", [])]
        ids = []
        for shot in analysis.get("shots", []):
            vlm = shot.get("vlm") or {}
            if vlm and vlm.get("usable") is False:
                quality_penalty = 0.5
            else:
                quality_penalty = 1.0
            usable = [tuple(r) for r in shot.get("usable_ranges") or []] or ([(shot["start_s"], shot["end_s"])] if not shot["quality"].get("black") else [])
            color = shot.get("color") or {}
            look = np.asarray([color.get(k, 0.0) for k in LOOK_KEYS], dtype=np.float32) * np.asarray([20, 20, 1, 1, 0.5], dtype=np.float32) if color else None
            cand = FootageCandidate(
                shot_id=shot["id"],
                media_id=media_id,
                source_path=media["path"],
                source_fps=src_fps,
                source_frames=src_frames,
                start_s=shot["start_s"],
                end_s=shot["end_s"],
                usable_ranges=usable,
                quality=float(shot["quality"]["score"]) * quality_penalty,
                motion_intensity=float((shot.get("motion") or {}).get("intensity", 0.4)),
                camera_move=(shot.get("motion") or {}).get("camera_move", "static"),
                shot_size=(shot.get("composition") or {}).get("shot_size", "medium"),
                face_count=int(shot.get("face_count", 0)),
                tags=[t for t in list(vlm.get("tags", [])) + [vlm.get("subject", ""), vlm.get("setting", "")] + list(shot.get("auto_tags") or []) if t],
                description=shot.get("description") or vlm.get("description") or " ".join(shot.get("auto_tags") or []),
                hook_potential=float(vlm.get("hook_potential", shot.get("hook_score", 0.5))),
                take=shot.get("take"),
                lighting=vlm.get("lighting") or ("night" if "night" in (shot.get("auto_tags") or []) else None),
                look=look,
                words=[w for w in words if shot["start_s"] <= w[0] < shot["end_s"]],
                motion_curve=curve,
                motion_curve_fps=curve_fps,
                rejected=bool(media.get("rejected")),
                aspect=float((media.get("width") or 9) / max(1, media.get("height") or 16)),
                subject_x=float((shot.get("composition") or {}).get("subject_x", 0.5)),
                subject_y=float((shot.get("composition") or {}).get("subject_y", 0.5)),
            )
            candidates.append(cand)
            shots[shot["id"]] = {**shot, "media_id": media_id, "embedding_model": shot.get("embedding_model")}
            ids.append(shot["id"])
        for a, b in zip(ids, ids[1:], strict=False):
            continuity.setdefault(a, []).append(b)
    return FootageGraph(candidates, shots, media_map, analyses, transcripts, continuity)


def semantic_scores(library: Library, graph: FootageGraph, texts: list[str]) -> dict[str, list[float]]:
    """SigLIP text->frame similarity per shot for each slot intent (empty when unavailable)."""
    from vanta.analysis.embeddings import semantic_text_embeddings

    if not texts or not any(t.strip() for t in texts):
        return {}
    try:
        result = semantic_text_embeddings([t or " " for t in texts])
    except Exception:
        return {}
    if not result:
        return {}
    model, text_vecs = result
    out: dict[str, list[float]] = {}
    ids, matrix = library.get_embeddings(model)
    lookup = dict(zip(ids, matrix, strict=True))
    for cand in graph.candidates:
        vec = lookup.get(cand.shot_id)
        if vec is None:
            continue
        v = vec / max(np.linalg.norm(vec), 1e-9)
        out[cand.shot_id] = [float(t @ v) for t in text_vecs]
    return out
