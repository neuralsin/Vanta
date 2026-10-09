"""Generate VANTA's vetted Fusion template library (comp files + manifests).

Run once (``python -m vanta.resolve.templates.build_fusion_library``); the
generated files are committed and are what the registry loads. Users may
replace any comp with a hand-built export that keeps the same tokens. Gate G3
validates text templates by rendering a frame and reading it back with OCR.

Coordinates: Fusion's Center is normalised with y pointing UP; the compiler
converts VANTA's top-left convention before substitution.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "fusion"
VERSION = "1.0.0"

HEADER = """Composition {
	CurrentTime = 0,
	RenderRange = { 0, {{LAST}} },
	GlobalRange = { 0, {{LAST}} },
	CurrentID = 128,
	HiQ = true,
	PlaybackUpdateMode = 0,
	Version = "Fusion Studio 21.1",
	SavedOutputs = 0,
	HeldTools = 0,
	DisabledTools = 0,
	LockedTools = 0,
	AudioOffset = 0,
	AutoRenderRange = true,
	Prefs = {
		Comp = {
			FrameFormat = { Width = {{WIDTH}}, Height = {{HEIGHT}}, AspectX = 1, AspectY = 1 },
		},
	},
	Tools = ordered() {
"""
FOOTER = """	},
}
"""


def src(op: str, output: str = "Output") -> str:
    return f'Input {{ SourceOp = "{op}", Source = "{output}", }}'


def val(value: str) -> str:
    return f"Input {{ Value = {value}, }}"


def tool(name: str, kind: str, inputs: dict[str, str], x: int = 0, y: int = 0, extra: str = "") -> str:
    lines = [f"\t\t{name} = {kind} {{"]
    if extra:
        lines.append(extra)
    lines.append("\t\t\tInputs = {")
    for key, value in inputs.items():
        key_txt = key if key.isidentifier() else f'["{key}"]'
        lines.append(f"\t\t\t\t{key_txt} = {value},")
    lines.append("\t\t\t},")
    lines.append(f"\t\t\tViewInfo = OperatorInfo {{ Pos = {{ {x}, {y} }} }},")
    lines.append("\t\t},")
    return "\n".join(lines) + "\n"


def spline(name: str, keys: list[tuple[str, str]], color: str = "Red = 220, Green = 220, Blue = 220") -> str:
    body = ", ".join(f"[{frame}] = {{ {value}, Flags = {{ Linear = true }} }}" for frame, value in keys)
    return f"\t\t{name} = BezierSpline {{\n\t\t\tSplineColor = {{ {color} }},\n\t\t\tNameSet = true,\n\t\t\tKeyFrames = {{ {body} }}\n\t\t}},\n"


def xypath(name: str, xkeys: list[tuple[str, str]], ykeys: list[tuple[str, str]]) -> str:
    out = spline(f"{name}X", xkeys, "Red = 250, Green = 59, Blue = 49")
    out += spline(f"{name}Y", ykeys, "Red = 252, Green = 131, Blue = 47")
    out += (
        f"\t\t{name} = XYPath {{\n\t\t\tShowKeyPoints = false,\n\t\t\tDrawMode = \"ModifyOnly\",\n"
        f"\t\t\tInputs = {{\n\t\t\t\tX = {src(name + 'X', 'Value')},\n\t\t\t\tY = {src(name + 'Y', 'Value')},\n\t\t\t}},\n\t\t}},\n"
    )
    return out


def media_in() -> str:
    return tool("MediaIn1", "MediaIn", {"GlobalOut": val("{{LAST}}"), "LayerName": val('"Layer 1"')}, 0, 0, extra="\t\t\tExtentSet = true,")


def media_out(input_op: str) -> str:
    return tool("MediaOut1", "MediaOut", {"Input": src(input_op)}, 1100, 0)


def transparent(name: str = "Transparent1") -> str:
    return tool(
        name,
        "Background",
        {"GlobalOut": val("{{LAST}}"), "Width": val("{{WIDTH}}"), "Height": val("{{HEIGHT}}"), "UseFrameFormatSettings": val("1"), "TopLeftAlpha": val("0")},
        0,
        -200,
    )


def solid(name: str, color_key: str, mask: str | None = None, x: int = 0, y: int = -260) -> str:
    inputs = {
        "GlobalOut": val("{{LAST}}"),
        "Width": val("{{WIDTH}}"),
        "Height": val("{{HEIGHT}}"),
        "UseFrameFormatSettings": val("1"),
        "TopLeftRed": val(f"{{{{{color_key}_R}}}}"),
        "TopLeftGreen": val(f"{{{{{color_key}_G}}}}"),
        "TopLeftBlue": val(f"{{{{{color_key}_B}}}}"),
        "TopLeftAlpha": val("1"),
    }
    if mask:
        inputs["EffectMask"] = src(mask)
    return tool(name, "Background", inputs, x, y)


def text_plus(name: str, *, text: str = "{{TEXT}}", center: tuple[str, str] = ("{{CENTER_X}}", "{{CENTER_Y}}"), size: str = "{{SIZE}}", spacing: str | None = None, x: int = 200, y: int = -200, center_src: str | None = None) -> str:
    inputs = {
        "GlobalOut": val("{{LAST}}"),
        "Width": val("{{WIDTH}}"),
        "Height": val("{{HEIGHT}}"),
        "UseFrameFormatSettings": val("1"),
        "StyledText": val(text),
        "Font": val("{{FONT}}"),
        "Style": val("{{STYLE}}"),
        "Size": val(size),
        "VerticalJustificationNew": val("3"),
        "HorizontalJustificationNew": val("3"),
        "Red1": val("{{COLOR_R}}"),
        "Green1": val("{{COLOR_G}}"),
        "Blue1": val("{{COLOR_B}}"),
        "Alpha1": val("{{COLOR_A}}"),
    }
    inputs["Center"] = src(center_src, "Value") if center_src else val(f"{{ {center[0]}, {center[1]} }}")
    if spacing:
        inputs["CharacterSpacing"] = spacing if spacing.startswith("Input") else val(spacing)
    return tool(name, "TextPlus", inputs, x, y)


def text_stack(prefix: str, source: str, x: int = 400) -> tuple[str, str]:
    """Shadow + outline + fill stack for a text source. Returns (tools, output op name)."""
    out = transparent(f"{prefix}Base")
    out += tool(f"{prefix}ShadowBC", "BrightnessContrast", {"Gain": val("0"), "Input": src(source)}, x, -320)
    out += tool(f"{prefix}ShadowBlur", "Blur", {"XBlurSize": val("{{SHADOW_SOFTNESS}}"), "Input": src(f"{prefix}ShadowBC")}, x + 100, -320)
    out += tool(f"{prefix}ShadowXf", "Transform", {"Center": val("{ 0.503, 0.496 }"), "Input": src(f"{prefix}ShadowBlur")}, x + 200, -320)
    out += tool(f"{prefix}Shadow", "Merge", {"Background": src(f"{prefix}Base"), "Foreground": src(f"{prefix}ShadowXf"), "Blend": val("{{SHADOW_OPACITY}}")}, x + 300, -320)
    out += tool(f"{prefix}Dilate", "ErodeDilate", {"XAmount": val("{{OUTLINE_WIDTH}}"), "Input": src(source)}, x, -420)
    out += solid(f"{prefix}OutlineFill", "OUTLINE_COLOR", mask=f"{prefix}Dilate", x=x + 100, y=-420)
    out += tool(f"{prefix}Outline", "Merge", {"Background": src(f"{prefix}Shadow"), "Foreground": src(f"{prefix}OutlineFill"), "Blend": val("{{OUTLINE}}")}, x + 300, -420)
    out += tool(f"{prefix}Stack", "Merge", {"Background": src(f"{prefix}Outline"), "Foreground": src(source)}, x + 400, -260)
    return out, f"{prefix}Stack"


def fade_keys() -> list[tuple[str, str]]:
    return [("{{IN_M1}}", "0"), ("{{IN}}", "0"), ("{{IN_RAMP}}", "1"), ("{{OUT_RAMP}}", "1"), ("{{OUT}}", "0")]


# ------------------------------------------------------------- param sets
def p(name, type_, required=True, default=None, **kw):
    return {"name": name, "type": type_, "required": required, "default": default, **kw}


TIMING = [
    p("in_frame", "int", False, 0, min=0, max=100000, description="first frame (clip-relative)"),
    p("out_frame", "int", False, None, min=1, max=100000, description="last frame (clip-relative)"),
    p("anim_frames", "int", False, 8, min=1, max=60),
]
TEXT_STYLE = [
    p("text", "string", max_length=120),
    p("font", "font", False, "Arial"),
    p("style", "string", False, "Bold", max_length=40),
    p("size", "number", False, 0.08, min=0.01, max=0.6, description="Fusion Text+ size"),
    p("color", "color", False, "#FFFFFF"),
    p("center", "point", False, [0.5, 0.6], description="Fusion coordinates (y up)"),
    p("shadow_opacity", "number", False, 0.55, min=0, max=1),
    p("shadow_softness", "number", False, 4, min=0, max=40),
    p("outline", "number", False, 0, min=0, max=1, description="outline opacity"),
    p("outline_width", "number", False, 0.004, min=0, max=0.05),
    p("outline_color", "color", False, "#000000"),
]
MATTE = [p("matte", "path", description="first frame of the RGBA matte sequence")]


def manifest(name, category, description, params, *, requires_matte=False, spatial=False, fallback=None, gate="G3", route="clip_comp", tags=None):
    return {
        "name": name,
        "version": VERSION,
        "category": category,
        "description": description,
        "file": "template.comp",
        "params": params,
        "requires_matte": requires_matte,
        "spatial_reason_required": spatial,
        "route": route,
        "gate": gate,
        "fallback": fallback,
        "tags": tags or [],
    }


def loader(name: str = "Matte1") -> str:
    return (
        f"\t\t{name} = Loader {{\n\t\t\tClips = {{\n\t\t\t\tClip {{\n\t\t\t\t\tID = \"Clip1\",\n\t\t\t\t\tFilename = {{{{MATTE}}}},\n"
        "\t\t\t\t\tFormatID = \"PNGFormat\",\n\t\t\t\t\tStartFrame = 0,\n\t\t\t\t\tLengthSetManually = true,\n\t\t\t\t\tTrimIn = 0,\n"
        "\t\t\t\t\tTrimOut = {{LAST}},\n\t\t\t\t\tExtendFirst = 0,\n\t\t\t\t\tExtendLast = 0,\n\t\t\t\t\tLoop = 0,\n\t\t\t\t\tAspectMode = 0,\n"
        "\t\t\t\t\tDepth = 0,\n\t\t\t\t\tTimeCode = 0,\n\t\t\t\t\tGlobalStart = 0,\n\t\t\t\t\tGlobalEnd = {{LAST}}\n\t\t\t\t}\n\t\t\t},\n"
        "\t\t\tInputs = { [\"Clip1.PNGFormat.PostMultiply\"] = Input { Value = 1, }, },\n"
        f"\t\t\tViewInfo = OperatorInfo {{ Pos = {{ 0, 300 }} }},\n\t\t}},\n"
    )


TEMPLATES: dict[str, tuple[dict, str]] = {}


def register(meta: dict, body: str) -> None:
    TEMPLATES[meta["name"]] = (meta, HEADER + body + FOOTER)


# ------------------------------------------------------------------- text
def _title(name: str, description: str, anim_tools: str, final_source: str, extra_params=None, tags=None):
    body = media_in() + text_plus("Text1") + anim_tools
    stack, top = text_stack("T", final_source)
    body += stack
    body += spline("TitleFade", fade_keys())
    body += tool("TitleOver", "Merge", {"Background": src("MediaIn1"), "Foreground": src(top), "Blend": src("TitleFade", "Value")}, 900, 0)
    body += media_out("TitleOver")
    register(manifest(name, "text", description, TEXT_STYLE + TIMING + (extra_params or []), tags=tags), body)


_title("text_basic", "Clean title with optional soft shadow and outline; fades in and out.", "", "Text1", tags=["title", "caption"])

_title(
    "slide",
    "Title slides in from an offset, holds, and slides out the same way.",
    xypath("SlidePath", [("{{IN}}", "{{FROM_OFFSET_X}}"), ("{{IN_RAMP}}", "0.5"), ("{{OUT_RAMP}}", "0.5"), ("{{OUT}}", "{{FROM_OFFSET_X}}")], [("{{IN}}", "{{FROM_OFFSET_Y}}"), ("{{IN_RAMP}}", "0.5"), ("{{OUT_RAMP}}", "0.5"), ("{{OUT}}", "{{FROM_OFFSET_Y}}")])
    + tool("SlideXf", "Transform", {"Center": src("SlidePath", "Value"), "Input": src("Text1")}, 300, -200),
    "SlideXf",
    [p("from_offset", "point", False, [0.2, 0.5], description="Transform centre at the start (0.5,0.5 = rest)")],
    ["title", "motion"],
)

_title(
    "tracking_reveal",
    "Letters converge from wide tracking while fading in (cinematic title reveal).",
    spline("TrackSpacing", [("{{IN}}", "{{TRACKING_FROM}}"), ("{{IN_RAMP}}", "1"), ("{{OUT}}", "1")])
    + tool("TrackXf", "Transform", {"Input": src("Text1")}, 300, -200),
    "TrackXf",
    [p("tracking_from", "number", False, 1.8, min=1.0, max=4.0)],
    ["title", "cinematic"],
)
# make tracking_reveal drive Text+ spacing
meta, comp = TEMPLATES["tracking_reveal"]
TEMPLATES["tracking_reveal"] = (meta, comp.replace('\t\t\t\tRed1 = Input { Value = {{COLOR_R}}, },', '\t\t\t\tRed1 = Input { Value = {{COLOR_R}}, },\n\t\t\t\tCharacterSpacing = Input { SourceOp = "TrackSpacing", Source = "Value", },', 1))

_title(
    "scale_pop",
    "Title pops in with a short overshoot scale (energetic hook line).",
    spline("PopSize", [("{{IN}}", "0.6"), ("{{IN_RAMP}}", "1.08"), ("{{MID}}", "1"), ("{{OUT}}", "1")])
    + tool("PopXf", "Transform", {"Size": src("PopSize", "Value"), "Input": src("Text1")}, 300, -200),
    "PopXf",
    tags=["title", "energy"],
)

_title(
    "text_2_5d",
    "2.5D title: soft shadow plus a slow measured parallax drift against the scene (reliable 3D fallback).",
    xypath("DriftPath", [("{{IN}}", "{{DRIFT_X0}}"), ("{{OUT}}", "{{DRIFT_X1}}")], [("{{IN}}", "0.5"), ("{{OUT}}", "0.5")])
    + spline("DriftSize", [("{{IN}}", "1"), ("{{OUT}}", "{{DRIFT_SCALE}}")])
    + tool("DriftXf", "Transform", {"Center": src("DriftPath", "Value"), "Size": src("DriftSize", "Value"), "Input": src("Text1")}, 300, -200),
    "DriftXf",
    [
        p("drift_x0", "number", False, 0.49, min=0.3, max=0.7),
        p("drift_x1", "number", False, 0.51, min=0.3, max=0.7),
        p("drift_scale", "number", False, 1.04, min=0.9, max=1.2),
    ],
    ["title", "spatial"],
)

# masked reveal: text appears as a soft rectangle mask slides across it
body = media_in() + text_plus("Text1")
body += xypath("RevealPath", [("{{IN}}", "-0.5"), ("{{IN_RAMP}}", "0.5"), ("{{OUT}}", "0.5")], [("{{IN}}", "0.5"), ("{{OUT}}", "0.5")])
body += tool("RevealMask", "RectangleMask", {"Center": src("RevealPath", "Value"), "Width": val("1"), "Height": val("1"), "SoftEdge": val("0.08")}, 300, -120)
body += tool("RevealXf", "Transform", {"Input": src("Text1"), "EffectMask": src("RevealMask", "Mask")}, 300, -200)
stack, top = text_stack("T", "RevealXf")
body += stack + spline("TitleFade", fade_keys())
body += tool("TitleOver", "Merge", {"Background": src("MediaIn1"), "Foreground": src(top), "Blend": src("TitleFade", "Value")}, 900, 0)
body += media_out("TitleOver")
register(manifest("masked_reveal", "text", "Text revealed by a soft wipe through scene geometry.", TEXT_STYLE + TIMING, tags=["title", "reveal"]), body)

# typewriter: cumulative prefixes, each visible for its window
body = media_in() + transparent("TypeBase")
body += "{{#KEYS}}" + text_plus("Type{{I}}", text="{{TEXT_PART}}", x=200, y=-200) + spline("TypeVis{{I}}", [("{{ITEM_IN}}", "1"), ("{{ITEM_OUT}}", "0")]) + tool(
    "TypeMerge{{I}}", "Merge", {"Background": "Input { SourceOp = {{PREV_NODE}}, Source = \"Output\", }", "Foreground": src("Type{{I}}"), "Blend": src("TypeVis{{I}}", "Value")}, 400, -200
) + "{{/KEYS}}"
stack, top = text_stack("T", "TypeFinal")
body += tool("TypeFinal", "Transform", {"Input": "Input { SourceOp = {{LAST_NODE}}, Source = \"Output\", }"}, 600, -200)
body += stack + spline("TitleFade", fade_keys())
body += tool("TitleOver", "Merge", {"Background": src("MediaIn1"), "Foreground": src(top), "Blend": src("TitleFade", "Value")}, 900, 0)
body += media_out("TitleOver")
register(
    manifest(
        "typewriter",
        "text",
        "Typewriter: characters appear one by one (cumulative text states, timed by the compiler).",
        [p for p in TEXT_STYLE if p["name"] != "text"] + TIMING + [p("text", "string", max_length=80), p("last_node", "string", False, "TypeBase", max_length=40)],
        tags=["title", "typewriter"],
    ),
    body,
)

# kinetic words: one big word at a time, timed to speech or beats
body = media_in() + transparent("KinBase")
body += "{{#WORDS}}" + text_plus("Word{{I}}", text="{{TEXT}}", size="{{WORD_SIZE}}", x=200, y=-200) + spline(
    "WordPop{{I}}", [("{{ITEM_IN}}", "0.75"), ("{{ITEM_IN_RAMP}}", "1.05"), ("{{ITEM_OUT}}", "1")]
) + tool("WordXf{{I}}", "Transform", {"Size": src("WordPop{{I}}", "Value"), "Input": src("Word{{I}}")}, 300, -200) + spline(
    "WordVis{{I}}", [("{{ITEM_IN}}", "1"), ("{{ITEM_OUT}}", "0")]
) + tool(
    "WordMerge{{I}}", "Merge", {"Background": "Input { SourceOp = {{PREV_NODE}}, Source = \"Output\", }", "Foreground": src("WordXf{{I}}"), "Blend": src("WordVis{{I}}", "Value")}, 400, -200
) + "{{/WORDS}}"
body += tool("KinFinal", "Transform", {"Input": "Input { SourceOp = {{LAST_NODE}}, Source = \"Output\", }"}, 600, -200)
stack, top = text_stack("T", "KinFinal")
body += stack
body += tool("TitleOver", "Merge", {"Background": src("MediaIn1"), "Foreground": src(top)}, 900, 0)
body += media_out("TitleOver")
register(
    manifest(
        "kinetic_words",
        "text",
        "Kinetic typography: one word at a time with a scale pop, timed to words or beats.",
        [p for p in TEXT_STYLE if p["name"] not in {"text"}] + TIMING + [p("last_node", "string", False, "KinBase", max_length=40), p("word_size", "number", False, 0.12, min=0.02, max=0.6)],
        tags=["typography", "rhythm"],
    ),
    body,
)
# word_size must reach per-word sections as a global token
meta, comp = TEMPLATES["kinetic_words"]
TEMPLATES["kinetic_words"] = (meta, comp)

# text behind subject (manual 13.4 graph)
body = media_in() + loader("Matte1") + text_plus("Text1")
stack, top = text_stack("T", "Text1")
body += stack + spline("TitleFade", fade_keys())
body += tool("TextOverBG", "Merge", {"Background": src("MediaIn1"), "Foreground": src(top), "Blend": src("TitleFade", "Value")}, 700, 0)
body += tool("SubjectOver", "Merge", {"Background": src("TextOverBG"), "Foreground": src("MediaIn1"), "EffectMask": src("Matte1")}, 900, 0)
body += media_out("SubjectOver")
register(
    manifest(
        "text_behind_subject",
        "compositing",
        "Title sits behind the subject: background, text, then the subject re-composited through its matte.",
        TEXT_STYLE + TIMING + MATTE,
        requires_matte=True,
        fallback="text_2_5d",
        gate="G3b",
        tags=["masking", "title", "spatial"],
    ),
    body,
)

# tracked callout: label + dot following a tracked path (points from VANTA's tracker)
body = media_in() + text_plus("Label1", center_src="CalloutPath", size="{{SIZE}}")
body += "\t\tCalloutPathX = BezierSpline {\n\t\t\tSplineColor = { Red = 250, Green = 59, Blue = 49 },\n\t\t\tNameSet = true,\n\t\t\tKeyFrames = { {{#POINTS}}[{{FRAME}}] = { {{X}}, Flags = { Linear = true } }, {{/POINTS}} }\n\t\t},\n"
body += "\t\tCalloutPathY = BezierSpline {\n\t\t\tSplineColor = { Red = 252, Green = 131, Blue = 47 },\n\t\t\tNameSet = true,\n\t\t\tKeyFrames = { {{#POINTS}}[{{FRAME}}] = { {{Y}}, Flags = { Linear = true } }, {{/POINTS}} }\n\t\t},\n"
body += '\t\tCalloutPath = XYPath {\n\t\t\tShowKeyPoints = false,\n\t\t\tDrawMode = "ModifyOnly",\n\t\t\tInputs = {\n\t\t\t\tX = Input { SourceOp = "CalloutPathX", Source = "Value", },\n\t\t\t\tY = Input { SourceOp = "CalloutPathY", Source = "Value", },\n\t\t\t},\n\t\t},\n'
stack, top = text_stack("T", "Label1")
body += stack + spline("TitleFade", fade_keys())
body += tool("CalloutOver", "Merge", {"Background": src("MediaIn1"), "Foreground": src(top), "Blend": src("TitleFade", "Value")}, 900, 0)
body += media_out("CalloutOver")
register(
    manifest(
        "tracked_callout",
        "text",
        "Label attached to a tracked point (simplest tracking method that solves it: point/planar track by VANTA).",
        [p for p in TEXT_STYLE if p["name"] != "center"] + TIMING,
        tags=["typography", "tracking"],
    ),
    body,
)

# 3D hover title (Fusion 3D). Spatial reason required.
body = media_in()
body += tool(
    "Text3D1",
    "Text3D",
    {
        "StyledText": val("{{TEXT}}"),
        "Font": val("{{FONT}}"),
        "Style": val("{{STYLE}}"),
        "Size": val("{{SIZE}}"),
        "Extrusion": val("1"),
        "ExtrusionDepth": val("{{DEPTH}}"),
        "Red1": val("{{COLOR_R}}"),
        "Green1": val("{{COLOR_G}}"),
        "Blue1": val("{{COLOR_B}}"),
        "Transform3DOp.Translate.X": val("{{POS_X}}"),
        "Transform3DOp.Translate.Y": src("HoverY", "Value"),
        "Transform3DOp.Rotate.Y": src("HoverRot", "Value"),
    },
    200,
    -200,
)
body += spline("HoverY", [("{{IN}}", "{{POS_Y}}"), ("{{MID}}", "{{POS_Y_HI}}"), ("{{OUT}}", "{{POS_Y}}")])
body += spline("HoverRot", [("{{IN}}", "-{{TILT}}"), ("{{OUT}}", "{{TILT}}")])
body += tool("Light1", "LightDirectional", {"Transform3DOp.Rotate.X": val("-35"), "Transform3DOp.Rotate.Y": val("25")}, 200, -320)
body += tool("Camera1", "Camera3D", {"Transform3DOp.Translate.Z": val("{{CAMERA_Z}}"), "AovType": val("1"), "AoV": val("{{FOV}}")}, 200, -420)
body += tool("Scene1", "Merge3D", {"SceneInput1": src("Text3D1"), "SceneInput2": src("Camera1"), "SceneInput3": src("Light1")}, 400, -300)
body += tool("Render1", "Renderer3D", {"GlobalOut": val("{{LAST}}"), "Width": val("{{WIDTH}}"), "Height": val("{{HEIGHT}}"), "UseFrameFormatSettings": val("1"), "SceneInput": src("Scene1")}, 600, -300)
stack, top = text_stack("T", "Render1")
body += stack + spline("TitleFade", fade_keys())
body += tool("HoverOver", "Merge", {"Background": src("MediaIn1"), "Foreground": src(top), "Blend": src("TitleFade", "Value")}, 900, 0)
body += media_out("HoverOver")
register(
    manifest(
        "text3d_hover",
        "3d",
        "Extruded 3D title hovering in scene space (Text3D, Merge3D, Camera3D, Renderer3D). Needs a spatial reason.",
        [p for p in TEXT_STYLE if p["name"] != "center"]
        + TIMING
        + [
            p("depth", "number", False, 0.15, min=0.0, max=1.0),
            p("pos_x", "number", False, 0.0, min=-2, max=2),
            p("pos_y", "number", False, 0.2, min=-2, max=2),
            p("pos_y_hi", "number", False, 0.24, min=-2, max=2),
            p("tilt", "number", False, 6, min=0, max=30),
            p("camera_z", "number", False, 3.0, min=0.5, max=20),
            p("fov", "number", False, 35, min=10, max=90),
        ],
        spatial=True,
        fallback="text_2_5d",
        gate="G14",
        tags=["3d", "spatial"],
    ),
    body,
)

# ------------------------------------------------------- subject effects
def _subject_isolate(prefix: str = "Subj") -> str:
    return transparent(f"{prefix}Base") + tool(f"{prefix}Only", "Merge", {"Background": src(f"{prefix}Base"), "Foreground": src("MediaIn1"), "EffectMask": src("Matte1")}, 300, 200)


body = media_in() + loader("Matte1") + _subject_isolate()
body += tool("GlowBlur", "Blur", {"XBlurSize": val("{{RADIUS}}"), "Input": src("SubjOnly")}, 450, 200)
body += tool("GlowGain", "BrightnessContrast", {"Gain": val("{{STRENGTH}}"), "Input": src("GlowBlur")}, 600, 200)
body += spline("GlowFade", fade_keys())
body += tool("GlowOver", "Merge", {"Background": src("MediaIn1"), "Foreground": src("GlowGain"), "ApplyMode": val('FuID { "Screen" }'), "Blend": src("GlowFade", "Value")}, 900, 0)
body += media_out("GlowOver")
register(
    manifest(
        "subject_glow",
        "effect",
        "Soft glow emanating from the masked subject (emphasise practical lights / hero subject).",
        TIMING + MATTE + [p("radius", "number", False, 18, min=1, max=120), p("strength", "number", False, 0.7, min=0.05, max=3)],
        requires_matte=True,
        gate="G6",
        tags=["glow", "masking"],
    ),
    body,
)

body = media_in() + loader("Matte1")
body += tool("OutlineDilate", "ErodeDilate", {"XAmount": val("{{WIDTH_PX}}"), "Input": src("Matte1")}, 300, 200)
body += solid("OutlineColor", "COLOR", mask="OutlineDilate", x=450, y=200)
body += tool("OutlineSoft", "Blur", {"XBlurSize": val("{{SOFTNESS}}"), "Input": src("OutlineColor")}, 600, 200)
body += spline("OutlineFade", fade_keys())
body += tool("OutlineUnder", "Merge", {"Background": src("MediaIn1"), "Foreground": src("OutlineSoft"), "Blend": src("OutlineFade", "Value")}, 750, 0)
body += tool("SubjectTop", "Merge", {"Background": src("OutlineUnder"), "Foreground": src("MediaIn1"), "EffectMask": src("Matte1")}, 900, 0)
body += media_out("SubjectTop")
register(
    manifest(
        "subject_outline",
        "effect",
        "Coloured outline around the masked subject (cut-out look).",
        TIMING + MATTE + [p("color", "color", False, "#FFFFFF"), p("width_px", "number", False, 0.006, min=0.0005, max=0.05), p("softness", "number", False, 1.5, min=0, max=20)],
        requires_matte=True,
        gate="G6",
        tags=["masking", "outline"],
    ),
    body,
)

body = media_in()
body += tool("BloomBC", "BrightnessContrast", {"Gain": val("1"), "Lift": val("-{{THRESHOLD}}"), "Input": src("MediaIn1")}, 300, 200)
body += tool("BloomBlur", "Blur", {"XBlurSize": val("{{RADIUS}}"), "Input": src("BloomBC")}, 450, 200)
body += tool("BloomOver", "Merge", {"Background": src("MediaIn1"), "Foreground": src("BloomBlur"), "ApplyMode": val('FuID { "Screen" }'), "Blend": val("{{STRENGTH}}")}, 900, 0)
body += media_out("BloomOver")
register(
    manifest(
        "cinematic_bloom",
        "effect",
        "Filmic highlight bloom/halation (purpose: soften practical highlights).",
        TIMING + [p("threshold", "number", False, 0.6, min=0.1, max=0.95), p("radius", "number", False, 25, min=1, max=150), p("strength", "number", False, 0.35, min=0, max=1)],
        gate="G3",
        tags=["glow"],
    ),
    body,
)

body = media_in()
body += spline("BlurAmt", [("{{IN}}", "0"), ("{{MID}}", "{{AMOUNT}}"), ("{{OUT}}", "0")])
body += tool("SpeedBlur", "DirectionalBlur", {"Type": val("0"), "Length": src("BlurAmt", "Value"), "Angle": val("{{ANGLE}}"), "Input": src("MediaIn1")}, 400, 0)
body += media_out("SpeedBlur")
register(
    manifest("speed_blur", "effect", "Directional motion blur peaking mid-range (smooth fast motion / energy).", TIMING + [p("amount", "number", False, 0.06, min=0, max=0.5), p("angle", "number", False, 0, min=-180, max=180)], tags=["motion"]),
    body,
)

body = media_in()
body += xypath("ParPath", [("{{IN}}", "{{PAN_X0}}"), ("{{OUT}}", "{{PAN_X1}}")], [("{{IN}}", "{{PAN_Y0}}"), ("{{OUT}}", "{{PAN_Y1}}")])
body += spline("ParSize", [("{{IN}}", "{{ZOOM_0}}"), ("{{OUT}}", "{{ZOOM_1}}")])
body += tool("ParXf", "Transform", {"Center": src("ParPath", "Value"), "Size": src("ParSize", "Value"), "Input": src("MediaIn1")}, 400, 0)
body += media_out("ParXf")
register(
    manifest(
        "parallax",
        "effect",
        "Slow push and drift (Ken Burns / 2.5D parallax) for stills or static shots.",
        TIMING
        + [
            p("pan_x0", "number", False, 0.5, min=0.3, max=0.7),
            p("pan_x1", "number", False, 0.52, min=0.3, max=0.7),
            p("pan_y0", "number", False, 0.5, min=0.3, max=0.7),
            p("pan_y1", "number", False, 0.5, min=0.3, max=0.7),
            p("zoom_0", "number", False, 1.0, min=0.8, max=2.0),
            p("zoom_1", "number", False, 1.08, min=0.8, max=2.0),
        ],
        tags=["motion", "stills"],
    ),
    body,
)

body = media_in()
body += tool("Freeze", "TimeStretcher", {"SourceTime": src("FreezeTime", "Value"), "Input": src("MediaIn1")}, 400, 0)
body += spline("FreezeTime", [("{{IN}}", "{{IN}}"), ("{{FREEZE_AT}}", "{{FREEZE_AT}}"), ("{{OUT}}", "{{FREEZE_AT}}")])
body += tool("FreezeFlash", "BrightnessContrast", {"Gain": src("FlashGain", "Value"), "Input": src("Freeze")}, 600, 0)
body += spline("FlashGain", [("{{FREEZE_AT}}", "1"), ("{{FREEZE_AT}}+1", "1.6"), ("{{FREEZE_AT}}+4", "1")])
body += media_out("FreezeFlash")
meta_body = body.replace("{{FREEZE_AT}}+1", "{{FREEZE_AT_P1}}").replace("{{FREEZE_AT}}+4", "{{FREEZE_AT_P4}}")
register(
    manifest(
        "freeze_frame",
        "effect",
        "Freeze on a moment with a short flash accent (hold attention).",
        TIMING + [p("freeze_at", "int", min=0, max=100000), p("freeze_at_p1", "int", min=0, max=100000), p("freeze_at_p4", "int", min=0, max=100000)],
        gate="G13",
        tags=["motion", "time"],
    ),
    meta_body,
)

# picture in picture and screen replacement use a second media file via Loader
body = media_in()
body += (
    "\t\tInset1 = Loader {\n\t\t\tClips = {\n\t\t\t\tClip {\n\t\t\t\t\tID = \"Clip1\",\n\t\t\t\t\tFilename = {{INSET}},\n\t\t\t\t\tStartFrame = 0,\n"
    "\t\t\t\t\tLengthSetManually = true,\n\t\t\t\t\tTrimIn = 0,\n\t\t\t\t\tTrimOut = {{LAST}},\n\t\t\t\t\tGlobalStart = 0,\n\t\t\t\t\tGlobalEnd = {{LAST}}\n\t\t\t\t}\n\t\t\t},\n"
    "\t\t\tViewInfo = OperatorInfo { Pos = { 0, 300 } },\n\t\t},\n"
)
body += tool("InsetXf", "Transform", {"Center": val("{ {{CENTER_X}}, {{CENTER_Y}} }"), "Size": val("{{SCALE}}"), "Input": src("Inset1")}, 300, 300)
body += spline("PipFade", fade_keys())
body += tool("PipOver", "Merge", {"Background": src("MediaIn1"), "Foreground": src("InsetXf"), "Blend": src("PipFade", "Value")}, 900, 0)
body += media_out("PipOver")
register(
    manifest(
        "picture_in_picture",
        "compositing",
        "Second clip as an inset (show context with detail).",
        TIMING + [p("inset", "path"), p("center", "point", False, [0.72, 0.7]), p("scale", "number", False, 0.33, min=0.1, max=0.9)],
        gate="G3",
        tags=["compositing"],
    ),
    body,
)

body = media_in()
body += (
    "\t\tScreen1 = Loader {\n\t\t\tClips = {\n\t\t\t\tClip {\n\t\t\t\t\tID = \"Clip1\",\n\t\t\t\t\tFilename = {{INSET}},\n\t\t\t\t\tStartFrame = 0,\n"
    "\t\t\t\t\tLengthSetManually = true,\n\t\t\t\t\tTrimIn = 0,\n\t\t\t\t\tTrimOut = {{LAST}},\n\t\t\t\t\tGlobalStart = 0,\n\t\t\t\t\tGlobalEnd = {{LAST}}\n\t\t\t\t}\n\t\t\t},\n"
    "\t\t\tViewInfo = OperatorInfo { Pos = { 0, 300 } },\n\t\t},\n"
)
body += tool(
    "Corner1",
    "CornerPositioner",
    {
        "TopLeft": val("{ {{TL_X}}, {{TL_Y}} }"),
        "TopRight": val("{ {{TR_X}}, {{TR_Y}} }"),
        "BottomLeft": val("{ {{BL_X}}, {{BL_Y}} }"),
        "BottomRight": val("{ {{BR_X}}, {{BR_Y}} }"),
        "Input": src("Screen1"),
    },
    300,
    300,
)
body += tool("ScreenOver", "Merge", {"Background": src("MediaIn1"), "Foreground": src("Corner1")}, 900, 0)
body += media_out("ScreenOver")
register(
    manifest(
        "screen_replacement",
        "compositing",
        "Replace a screen/sign with another clip via a corner pin (static corners; tracked corners via planar tracker in Resolve).",
        TIMING + [p("inset", "path")] + [p(k, "point", False, d) for k, d in (("tl", [0.3, 0.7]), ("tr", [0.7, 0.7]), ("bl", [0.3, 0.3]), ("br", [0.7, 0.3]))],
        gate="G3",
        tags=["compositing", "tracking"],
    ),
    body,
)

body = media_in()
body += tool("BarTop", "RectangleMask", {"Center": val("{ 0.5, 1 }"), "Width": val("1"), "Height": val("{{BAR}}")}, 300, -100)
body += tool("BarBottom", "RectangleMask", {"Center": val("{ 0.5, 0 }"), "Width": val("1"), "Height": val("{{BAR}}"), "EffectMask": src("BarTop", "Mask")}, 300, -200)
body += tool("Bars", "Background", {"GlobalOut": val("{{LAST}}"), "Width": val("{{WIDTH}}"), "Height": val("{{HEIGHT}}"), "UseFrameFormatSettings": val("1"), "TopLeftAlpha": val("1"), "EffectMask": src("BarBottom", "Mask")}, 450, -200)
body += tool("BarsOver", "Merge", {"Background": src("MediaIn1"), "Foreground": src("Bars")}, 900, 0)
body += media_out("BarsOver")
register(manifest("letterbox", "compositing", "Cinematic bars (prefer Resolve output blanking when available).", TIMING + [p("bar", "number", False, 0.24, min=0.02, max=0.6)], tags=["composition"]), body)

body = media_in()
body += "\t\tShakeX = BezierSpline {\n\t\t\tSplineColor = { Red = 250, Green = 59, Blue = 49 },\n\t\t\tNameSet = true,\n\t\t\tKeyFrames = { {{#KEYS}}[{{FRAME}}] = { {{X}}, Flags = { Linear = true } }, {{/KEYS}} }\n\t\t},\n"
body += "\t\tShakeY = BezierSpline {\n\t\t\tSplineColor = { Red = 252, Green = 131, Blue = 47 },\n\t\t\tNameSet = true,\n\t\t\tKeyFrames = { {{#KEYS}}[{{FRAME}}] = { {{Y}}, Flags = { Linear = true } }, {{/KEYS}} }\n\t\t},\n"
body += '\t\tShakePath = XYPath {\n\t\t\tShowKeyPoints = false,\n\t\t\tDrawMode = "ModifyOnly",\n\t\t\tInputs = {\n\t\t\t\tX = Input { SourceOp = "ShakeX", Source = "Value", },\n\t\t\t\tY = Input { SourceOp = "ShakeY", Source = "Value", },\n\t\t\t},\n\t\t},\n'
body += tool("ShakeXf", "Transform", {"Center": src("ShakePath", "Value"), "Size": val("{{OVERSCAN}}"), "Input": src("MediaIn1")}, 400, 0)
body += media_out("ShakeXf")
register(manifest("camera_shake", "effect", "Impact shake with deterministic, seeded keyframes (overscanned, no black edges).", TIMING + [p("overscan", "number", False, 1.06, min=1.0, max=1.3)], tags=["motion", "impact"]), body)

# -------------------------------------------- transition halves (per clip)
def _half(name: str, description: str, tools: str, out: str, extra=None, tags=None):
    register(
        manifest(
            name,
            "transition",
            description,
            TIMING + [p("edge", "enum", False, "tail", choices=["tail", "head"])] + (extra or []),
            route="transition_halves",
            fallback="cross_dissolve",
            gate="G12",
            tags=tags or ["transition"],
        ),
        media_in() + tools + media_out(out),
    )


_half(
    "zoom_punch",
    "Zoom punch: outgoing clip scales up with radial-ish blur into the cut, incoming settles from oversize.",
    spline("PunchSize", [("{{IN}}", "{{SIZE_A}}"), ("{{OUT}}", "{{SIZE_B}}")]) + spline("PunchBlur", [("{{IN}}", "{{BLUR_A}}"), ("{{OUT}}", "{{BLUR_B}}")])
    + tool("PunchXf", "Transform", {"Size": src("PunchSize", "Value"), "Input": src("MediaIn1")}, 300, 0)
    + tool("PunchBlurT", "DirectionalBlur", {"Type": val("2"), "Length": src("PunchBlur", "Value"), "Input": src("PunchXf")}, 500, 0),
    "PunchBlurT",
    [p("size_a", "number", False, 1.0, min=0.5, max=3), p("size_b", "number", False, 1.35, min=0.5, max=3), p("blur_a", "number", False, 0.0, min=0, max=0.5), p("blur_b", "number", False, 0.12, min=0, max=0.5)],
)
_half(
    "whip_pan",
    "Whip pan: horizontal directional blur and offset across the cut.",
    xypath("WhipPath", [("{{IN}}", "{{X_A}}"), ("{{OUT}}", "{{X_B}}")], [("{{IN}}", "0.5"), ("{{OUT}}", "0.5")])
    + spline("WhipBlur", [("{{IN}}", "{{BLUR_A}}"), ("{{OUT}}", "{{BLUR_B}}")])
    + tool("WhipXf", "Transform", {"Center": src("WhipPath", "Value"), "Edges": val("3"), "Input": src("MediaIn1")}, 300, 0)
    + tool("WhipBlurT", "DirectionalBlur", {"Type": val("0"), "Length": src("WhipBlur", "Value"), "Angle": val("0"), "Input": src("WhipXf")}, 500, 0),
    "WhipBlurT",
    [p("x_a", "number", False, 0.5, min=-1, max=2), p("x_b", "number", False, 0.2, min=-1, max=2), p("blur_a", "number", False, 0.0, min=0, max=0.6), p("blur_b", "number", False, 0.25, min=0, max=0.6)],
)
_half(
    "flash_transition",
    "Flash: brightness spikes into the cut and decays after it (punctuate a beat or drop).",
    spline("FlashGain", [("{{IN}}", "{{GAIN_A}}"), ("{{OUT}}", "{{GAIN_B}}")]) + tool("FlashBC", "BrightnessContrast", {"Gain": src("FlashGain", "Value"), "Input": src("MediaIn1")}, 300, 0),
    "FlashBC",
    [p("gain_a", "number", False, 1.0, min=0.2, max=6), p("gain_b", "number", False, 3.0, min=0.2, max=6)],
)
_half(
    "glitch",
    "Digital glitch: short horizontal displacement jitter around the cut (seeded keys).",
    "\t\tGlitchX = BezierSpline {\n\t\t\tSplineColor = { Red = 250, Green = 59, Blue = 49 },\n\t\t\tNameSet = true,\n\t\t\tKeyFrames = { {{#KEYS}}[{{FRAME}}] = { {{X}}, Flags = { StepIn = true } }, {{/KEYS}} }\n\t\t},\n"
    + "\t\tGlitchY = BezierSpline {\n\t\t\tSplineColor = { Red = 252, Green = 131, Blue = 47 },\n\t\t\tNameSet = true,\n\t\t\tKeyFrames = { {{#KEYS}}[{{FRAME}}] = { {{Y}}, Flags = { StepIn = true } }, {{/KEYS}} }\n\t\t},\n"
    + '\t\tGlitchPath = XYPath {\n\t\t\tShowKeyPoints = false,\n\t\t\tDrawMode = "ModifyOnly",\n\t\t\tInputs = {\n\t\t\t\tX = Input { SourceOp = "GlitchX", Source = "Value", },\n\t\t\t\tY = Input { SourceOp = "GlitchY", Source = "Value", },\n\t\t\t},\n\t\t},\n'
    + tool("GlitchXf", "Transform", {"Center": src("GlitchPath", "Value"), "Edges": val("3"), "Input": src("MediaIn1")}, 300, 0),
    "GlitchXf",
)


# ------------------------------------------------------- overlay templates
OVERLAY_TEMPLATES = {
    "word_highlight": "Word-level karaoke captions (2-4 word chunks, active word highlighted); rendered by VANTA as an RGBA overlay and also exported as SRT.",
    "caption_clean": "Clean captions without per-word highlight; RGBA overlay + SRT.",
}


def build(root: Path = ROOT) -> list[str]:
    root.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (meta, comp) in TEMPLATES.items():
        folder = root / name
        folder.mkdir(exist_ok=True)
        (folder / "template.comp").write_text(comp, encoding="utf-8")
        (folder / "manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        written.append(name)
    for name, description in OVERLAY_TEMPLATES.items():
        folder = root / name
        folder.mkdir(exist_ok=True)
        (folder / "template.comp").write_text("-- rendered by VANTA's overlay renderer; no Fusion comp is imported\n", encoding="utf-8")
        meta = manifest(
            name,
            "text",
            description,
            [
                p("font", "font", False, "Arial"),
                p("color", "color", False, "#FFFFFF"),
                p("highlight", "color", False, "#FFD23F"),
                p("size", "number", False, 0.045, min=0.015, max=0.2, description="cap height / frame height"),
                p("y", "number", False, 0.68, min=0.05, max=0.95),
                p("outline", "number", False, 1.0, min=0, max=1),
                p("backing", "number", False, 0.0, min=0, max=1),
                p("uppercase", "bool", False, True),
            ],
            route="overlay",
            gate="G3",
            tags=["captions"],
        )
        (folder / "manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        written.append(name)
    return written


if __name__ == "__main__":
    names = build()
    print(f"wrote {len(names)} templates: {', '.join(names)}")
