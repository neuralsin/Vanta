"""Phase 3: long-footage moments, camera log decode, comp chaining, feature manual,
effects planning, model-facing labels, preflight and the director's thinking retry."""

from __future__ import annotations

import re

import httpx
import numpy as np
import pytest

from vanta.analysis.shots import ShotSpan, split_long_takes
from vanta.analysis.signals import FrameSignals
from vanta.compile.fusion import _split_comp, chain_comps, load_registry, render_template
from vanta.core.camera_log import PROFILES, decode_to_display, detect_profile, looks_like_log
from vanta.core.schemas import EditSpec, SliderSet
from vanta.reasoning import features
from vanta.reasoning.effects_plan import plan_effects
from vanta.reasoning.footage_graph import FootageGraph
from vanta.solvers.cut import FootageCandidate, Placement, Slot
from vanta.style.brain import StylePackage

# ------------------------------------------------------------------ moments


def _signals(n: int, fps: float = 25.0, change_every: int = 100) -> FrameSignals:
    z = np.zeros(n, dtype=np.float32)
    hist = z.copy()
    hist[change_every::change_every] = 1.0  # a content change every 4 s
    return FrameSignals(fps, 320, 180, z + 0.5, hist, z, z + 1, z + 0.4, z + 0.01, z, z, z, z, z + 0.005, z)


def test_long_take_is_split_into_moments_at_content_changes():
    fps, n = 25.0, 25 * 30  # one 30 s take
    moments = split_long_takes([ShotSpan(0, 0, n, fps)], _signals(n, fps))
    lengths = [(m.end_frame - m.start_frame) / fps for m in moments]
    assert len(moments) >= 5 and all(1.6 <= length <= 8.0 for length in lengths)
    assert moments[0].start_frame == 0 and moments[-1].end_frame == n
    assert all(a.end_frame == b.start_frame for a, b in zip(moments, moments[1:], strict=False)), "contiguous"
    assert {m.take for m in moments} == {0}, "moments remember their take"
    cuts = [m.start_frame for m in moments[1:]]
    assert sum(min(abs(c - k) for k in range(100, n, 100)) <= 13 for c in cuts) >= len(cuts) * 0.6, "splits land near content changes"


def test_short_shots_are_kept_whole():
    sig = _signals(200)
    shots = [ShotSpan(0, 0, 100, 25.0), ShotSpan(1, 100, 200, 25.0)]
    assert [(m.start_frame, m.end_frame) for m in split_long_takes(shots, sig)] == [(0, 100), (100, 200)]


# --------------------------------------------------------------- camera log


@pytest.mark.parametrize(
    ("profile", "grey_code"),
    [("slog3", 420 / 1023), ("vlog", 0.423), ("logc3", 0.391), ("clog3", 0.343), ("flog", 0.459)],
)
def test_log_profiles_decode_18_percent_grey_to_a_normal_display_mid(profile, grey_code):
    out = decode_to_display(np.full((1, 1, 3), grey_code, np.float32), profile)
    assert 0.33 < float(out.mean()) < 0.52, f"{profile} grey -> {out.mean():.3f}"
    assert np.allclose(out[..., 0], out[..., 1], atol=0.02), "neutral stays neutral"


def test_decode_is_monotonic_and_bounded_for_every_profile():
    ramp = np.linspace(0, 1, 256, dtype=np.float32)[:, None, None].repeat(3, axis=2)
    for key in PROFILES:
        y = decode_to_display(ramp, key)[:, 0, 0]
        assert y.min() >= 0 and y.max() <= 1, key
        assert np.all(np.diff(y) >= -1e-4), f"{key} must not invert tones"


def test_profile_detection_prefers_metadata_then_statistics():
    assert detect_profile({"color_transfer": "arib-std-b67"}, [])[0] == "hlg"
    assert detect_profile({"color_transfer": "smpte2084"}, [])[0] == "pq"
    flat = {"luma_p05": 0.14, "luma_p95": 0.62, "chroma_mean": 7.0}
    normal = {"luma_p05": 0.02, "luma_p95": 0.93, "chroma_mean": 28.0}
    assert looks_like_log(flat)[0] and not looks_like_log(normal)[0]
    assert detect_profile({}, [flat] * 5)[0] == "generic_log"
    assert detect_profile({}, [normal] * 5)[0] == "rec709"


