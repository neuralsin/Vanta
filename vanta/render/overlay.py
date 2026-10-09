"""RGBA overlay renderer for titles and word-level captions.

Renders the same animation vocabulary as the Fusion templates so the preview,
the pre-rendered-overlay fallback route (G3) and Resolve agree visually.
Text is supersampled 2x and downscaled for clean edges; outlines are true
stroke outlines; shadows are Gaussian-blurred; active caption words pop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from vanta.core.schemas import CaptionChunk, EditDecisionGraph, TextLayer

SUPERSAMPLE = 2


@lru_cache(maxsize=64)
def _font(path: str, px: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, px)
    except OSError:
        for fallback in ("arialbd.ttf", "arial.ttf", "segoeuib.ttf"):
            try:
                return ImageFont.truetype(fallback, px)
            except OSError:
                continue
        return ImageFont.load_default(size=px)


def _font_path(layer_font_file: str | None, font_name: str | None) -> str:
    if layer_font_file and Path(layer_font_file).is_file():
        return layer_font_file
    if font_name:

        for f in _font_index():
            if f["name"].casefold() == font_name.casefold():
                return f["file"]
    return "arialbd.ttf"


@lru_cache(maxsize=1)
def _font_index() -> tuple[dict, ...]:
    from vanta.solvers.text import scan_fonts

    return tuple(scan_fonts())


def _hex(color: str, alpha: float = 1.0) -> tuple[int, int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), int(255 * max(0.0, min(1.0, alpha)))


def _ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _back_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


@dataclass
class _Style:
    font_path: str
    px: int
    fill: tuple[int, int, int, int]
    outline: bool
    shadow: bool
    backing: bool
    spacing: float = 0.0


def _draw_text(canvas_w: int, canvas_h: int, text: str, cx: float, cy: float, style: _Style, *, scale: float = 1.0, alpha: float = 1.0, word_colors: list[tuple[int, int, int, int]] | None = None, clip_x: float | None = None) -> Image.Image:
    """Render one (possibly multi-word) line centred at (cx, cy) in canvas pixels."""
    s = SUPERSAMPLE
    px = max(4, int(style.px * scale * s))
    font = _font(style.font_path, px)
    words = text.split(" ")
    space = font.getlength(" ")
    widths = [font.getlength(w) + style.spacing * px * max(0, len(w) - 1) for w in words]
    total = sum(widths) + space * (len(words) - 1)
    ascent, descent = font.getmetrics()
    height = ascent + descent
    pad = int(px * 0.6)
    layer = Image.new("RGBA", (int(total) + 2 * pad, height + 2 * pad), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    stroke = max(1, int(px * 0.07)) if style.outline else 0
    x = pad
    glyphs = []
    for i, word in enumerate(words):
        color = word_colors[i] if word_colors else style.fill
        if style.spacing:
            cx_ = x
            for ch in word:
                glyphs.append((cx_, ch, color))
                cx_ += font.getlength(ch) + style.spacing * px
        else:
            glyphs.append((x, word, color))
        x += widths[i] + space
    if style.backing:
        draw.rounded_rectangle([pad * 0.4, pad * 0.5, layer.width - pad * 0.4, layer.height - pad * 0.5], radius=int(px * 0.25), fill=(0, 0, 0, 150))
    if style.shadow:
        shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        for gx, g, _ in glyphs:
            sd.text((gx + px * 0.05, pad + px * 0.06), g, font=font, fill=(0, 0, 0, 170), stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
        shadow = shadow.filter(ImageFilter.GaussianBlur(px * 0.08))
        layer = Image.alpha_composite(layer, shadow)
        draw = ImageDraw.Draw(layer)
    for gx, g, color in glyphs:
        draw.text((gx, pad), g, font=font, fill=color, stroke_width=stroke, stroke_fill=(0, 0, 0, color[3]))
    if s != 1:
        layer = layer.resize((max(1, layer.width // s), max(1, layer.height // s)), Image.LANCZOS)
    if clip_x is not None:
        arr = np.array(layer)
        cut = int(arr.shape[1] * max(0.0, min(1.0, clip_x)))
        arr[:, cut:, 3] = 0
        layer = Image.fromarray(arr)
    if alpha < 1:
        arr = np.array(layer).astype(np.float32)
        arr[..., 3] *= alpha
        layer = Image.fromarray(arr.astype(np.uint8))
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    canvas.alpha_composite(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)))
    return canvas


def _wrap(text: str, max_chars: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > max_chars:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines or [text]


def render_text_layer(layer: TextLayer, frame: int, width: int, height: int, fps: float) -> Image.Image | None:
    if not (layer.start_frame <= frame < layer.end_frame):
        return None
    local = frame - layer.start_frame
    duration = layer.end_frame - layer.start_frame
    ramp = max(2, min(int(0.27 * fps), duration // 3))
    t_in = local / ramp
    t_out = (duration - local) / ramp
    alpha = min(1.0, _ease_out(t_in), _ease_out(t_out))
    cap_px = layer.size * height
    style = _Style(_font_path(layer.params.get("font_file"), layer.font), int(cap_px / 0.72), _hex(layer.color), layer.outline, layer.shadow, bool(layer.params.get("backing")))
    cx, cy = layer.x * width, layer.y * height
    template = layer.template
    scale = 1.0
    clip_x = None
    text = layer.text
    if template == "slide":
        cx -= (1 - _ease_out(t_in)) * 0.2 * width
        cx += (1 - _ease_out(t_out)) * 0.2 * width if t_out < 1 else 0
        alpha = min(1.0, _ease_out(t_out))
    elif template == "scale_pop":
        scale = _back_out(t_in) * 0.4 + 0.6 if t_in < 1 else 1.0
    elif template == "tracking_reveal":
        style.spacing = 0.8 * (1 - _ease_out(local / max(1, duration * 0.6)))
    elif template == "typewriter":
        count = max(1, int(len(text) * min(1.0, local / max(1, duration * 0.55))))
        text = text[:count]
        alpha = min(1.0, _ease_out(t_out))
    elif template == "kinetic_words":
        words = text.split()
        per = max(1, duration // max(1, len(words)))
        idx = min(len(words) - 1, local // per)
        text = words[idx]
        scale = 1.6 * (_back_out((local - idx * per) / max(1, per * 0.35)) * 0.3 + 0.7)
        alpha = 1.0
    elif template == "text_2_5d":
        cx += (local / max(1, duration) - 0.5) * 0.02 * width
        scale = 1 + 0.04 * local / max(1, duration)
    elif template == "masked_reveal":
        clip_x = _ease_out(t_in)
        alpha = min(1.0, _ease_out(t_out))
    lines = _wrap(text, 18) if template not in {"kinetic_words"} else [text]
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    line_h = style.px * scale * 1.05
    top = cy - line_h * (len(lines) - 1) / 2
    for i, line in enumerate(lines):
        canvas.alpha_composite(_draw_text(width, height, line, cx, top + i * line_h, style, scale=scale, alpha=alpha, clip_x=clip_x))
    return canvas


def render_caption(chunk: CaptionChunk, frame: int, width: int, height: int, *, font_path: str, size: float = 0.045, color: str = "#FFFFFF", highlight: str = "#FFD23F") -> Image.Image | None:
    if not (chunk.start_frame <= frame < chunk.end_frame):
        return None
    style = _Style(font_path, int(size * height / 0.72), _hex(color), True, True, False)
    words = chunk.text.split(" ")
    active = None
    for i, w in enumerate(chunk.words):
        if w["start_frame"] <= frame < max(w["end_frame"], w["start_frame"] + 2):
            active = i
    colors = [_hex(highlight) if i == active else _hex(color) for i in range(len(words))]
    local = frame - chunk.start_frame
    pop = 1.0 + 0.06 * math.exp(-local / 3.0)
    return _draw_text(width, height, chunk.text, width / 2, chunk.y * height, style, scale=pop, word_colors=colors if len(colors) == len(words) else None)


def render_overlay_frame(edg: EditDecisionGraph, frame: int, width: int, height: int, *, include_captions: bool = True, caption_style: dict | None = None) -> Image.Image | None:
    canvas = None
    for layer in edg.texts:
        if layer.behind_subject:
            continue  # composited against the matte by the preview renderer
        img = render_text_layer(layer, frame, width, height, edg.fps)
        if img is not None:
            canvas = img if canvas is None else Image.alpha_composite(canvas, img)
    if include_captions:
        style = caption_style or {}
        font_path = _font_path(style.get("font_file"), style.get("font"))
        for chunk in edg.captions:
            img = render_caption(chunk, frame, width, height, font_path=font_path, size=style.get("size", 0.045), color=style.get("color", "#FFFFFF"), highlight=style.get("highlight", "#FFD23F"))
            if img is not None:
                canvas = img if canvas is None else Image.alpha_composite(canvas, img)
    return canvas


def write_overlay_sequence(edg: EditDecisionGraph, out_dir: Path, width: int, height: int, *, layers: str = "all", progress=None) -> tuple[Path, int]:
    """Write ``overlay_%05d.png`` for every timeline frame (fully transparent frames included)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    total = edg.duration_frames
    blank = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for frame in range(total):
        img = render_overlay_frame(edg, frame, width, height, include_captions=layers in {"all", "captions"})
        (img or blank).save(out_dir / f"overlay_{frame:05d}.png", optimize=False, compress_level=1)
        if progress and frame % 15 == 0:
            progress(frame / max(1, total))
    return out_dir / "overlay_%05d.png", total


def write_srt(edg: EditDecisionGraph, path: Path) -> Path:
    def stamp(frame: int) -> str:
        seconds = frame / edg.fps
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"

    lines = []
    for i, chunk in enumerate(edg.captions, 1):
        lines += [str(i), f"{stamp(chunk.start_frame)} --> {stamp(chunk.end_frame)}", chunk.text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
