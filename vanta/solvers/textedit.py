"""Text-based editing (manual 12.3): words -> kept ranges -> source cuts with handles.

Handles are ~40 ms before and ~80 ms after each kept span, boundaries snap to
an audio zero-crossing, close ranges merge, and the result is a rippled list of
source ranges that the EDG turns into shots with linked audio (A/V sync kept).
Captions are regenerated from the edited transcript.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from vanta.analysis.transcribe import FILLERS, Transcript

PRE_HANDLE_S = 0.04
POST_HANDLE_S = 0.08
MERGE_GAP_S = 0.12


@dataclass
class WordRef:
    index: int
    text: str
    start_s: float
    end_s: float
    segment: int


@dataclass
class KeptRange:
    start_s: float
    end_s: float
    words: list[WordRef]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def flatten(transcript: Transcript) -> list[WordRef]:
    out = []
    for si, seg in enumerate(transcript.segments):
        for w in seg.words:
            out.append(WordRef(len(out), w.text, w.start_s, w.end_s, si))
    return out


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", text.casefold())


def drop_fillers(words: list[WordRef]) -> set[int]:
    drop = set()
    for i, w in enumerate(words):
        token = _norm(w.text)
        if token in FILLERS:
            drop.add(i)
        elif i + 1 < len(words) and f"{token} {_norm(words[i + 1].text)}" in FILLERS:
            drop.update({i, i + 1})
    return drop


def drop_repeats(words: list[WordRef], *, max_n: int = 6) -> set[int]:
    """Remove the first copy of an immediately repeated phrase ('I went I went to...')."""
    tokens = [_norm(w.text) for w in words]
    drop: set[int] = set()
    i = 0
    while i < len(tokens):
        removed = False
        for n in range(max_n, 0, -1):
            if i + 2 * n <= len(tokens) and tokens[i : i + n] == tokens[i + n : i + 2 * n] and all(tokens[i : i + n]):
                drop.update(range(i, i + n))
                i += n
                removed = True
                break
        if not removed:
            i += 1
    return drop


def sentences(words: list[WordRef]) -> list[list[WordRef]]:
    out, current = [], []
    for w in words:
        current.append(w)
        if w.text.rstrip()[-1:] in ".!?":
            out.append(current)
            current = []
    if current:
        out.append(current)
    return out


def keep_matching(words: list[WordRef], terms: list[str]) -> set[int]:
    """Indices of sentences mentioning any term (keyword match; semantic match is layered above)."""
    wanted = {_norm(t) for t in terms if t.strip()}
    keep = set()
    for sentence in sentences(words):
        tokens = {_norm(w.text) for w in sentence}
        if tokens & wanted or any(t in " ".join(tokens) for t in wanted if len(t) > 3):
            keep.update(w.index for w in sentence)
    return keep


def kept_ranges(words: list[WordRef], drop: set[int], *, max_pause_s: float | None = 0.45) -> list[KeptRange]:
    ranges: list[KeptRange] = []
    for w in words:
        if w.index in drop:
            continue
        if ranges and (w.start_s - ranges[-1].end_s) <= (max_pause_s if max_pause_s is not None else 1e9) and ranges[-1].words[-1].index == w.index - 1:
            ranges[-1].end_s = w.end_s
            ranges[-1].words.append(w)
        else:
            ranges.append(KeptRange(w.start_s, w.end_s, [w]))
    return ranges


def _zero_crossing(audio: np.ndarray | None, sr: int, t: float, window_s: float = 0.01) -> float:
    if audio is None or not len(audio):
        return t
    i = int(t * sr)
    r = int(window_s * sr)
    lo, hi = max(1, i - r), min(len(audio) - 1, i + r)
    seg = audio[lo:hi]
    signs = np.nonzero(np.diff(np.signbit(seg)))[0]
    if not len(signs):
        return t
    best = signs[np.argmin(np.abs(signs + lo - i))] + lo
    return best / sr


def to_source_ranges(ranges: list[KeptRange], *, media_duration_s: float, audio: np.ndarray | None = None, sr: int = 22050) -> list[tuple[float, float, str]]:
    """Apply handles + zero crossings, merge near ranges, clamp to media."""
    out: list[list] = []
    for r in ranges:
        a = _zero_crossing(audio, sr, max(0.0, r.start_s - PRE_HANDLE_S))
        b = _zero_crossing(audio, sr, min(media_duration_s, r.end_s + POST_HANDLE_S))
        if out and a - out[-1][1] <= MERGE_GAP_S:
            out[-1][1] = b
            out[-1][2] += " " + r.text
        else:
            out.append([a, b, r.text])
    return [(round(a, 4), round(b, 4), t) for a, b, t in out if b - a >= 0.12]


def trim_to_duration(ranges: list[KeptRange], target_s: float, importance: dict[int, float] | None = None) -> list[KeptRange]:
    """Drop least-important ranges until under the target duration (story order kept)."""
    importance = importance or {}
    kept = list(ranges)

    def total() -> float:
        return sum(r.end_s - r.start_s for r in kept)

    while kept and total() > target_s:
        scores = [(np.mean([importance.get(w.index, 0.5) for w in r.words]) / max(0.2, r.end_s - r.start_s), i) for i, r in enumerate(kept)]
        if len(kept) > 1:
            scores = [s for s in scores if s[1] != 0]  # protect the opening line
        _, worst = min(scores)
        kept.pop(worst)
    return kept


def apply_command(transcript: Transcript, command: str) -> tuple[set[int], str]:
    """Deterministic interpretations for common text-edit commands (LLM fallback handled upstream)."""
    words = flatten(transcript)
    lower = command.casefold()
    if "filler" in lower or re.search(r"\bums?\b|\buhs?\b", lower):
        return drop_fillers(words), "removed filler words"
    if "repeat" in lower:
        return drop_repeats(words), "removed repeated phrases"
    if "pause" in lower or "silence" in lower:
        return set(), "pauses removed by tightening ranges"
    match = re.search(r"(?:only keep|keep only|mention(?:s|ing)?|about)\s+(?:the\s+)?(.+)", lower)
    if match:
        terms = [t for t in re.split(r",| and | or ", match.group(1)) if t.strip()]
        keep = keep_matching(words, [re.sub(r"[^a-z0-9 ]", "", t).strip() for t in terms])
        return {w.index for w in words} - keep, f"kept sentences mentioning {', '.join(terms)}"
    match = re.search(r"remove (?:the )?(?:part|bit|section) (?:about|where|with) (.+)", lower)
    if match:
        hit = keep_matching(words, [match.group(1)])
        return hit, f"removed the part about {match.group(1)}"
    return set(), "no deterministic interpretation"