# ------------------------------------------------------------ comp chaining


def _render(tmp_path, name, **params):
    registry = load_registry()
    manifest = registry[name]
    base = {"in_frame": 0, "out_frame": 40}
    if name in {"text_basic", "tracking_reveal", "scale_pop", "slide", "text_2_5d", "masked_reveal"}:
        base["text"] = "NIGHT {RIDE} \"x\""
    if manifest.requires_matte:
        (tmp_path / "matte_00000.png").write_bytes(b"png")
        base["matte"] = str(tmp_path / "matte_00000.png")
    base.update(params)
    return render_template(manifest, base, tmp_path / f"{name}.comp", width=1080, height=1920, duration_frames=48).read_text(encoding="utf-8")


CHAINABLE = ["cinematic_bloom", "camera_shake", "speed_blur", "parallax", "subject_glow", "subject_outline", "letterbox", "zoom_punch", "flash_transition"]


@pytest.mark.parametrize("effect", CHAINABLE)
def test_any_template_chains_with_a_title_without_dangling_references(tmp_path, effect):
    extra = {"edge": "tail"} if effect in {"zoom_punch", "flash_transition"} else {}
    merged = chain_comps([_render(tmp_path, effect, **extra), _render(tmp_path, "text_basic")])
    _, tools, _ = _split_comp(merged)
    names = [n for n, _, _ in tools]
    assert names.count("MediaIn1") == 1 and names.count("MediaOut1") == 1
    assert len(names) == len(set(names)), "tool names are unique after namespacing"
    assert set(re.findall(r'SourceOp = "([^"]+)"', merged)) <= set(names), "every reference resolves"
    assert merged.count("{") == merged.count("}")
    # the title is composited over the effect's output, not over the raw clip
    title_bg = re.search(r'V1_TitleOver = Merge \{.*?Background = Input \{ SourceOp = "([^"]+)"', merged, re.S)
    assert title_bg and title_bg.group(1).startswith("V0_")


def test_chain_of_one_is_identity(tmp_path):
    comp = _render(tmp_path, "text_basic")
    assert chain_comps([comp]) == comp


# ----------------------------------------------------------- feature manual


def test_feature_manual_offers_only_what_the_edit_can_execute():
    vertical = {f.key for f in features.available({"speech": False, "landscape": False, "masking": 0.0, "three_d": 0.0})}
    assert "letterbox" not in vertical and "word_highlight" not in vertical
    assert "subject_glow" not in vertical and "text_behind_subject" not in vertical and "text3d_hover" not in vertical
    assert {"text_basic", "parallax", "speed_blur", "freeze_frame", "cinematic_bloom"} <= vertical
    rich = {f.key for f in features.available({"speech": True, "landscape": True, "masking": 0.6, "three_d": 0.5, "sfx_library": True})}
    assert {"letterbox", "word_highlight", "subject_glow", "text_behind_subject", "text3d_hover", "sfx_hit"} <= rich
    registry = set(load_registry())
    assert all(f.key in registry for f in features.FEATURES if f.kind in {"text", "effect"}), "every manual entry has a template"
    text = features.prompt_catalogue(features.available({}))
    assert "USE:" in text and "AVOID:" in text


# ---------------------------------------------------------- effects planning


def _cand(i: int, *, move: str = "static", motion: float = 0.05, lighting: str | None = None, faces: int = 0) -> FootageCandidate:
    return FootageCandidate(f"m{i}", "med_a", "a.mp4", 30.0, 900, 0.0, 10.0, [(0.0, 10.0)], quality=0.8, motion_intensity=motion, camera_move=move, face_count=faces, lighting=lighting, subject_x=0.6)


def _placements(cands: list[FootageCandidate], *, drop_at: int | None = None) -> list[Placement]:
    out = []
    for i, c in enumerate(cands):
        slot = Slot(index=i, start_s=i * 2.0, end_s=i * 2.0 + 2.0, start_frame=i * 60, end_frame=i * 60 + 60, section="build", role="build", beat_kind="downbeat", is_drop=(i == drop_at), energy=0.6)
        out.append(Placement(slot=slot, candidate=c, source_in_frame=0, source_out_frame=60, confidence=0.8, terms={}, rationale=[]))
    return out


