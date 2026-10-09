"""Preflight: what a run will cost and what would make it weak, before any hours are spent.

A long-footage edit is a 2-7 hour job on the guaranteed hardware (RTX 4050
6 GB, 12 GB RAM). The estimate uses per-stage throughput constants for that
budget (see docs/DEFERRED_VERIFICATION.md: they are calibrated by the deferred
benchmarks) and the readiness checks name everything that would silently lower
quality: no director model, unlicensed music, thin style evidence, unresolved
camera log, ungated Resolve operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vanta.config import Settings, get_settings
from vanta.core.schemas import EditSpec
from vanta.storage.library import Library

# minutes of work per minute of source on the guaranteed hardware (pre-benchmark estimates)
ANALYSIS_MIN_PER_MIN = 0.55  # decode, flow, shots, colour, faces, OCR, embeddings
VLM_SECONDS_PER_BATCH = 80.0  # one 16-moment footage-logging call on qwen3-vl:8b with partial offload
MOMENT_S = 4.0
WHISPER_MIN_PER_MIN = 0.12
PLAN_MIN = 12.0  # director passes with thinking + grade solve for ~15 shots
MATTE_MIN_PER_SHOT = 1.5
PREVIEW_MIN = 3.0
REPAIR_LOOP_MIN = 7.0  # preview render + QC + critic, per iteration (max 3)
BUILD_MIN = 6.0  # Resolve build with read-back verification + proxy render
BUDGET_H = 7.0


@dataclass
class Preflight:
    estimate_min: dict[str, float] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    good: list[str] = field(default_factory=list)

    @property
    def total_h(self) -> float:
        return round(sum(self.estimate_min.values()) / 60, 2)

    def to_dict(self) -> dict:
        return {"estimate_min": self.estimate_min, "total_h": self.total_h, "blockers": self.blockers, "warnings": self.warnings, "good": self.good}


def run_preflight(library: Library, spec: EditSpec, footage_ids: list[str], *, profile_id: str | None = None, settings: Settings | None = None, model_ready: tuple[bool, str] | None = None, gates_ok: dict[str, bool] | None = None) -> Preflight:
    settings = settings or get_settings()
    pf = Preflight()
    footage = [m for m in (library.get_media(i) for i in footage_ids) if m]
    if not footage:
        pf.blockers.append("no footage selected")
        return pf
    from vanta.analysis.pipeline import is_outdated

    pending = [m for m in footage if m.get("analysis_status") != "done" or is_outdated(m)]  # outdated = re-analysis
    pending_min = sum((m.get("duration_s") or 0) for m in pending) / 60
    total_min = sum((m.get("duration_s") or 0) for m in footage) / 60
    if pending:
        moments = pending_min * 60 / MOMENT_S
        pf.estimate_min["analysis"] = round(pending_min * ANALYSIS_MIN_PER_MIN, 1)
        pf.estimate_min["footage_logging"] = round(moments / 16 * VLM_SECONDS_PER_BATCH / 60, 1)
        if any(m.get("has_audio") for m in pending):
            pf.estimate_min["transcription"] = round(pending_min * WHISPER_MIN_PER_MIN, 1)
    pf.estimate_min["planning"] = PLAN_MIN
    sl = spec.sliders
    expected_mattes = (1 if sl.masking >= 0.35 else 0) + (1 if sl.glow >= 0.5 and sl.masking >= 0.3 else 0)
    if expected_mattes:
        pf.estimate_min["mattes"] = expected_mattes * MATTE_MIN_PER_SHOT
    pf.estimate_min["preview_and_repair"] = PREVIEW_MIN + 3 * REPAIR_LOOP_MIN
    pf.estimate_min["resolve_build"] = BUILD_MIN
    if pf.total_h > BUDGET_H:
        pf.warnings.append(f"estimated {pf.total_h:.1f} h exceeds the {BUDGET_H:.0f} h budget: disable the weakest clips or analyse them overnight first")
    else:
        pf.good.append(f"estimated {pf.total_h:.1f} h (budget {BUDGET_H:.0f} h)")

    outdated = [m.get("title") or m["id"] for m in footage if is_outdated(m)]
    if outdated:
        pf.warnings.append(f"{len(outdated)} clip(s) were analysed by an older VANTA (colour measured before the matrix fix): re-analyse them in Footage — {', '.join(outdated[:4])}")
    # footage sufficiency
    if total_min * 60 < spec.duration_s * 3:
        pf.warnings.append(f"only {total_min * 60:.0f} s of footage for a {spec.duration_s:.0f} s edit: shot choice will be thin")
    if not 30 <= spec.duration_s <= 45:
        pf.warnings.append(f"duration {spec.duration_s:.0f} s is outside the 30-45 s reel target")
    # camera log
    for m in footage:
        info = m.get("info_json") or {}
        if info.get("color_profile"):
            continue
        transfer = str(info.get("color_transfer") or "").casefold()
        if "log" in transfer or "arib" in transfer or "2084" in transfer:
            pf.warnings.append(f"{m.get('title')}: HDR/log transfer flagged; set the exact camera profile in Footage for perfect colour")
    # style evidence
    if profile_id:
        refs = [m for m in library.list_media("reference", enabled_only=True) if m.get("profile_id") == profile_id and m.get("analysis_status") == "done"]
        approved = library.list_rules(profile_id=profile_id, state="approved")
        stale_refs = [m for m in library.list_media("reference", enabled_only=True) if m.get("profile_id") == profile_id and is_outdated(m)]
        if stale_refs:
            pf.warnings.append(f"{len(stale_refs)} reference reel(s) were measured by an older VANTA: re-analyse them so the learned colour targets are exact")
        if len(refs) < 3:
            pf.warnings.append(f"style profile has {len(refs)} analysed reference reels: 3+ are needed for reliable style induction")
        else:
            pf.good.append(f"{len(refs)} analysed reference reels")
        if not approved:
            pf.warnings.append("no approved style rules yet: VANTA uses measured reference statistics only (approve rules in Style)")
    else:
        pf.warnings.append("no style profile selected: the edit follows defaults, not your references")
    # music
    music = [m for m in library.list_media("music", enabled_only=True) if m.get("analysis_status") == "done"]
    licensed = [m for m in music if (m.get("rights_json") or {}).get("licence_confirmed") or (m.get("rights_json") or {}).get("platform_cleared")]
    if not music:
        pf.warnings.append("no analysed music: cuts follow a silent guide grid; add the platform sound in the app")
    elif not licensed:
        pf.warnings.append("no music with a confirmed licence: final render will be blocked until rights are confirmed")
    else:
        pf.good.append(f"{len(licensed)} licensed track(s)")
    # director model
    if model_ready is not None:
        ok, why = model_ready
        if ok:
            pf.good.append(f"creative director: {why}")
        else:
            pf.warnings.append(f"creative director unavailable ({why}): the deterministic director is used and quality is lower; start Ollama and pull {settings.models.director_model}")
    # resolve gates
    if gates_ok is not None:
        if not gates_ok.get("structure"):
            pf.warnings.append("Resolve compatibility check has not passed: run it in System before building in Resolve")
        for op, label in (("grade_lut", "grading"), ("fusion_text", "Fusion titles/effects"), ("transitions", "transitions"), ("mattes", "subject mattes")):
            if not gates_ok.get(op, True):
                pf.warnings.append(f"{label} gate not passed: Resolve uses the fallback for it (VANTA preview is unaffected)")
    return pf
