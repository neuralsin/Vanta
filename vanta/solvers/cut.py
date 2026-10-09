"""Cut solver (manual 12.1, 12.2): slot timing from the beat grid + learned pacing,
then shot assignment by second-order Viterbi so reuse is penalised two cuts back.

The LLM supplies slot intent; this solver places clips. All timing is snapped
to the timeline frame grid (a 30 fps frame is 33 ms, inside the +/-40 ms beat
tolerance), and every placement records its score terms as rationale.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

SOLVER_VERSION = "cut-v3"

BEAT_WEIGHT = {"downbeat": 1.0, "strong": 0.8, "soft": 0.35}
SECTION_FACTOR = {"intro": 1.25, "verse": 1.0, "build": 0.85, "drop": 0.6, "breakdown": 1.3, "outro": 1.35, "payoff": 0.75, "hook": 0.8}


@dataclass
class Slot:
    index: int
    start_s: float
    end_s: float
    start_frame: int
    end_frame: int
    section: str = "verse"
    beat_kind: str = "none"  # kind of beat the slot starts on
    energy: float = 0.5
    is_drop: bool = False
    intent: str = ""
    preferred_tags: list[str] = field(default_factory=list)
    preferred_shots: list[str] = field(default_factory=list)
    avoid_shots: list[str] = field(default_factory=list)
    locked_shot: str | None = None
    role: str = "build"

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame


@dataclass
class FootageCandidate:
    shot_id: str
    media_id: str
    source_path: str
    source_fps: float
    source_frames: int
    start_s: float
    end_s: float
    usable_ranges: list[tuple[float, float]]
    quality: float = 0.6
    motion_intensity: float = 0.4
    camera_move: str = "static"
    shot_size: str = "medium"
    face_count: int = 0
    tags: list[str] = field(default_factory=list)
    description: str = ""
    hook_potential: float = 0.5
    lighting: str | None = None
    look: np.ndarray | None = None
    semantic: dict[int, float] = field(default_factory=dict)  # slot index -> similarity (precomputed)
    words: list[tuple[float, float]] = field(default_factory=list)  # word spans (source seconds)
    motion_curve: np.ndarray | None = None
    motion_curve_fps: float = 0.0
    rejected: bool = False
    take: int | None = None  # continuous camera take the moment belongs to
    aspect: float = 0.5625  # source display width / height
    subject_x: float = 0.5
    subject_y: float = 0.5

    def longest_range(self) -> float:
        return max((b - a for a, b in self.usable_ranges), default=0.0)


@dataclass
class Placement:
    slot: Slot
    candidate: FootageCandidate
    source_in_frame: int
    source_out_frame: int
    confidence: float
    terms: dict[str, float]
    rationale: list[str]


# ------------------------------------------------------------------ timing
def _section_at(sections: list[dict], t: float) -> dict:
    for s in sections:
        if s["start_s"] <= t < s["end_s"]:
            return s
    return sections[-1] if sections else {"label": "verse", "start_s": 0.0, "end_s": 1e9, "energy": 0.5}


def _energy_at(grid: dict, t: float) -> float:
    section = _section_at(grid.get("sections", []), t)
    return float(np.clip(section.get("energy", 0.5) * 1.2, 0, 1))


def plan_slots(
    grid: dict,
    duration_s: float,
    fps: float,
    *,
    median_s: float | None = None,
    p10_s: float | None = None,
    p90_s: float | None = None,
    first_shot_s: float | None = None,
    pacing: float = 0.5,
    beat_sync: float = 0.7,
) -> list[Slot]:
    """Choose cut times: a beat is a candidate, never an obligation (soft beats prefer holds)."""
    median = median_s or 1.6
    lo = max(0.35, (p10_s or median * 0.45) * 0.75)
    hi = max(lo + 0.3, (p90_s or median * 2.2) * 1.15)
    pace = float(np.interp(pacing, [0, 0.5, 1], [1.5, 1.0, 0.55]))
    beats = sorted(grid.get("beats", []), key=lambda b: b["time_s"])
    drops = sorted(d for d in grid.get("drops", []) if 0.3 < d < duration_s - 0.3)
    sections = grid.get("sections", [])
    frame = 1.0 / fps
    hook_max = min(hi, max(lo, (first_shot_s or median) * 1.05))
    cuts = [0.0]
    t = 0.0
    while t < duration_s - lo:
        section = _section_at(sections, t)
        label = section.get("label", "verse")
        factor = SECTION_FACTOR.get(label, 1.0)
        if label == "build":
            span = max(section["end_s"] - section["start_s"], 1e-3)
            factor = float(np.interp((t - section["start_s"]) / span, [0, 1], [1.0, 0.65]))
        target = float(np.clip(median * factor * pace, lo, hi))
        if len(cuts) == 1:
            target = min(target, hook_max)
        upcoming_drop = next((d for d in drops if t + lo * 0.6 < d <= t + target * 1.6), None)
        if upcoming_drop is not None:
            nxt = upcoming_drop
        else:
            options = []
            for b in beats:
                gap = b["time_s"] - t
                if gap < lo or gap > (hook_max if len(cuts) == 1 else hi):
                    continue
                weight = BEAT_WEIGHT.get(b["kind"], 0.35)
                cost = abs(gap - target) / target + beat_sync * 0.8 * (1 - weight)
                options.append((cost, b["time_s"]))
            if options and beat_sync > 0.15:
                nxt = min(options)[1]
            else:
                nxt = t + target
        if duration_s - nxt < lo:
            break
        nxt = round(nxt / frame) * frame
        if nxt <= t + frame:
            nxt = t + max(lo, frame * 2)
        cuts.append(nxt)
        t = nxt
    cuts.append(duration_s)
    total_frames = int(round(duration_s * fps))
    slots: list[Slot] = []
    beat_times = {round(b["time_s"], 3): b["kind"] for b in beats}
    for i in range(len(cuts) - 1):
        a, b = cuts[i], cuts[i + 1]
        fa = int(round(a * fps))
        fb = total_frames if i == len(cuts) - 2 else int(round(b * fps))
        if fb <= fa:
            continue
        nearest = min(beat_times, key=lambda bt: abs(bt - a)) if beat_times else None
        kind = beat_times[nearest] if nearest is not None and abs(nearest - a) <= 0.045 else "none"
        label = _section_at(sections, a + 1e-3).get("label", "verse")
        is_drop = any(abs(a - d) < 0.05 for d in drops)
        role = "hook" if i == 0 else ("drop" if is_drop else ("outro" if i == len(cuts) - 2 else label))
        slots.append(
            Slot(
                index=len(slots),
                start_s=fa / fps,
                end_s=fb / fps,
                start_frame=fa,
                end_frame=fb,
                section="drop" if is_drop else label,
                beat_kind=kind,
                energy=1.0 if is_drop else _energy_at(grid, a + 1e-3),
                is_drop=is_drop,
                role=role,
            )
        )
    return slots


# ---------------------------------------------------------------- scoring
def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.casefold()) if len(w) > 2}


def semantic_fit(slot: Slot, cand: FootageCandidate) -> float:
    if slot.index in cand.semantic:
        return float(np.clip((cand.semantic[slot.index] + 0.05) / 0.25, 0, 1))
    wanted = set(t.casefold() for t in slot.preferred_tags) | _tokens(slot.intent)
    if not wanted:
        return 0.5
    have = set(t.casefold() for t in cand.tags) | _tokens(cand.description)
    overlap = len(wanted & have)
    return float(np.clip(0.35 + 0.65 * overlap / max(3, len(wanted) * 0.6), 0, 1))


def unary_terms(slot: Slot, cand: FootageCandidate, *, size_prior: dict[str, float], move_prior: dict[str, float], variety: float) -> dict[str, float] | None:
    if cand.rejected or cand.shot_id in slot.avoid_shots:
        return None
    if cand.longest_range() + 1e-6 < slot.duration_s:
        return None
    terms = {
        "semantic": semantic_fit(slot, cand),
        "quality": float(np.clip(cand.quality, 0, 1)),
        "energy": float(1 - abs(slot.energy - cand.motion_intensity)),
        "style_size": float(size_prior.get(cand.shot_size, 0.2) / max(max(size_prior.values(), default=1.0), 1e-6)),
        "style_motion": float(move_prior.get(cand.camera_move, 0.2) / max(max(move_prior.values(), default=1.0), 1e-6)),
        "hook": float(cand.hook_potential) if slot.role == "hook" else 0.5,
        "preferred": 1.0 if cand.shot_id in slot.preferred_shots else 0.5,
    }
    if slot.is_drop:
        terms["energy"] = float(np.clip(cand.motion_intensity * 1.2, 0, 1))
    return terms


UNARY_WEIGHTS = {"semantic": 0.26, "quality": 0.2, "energy": 0.14, "style_size": 0.08, "style_motion": 0.06, "hook": 0.12, "preferred": 0.14}


def _unary_cost(terms: dict[str, float]) -> float:
    return 1.0 - sum(UNARY_WEIGHTS[k] * terms[k] for k in UNARY_WEIGHTS)


def _pair_cost(a: FootageCandidate | None, b: FootageCandidate, variety: float) -> float:
    if a is None:
        return 0.0
    cost = 0.0
    if a.shot_id == b.shot_id:
        cost += 1.0
    if a.shot_size == b.shot_size:
        cost += 0.12 + 0.12 * variety
    if a.camera_move == b.camera_move and a.camera_move != "static":
        cost += 0.08
    if a.media_id == b.media_id and a.shot_id != b.shot_id:
        cost += 0.04
        if a.take is not None and a.take == b.take:
            # adjacent moments of one take read as a jump cut unless framing changes
            cost += 0.18 if a.shot_size == b.shot_size else 0.06
    if a.look is not None and b.look is not None:
        cost += 0.08 * float(min(1.0, np.linalg.norm(a.look - b.look) / 20.0))
    return cost


def _second_cost(a: FootageCandidate | None, c: FootageCandidate) -> float:
    if a is None:
        return 0.0
    return 0.6 if a.shot_id == c.shot_id else 0.0


def _diverse(options: list[tuple[int, dict[str, float], float]], candidates: list[FootageCandidate], beam: int, per_take: int = 2) -> list[tuple[int, dict[str, float], float]]:
    """Best options with coverage: at most ``per_take`` moments of one continuous take, then fill."""
    out, counts, chosen = [], {}, set()
    for opt in options:
        cand = candidates[opt[0]]
        key = (cand.media_id, cand.take if cand.take is not None else cand.shot_id)
        if counts.get(key, 0) < per_take:
            counts[key] = counts.get(key, 0) + 1
            out.append(opt)
            chosen.add(opt[0])
            if len(out) >= beam:
                return out
    return out + [o for o in options if o[0] not in chosen][: beam - len(out)]


def _viterbi(per_slot: list[list[tuple[int, dict[str, float], float]]], candidates: list[FootageCandidate], variety: float) -> list[int]:
    dp: list[dict[tuple[int | None, int], tuple[float, tuple[int | None, int] | None]]] = []
    dp.append({(None, idx): (cost, None) for idx, _, cost in per_slot[0]})
    for j in range(1, len(per_slot)):
        layer: dict[tuple[int | None, int], tuple[float, tuple[int | None, int] | None]] = {}
        for cur, _, ucost in per_slot[j]:
            for (pp, p), (prev_cost, _) in dp[j - 1].items():
                if cur == p or cur == pp:
                    continue  # never the same moment twice in a row
                total = (
                    prev_cost
                    + ucost
                    + _pair_cost(candidates[p], candidates[cur], variety)
                    + _second_cost(candidates[pp] if pp is not None else None, candidates[cur])
                )
                key = (p, cur)
                if key not in layer or total < layer[key][0]:
                    layer[key] = (total, (pp, p))
        if not layer:  # only one option everywhere: allow repeats rather than fail
            for cur, _, ucost in per_slot[j]:
                for (pp, p), (prev_cost, _) in dp[j - 1].items():
                    layer[(p, cur)] = (prev_cost + ucost + 1.0, (pp, p))
        dp.append(layer)
    state = min(dp[-1], key=lambda k: dp[-1][k][0])
    path = [state[1]]
    for j in range(len(per_slot) - 1, 0, -1):
        _, back = dp[j][state]
        state = back  # type: ignore[assignment]
        path.append(state[1])
    path.reverse()
    return path


def assign_shots(
    slots: list[Slot],
    candidates: list[FootageCandidate],
    *,
    size_prior: dict[str, float] | None = None,
    move_prior: dict[str, float] | None = None,
    variety: float = 0.6,
    beam: int = 24,
) -> list[Placement]:
    """Second-order Viterbi over (previous, current) candidate pairs, with global variety.

    The DP sees two slots back; a repair loop then removes any moment used
    twice anywhere in the edit and balances how many slots one continuous take
    may supply, re-solving until the sequence is clean (long camera rolls
    otherwise repeat their few best moments).
    """
    if not slots:
        return []
    size_prior = size_prior or {"medium": 1.0}
    move_prior = move_prior or {"static": 1.0}
    per_slot: list[list[tuple[int, dict[str, float], float]]] = []
    full: list[list[tuple[int, dict[str, float], float]]] = []
    for slot in slots:
        options = []
        for idx, cand in enumerate(candidates):
            if slot.locked_shot and cand.shot_id != slot.locked_shot:
                continue
            terms = unary_terms(slot, cand, size_prior=size_prior, move_prior=move_prior, variety=variety)
            if terms is None:
                continue
            options.append((idx, terms, _unary_cost(terms)))
        if not options:
            # nothing long enough: allow the longest ranges and let the in-point solver trim
            for idx, cand in sorted(enumerate(candidates), key=lambda x: -x[1].longest_range())[:beam]:
                if cand.rejected:
                    continue
                terms = {k: 0.3 for k in UNARY_WEIGHTS}
                options.append((idx, terms, 1.2))
        if not options:
            raise ValueError(f"no usable footage for slot {slot.index}")
        options.sort(key=lambda o: o[2])
        full.append(options)
        per_slot.append(_diverse(options, candidates, beam))
    takes_available = len({(c.media_id, c.take if c.take is not None else c.shot_id) for c in candidates if not c.rejected})
    take_cap = max(2, math.ceil(len(slots) / max(1, takes_available)), int(round(len(slots) * (1 - variety) * 0.4)))
    banned: list[set[int]] = [set() for _ in slots]
    path = _viterbi(per_slot, candidates, variety)
    for _ in range(6 * len(slots)):  # each round bans at least one (slot, moment) pair, so it terminates
        changed = False
        uses: dict[int, list[int]] = {}
        for j, idx in enumerate(path):
            uses.setdefault(idx, []).append(j)
        for idx, slots_using in uses.items():
            if len(slots_using) > 1:
                keep = min(slots_using, key=lambda j: next(c for i, _, c in per_slot[j] if i == idx))
                for j in slots_using:
                    if j != keep and not slots[j].locked_shot:
                        banned[j].add(idx)
                        changed = True
        by_take: dict[tuple, list[int]] = {}
        for j, idx in enumerate(path):
            cand = candidates[idx]
            by_take.setdefault((cand.media_id, cand.take if cand.take is not None else cand.shot_id), []).append(j)
        for js in by_take.values():
            if len(js) > take_cap:
                for j in sorted(js, key=lambda j: -next(c for i, _, c in per_slot[j] if i == path[j]))[: len(js) - take_cap]:
                    if not slots[j].locked_shot:
                        banned[j].add(path[j])
                        changed = True
        if not changed:
            break
        for j in range(len(slots)):
            pool = [o for o in full[j] if o[0] not in banned[j]] or full[j]
            per_slot[j] = _diverse(pool, candidates, beam)
        path = _viterbi(per_slot, candidates, variety)
    terms_lookup = [{idx: (terms, cost) for idx, terms, cost in opts} for opts in per_slot]
    placements: list[Placement] = []
    used: dict[str, list[tuple[float, float]]] = {}
    for j, (slot, idx) in enumerate(zip(slots, path, strict=True)):
        cand = candidates[idx]
        terms, cost = terms_lookup[j][idx]
        alts = sorted(c for i, (_, c) in terms_lookup[j].items() if i != idx)
        margin = (alts[0] - cost) if alts else 0.3
        confidence = float(np.clip(0.62 + 0.3 * math.tanh(margin * 5) + 0.08 * (terms.get("quality", 0.5) - 0.5), 0.35, 0.97))
        src_in, src_out = choose_in_point(slot, cand, used.get(cand.shot_id, []), timeline_fps=round(slot.frames / max(slot.duration_s, 1e-6), 3))
        used.setdefault(cand.shot_id, []).append((src_in / cand.source_fps, src_out / cand.source_fps))
        rationale = [
            f"slot {slot.index} ({slot.role}, {slot.section}, starts on {slot.beat_kind} beat)",
            "fit " + ", ".join(f"{k} {v:.2f}" for k, v in terms.items()),
        ]
        if slot.intent:
            rationale.append(f"intent: {slot.intent}")
        placements.append(Placement(slot, cand, src_in, src_out, round(confidence, 3), terms, rationale))
    return placements


def choose_in_point(slot: Slot, cand: FootageCandidate, used: list[tuple[float, float]], *, timeline_fps: float) -> tuple[int, int]:
    """Pick source frames: avoid reused ranges, align motion peaks on drops, never cut mid-word."""
    need = slot.duration_s
    best: tuple[float, float] | None = None
    for a, b in sorted(cand.usable_ranges, key=lambda r: -(r[1] - r[0])):
        if b - a + 1e-6 < need:
            continue
        options = np.linspace(a, b - need, num=max(1, min(25, int((b - a - need) * 10) + 1)))
        for s in options:
            e = s + need
            overlap = sum(max(0.0, min(e, ub) - max(s, ua)) for ua, ub in used)
            score = -overlap * 3.0
            centre = (a + b - need) / 2
            score -= 0.15 * abs(s - centre) / max(b - a, 1e-6)
            if cand.motion_curve is not None and cand.motion_curve_fps:
                i0 = int(s * cand.motion_curve_fps)
                i1 = max(i0 + 1, int(e * cand.motion_curve_fps))
                window = cand.motion_curve[i0:i1]
                if len(window):
                    peak = float(np.argmax(window)) / len(window)
                    if slot.is_drop or slot.role == "hook":
                        score += 0.4 * (1 - abs(peak - 0.15))  # motion peak right after the cut
                    score += 0.1 * float(np.mean(window)) / (float(np.max(cand.motion_curve)) + 1e-6)
            for ws, we in cand.words:
                if ws < s < we or ws < e < we:
                    score -= 0.5  # mid-word cut
            if best is None or score > best[0]:
                best = (score, s)
    if best is None:
        a, b = max(cand.usable_ranges, key=lambda r: r[1] - r[0]) if cand.usable_ranges else (cand.start_s, cand.end_s)
        start = a
    else:
        start = best[1]
    fps = cand.source_fps
    src_in = int(round(start * fps))
    length = int(round(need * fps))
    if abs(fps - timeline_fps) < 1e-3:
        length = slot.frames
    src_in = max(0, min(src_in, cand.source_frames - length))
    return src_in, src_in + max(1, length)
