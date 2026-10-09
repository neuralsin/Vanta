"""The Style Brain (manual 9): profiles, per-dimension taste, rules, retrieval, KB.

Profiles isolate coherent modes and are never averaged together. Dimension
weights in the Edit Spec are the only sanctioned way to borrow across
references. Approved rules are authoritative; candidates are low-precedence
observations until the user approves them in the review queue.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from vanta.analysis.pipeline import load_analysis
from vanta.config import Settings, get_settings
from vanta.core.schemas import DIMENSIONS, EditSpec
from vanta.storage.library import Library
from vanta.style.capsules import build_capsules
from vanta.style.induction import (
    describe_conditions,
    induce_conditional_rules,
    mine_principles,
    samples_from_analyses,
)

STYLE_SCHEMA_VERSION = "style-v2"

# precedence ladder (manual 3): lower number wins
PRECEDENCE = {
    "safety": 1,
    "instruction": 2,
    "brief": 3,
    "correction": 4,
    "rule_high": 5,
    "principle": 6,
    "observation": 7,
}


def _reel_stats(analysis: dict) -> dict[str, Any]:
    """Per-reel, per-dimension statistics stored for weighted borrowing."""
    shots = analysis.get("shots", [])
    colored = [s for s in shots if s.get("color")]
    look = {}
    if colored:
        for key in ("luma_p05", "luma_p50", "luma_p95", "contrast", "black_point", "shadow_a", "shadow_b", "mid_a", "mid_b", "high_a", "high_b", "chroma_mean", "chroma_shadow", "chroma_high"):
            look[key] = float(np.median([s["color"][key] for s in colored]))
        skin = [s["color"]["skin_hue"] for s in colored if s["color"].get("skin_hue") is not None]
        look["skin_hue"] = float(np.median(skin)) if skin else None
    texts = analysis.get("text_events", [])
    duration = max(analysis.get("duration_s", 0.0), 1e-3)
    transitions = Counter(b["kind"] for b in analysis.get("boundaries", []))
    effects = Counter(e["type"] for s in shots for e in s.get("effects", []))
    effects.update(e["type"] for s in shots for e in (s.get("motion") or {}).get("speed_events", []))
    audio = analysis.get("audio") or {}
    return {
        "reference_id": analysis["media_id"],
        "title": analysis.get("title"),
        "duration_s": duration,
        "rhythm": {
            **(analysis.get("pacing") or {}),
            "curve": analysis.get("pacing_curve"),
            "beat": analysis.get("beat_relation"),
            "transitions_per_min": {k: v / duration * 60 for k, v in transitions.items()},
            "structure": analysis.get("structure"),
        },
        "color": look,
        "typography": {
            "count": len(texts),
            "per_min": len(texts) / duration * 60,
            "positions": [t["position"] for t in texts],
            "font_classes": [((t.get("typography") or {}).get("font_class")) for t in texts if t.get("typography")],
            "cases": [((t.get("typography") or {}).get("case")) for t in texts if t.get("typography")],
            "sizes": [((t.get("typography") or {}).get("size_frac")) for t in texts if t.get("typography")],
            "fills": [((t.get("typography") or {}).get("fill_hex")) for t in texts if t.get("typography")],
            "outline_share": float(np.mean([bool((t.get("typography") or {}).get("outline")) for t in texts])) if texts else 0.0,
            "animations": [t["animation"] for t in texts],
            "caption_like": sum(1 for t in texts if t["position"][1] > 0.55 and len(t.get("words", [])) >= 2),
        },
        "composition": {
            "shot_sizes": Counter((s.get("composition") or {}).get("shot_size", "unknown") for s in shots),
            "thirds": float(np.mean([(s.get("composition") or {}).get("thirds_score", 0) for s in shots])) if shots else 0.0,
            "negative_space": float(np.mean([(s.get("composition") or {}).get("negative_space", 0) for s in shots])) if shots else 0.0,
        },
        "motion": {
            "moves": Counter((s.get("motion") or {}).get("camera_move", "unknown") for s in shots),
            "intensity": float(np.mean([(s.get("motion") or {}).get("intensity", 0) for s in shots])) if shots else 0.0,
            "speed_events_per_min": sum(len((s.get("motion") or {}).get("speed_events", [])) for s in shots) / duration * 60,
        },
        "masking": {"text_behind_subject": sum(1 for t in texts if t.get("occlusion_observed"))},
        "fusion": dict(effects),
        "audio": {k: audio.get(k) for k in ("bpm", "mean_energy", "mean_brightness", "key", "drops_s")} if audio and not audio.get("error") else {},
    }


def _weighted_median(values: list[float], weights: list[float]) -> float | None:
    pairs = [(v, w) for v, w in zip(values, weights, strict=True) if v is not None and w > 0]
    if not pairs:
        return None
    pairs.sort()
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return float(v)
    return float(pairs[-1][0])


@dataclass
class StylePackage:
    """Contextual style package assembled at plan time (manual 9.5)."""

    profile_id: str | None
    profile_name: str | None
    dimension_weights: dict[str, dict[str, float]]
    rhythm: dict[str, Any] = field(default_factory=dict)
    color: dict[str, Any] = field(default_factory=dict)
    typography: dict[str, Any] = field(default_factory=dict)
    composition: dict[str, Any] = field(default_factory=dict)
    motion: dict[str, Any] = field(default_factory=dict)
    masking: dict[str, Any] = field(default_factory=dict)
    fusion: dict[str, Any] = field(default_factory=dict)
    audio: dict[str, Any] = field(default_factory=dict)
    rules: list[dict] = field(default_factory=list)
    negatives: list[str] = field(default_factory=list)
    per_shot: dict[str, dict] = field(default_factory=dict)
    techniques: list[dict] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def summary_for_prompt(self, max_rules: int = 14) -> dict:
        return {
            "profile": self.profile_name,
            "rhythm": {k: self.rhythm.get(k) for k in ("median_s", "p10_s", "p90_s", "cuts_per_minute", "beat_sync", "first_shot_s")},
            "color_intent": self.color.get("intent"),
            "color_relations": self.color.get("relations"),
            "typography": {k: self.typography.get(k) for k in ("font_class", "case", "y", "texts_per_reel", "animation")},
            "effects": self.fusion.get("top", [])[:8],
            "rules": [r.get("statement") for r in self.rules[:max_rules]],
            "never": self.negatives,
        }


class StyleBrain:
    def __init__(self, library: Library, settings: Settings | None = None):
        self.library = library
        self.settings = settings or get_settings()

    # ------------------------------------------------------------ profiles
    def assign(self, media_id: str, profile_name: str) -> dict:
        profile = self.library.ensure_profile(profile_name)
        self.library.update_media(media_id, profile_id=profile["id"])
        return profile

    def suggest_profile(self, media: dict) -> str:
        analysis = load_analysis(media, self.settings) or {}
        hint = ((analysis.get("vlm") or {}).get("profile_hint") or "").strip().lower()
        return hint or "my-style"

    def references(self, profile_id: str) -> list[dict]:
        return [
            m
            for m in self.library.list_media("reference", enabled_only=True)
            if m.get("profile_id") == profile_id and m.get("analysis_status") == "done"
        ]

    def analyses(self, profile_id: str) -> list[dict]:
        out = []
        for media in self.references(profile_id):
            analysis = load_analysis(media, self.settings)
            if analysis:
                out.append(analysis)
        return out

    # -------------------------------------------------------------- learn
    def ingest_reference(self, media: dict) -> list[dict]:
        """Build capsules for one analysed reference (stored, unapproved)."""
        analysis = load_analysis(media, self.settings)
        if not analysis:
            raise ValueError(f"{media['id']} has not been analysed")
        capsules = [c.model_dump(mode="json") for c in build_capsules(analysis)]
        self.library.replace_capsules(media["id"], capsules)
        return capsules

    def rebuild_profile(self, profile_id: str) -> dict:
        """Recompute statistics, principles and candidate rules for one profile."""
        profile = self.library.get_profile(profile_id)
        if not profile:
            raise KeyError(profile_id)
        analyses = self.analyses(profile_id)
        for media in self.references(profile_id):
            if not self.library.list_capsules(media["id"]):
                self.ingest_reference(media)
        reels = [_reel_stats(a) for a in analyses]
        principles = mine_principles(analyses, profile["name"])
        conditional = induce_conditional_rules(samples_from_analyses(analyses), profile["name"])
        for rule in [*principles, *conditional]:
            payload = rule.model_dump(mode="json")
            payload["profile"] = profile_id
            self.library.upsert_rule(payload)
        data = {
            "schema": STYLE_SCHEMA_VERSION,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "reels": reels,
            "reference_count": len(reels),
            "readiness": self.readiness(len(reels)),
            "vlm_intents": [
                {"reference_id": a["media_id"], "color": (a.get("vlm") or {}).get("color_intent"), "typography": (a.get("vlm") or {}).get("typography_intent"), "rhythm": (a.get("vlm") or {}).get("rhythm_intent")}
                for a in analyses
                if a.get("vlm")
            ],
        }
        self.library.update_profile(profile_id, data_json=data)
        self.export_kb(profile_id)
        return data

    @staticmethod
    def readiness(n_reels: int) -> dict[str, Any]:
        """Manual 9.3 sample-size guidance, shown honestly in the UI."""
        if n_reels < 3:
            level, note = "seed", "Fewer than 3 reels: statistics only, no conditional rules yet."
        elif n_reels < 15:
            level, note = "forming", "Principles forming; 15-25 reels give stable universal principles."
        elif n_reels < 40:
            level, note = "stable", "Stable principles; rich conditional rules usually need 40+ reels."
        else:
            level, note = "rich", "Enough reels for conditional rules; variety now beats volume."
        return {"level": level, "reels": n_reels, "note": note}

    # ----------------------------------------------------------- retrieval
    def dimension_weights(self, profile_id: str | None, spec: EditSpec | None) -> dict[str, dict[str, float]]:
        refs = self.references(profile_id) if profile_id else []
        disabled = set(spec.disabled_references if spec else [])
        weights: dict[str, dict[str, float]] = {dim: {} for dim in DIMENSIONS}
        for media in refs:
            if media["id"] in disabled:
                continue
            for dim in DIMENSIONS:
                weights[dim][media["id"]] = 1.0
        if spec:
            for ref_id, dims in spec.reference_weights.items():
                if ref_id in disabled:
                    continue
                explicit = set(dims)
                for dim in DIMENSIONS:
                    if dim in explicit:
                        weights[dim][ref_id] = dims[dim]
                # a reference named with explicit dims only lends those dimensions
                if explicit and ref_id not in {m["id"] for m in refs}:
                    for dim in DIMENSIONS:
                        weights[dim].setdefault(ref_id, 0.0)
            # explicit weights dominate: when any reference has an explicit weight for a dim,
            # unnamed references in that dim fall back to a low share
            for dim in DIMENSIONS:
                if any(dim in dims for dims in spec.reference_weights.values()):
                    for ref_id in list(weights[dim]):
                        if dim not in spec.reference_weights.get(ref_id, {}):
                            weights[dim][ref_id] = min(weights[dim][ref_id], 0.15)
        return weights

    def _reel_stats_for(self, ref_ids: list[str], profile_id: str | None) -> dict[str, dict]:
        stats: dict[str, dict] = {}
        if profile_id:
            profile = self.library.get_profile(profile_id) or {}
            for reel in (profile.get("data_json") or {}).get("reels", []):
                stats[reel["reference_id"]] = reel
        for ref_id in ref_ids:
            if ref_id not in stats:
                media = self.library.get_media(ref_id)
                analysis = load_analysis(media, self.settings) if media else None
                if analysis:
                    stats[ref_id] = _reel_stats(analysis)
        return stats

    def retrieve(self, profile_id: str | None, spec: EditSpec, target_shots: list[dict] | None = None) -> StylePackage:
        weights = self.dimension_weights(profile_id, spec)
        all_refs = sorted({r for dim in weights.values() for r in dim})
        stats = self._reel_stats_for(all_refs, profile_id)
        profile = self.library.get_profile(profile_id) if profile_id else None
        pkg = StylePackage(profile_id=profile_id, profile_name=profile["name"] if profile else None, dimension_weights=weights)

        def wm(dim: str, getter) -> float | None:
            refs = [r for r, w in weights[dim].items() if w > 0 and r in stats]
            return _weighted_median([getter(stats[r]) for r in refs], [weights[dim][r] for r in refs])

        pkg.rhythm = {
            "median_s": wm("rhythm", lambda s: s["rhythm"].get("median_s")),
            "p10_s": wm("rhythm", lambda s: s["rhythm"].get("p10_s")),
            "p90_s": wm("rhythm", lambda s: s["rhythm"].get("p90_s")),
            "cuts_per_minute": wm("rhythm", lambda s: s["rhythm"].get("cuts_per_minute")),
            "beat_sync": wm("rhythm", lambda s: (s["rhythm"].get("beat") or {}).get("within_40ms")),
            "first_shot_s": wm("rhythm", lambda s: s["rhythm"].get("first_shot_s")),
            "escalation": wm("rhythm", lambda s: (s["rhythm"].get("structure") or {}).get("escalation")),
            "transitions_per_min": _merge_counts([stats[r]["rhythm"].get("transitions_per_min", {}) for r, w in weights["rhythm"].items() if w > 0 and r in stats]),
        }
        look_keys = ("luma_p05", "luma_p50", "luma_p95", "contrast", "black_point", "shadow_a", "shadow_b", "mid_a", "mid_b", "high_a", "high_b", "chroma_mean", "chroma_shadow", "chroma_high")
        target_look = {k: wm("color", lambda s, k=k: (s.get("color") or {}).get(k)) for k in look_keys}
        pkg.color = {"target_look": {k: v for k, v in target_look.items() if v is not None}}
        if target_look.get("high_b") is not None and target_look.get("shadow_b") is not None:
            sep = target_look["high_b"] - target_look["shadow_b"]
            pkg.color["relations"] = {
                "warm_cool_separation": round(sep, 2),
                "statement": "warm highlights over cool shadows" if sep > 3 else ("cool highlights over warm shadows" if sep < -3 else "neutral tonal colour balance"),
            }
        intents = ((profile or {}).get("data_json") or {}).get("vlm_intents", [])
        colour_intents = [i["color"] for i in intents if i.get("color") and weights["color"].get(i["reference_id"], 0) > 0.5]
        pkg.color["intent"] = colour_intents[0] if colour_intents else pkg.color.get("relations", {}).get("statement")
        typo_refs = [r for r, w in weights["typography"].items() if w > 0 and r in stats]
        fonts = Counter(f for r in typo_refs for f in stats[r]["typography"]["font_classes"] if f)
        cases = Counter(c for r in typo_refs for c in stats[r]["typography"]["cases"] if c)
        anims = Counter(a for r in typo_refs for a in stats[r]["typography"]["animations"])
        ys = [p[1] for r in typo_refs for p in stats[r]["typography"]["positions"]]
        xs = [p[0] for r in typo_refs for p in stats[r]["typography"]["positions"]]
        sizes = [s for r in typo_refs for s in stats[r]["typography"]["sizes"] if s]
        fills = Counter(f for r in typo_refs for f in stats[r]["typography"]["fills"] if f)
        pkg.typography = {
            "font_class": fonts.most_common(1)[0][0] if fonts else "bold sans",
            "case": cases.most_common(1)[0][0] if cases else "upper",
            "animation": anims.most_common(1)[0][0] if anims else "static",
            "x": float(np.median(xs)) if xs else 0.5,
            "y": float(np.median(ys)) if ys else 0.42,
            "size": float(np.median(sizes)) if sizes else 0.05,
            "fill": fills.most_common(1)[0][0] if fills else "#ffffff",
            "outline": float(np.mean([stats[r]["typography"]["outline_share"] for r in typo_refs])) > 0.5 if typo_refs else False,
            "texts_per_reel": float(np.mean([stats[r]["typography"]["count"] for r in typo_refs])) if typo_refs else 0.0,
            "captions": float(np.mean([stats[r]["typography"]["caption_like"] > 0 for r in typo_refs])) if typo_refs else 0.0,
        }
        comp_refs = [r for r, w in weights["composition"].items() if w > 0 and r in stats]
        pkg.composition = {
            "shot_sizes": _merge_counts([dict(stats[r]["composition"]["shot_sizes"]) for r in comp_refs]),
            "thirds": float(np.mean([stats[r]["composition"]["thirds"] for r in comp_refs])) if comp_refs else 0.5,
        }
        mot_refs = [r for r, w in weights["motion"].items() if w > 0 and r in stats]
        pkg.motion = {
            "moves": _merge_counts([dict(stats[r]["motion"]["moves"]) for r in mot_refs]),
            "intensity": float(np.mean([stats[r]["motion"]["intensity"] for r in mot_refs])) if mot_refs else 0.4,
            "speed_events_per_min": float(np.mean([stats[r]["motion"]["speed_events_per_min"] for r in mot_refs])) if mot_refs else 0.0,
        }
        mask_refs = [r for r, w in weights["masking"].items() if w > 0 and r in stats]
        pkg.masking = {"text_behind_subject_share": float(np.mean([stats[r]["masking"]["text_behind_subject"] > 0 for r in mask_refs])) if mask_refs else 0.0}
        fx_refs = [r for r, w in weights["fusion"].items() if w > 0 and r in stats]
        share = Counter()
        for r in fx_refs:
            for name in stats[r]["fusion"]:
                share[name] += weights["fusion"][r]
        total = sum(weights["fusion"][r] for r in fx_refs) or 1.0
        pkg.fusion = {"top": [{"effect": k, "share": round(v / total, 3)} for k, v in share.most_common()]}
        audio_refs = [r for r, w in weights["audio"].items() if w > 0 and r in stats and stats[r].get("audio")]
        pkg.audio = {
            "bpm": _weighted_median([stats[r]["audio"].get("bpm") for r in audio_refs], [weights["audio"][r] for r in audio_refs]),
            "energy": _weighted_median([stats[r]["audio"].get("mean_energy") for r in audio_refs], [weights["audio"][r] for r in audio_refs]),
            "drop_usage": float(np.mean([bool(stats[r]["audio"].get("drops_s")) for r in audio_refs])) if audio_refs else 0.0,
        }
        rules = self.library.list_rules(profile_id=profile_id) if profile_id else self.library.list_rules()
        usable = [r for r in rules if r["approval_state"] in {"approved", "candidate"}]
        usable.sort(key=lambda r: (r["approval_state"] != "approved", -r["confidence"]))
        pkg.rules = [
            {**r["json"], "approval_state": r["approval_state"], "precedence": PRECEDENCE["rule_high"] if r["approval_state"] == "approved" and r["kind"] != "principle" else (PRECEDENCE["principle"] if r["approval_state"] == "approved" else PRECEDENCE["observation"])}
            for r in usable
        ]
        pkg.negatives = sorted(
            set(spec.never)
            | {a for r in usable if r["approval_state"] == "approved" for a in r["json"].get("avoid", [])}
            | {r["json"].get("statement", "") for r in usable if r["kind"] == "negative" and r["approval_state"] == "approved"}
        )
        capsules = [c for r in all_refs for c in self.library.list_capsules(r)]
        pkg.techniques = [
            {
                "technique": c["technique"],
                "dimension": c["dimension"],
                "reference": c["reference_id"],
                "confidence": c["confidence"],
                "purpose": c["json"].get("purpose"),
                "implementation": c["json"].get("implementation"),
                "conditions": c["json"].get("conditions"),
                "approved": bool(c["approved"]),
                "weight": weights.get(c["dimension"], {}).get(c["reference_id"], 0.0) if c["dimension"] in weights else 0.0,
                "evidence": [e.get("artifact") for e in c["json"].get("evidence", [])][:2],
            }
            for c in capsules
            if c["technique"] not in {"pacing", "look", "framing", "camera_motion", "music"}
        ]
        for shot in target_shots or []:
            pkg.per_shot[shot["id"]] = self.shot_context(shot, pkg)
        pkg.provenance = {
            "style_schema": STYLE_SCHEMA_VERSION,
            "references": all_refs,
            "approved_rules": [r["id"] for r in usable if r["approval_state"] == "approved"],
            "candidate_rules": [r["id"] for r in usable if r["approval_state"] == "candidate"],
        }
        return pkg

    def shot_context(self, shot: dict, pkg: StylePackage) -> dict:
        """Code evaluates which rule conditions are true for one target shot."""
        conditions = describe_conditions(shot)
        matched = [
            r
            for r in pkg.rules
            if r.get("kind") == "conditional" and r.get("when") and all(conditions.get(c) == v for c, v in r["when"].items())
        ]
        exemplars: list[tuple[str, float]] = []
        model = shot.get("embedding_model")
        if model:
            vec_ids, matrix = self.library.get_embeddings(model, owner_prefix=shot["id"])
            if vec_ids:
                refs = [r for r, w in pkg.dimension_weights["color"].items() if w > 0]
                for ref in refs:
                    exemplars.extend(self.library.nearest(model, matrix[0], k=3, owner_prefix=ref))
                exemplars.sort(key=lambda x: -x[1])
        return {"conditions": conditions, "rules": [r["id"] for r in matched], "rule_effects": [r.get("prefer") for r in matched], "exemplars": exemplars[:5]}

    # ------------------------------------------------------------------ KB
    def export_kb(self, profile_id: str) -> Path:
        """Human-readable, git-versionable knowledge base of approved knowledge."""
        profile = self.library.get_profile(profile_id)
        if not profile:
            raise KeyError(profile_id)
        folder = self.settings.kb_dir / profile["name"]
        folder.mkdir(parents=True, exist_ok=True)
        rules = self.library.list_rules(profile_id=profile_id)
        approved = [r["json"] | {"approval_state": "approved"} for r in rules if r["approval_state"] == "approved"]
        rejected = [{"id": r["id"], "statement": r["json"].get("statement")} for r in rules if r["approval_state"] == "rejected"]
        payload = {
            "profile": profile["name"],
            "description": profile.get("description"),
            "schema": STYLE_SCHEMA_VERSION,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "readiness": ((profile.get("data_json") or {}).get("readiness")),
            "approved_rules": approved,
            "rejected_rules": rejected,
        }
        path = folder / "style.yaml"
        path.write_text(yaml.safe_dump(json.loads(json.dumps(payload, default=str)), sort_keys=False, allow_unicode=True), encoding="utf-8")
        return path

    def stale_rules(self, months: int = 6) -> list[dict]:
        """Rules not reconfirmed for ``months`` are flagged (manual 18)."""
        cutoff = time.time() - months * 30 * 86400
        out = []
        for rule in self.library.list_rules(state="approved"):
            stamp = rule.get("last_confirmed") or rule.get("created_at")
            try:
                ts = time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
            except (TypeError, ValueError):
                continue
            if ts < cutoff:
                out.append(rule)
        return out


def _merge_counts(items: list[dict]) -> dict[str, float]:
    total = Counter()
    for item in items:
        total.update({k: float(v) for k, v in item.items()})
    norm = sum(total.values()) or 1.0
    return {k: round(v / norm, 3) for k, v in total.most_common()}
