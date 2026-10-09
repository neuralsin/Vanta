"""Music engine (manual 14): ranking with reasons and beat-grid segment alignment.

Licence safety: only library tracks with recorded rights are eligible; a
reference reel's music is never a candidate. When no cleared track exists the
edit gets a silent guide track plus a sync-point marker.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import numpy as np

SOLVER_VERSION = "music-v2"

MOOD_WORDS = {
    "dark": {"brightness": -1.0, "energy": 0.0},
    "moody": {"brightness": -0.8, "energy": -0.2},
    "cinematic": {"brightness": -0.3, "energy": 0.2},
    "night": {"brightness": -0.7, "energy": 0.1},
    "bright": {"brightness": 1.0, "energy": 0.3},
    "happy": {"brightness": 0.9, "energy": 0.4},
    "chill": {"brightness": 0.0, "energy": -0.8},
    "calm": {"brightness": 0.0, "energy": -1.0},
    "energetic": {"brightness": 0.3, "energy": 1.0},
    "hype": {"brightness": 0.4, "energy": 1.0},
    "aggressive": {"brightness": -0.2, "energy": 1.0},
    "epic": {"brightness": 0.0, "energy": 0.8},
    "emotional": {"brightness": -0.2, "energy": -0.3},
    "fast": {"brightness": 0.2, "energy": 0.8},
    "slow": {"brightness": 0.0, "energy": -0.7},
}


@dataclass
class Segment:
    start_s: float
    end_s: float
    drop_at_s: float | None  # relative to segment start
    first_downbeat_s: float  # relative to segment start
    bars: int


@dataclass
class MusicCandidate:
    media_id: str
    title: str
    score: float
    segment: Segment
    bpm: float
    terms: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    licence_confirmed: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def mood_targets(request: str) -> dict[str, float]:
    words = re.findall(r"[a-z]+", request.casefold())
    brightness = [MOOD_WORDS[w]["brightness"] for w in words if w in MOOD_WORDS]
    energy = [MOOD_WORDS[w]["energy"] for w in words if w in MOOD_WORDS]
    return {
        "brightness": float(np.mean(brightness)) if brightness else 0.0,
        "energy": float(np.mean(energy)) if energy else 0.0,
    }


def choose_segment(audio: dict, duration_s: float, *, drop_target_frac: float = 0.4, hook_s: float = 0.0) -> Segment | None:
    """Pick a start downbeat so that (a) a downbeat lands at t=0, (b) a drop lands near the
    payoff (``drop_target_frac`` of the edit) when available, (c) the edit ends on a bar."""
    total = float(audio.get("duration_s", 0.0))
    if total < duration_s * 0.95:
        return None
    beats = audio.get("beats", [])
    downbeats = [b["time_s"] for b in beats if b["kind"] == "downbeat"]
    bpm = float(audio.get("bpm") or 120.0)
    bar = 4 * 60.0 / bpm
    if not downbeats:
        downbeats = list(np.arange(0.0, total, bar))
    drops = audio.get("drops_s", [])
    best: tuple[float, Segment] | None = None
    target_drop = duration_s * drop_target_frac
    bars_needed = max(1, int(round(duration_s / bar)))
    for start in downbeats:
        end = start + bars_needed * bar
        if end > total + 0.05:
            break
        drops_in = [d - start for d in drops if start + 0.5 < d < end - 1.0]
        drop_rel = min(drops_in, key=lambda d: abs(d - target_drop)) if drops_in else None
        cost = 0.0
        if drops:
            cost += abs(drop_rel - target_drop) / max(duration_s, 1) if drop_rel is not None else 0.6
        # prefer starting after the intro and not ending in a fade-out
        cost += 0.1 * (start < 1.0)
        cost += 0.2 * max(0.0, (end - (total - 4.0)) / 4.0)
        if hook_s and drop_rel is not None and drop_rel < hook_s:
            cost += 0.3
        if best is None or cost < best[0]:
            best = (
                cost,
                Segment(
                    start_s=round(start, 4),
                    end_s=round(end, 4),
                    drop_at_s=None if drop_rel is None else round(drop_rel, 4),
                    first_downbeat_s=0.0,
                    bars=bars_needed,
                ),
            )
    return best[1] if best else None


def rank_tracks(
    tracks: list[dict],
    *,
    request: str,
    duration_s: float,
    style_audio: dict | None = None,
    footage_energy: float | None = None,
    recent_ids: list[str] | None = None,
    clap_scores: dict[str, float] | None = None,
    top: int = 5,
) -> list[MusicCandidate]:
    """Score every analysed library track; return the top candidates with reasons.

    ``tracks`` items: library media rows with an ``analysis`` dict attached.
    """
    targets = mood_targets(request)
    style_audio = style_audio or {}
    recent = set(recent_ids or [])
    candidates: list[MusicCandidate] = []
    for track in tracks:
        if track.get("rejected") or not track.get("enabled", 1):
            continue
        analysis = track.get("analysis") or {}
        audio = analysis.get("audio") or {}
        if not audio or audio.get("error"):
            continue
        segment = choose_segment(audio, duration_s)
        if segment is None:
            continue
        rights = track.get("rights_json") or {}
        terms: dict[str, float] = {}
        reasons: list[str] = []
        warnings: list[str] = []
        # semantic fit
        if clap_scores and track["id"] in clap_scores:
            terms["semantic"] = float(np.clip((clap_scores[track["id"]] + 0.1) / 0.5, 0, 1))
            reasons.append(f"CLAP match to the brief {clap_scores[track['id']]:.2f}")
        else:
            title = f"{track.get('title', '')} {' '.join(track.get('tags_json') or [])}".casefold()
            words = set(re.findall(r"[a-z]+", request.casefold()))
            overlap = len(words & set(re.findall(r"[a-z]+", title)))
            brightness = audio.get("mean_brightness", 0.1) / 0.15 - 1
            energy = audio.get("mean_energy", 0.6) / 0.6 - 1
            mood = 1 - 0.5 * (abs(np.clip(brightness, -1, 1) - targets["brightness"]) + abs(np.clip(energy, -1, 1) - targets["energy"])) / 2
            terms["semantic"] = float(np.clip(0.6 * mood + 0.15 * min(overlap, 3) / 3 + 0.25 * 0.5, 0, 1))
            if overlap:
                reasons.append(f"title/tags share {overlap} brief word(s)")
        # tempo fit (half/double time equivalent)
        bpm = float(audio.get("bpm") or 0)
        want_bpm = style_audio.get("bpm")
        if want_bpm and bpm:
            ratio = min(abs(np.log2(bpm / want_bpm)), abs(np.log2(bpm * 2 / want_bpm)), abs(np.log2(bpm / 2 / want_bpm)))
            terms["tempo"] = float(np.exp(-((ratio / 0.12) ** 2)))
            reasons.append(f"{bpm:.0f} BPM vs your style's {want_bpm:.0f}")
        else:
            terms["tempo"] = 0.6
        # energy fit
        want_energy = style_audio.get("energy")
        track_energy = float(audio.get("mean_energy", 0.6))
        if footage_energy is not None:
            terms["energy"] = float(1 - min(1.0, abs(track_energy - (0.45 + 0.4 * footage_energy)) * 2.5))
        elif want_energy is not None:
            terms["energy"] = float(1 - min(1.0, abs(track_energy - want_energy) * 2.5))
        else:
            terms["energy"] = 0.6
        # drop timing
        if segment.drop_at_s is not None:
            terms["drop"] = float(np.exp(-(((segment.drop_at_s / duration_s) - 0.4) / 0.2) ** 2))
            reasons.append(f"drop lands at {segment.drop_at_s:.1f}s into the edit")
        else:
            terms["drop"] = 0.35 if style_audio.get("drop_usage", 0) > 0.5 else 0.6
        # length fit
        terms["length"] = 1.0 if audio["duration_s"] >= duration_s + 4 else 0.7
        # freshness and preference
        terms["freshness"] = 0.3 if track["id"] in recent else 1.0
        if track["id"] in recent:
            reasons.append("used in a recent edit")
        terms["preference"] = 1.0 if track.get("favorite") else 0.6
        if track.get("favorite"):
            reasons.append("marked as a favourite")
        licence = bool(rights.get("platform_cleared") or rights.get("licence_confirmed"))
        if not licence:
            warnings.append("licence not confirmed: you must confirm rights before final render")
        weights = {"semantic": 0.28, "tempo": 0.16, "energy": 0.16, "drop": 0.16, "length": 0.08, "freshness": 0.08, "preference": 0.08}
        score = sum(weights[k] * terms[k] for k in weights)
        candidates.append(
            MusicCandidate(
                media_id=track["id"],
                title=track.get("title") or track["id"],
                score=round(float(score), 4),
                segment=segment,
                bpm=bpm,
                terms={k: round(v, 3) for k, v in terms.items()},
                reasons=reasons,
                licence_confirmed=licence,
                warnings=warnings,
            )
        )
    candidates.sort(key=lambda c: -c.score)
    return candidates[:top]


def timeline_beat_grid(audio: dict, segment: Segment) -> dict:
    """Beat grid relative to the edit start (t=0 at the chosen segment start)."""
    beats = [
        {**b, "time_s": round(b["time_s"] - segment.start_s, 4)}
        for b in audio.get("beats", [])
        if segment.start_s - 0.01 <= b["time_s"] <= segment.end_s + 0.01
    ]
    sections = []
    for s in audio.get("sections", []):
        a, b = max(s["start_s"], segment.start_s), min(s["end_s"], segment.end_s)
        if b > a:
            sections.append({**s, "start_s": round(a - segment.start_s, 4), "end_s": round(b - segment.start_s, 4)})
    drops = [round(d - segment.start_s, 4) for d in audio.get("drops_s", []) if segment.start_s < d < segment.end_s]
    return {"beats": beats, "sections": sections, "drops": drops, "bpm": audio.get("bpm"), "duration_s": segment.end_s - segment.start_s}


def synthetic_grid(duration_s: float, bpm: float = 100.0) -> dict:
    """Guide grid when no cleared music is chosen (silent guide track)."""
    period = 60.0 / bpm
    beats = []
    for i, t in enumerate(np.arange(0.0, duration_s, period)):
        kind = "downbeat" if i % 4 == 0 else "soft"
        beats.append({"time_s": round(float(t), 4), "strength": 1.0 if kind == "downbeat" else 0.5, "kind": kind, "bar": i // 4, "beat_in_bar": i % 4})
    return {"beats": beats, "sections": [{"start_s": 0.0, "end_s": duration_s, "label": "verse", "energy": 0.5}], "drops": [], "bpm": bpm, "duration_s": duration_s}
