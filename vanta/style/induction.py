"""Rule induction (manual 9.3) and universal-principle mining.

Conditional rules: per-shot descriptor vectors (conditions) and look vectors
(treatments) are split on candidate conditions. A rule is proposed only with
support >= 8 shots across >= 3 reels, a meaningful effect size, and a gain over
the universal baseline under leave-one-reel-out validation. Everything lands
in the review queue as ``candidate``; nothing enters the KB without approval.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np

from vanta.core.schemas import StyleRule

MIN_SUPPORT_SHOTS = 8
MIN_REELS = 3
MIN_EFFECT_SIZE = 0.5
MIN_LORO_GAIN = 0.05

# look targets: (key in colour stats, human label, unit)
TARGETS = {
    "shadow_b": ("shadow warmth (b*)", "b*"),
    "high_b": ("highlight warmth (b*)", "b*"),
    "shadow_a": ("shadow tint (a*)", "a*"),
    "contrast": ("contrast", "luma"),
    "black_point": ("black point", "luma"),
    "chroma_mean": ("saturation", "C*"),
    "luma_p50": ("exposure (median luma)", "luma"),
}


@dataclass
class ShotSample:
    reel: str
    shot_id: str
    conditions: dict[str, bool]
    look: dict[str, float]
    extras: dict = field(default_factory=dict)


def describe_conditions(shot: dict) -> dict[str, bool]:
    """Content descriptors used as rule conditions (VLM lighting class preferred)."""
    color = shot.get("color") or {}
    comp = shot.get("composition") or {}
    motion = shot.get("motion") or {}
    vlm = shot.get("vlm") or {}
    lighting = vlm.get("lighting")
    night = lighting in {"night", "neon"} if lighting else color.get("luma_p50", 0.5) < 0.22
    return {
        "night": bool(night),
        "golden_hour": lighting == "golden_hour",
        "indoor": lighting in {"indoor_practical", "studio"},
        "person": bool(shot.get("face_count", 0)),
        "close_up": comp.get("shot_size") in {"extreme_close", "close", "medium_close"},
        "wide": comp.get("shot_size") in {"wide", "extreme_wide"},
        "high_motion": motion.get("intensity", 0) > 0.45,
        "static_camera": motion.get("camera_move") == "static",
        "negative_space": comp.get("negative_space", 0) > 0.4,
        "cool_environment": color.get("mid_b", 0) < -3,
        "warm_environment": color.get("mid_b", 0) > 6,
        "low_key": color.get("luma_p50", 0.5) < 0.3,
    }


def samples_from_analyses(analyses: list[dict]) -> list[ShotSample]:
    out = []
    for analysis in analyses:
        for shot in analysis.get("shots", []):
            if not shot.get("color"):
                continue
            out.append(
                ShotSample(
                    reel=analysis["media_id"],
                    shot_id=shot["id"],
                    conditions=describe_conditions(shot),
                    look={k: float(shot["color"][k]) for k in TARGETS},
                    extras={"thumb": shot.get("thumb")},
                )
            )
    return out


def _cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or len(b) < 2:
        return 0.0
    pooled = np.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
    return float((a.mean() - b.mean()) / max(pooled, 1e-6))


def _loro_gain(samples: list[ShotSample], cond: tuple[str, ...], target: str) -> float:
    """Relative MSE reduction of the conditional mean vs the universal mean, leave-one-reel-out."""
    reels = sorted({s.reel for s in samples})
    base_err, cond_err = 0.0, 0.0
    counted = 0
    for held in reels:
        train = [s for s in samples if s.reel != held]
        test = [s for s in samples if s.reel == held]
        if not train or not test:
            continue
        universal = np.mean([s.look[target] for s in train])
        match_train = [s.look[target] for s in train if all(s.conditions[c] for c in cond)]
        other_train = [s.look[target] for s in train if not all(s.conditions[c] for c in cond)]
        if len(match_train) < 2:
            continue
        m_mean = float(np.mean(match_train))
        o_mean = float(np.mean(other_train)) if other_train else universal
        for s in test:
            pred = m_mean if all(s.conditions[c] for c in cond) else o_mean
            base_err += (s.look[target] - universal) ** 2
            cond_err += (s.look[target] - pred) ** 2
            counted += 1
    if not counted or base_err <= 1e-9:
        return 0.0
    return float((base_err - cond_err) / base_err)


def _rule_id(profile: str, cond: tuple[str, ...], target: str) -> str:
    digest = hashlib.sha1(f"{profile}:{'+'.join(cond)}:{target}".encode()).hexdigest()[:10]
    return f"color.{'_'.join(cond)}.{target}.{digest}"


def induce_conditional_rules(samples: list[ShotSample], profile: str, *, max_conditions: int = 2) -> list[StyleRule]:
    if len({s.reel for s in samples}) < MIN_REELS:
        return []
    names = sorted(samples[0].conditions) if samples else []
    candidates: list[StyleRule] = []
    combos = [(c,) for c in names] + (list(combinations(names, 2)) if max_conditions >= 2 else [])
    for cond in combos:
        matched = [s for s in samples if all(s.conditions[c] for c in cond)]
        rest = [s for s in samples if not all(s.conditions[c] for c in cond)]
        reels = {s.reel for s in matched}
        if len(matched) < MIN_SUPPORT_SHOTS or len(reels) < MIN_REELS or len(rest) < 4:
            continue
        for target, (label, unit) in TARGETS.items():
            a = np.asarray([s.look[target] for s in matched])
            b = np.asarray([s.look[target] for s in rest])
            d = _cohens_d(a, b)
            if abs(d) < MIN_EFFECT_SIZE:
                continue
            gain = _loro_gain(samples, cond, target)
            if gain < MIN_LORO_GAIN:
                continue
            direction = "higher" if d > 0 else "lower"
            contradictions = [s for s in matched if (s.look[target] - b.mean()) * np.sign(d) < 0]
            confidence = float(np.clip(0.45 + 0.15 * min(abs(d), 2) + 0.6 * gain + 0.01 * min(len(matched), 30) - 0.3 * len(contradictions) / len(matched), 0.05, 0.97))
            when = " + ".join(cond)
            statement = (
                f"WHEN {when} THEN {label} {direction}: {a.mean():.3g} vs {b.mean():.3g} {unit} "
                f"(d={d:.2f}, LORO gain {gain * 100:.0f}%, {len(matched)} shots / {len(reels)} reels)"
            )
            candidates.append(
                StyleRule(
                    id=_rule_id(profile, cond, target),
                    dimension="color",
                    profile=profile,
                    kind="conditional",
                    when={c: True for c in cond},
                    prefer={target: round(float(a.mean()), 4), "baseline": round(float(b.mean()), 4), "direction": direction},
                    confidence=round(confidence, 3),
                    support=len(matched),
                    contradictions=len(contradictions),
                    reels=len(reels),
                    effect_size=round(d, 3),
                    validation_gain=round(gain, 3),
                    statement=statement,
                    evidence=[s.shot_id for s in matched[:12]],
                    counter_examples=[s.shot_id for s in contradictions[:6]],
                    origin="induction",
                )
            )
    # keep the strongest rule per (condition set, target family) and drop pairs dominated by a single condition
    candidates.sort(key=lambda r: (-(r.validation_gain or 0), -abs(r.effect_size or 0)))
    kept: list[StyleRule] = []
    for rule in candidates:
        target = next(k for k in rule.prefer if k in TARGETS)
        dominated = any(
            target in other.prefer
            and set(other.when) < set(rule.when)
            and (other.validation_gain or 0) >= (rule.validation_gain or 0) - 0.02
            for other in kept
        )
        if not dominated:
            kept.append(rule)
    return kept[:40]


def mine_principles(analyses: list[dict], profile: str) -> list[StyleRule]:
    """Universal principles: properties consistent across >= 70% of a profile's reels."""
    n = len(analyses)
    if n < 2:
        return []
    rules: list[StyleRule] = []

    def add(rule_key: str, dimension: str, statement: str, prefer: dict, support: int, consistency: float, evidence: list[str]):
        rules.append(
            StyleRule(
                id=f"{dimension}.principle.{rule_key}.{hashlib.sha1(profile.encode()).hexdigest()[:6]}",
                dimension=dimension,
                profile=profile,
                kind="principle",
                prefer=prefer,
                confidence=round(float(np.clip(0.4 + 0.5 * consistency + 0.02 * min(n, 20), 0, 0.97)), 3),
                support=support,
                reels=n,
                statement=statement,
                evidence=evidence[:12],
                origin="principle_mining",
            )
        )

    medians = [a["pacing"].get("median_s") for a in analyses if a.get("pacing")]
    if len(medians) >= 2:
        med = np.asarray(medians)
        spread = float(np.std(med) / max(np.mean(med), 1e-6))
        if spread < 0.45:
            p10 = float(np.median([a["pacing"]["p10_s"] for a in analyses if a.get("pacing")]))
            p90 = float(np.median([a["pacing"]["p90_s"] for a in analyses if a.get("pacing")]))
            add(
                "shot_length",
                "rhythm",
                f"median shot length {np.median(med):.2f}s (p10 {p10:.2f}s, p90 {p90:.2f}s) across {len(med)} reels",
                {"median_s": float(np.median(med)), "p10_s": p10, "p90_s": p90},
                len(med),
                1 - spread,
                [a["media_id"] for a in analyses],
            )
    beat = [a["beat_relation"]["within_40ms"] for a in analyses if a.get("beat_relation")]
    if len(beat) >= 2 and np.mean(np.asarray(beat) > 0.55) >= 0.7:
        add("beat_lock", "rhythm", f"cuts lock to the beat ({np.mean(beat) * 100:.0f}% within 40 ms)", {"beat_sync": float(np.mean(beat))}, len(beat), float(np.mean(np.asarray(beat) > 0.55)), [a["media_id"] for a in analyses])
    seps = []
    for a in analyses:
        shots = [s for s in a.get("shots", []) if s.get("color")]
        if shots:
            seps.append(float(np.median([s["color"]["high_b"] - s["color"]["shadow_b"] for s in shots])))
    if len(seps) >= 2:
        sign = np.sign(np.median(seps))
        consistent = float(np.mean(np.sign(seps) == sign)) if sign else 0.0
        if consistent >= 0.7 and abs(np.median(seps)) > 3:
            add(
                "warm_cool_separation",
                "color",
                ("warm highlights over cool shadows" if sign > 0 else "cool highlights over warm shadows")
                + f" (median Δb* {np.median(seps):.1f})",
                {"highlight_minus_shadow_b": float(np.median(seps))},
                len(seps),
                consistent,
                [a["media_id"] for a in analyses],
            )
    blacks = [float(np.median([s["color"]["black_point"] for s in a["shots"] if s.get("color")])) for a in analyses if any(s.get("color") for s in a.get("shots", []))]
    if len(blacks) >= 2:
        if np.mean(np.asarray(blacks) < 0.02) >= 0.7:
            add("deep_blacks", "color", "deep, near-crushed blacks", {"black_point_max": 0.02}, len(blacks), float(np.mean(np.asarray(blacks) < 0.02)), [])
        elif np.mean(np.asarray(blacks) > 0.06) >= 0.7:
            add("matte_blacks", "color", "lifted, matte blacks", {"black_point_min": 0.06}, len(blacks), float(np.mean(np.asarray(blacks) > 0.06)), [])
    text_reels = [a for a in analyses if a.get("text_events")]
    if text_reels:
        share = len(text_reels) / n
        positions = [t["position"][1] for a in text_reels for t in a["text_events"]]
        classes = [((t.get("typography") or {}).get("font_class")) for a in text_reels for t in a["text_events"] if t.get("typography")]
        if share >= 0.7 and positions:
            top = max(set(classes), key=classes.count) if classes else "sans"
            add(
                "restrained_typography",
                "typography",
                f"on-screen text in {share * 100:.0f}% of reels, typically {top}, centred around y={np.median(positions):.2f}",
                {"font_class": top, "y": float(np.median(positions)), "texts_per_reel": float(np.mean([len(a['text_events']) for a in text_reels]))},
                len(text_reels),
                share,
                [a["media_id"] for a in text_reels],
            )
        elif share <= 0.3:
            add("minimal_text", "typography", "text is rare or absent", {"texts_per_reel": 0.0}, n - len(text_reels), 1 - share, [])
    hooks = [a["structure"]["hook"]["first_shot_s"] for a in analyses if a.get("structure")]
    if len(hooks) >= 2 and np.mean(np.asarray(hooks) < 1.2) >= 0.7:
        add("fast_hook", "rhythm", f"hook shot under 1.2 s (median {np.median(hooks):.2f}s)", {"first_shot_max_s": 1.2}, len(hooks), float(np.mean(np.asarray(hooks) < 1.2)), [])
    return rules
