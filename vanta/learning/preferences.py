"""Preference memory (manual 18): corrections -> counts -> proposed rules (reviewed).

When the same correction signal recurs under matching conditions (default 4
times) a candidate rule enters the review queue; nothing becomes authoritative
silently. Approved preference rules adjust future Edit Specs; confidence
thresholds are calibrated from recorded accept/reject outcomes.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from vanta.core.schemas import EditSpec, StyleRule
from vanta.storage.library import Library

RECURRENCE = 4

# signal -> (dimension, statement, preference payload, negative?)
SIGNAL_RULES: dict[str, tuple[str, str, dict[str, Any], bool]] = {
    "grade_too_aggressive": ("color", "You consistently soften VANTA's grades: start colour intensity lower", {"slider:color_intensity": -0.15, "slider:contrast": -0.08}, False),
    "grade_too_weak": ("color", "You push VANTA's grades further: start colour intensity higher", {"slider:color_intensity": 0.12}, False),
    "color_too_strong": ("color", "Grades feel too strong to you", {"slider:color_intensity": -0.12}, False),
    "color_too_weak": ("color", "Grades feel too weak to you", {"slider:color_intensity": 0.1}, False),
    "warmer": ("color", "You warm the image after VANTA's grade", {"slider:warmth": 0.1}, False),
    "cooler": ("color", "You cool the image after VANTA's grade", {"slider:warmth": -0.1}, False),
    "pacing_slower": ("rhythm", "You lengthen VANTA's shots: cut slower", {"slider:pacing": -0.12}, False),
    "pacing_faster": ("rhythm", "You shorten VANTA's shots: cut faster", {"slider:pacing": 0.12}, False),
    "shots_longer": ("rhythm", "You extend individual shots", {"slider:pacing": -0.06}, False),
    "shots_shorter": ("rhythm", "You trim individual shots", {"slider:pacing": 0.06}, False),
    "text_removed": ("typography", "You remove VANTA's titles: use less text", {"slider:typography_density": -0.15}, False),
    "typography_too_strong": ("typography", "Too much text", {"slider:typography_density": -0.15}, False),
    "effect_rejected": ("fusion", "You reject effects VANTA adds", {"slider:motion_effects": -0.15}, True),
    "fusion_too_strong": ("fusion", "Effects feel too strong", {"slider:motion_effects": -0.15}, False),
    "grade_rejected": ("color", "You remove VANTA's grades", {"slider:color_intensity": -0.2}, True),
}


def _rule_id(signal: str, context_key: str) -> str:
    return f"pref.{signal}.{hashlib.sha1(context_key.encode()).hexdigest()[:8]}"


def mine_preferences(library: Library, *, profile_id: str | None = None) -> list[StyleRule]:
    """Count correction signals (by context) and propose candidate rules at the recurrence threshold."""
    rows = library.list_corrections()
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        payload = row["json"]
        signal = payload.get("signal")
        if not signal or signal not in SIGNAL_RULES:
            continue
        ctx = payload.get("context") or {}
        context_key = ctx.get("role") or "any"
        groups[(signal, context_key)].append(payload)
        if context_key != "any":
            groups[(signal, "any")].append(payload)
    proposed: list[StyleRule] = []
    for (signal, context_key), items in groups.items():
        if len(items) < RECURRENCE:
            continue
        dimension, statement, prefer, negative = SIGNAL_RULES[signal]
        opposite = {"grade_too_aggressive": "grade_too_weak", "grade_too_weak": "grade_too_aggressive", "pacing_slower": "pacing_faster", "pacing_faster": "pacing_slower", "warmer": "cooler", "cooler": "warmer"}.get(signal)
        contradictions = len(groups.get((opposite, context_key), [])) if opposite else 0
        confidence = float(np.clip(np.mean([i.get("confidence", 0.7) for i in items]) * (len(items) / (len(items) + contradictions + 1)) + 0.05 * min(len(items), 8), 0.05, 0.95))
        rule = StyleRule(
            id=_rule_id(signal, context_key),
            dimension=dimension,
            profile=profile_id or "global",
            kind="negative" if negative else "preference",
            when={"role": context_key} if context_key != "any" else {},
            prefer=prefer,
            avoid=[signal] if negative else [],
            confidence=round(confidence, 3),
            support=len(items),
            contradictions=contradictions,
            statement=f"{statement} (seen {len(items)}x{'' if context_key == 'any' else f' on {context_key} shots'})",
            evidence=[i.get("id", "") for i in items[:12]],
            origin="corrections",
        )
        payload = rule.model_dump(mode="json")
        payload["profile"] = profile_id
        library.upsert_rule(payload)
        proposed.append(rule)
    return proposed


def apply_preferences(spec: EditSpec, rules: list[dict]) -> tuple[EditSpec, list[str]]:
    """Approved preference rules nudge the starting sliders (precedence 4: confirmed corrections)."""
    notes = []
    sliders = spec.sliders.model_dump()
    for rule in rules:
        if rule.get("approval_state") != "approved" or rule.get("kind") not in {"preference", "negative"}:
            continue
        for key, delta in (rule.get("prefer") or {}).items():
            if not key.startswith("slider:"):
                continue
            name = key.split(":", 1)[1]
            if name == "warmth":
                continue
            if name in sliders and isinstance(delta, (int, float)):
                before = sliders[name]
                sliders[name] = float(np.clip(before + delta, 0.0, 1.0))
                notes.append(f"{rule.get('statement', rule.get('id'))}: {name} {before:.2f} -> {sliders[name]:.2f}")
    return spec.model_copy(update={"sliders": spec.sliders.model_copy(update=sliders)}), notes


def calibration(library: Library, *, bins: int = 5) -> dict[str, Any]:
    """Predicted confidence vs actual acceptance (manual 10/18). Suggests thresholds after 50+ decisions."""
    rows = [r for r in library.list_decisions(resolved_only=True)]
    if not rows:
        return {"decisions": 0, "bins": [], "suggested": None}
    conf = np.asarray([r["predicted_confidence"] for r in rows])
    acc = np.asarray([r["accepted"] for r in rows], dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    table = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        mask = (conf >= lo) & (conf < hi if hi < 1 else conf <= hi)
        if mask.any():
            table.append({"range": [round(float(lo), 2), round(float(hi), 2)], "n": int(mask.sum()), "predicted": round(float(conf[mask].mean()), 3), "accepted": round(float(acc[mask].mean()), 3)})
    suggested = None
    if len(rows) >= 50:
        # threshold where observed acceptance first reaches 0.9 / 0.7 / 0.5
        order = np.argsort(conf)
        c_sorted, a_sorted = conf[order], acc[order]
        window = max(5, len(rows) // 10)
        smooth = np.convolve(a_sorted, np.ones(window) / window, mode="same")

        def threshold(target: float) -> float | None:
            idx = np.nonzero(smooth >= target)[0]
            return round(float(c_sorted[idx[0]]), 3) if len(idx) else None

        suggested = {"execute": threshold(0.9), "note": threshold(0.7), "preview": threshold(0.5)}
    return {"decisions": len(rows), "bins": table, "suggested": suggested, "per_dimension": dict(Counter(r["dimension"] for r in rows))}


def agreement(library: Library) -> dict[str, float]:
    """AI choice vs your final choice per dimension (accepted share of resolved decisions)."""
    by_dim: dict[str, list[int]] = defaultdict(list)
    for row in library.list_decisions(resolved_only=True):
        by_dim[row["dimension"]].append(int(row["accepted"]))
    return {dim: round(float(np.mean(v)), 3) for dim, v in by_dim.items()}
