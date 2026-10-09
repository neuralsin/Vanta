"""Fusion template safety and the closed Resolve protocol (model text can never become code)."""

from __future__ import annotations

import re

import pytest

from vanta.compile.fusion import TemplateError, load_registry, render_template, validate_params
from vanta.resolve.protocol import ProtocolError, validate_request

REQUIRED = {
    "text": "NIGHT RIDE",
    "matte": None,
    "inset": None,
    "freeze_at": 10,
    "freeze_at_p1": 11,
    "freeze_at_p4": 14,
}


def _params_for(manifest, tmp_path):
    params = {}
    for spec in manifest.params:
        if spec.required and spec.default is None:
            if spec.type == "path":
                f = tmp_path / "matte_00000.png"
                f.write_bytes(b"x")
                params[spec.name] = str(f)
            elif spec.name in REQUIRED:
                params[spec.name] = REQUIRED[spec.name]
            elif spec.type in {"number", "int"}:
                params[spec.name] = spec.min or 0
            else:
                params[spec.name] = "x"
    return params


def test_every_registered_template_renders_balanced_with_no_open_tokens(tmp_path):
    registry = load_registry()
    assert len(registry) >= 25
    for name, manifest in registry.items():
        if manifest.route == "overlay":
            continue
        params = _params_for(manifest, tmp_path)
        if name == "typewriter":
            params.update(keys=[{"text_part": "N", "start_frame": 0, "end_frame": 5, "prev_node": "TypeBase"}], last_node="TypeMerge1")
        if name == "kinetic_words":
            params.update(words=[{"text": "GO", "start_frame": 0, "end_frame": 10, "prev_node": "KinBase"}], last_node="WordMerge1")
        if name in {"tracked_callout"}:
            params["points"] = [{"frame": 0, "x": 0.4, "y": 0.5}, {"frame": 20, "x": 0.6, "y": 0.5}]
        if name in {"camera_shake", "glitch"}:
            params["keys"] = [{"frame": 0, "x": 0.5, "y": 0.5}, {"frame": 4, "x": 0.51, "y": 0.49}]
        out = render_template(manifest, params, tmp_path / f"{name}.comp", width=1080, height=1920, duration_frames=60)
        text = out.read_text(encoding="utf-8")
        assert "{{" not in text, name
        assert text.count("{") == text.count("}"), name
        if manifest.route == "clip_comp" or manifest.route == "transition_halves":
            assert "MediaIn1" in text and "MediaOut1" in text, name


def test_text_cannot_escape_its_lua_string(tmp_path):
    manifest = load_registry()["text_basic"]
    evil = '"}, Tools = ordered() { Evil = RunScript { } }, x = {"'
    out = render_template(manifest, {"text": evil}, tmp_path / "evil.comp", width=1080, height=1920, duration_frames=30)
    text = out.read_text(encoding="utf-8")
    assert "RunScript" in text  # present only as escaped string content
    match = re.search(r'StyledText = Input \{ Value = (".*?(?<!\\)"), \}', text)
    assert match and '\\"' in match.group(1)
    assert "Evil = RunScript" not in text.split("StyledText")[0]


def test_template_parameters_are_validated_against_the_manifest():
    manifest = load_registry()["text_basic"]
    with pytest.raises(TemplateError, match="unknown"):
        validate_params(manifest, {"text": "a", "lua": "os.exit()"})
    with pytest.raises(TemplateError, match="outside"):
        validate_params(manifest, {"text": "a", "size": 9.0})
    with pytest.raises(TemplateError, match="not installed"):
        validate_params(manifest, {"text": "a", "font": "Imaginary Font"}, fonts={"Arial"})
    with pytest.raises(TemplateError, match="colour"):
        validate_params(manifest, {"text": "a", "color": "red; drop"})


def test_protocol_confines_writes_to_the_output_root_and_local_paths(tmp_path):
    root = str(tmp_path / "data")
    with pytest.raises(ProtocolError, match="output root"):
        validate_request({"action": "render_start", "params": {"timeline_id": "t", "target_dir": "C:/Windows/Temp/x", "custom_name": "x", "width": 540, "height": 960}}, allow_mutations=True, output_root=root)
    with pytest.raises(ProtocolError, match="local"):
        validate_request({"action": "snapshot", "params": {"label": "s", "export_path": "\\\\server\\share\\x.drp"}}, allow_mutations=True, output_root=root)
    with pytest.raises(ProtocolError, match="unexpected property"):
        validate_request({"action": "set_properties", "params": {"timeline_id": "t", "item_id": "i", "properties": {"Script": 1}}}, allow_mutations=True)
    with pytest.raises(ProtocolError, match="one of"):
        validate_request({"action": "add_transition", "params": {"timeline_id": "t", "item_id": "i", "type": "Lua Wipe", "position": "start", "alignment": "center", "duration": 10}}, allow_mutations=True)
    with pytest.raises(ProtocolError, match="VANTA_GATE_"):
        validate_request({"action": "create_project", "params": {"name": "Client Project"}}, allow_mutations=True)
    with pytest.raises(ProtocolError, match="VANTA"):
        validate_request({"action": "create_timeline", "params": {"name": "Final Cut", "snapshot_id": "s"}}, allow_mutations=True)
    action, params = validate_request({"action": "read_state", "params": {"scope": "timeline_items", "timeline_id": "t"}}, allow_mutations=False)
    assert action == "read_state" and params["scope"] == "timeline_items"