def _pkg(**shares) -> StylePackage:
    return StylePackage(None, None, {}, fusion={"top": [{"effect": k, "share": v} for k, v in shares.items()]})


def test_effects_respect_footage_conditions_and_timeline_shape():
    registry = set(load_registry())
    cands = [_cand(0), _cand(1, move="pan_right", motion=0.8), _cand(2, lighting="night", faces=1)]
    spec = EditSpec(aspect="9:16", fps=30, goal="cinematic night ride, anamorphic letterbox", duration_s=6, sliders=SliderSet(motion_effects=0.7, glow=0.7, masking=0.5))
    proposals = [{"slot": 1, "template": "parallax", "purpose": "drift", "confidence": 0.9}, {"slot": 2, "template": "subject_glow", "purpose": "hero glow", "confidence": 0.8}, {"slot": 0, "template": "freeze_frame", "purpose": "hold", "confidence": 0.8}]
    shots = {"m2": {"composition": {"subject_size": 0.2}}}
    landscape = plan_effects(spec, _pkg(parallax=0.3), _placements(cands), ["s0", "s1", "s2"], fps=30, registry=registry, director_effects=proposals, landscape=True, shot_records=shots)
    by = {(e.template, e.shot_id) for e in landscape.effects}
    assert ("parallax", "s0") in by, "locked-off shot gets the slow push"
    assert ("parallax", "s1") not in by and any(r["effect"] == "parallax" and r["at"] == "s1" for r in landscape.rejected), "never on a moving shot"
    assert ("subject_glow", "s2") in by and "s2" in landscape.matte_shots.values(), "subject effects request a matte"
    assert {("letterbox", s) for s in ("s0", "s1", "s2")} <= by
    freeze = next(e for e in landscape.effects if e.template == "freeze_frame")
    assert freeze.params["freeze_at"] < freeze.params["freeze_at_p1"] < freeze.params["freeze_at_p4"] < 60
    vertical = plan_effects(spec, _pkg(parallax=0.3), _placements(cands), ["s0", "s1", "s2"], fps=30, registry=registry, director_effects=proposals, landscape=False, shot_records=shots)
    assert not any(e.template == "letterbox" for e in vertical.effects), "no bars on a vertical reel"


def test_never_list_and_masking_slider_veto_effects():
    registry = set(load_registry())
    cands = [_cand(0, lighting="night", faces=1)]
    proposals = [{"slot": 0, "template": "subject_glow", "purpose": "glow", "confidence": 0.9}]
    off = EditSpec(aspect="9:16", fps=30, goal="x", duration_s=2, sliders=SliderSet(masking=0.0, glow=0.8))
    plan = plan_effects(off, _pkg(), _placements(cands), ["s0"], fps=30, registry=registry, director_effects=proposals)
    assert not any(e.template == "subject_glow" for e in plan.effects) and plan.rejected
    never = EditSpec(aspect="9:16", fps=30, goal="x", duration_s=2, never=["no glow"], sliders=SliderSet(masking=0.8, glow=0.8))
    plan = plan_effects(never, _pkg(), _placements(cands), ["s0"], fps=30, registry=registry, director_effects=proposals)
    assert not plan.effects and "never" in plan.rejected[0]["reason"]


# ----------------------------------------------------------- footage labels


