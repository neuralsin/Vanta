"""Fusion template registry and token substitution (manual 13.1, 13.2, 16.2).

The planner selects ``template + parameters``; this module validates every
parameter against the template manifest (type, range, enum, registry
membership for fonts) and substitutes tokens into a vetted .comp file. It
never evaluates text from a model: values are escaped as Lua string/number
literals and the only control structure is a fixed ``{{#WORDS}}...{{/WORDS}}``
repeat section expanded by this code.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vanta.config import get_settings

TOKEN = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
SECTION = re.compile(r"\{\{#([A-Z]+)\}\}(.*?)\{\{/\1\}\}", re.S)


class TemplateError(ValueError):
    pass


@dataclass
class ParamSpec:
    name: str
    type: str  # string | number | int | color | point | enum | font | bool | path | words
    required: bool = True
    default: Any = None
    min: float | None = None
    max: float | None = None
    choices: list[str] | None = None
    max_length: int | None = None
    description: str = ""


@dataclass
class TemplateManifest:
    name: str
    version: str
    category: str  # text | transition | effect | compositing | 3d | utility
    description: str
    file: str
    params: list[ParamSpec]
    requires_matte: bool = False
    spatial_reason_required: bool = False
    route: str = "clip_comp"  # clip_comp | transition_comp | overlay
    gate: str = "G3"
    fallback: str | None = None
    preview: str | None = None
    tags: list[str] = field(default_factory=list)
    folder: Path | None = None

    def param(self, name: str) -> ParamSpec | None:
        return next((p for p in self.params if p.name == name), None)


def templates_root() -> Path:
    return get_settings().templates_dir / "fusion"


def load_registry(root: Path | None = None) -> dict[str, TemplateManifest]:
    root = root or templates_root()
    registry: dict[str, TemplateManifest] = {}
    for manifest_path in sorted(root.glob("*/manifest.json")):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        params = [ParamSpec(**p) for p in data.pop("params", [])]
        manifest = TemplateManifest(params=params, folder=manifest_path.parent, **data)
        if not (manifest_path.parent / manifest.file).is_file():
            continue  # a manifest without its vetted comp file is not registered
        registry[manifest.name] = manifest
    return registry


# ----------------------------------------------------------------- literals
def lua_string(value: str) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "\\n")
    text = "".join(ch for ch in text if ch == "\t" or ord(ch) >= 32)
    return f'"{text}"'


def lua_number(value: float) -> str:
    if not math.isfinite(float(value)):
        raise TemplateError("non-finite number in template parameters")
    return f"{float(value):.6g}"


def parse_color(value: str) -> tuple[float, float, float, float]:
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?", value.strip())
    if not match:
        raise TemplateError(f"invalid colour {value!r}; expected #RRGGBB")
    rgb = match.group(1)
    alpha = int(match.group(2), 16) / 255 if match.group(2) else 1.0
    return int(rgb[0:2], 16) / 255, int(rgb[2:4], 16) / 255, int(rgb[4:6], 16) / 255, alpha


# --------------------------------------------------------------- validation
def validate_params(manifest: TemplateManifest, params: dict[str, Any], *, fonts: set[str] | None = None) -> dict[str, Any]:
    unknown = set(params) - {p.name for p in manifest.params}
    if unknown:
        raise TemplateError(f"{manifest.name}: unknown parameter(s) {sorted(unknown)}")
    clean: dict[str, Any] = {}
    for spec in manifest.params:
        if spec.name not in params or params[spec.name] is None:
            if spec.required and spec.default is None:
                raise TemplateError(f"{manifest.name}: missing required parameter {spec.name}")
            value = spec.default
        else:
            value = params[spec.name]
        if value is None:
            continue
        if spec.type in {"number", "int"}:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise TemplateError(f"{spec.name} must be numeric") from exc
            if spec.min is not None and number < spec.min or spec.max is not None and number > spec.max:
                raise TemplateError(f"{spec.name}={number} outside [{spec.min}, {spec.max}]")
            value = int(round(number)) if spec.type == "int" else number
        elif spec.type in {"string", "words"}:
            if not isinstance(value, (str, list)):
                raise TemplateError(f"{spec.name} must be text")
            if isinstance(value, str) and spec.max_length and len(value) > spec.max_length:
                raise TemplateError(f"{spec.name} longer than {spec.max_length} characters")
        elif spec.type == "color":
            parse_color(str(value))
        elif spec.type == "point":
            if not (isinstance(value, (list, tuple)) and len(value) == 2):
                raise TemplateError(f"{spec.name} must be [x, y]")
            value = [float(value[0]), float(value[1])]
            if not all(-0.5 <= v <= 1.5 for v in value):
                raise TemplateError(f"{spec.name} outside the frame")
        elif spec.type == "enum":
            if value not in (spec.choices or []):
                raise TemplateError(f"{spec.name} must be one of {spec.choices}")
        elif spec.type == "font":
            if fonts is not None and str(value) not in fonts:
                raise TemplateError(f"font {value!r} is not installed (registry check)")
        elif spec.type == "bool":
            value = bool(value)
        elif spec.type == "path":
            if not Path(str(value)).exists():
                raise TemplateError(f"{spec.name} path does not exist: {value}")
        clean[spec.name] = value
    return clean


# ------------------------------------------------------------- substitution
def _token_values(params: dict[str, Any], manifest: TemplateManifest, *, width: int, height: int, duration_frames: int) -> dict[str, str]:
    values: dict[str, str] = {"WIDTH": str(width), "HEIGHT": str(height), "DURATION": str(duration_frames), "LAST": str(max(0, duration_frames - 1))}
    for spec in manifest.params:
        if spec.name not in params:
            continue
        value = params[spec.name]
        key = spec.name.upper()
        if spec.type in {"string", "font", "enum", "path"}:
            values[key] = lua_string(str(value).replace("\\", "/") if spec.type == "path" else str(value))
            values[f"{key}_RAW"] = str(value)
        elif spec.type in {"number", "int"}:
            values[key] = lua_number(value)
        elif spec.type == "bool":
            values[key] = "1" if value else "0"
        elif spec.type == "color":
            r, g, b, a = parse_color(str(value))
            values.update({f"{key}_R": lua_number(r), f"{key}_G": lua_number(g), f"{key}_B": lua_number(b), f"{key}_A": lua_number(a)})
        elif spec.type == "point":
            values[f"{key}_X"] = lua_number(value[0])
            values[f"{key}_Y"] = lua_number(value[1])
    # derived timing (frames relative to the clip comp start)
    start = int(float(params.get("in_frame", 0)))
    end = int(float(params.get("out_frame", duration_frames)))
    end = max(start + 2, min(end, duration_frames))
    ramp = max(1, int(float(params.get("anim_frames", 8))))
    ramp = min(ramp, max(1, (end - start) // 3))
    values.update(
        {
            "IN": str(start),
            "OUT": str(end),
            "IN_RAMP": str(start + ramp),
            "OUT_RAMP": str(end - ramp),
            "MID": str((start + end) // 2),
            "IN_M1": str(max(0, start - 1)),
            "OUT_P1": str(end + 1),
        }
    )
    return values


SECTIONS = {"WORDS", "KEYS", "POINTS"}


def _item_tokens(item: dict[str, Any], index: int, base: dict[str, str]) -> dict[str, str]:
    local = dict(base)
    local["I"] = str(index + 1)
    for key, value in item.items():
        token = key.upper()
        if isinstance(value, bool):
            local[token] = "1" if value else "0"
        elif isinstance(value, (int, float)):
            local[token] = str(int(value)) if isinstance(value, int) else lua_number(value)
        elif isinstance(value, str):
            local[token] = lua_string(value)
        else:
            raise TemplateError(f"section item field {key} has an unsupported type")
    if "start_frame" in item:
        local["ITEM_IN"] = str(int(item["start_frame"]))
        local["ITEM_IN_RAMP"] = str(int(item["start_frame"]) + 3)
    if "end_frame" in item:
        local["ITEM_OUT"] = str(int(item["end_frame"]))
    return local


def _expand_sections(text: str, params: dict[str, Any], values: dict[str, str]) -> str:
    """Expand fixed repeat sections from list parameters (data only, no logic)."""

    def repl(match: re.Match) -> str:
        name, body = match.group(1), match.group(2)
        if name not in SECTIONS:
            raise TemplateError(f"unsupported template section {name}")
        items = params.get(name.lower()) or []
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise TemplateError(f"section {name} needs a list of objects")
        if len(items) > 400:
            raise TemplateError(f"section {name} is too long")
        return "".join(
            TOKEN.sub(lambda m, local=_item_tokens(item, i, values): local.get(m.group(1), m.group(0)), body)
            for i, item in enumerate(items)
        )

    return SECTION.sub(repl, text)


def render_template(
    manifest: TemplateManifest,
    params: dict[str, Any],
    out_path: Path,
    *,
    width: int,
    height: int,
    duration_frames: int,
    fonts: set[str] | None = None,
) -> Path:
    """Validate, substitute and write a ready-to-import .comp file. Returns its path."""
    clean = validate_params(manifest, {k: v for k, v in params.items() if k.upper() not in SECTIONS}, fonts=fonts)
    for section in SECTIONS:
        if section.lower() in params:
            clean[section.lower()] = params[section.lower()]
    source = (manifest.folder / manifest.file).read_text(encoding="utf-8")  # type: ignore[operator]
    values = _token_values(clean, manifest, width=width, height=height, duration_frames=duration_frames)
    text = _expand_sections(source, clean, values)
    missing = sorted(set(TOKEN.findall(text)) - set(values))
    if missing:
        raise TemplateError(f"{manifest.name}: unresolved token(s) {missing}")
    text = TOKEN.sub(lambda m: values[m.group(1)], text)
    if text.count("{") != text.count("}"):
        raise TemplateError(f"{manifest.name}: rendered comp has unbalanced braces")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path


def registry_summary(registry: dict[str, TemplateManifest]) -> list[dict]:
    return [
        {
            "name": m.name,
            "category": m.category,
            "description": m.description,
            "requires_matte": m.requires_matte,
            "spatial_reason_required": m.spatial_reason_required,
            "params": [p.name for p in m.params],
            "fallback": m.fallback,
            "gate": m.gate,
        }
        for m in registry.values()
    ]


# ------------------------------------------------------------------ comp chaining
# Resolve renders only the *active* Fusion comp of a timeline clip, so several
# VANTA layers on one clip (an effect, a title, a transition half) are merged
# into a single comp: each template's MediaIn is fed by the previous template's
# output, tool names are namespaced, and one MediaOut closes the chain.
_TOOL_HEAD = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*) = ([A-Za-z_][A-Za-z0-9_]*) \{")
_SOURCE_OP = re.compile(r'SourceOp = "([^"]+)"')
_POS = re.compile(r"Pos = \{ (-?[\d.]+), (-?[\d.]+) \}")


def _match_brace(text: str, open_at: int) -> int:
    """Index just past the brace that closes ``text[open_at]`` (string-literal aware)."""
    depth, i, n = 0, open_at, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise TemplateError("unbalanced braces in comp")


def _split_comp(text: str) -> tuple[str, list[tuple[str, str, str]], str]:
    """(head up to the tools block, [(name, type, entry)], tail after it)."""
    marker = re.search(r"Tools = ordered\(\) \{", text)
    if not marker:
        raise TemplateError("comp has no Tools block")
    open_at = marker.end() - 1
    close_at = _match_brace(text, open_at)
    body = text[open_at + 1 : close_at - 1]
    entries, i = [], 0
    while True:
        m = _TOOL_HEAD.match(body, i)
        if not m:
            break
        end = _match_brace(body, m.end() - 1)
        entries.append((m.group(1), m.group(2), body[m.start() : end].strip()))
        i = end
        while i < len(body) and body[i] in ", \t\r\n":
            i += 1
    if body[i:].strip():
        raise TemplateError("could not parse every tool in the comp")
    return text[: open_at + 1], entries, text[close_at - 1 :]


def chain_comps(texts: list[str]) -> str:
    """Merge rendered single-clip comps into one comp, applied in list order."""
    if not texts:
        raise TemplateError("nothing to chain")
    if len(texts) == 1:
        return texts[0]
    head, _, tail = _split_comp(texts[0])
    tools: list[str] = []
    upstream = "MediaIn1"
    for index, text in enumerate(texts):
        _, entries, _ = _split_comp(text)
        outs = [e for e in entries if e[1] == "MediaOut"]
        if len(outs) != 1 or not any(e[1] == "MediaIn" for e in entries):
            raise TemplateError("a chained comp needs exactly one MediaIn and one MediaOut")
        feed = _SOURCE_OP.search(outs[0][2])
        if not feed:
            raise TemplateError("MediaOut has no input")
        rename = {name: f"V{index}_{name}" for name, kind, _ in entries if kind not in {"MediaIn", "MediaOut"}}
        for name, kind, _ in entries:
            if kind == "MediaIn":
                rename[name] = upstream if index else "MediaIn1"
        for name, kind, entry in entries:
            if kind == "MediaOut" or (kind == "MediaIn" and index):
                continue
            entry = _SOURCE_OP.sub(lambda m, names=rename: f'SourceOp = "{names.get(m.group(1), m.group(1))}"', entry)
            entry = rename[name] + entry[len(name) :]
            entry = _POS.sub(lambda m, dy=700 * index: f"Pos = {{ {m.group(1)}, {float(m.group(2)) + dy:g} }}", entry)
            tools.append(entry)
        upstream = rename.get(feed.group(1), feed.group(1))
    tools.append(f'MediaOut1 = MediaOut {{\n\t\t\tInputs = {{\n\t\t\t\tInput = Input {{ SourceOp = "{upstream}", Source = "Output", }},\n\t\t\t}},\n\t\t\tViewInfo = OperatorInfo {{ Pos = {{ 1400, 0 }} }},\n\t\t}}')
    body = "".join(f"\n\t\t{t}," for t in tools)
    return head + body + "\n\t" + tail