def test_labels_are_unique_across_clips_and_resolve_back():
    cands = [FootageCandidate(f"{media}_s{i:03d}", media, "x.mp4", 30, 300, 0, 10, [(0, 10)], take=i // 2) for media in ("med_a", "med_b") for i in range(4)]
    graph = FootageGraph(cands, {c.shot_id: {} for c in cands}, {}, {})
    labels = graph.labels()
    assert len(set(labels.values())) == len(cands)
    assert labels["med_a_s000"] == "A1" and labels["med_b_s000"] == "B1", "same suffix, different clips, different labels"
    assert graph.resolve_label("B1") == "med_b_s000" and graph.resolve_label("b2") == "med_b_s001"
    assert graph.resolve_label("med_a_s003") == "med_a_s003" and graph.resolve_label("Z9") is None
    shown = graph.showcase(4)
    assert {c.media_id for c in shown} == {"med_a", "med_b"} and len({(c.media_id, c.take) for c in shown}) == 4, "showcase covers clips and takes"


# ------------------------------------------------------------------ preflight


def test_preflight_estimates_long_footage_and_flags_quality_risks(tmp_path):
    from vanta.storage.library import Library
    from vanta.workers.preflight import run_preflight

    lib = Library(tmp_path / "lib.db")
    ids = []
    for i in range(2):
        media = lib.add_media(kind="footage", path=str(tmp_path / f"f{i}.mov"), content_key=f"k{i}", title=f"roll {i}", duration_s=5400.0, has_audio=1, info_json={"color_transfer": "log"})
        ids.append(media["id"])
    pf = run_preflight(lib, EditSpec(aspect="9:16", fps=30, goal="x", duration_s=38), ids, model_ready=(False, "not pulled"), gates_ok={"structure": False})
    assert pf.total_h > 4, "three hours of camera footage is a multi-hour job"
    joined = " ".join(pf.warnings)
    assert "camera profile" in joined and "creative director unavailable" in joined and "no style profile" in joined
    assert "Resolve compatibility" in joined and not pf.blockers


# ------------------------------------------------------------- think retry


def test_director_retries_without_thinking_when_the_model_cannot_think(monkeypatch):
    from pydantic import BaseModel

    from vanta.reasoning.llm import OllamaProvider

    class Out(BaseModel):
        ok: bool

    sent = []

    def fake_post(url, json, timeout):
        if url.endswith("/api/chat"):
            sent.append(dict(json))
        request = httpx.Request("POST", url)
        if json.get("think"):
            return httpx.Response(400, request=request, text='{"error":"model does not support thinking"}')
        return httpx.Response(200, request=request, json={"message": {"content": '{"ok": true}'}})

    provider = OllamaProvider(model="qwen3-vl:8b")
    monkeypatch.setattr(provider, "available", lambda: (True, "ready"))
    monkeypatch.setattr(httpx, "post", fake_post)
    result = provider.generate_json(system="s", prompt="p", schema=Out, prompt_version="t", think=True)
    assert result.data == {"ok": True} and [b["think"] for b in sent] == [True, False]


# ------------------------------------------------------- cut variety at scale


def test_long_roll_edit_never_repeats_moments_and_spreads_takes():
    import time

    from vanta.solvers.cut import assign_shots

    rng = np.random.default_rng(3)
    cands = []
    for take in range(60):  # ~3 h of camera roll split into 4 s moments
        for m in range(45):
            q = 0.95 if (take < 2 and m < 6) else float(rng.uniform(0.45, 0.8))  # two takes hold all the "best" moments
            cands.append(FootageCandidate(f"med_{take // 20}_s{take:03d}_m{m}", f"med_{take // 20}", "a.mp4", 30.0, 9000, m * 4.0, m * 4.0 + 4.0, [(m * 4.0, m * 4.0 + 4.0)], quality=q, hook_potential=q, motion_intensity=float(rng.uniform(0.1, 0.9)), take=take, shot_size=["wide", "medium", "close"][m % 3]))
    slots = [Slot(index=i, start_s=i * 2.5, end_s=i * 2.5 + 2.5, start_frame=i * 75, end_frame=i * 75 + 75, role="build") for i in range(16)]
    started = time.monotonic()
    placements = assign_shots(slots, cands, variety=0.6)
    elapsed = time.monotonic() - started
    ids = [p.candidate.shot_id for p in placements]
    takes = [p.candidate.take for p in placements]
    assert len(set(ids)) == len(ids), "no moment is used twice"
    assert max(takes.count(t) for t in set(takes)) <= 3, "no take dominates the edit"
    assert all(a != b for a, b in zip(takes, takes[1:], strict=False)) or len(set(takes)) > 8
    assert elapsed < 60, f"assignment over 2,700 moments took {elapsed:.1f}s"
