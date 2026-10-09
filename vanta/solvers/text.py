"""Text placement, legibility and font mapping (manual 13.3, 13.8).

Candidate positions are scored by distance from faces and the subject matte,
platform safe zones, saliency, background complexity, contrast against the
blurred local background and the user's habitual position. When a position is
otherwise good but contrast fails, outline/shadow/backing is added before the
text is moved. Exact fonts are never guessed: a font *class* maps onto the
installed-font registry.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from vanta.analysis.frame_vision import detect_faces, saliency_map

SOLVER_VERSION = "text-v2"


@dataclass(frozen=True)
class SafeZone:
    top: float
    bottom: float
    left: float
    right: float


# fractions of frame height/width covered by platform UI (current app layouts; editable in settings)
SAFE_ZONES = {
    "instagram_reel": SafeZone(top=0.12, bottom=0.24, left=0.05, right=0.13),
    "tiktok": SafeZone(top=0.10, bottom=0.27, left=0.05, right=0.16),
    "youtube_short": SafeZone(top=0.08, bottom=0.21, left=0.05, right=0.14),
    "landscape": SafeZone(top=0.05, bottom=0.08, left=0.05, right=0.05),
}


@dataclass
class TextBox:
    x: float  # centre, normalised
    y: float
    w: float
    h: float

    def rect(self, width: int, height: int) -> tuple[int, int, int, int]:
        x0 = int((self.x - self.w / 2) * width)
        y0 = int((self.y - self.h / 2) * height)
        return max(0, x0), max(0, y0), min(width, x0 + int(self.w * width)), min(height, y0 + int(self.h * height))


@dataclass
class Placement:
    x: float
    y: float
    box: TextBox
    score: float
    contrast_ratio: float
    outline: bool
    shadow: bool
    backing: bool
    color: str
    terms: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def estimate_box(text: str, size: float, aspect: float, *, char_width: float = 0.56, max_width: float = 0.8) -> tuple[TextBox, int]:
    """Approximate the text block (wraps to keep within ``max_width``). ``size`` = cap height/frame height."""
    chars = max(1, len(text))
    line_w = chars * char_width * size / aspect
    lines = 1
    while line_w / lines > max_width and lines < 4:
        lines += 1
    w = min(max_width, line_w / lines * 1.06)
    h = size * 1.35 * lines
    return TextBox(0.5, 0.5, w, h), lines


def _relative_luminance(rgb: np.ndarray) -> np.ndarray:
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return lin @ np.array([0.2126, 0.7152, 0.0722])


def contrast_ratio(text_hex: str, background_rgb: np.ndarray) -> float:
    rgb = np.array([int(text_hex[i : i + 2], 16) for i in (1, 3, 5)], dtype=np.float64) / 255
    lt = float(_relative_luminance(rgb))
    lb = float(np.median(_relative_luminance(background_rgb.reshape(-1, 3).astype(np.float64) / 255)))
    hi, lo = max(lt, lb), min(lt, lb)
    return (hi + 0.05) / (lo + 0.05)


def place_text(
    frames: list[np.ndarray],
    text: str,
    *,
    size: float = 0.055,
    color: str = "#FFFFFF",
    platform: str = "instagram_reel",
    habitual: tuple[float, float] | None = None,
    matte: np.ndarray | None = None,
    behind_subject: bool = False,
    avoid_boxes: list[TextBox] | None = None,
) -> Placement:
    """Choose a position over the frames the text spans (all frames must be satisfied)."""
    zone = SAFE_ZONES.get(platform, SAFE_ZONES["instagram_reel"])
    h, w = frames[0].shape[:2]
    box, _ = estimate_box(text, size, w / h)
    faces = [f for frame in frames for f in detect_faces(frame)]
    sal = np.mean([cv2.resize(saliency_map(f), (64, 112)) for f in frames], axis=0)
    blurred = [cv2.GaussianBlur(f, (0, 0), 6) for f in frames]
    edges = np.mean([cv2.Canny(cv2.cvtColor(f, cv2.COLOR_RGB2GRAY), 60, 160) for f in frames], axis=0) / 255
    xs = [0.5] if box.w > 0.6 else [0.5, 0.33, 0.67]
    ys = np.arange(zone.top + box.h / 2 + 0.01, 1 - zone.bottom - box.h / 2, 0.025)
    best: Placement | None = None
    for x in xs:
        for y in ys:
            cand = TextBox(x, float(y), box.w, box.h)
            x0, y0, x1, y1 = cand.rect(w, h)
            if x0 < zone.left * w or x1 > (1 - zone.right) * w:
                continue
            terms: dict[str, float] = {}
            # faces: hard rule 'never text over faces' unless explicitly behind subject
            face_overlap = 0.0
            for f in faces:
                fx0, fy0 = f["x"] * w, f["y"] * h
                fx1, fy1 = fx0 + f["w"] * w, fy0 + f["h"] * h
                ix = max(0.0, min(x1, fx1) - max(x0, fx0))
                iy = max(0.0, min(y1, fy1) - max(y0, fy0))
                face_overlap = max(face_overlap, ix * iy / max(1.0, (fx1 - fx0) * (fy1 - fy0)))
            if face_overlap > 0.02 and not behind_subject:
                continue
            terms["faces"] = 1 - face_overlap
            sy0, sy1 = int(y0 / h * 112), max(int(y0 / h * 112) + 1, int(y1 / h * 112))
            sx0, sx1 = int(x0 / w * 64), max(int(x0 / w * 64) + 1, int(x1 / w * 64))
            terms["saliency"] = 1 - float(sal[sy0:sy1, sx0:sx1].mean())
            if matte is not None:
                m = cv2.resize(matte.astype(np.float32), (w, h))[y0:y1, x0:x1]
                cover = float(m.mean()) if m.size else 0.0
                # behind-subject text wants partial occlusion (30-60%), otherwise none
                terms["matte"] = float(np.exp(-(((cover - 0.4) / 0.2) ** 2))) if behind_subject else 1 - cover
            terms["calm_background"] = 1 - float(edges[y0:y1, x0:x1].mean()) * 3 if y1 > y0 and x1 > x0 else 0.0
            ratios = [contrast_ratio(color, b[y0:y1, x0:x1]) for b in blurred if y1 > y0 and x1 > x0]
            ratio = float(min(ratios)) if ratios else 1.0
            terms["contrast"] = float(np.clip((ratio - 1.5) / 3.0, 0, 1))
            if habitual:
                terms["habit"] = float(np.exp(-(((x - habitual[0]) / 0.25) ** 2 + ((y - habitual[1]) / 0.12) ** 2)))
            else:
                terms["habit"] = float(np.exp(-(((y - 0.42) / 0.2) ** 2)))
            if avoid_boxes:
                clash = any(abs(cand.y - b.y) < (cand.h + b.h) / 2 and abs(cand.x - b.x) < (cand.w + b.w) / 2 for b in avoid_boxes)
                if clash:
                    continue
            weights = {"faces": 0.2, "saliency": 0.18, "matte": 0.12, "calm_background": 0.15, "contrast": 0.2, "habit": 0.15}
            score = sum(weights[k] * terms.get(k, 0.7) for k in weights)
            if best is None or score > best.score:
                best = Placement(float(x), float(y), cand, float(score), ratio, False, False, False, color, terms)
    if best is None:
        cand = TextBox(0.5, zone.top + box.h / 2 + 0.02, box.w, box.h)
        best = Placement(0.5, cand.y, cand, 0.2, 1.0, True, True, True, color, {}, ["no candidate cleared faces/safe zone: top-safe fallback with backing"])
    # legibility: style the text before moving it (manual 13.3)
    if best.contrast_ratio < 4.5:
        best.shadow = True
        best.reasons.append(f"contrast {best.contrast_ratio:.1f}:1 below 4.5:1 -> soft shadow")
    if best.contrast_ratio < 3.0:
        best.outline = True
        best.reasons.append("contrast below 3:1 -> outline")
    if best.contrast_ratio < 1.8:
        best.backing = True
        best.reasons.append("contrast below 1.8:1 -> backing plate")
    best.reasons.insert(0, f"placed at ({best.x:.2f}, {best.y:.2f}); " + ", ".join(f"{k} {v:.2f}" for k, v in best.terms.items()))
    return best


# ------------------------------------------------------------- font registry
WEIGHT_WORDS = {
    "heavy": ("black", "heavy", "extrabold", "ultra"),
    "bold": ("bold", "semibold", "demibold", "bd"),
    "regular": ("regular", "book", "normal", "medium", ""),
    "light": ("light", "thin", "extralight", "hairline"),
}
WIDTH_WORDS = {"condensed": ("condensed", "narrow", "compressed", "cond"), "wide": ("extended", "expanded", "wide")}
SERIF_HINTS = ("serif", "times", "georgia", "garamond", "bodoni", "playfair", "cambria", "baskerville")


def scan_fonts(extra_dirs: list[Path] | None = None) -> list[dict]:
    """Inventory installed font files (Windows system + user fonts)."""
    dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    dirs += extra_dirs or []
    fonts = []
    for folder in dirs:
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.suffix.lower() not in {".ttf", ".otf"}:
                continue
            name = re.sub(r"[_\-]+", " ", path.stem)
            lower = name.casefold()
            weight = next((w for w, words in WEIGHT_WORDS.items() if any(word and word in lower for word in words)), "regular")
            width = next((w for w, words in WIDTH_WORDS.items() if any(word in lower for word in words)), "normal")
            fonts.append(
                {
                    "name": name,
                    "file": str(path),
                    "weight": weight,
                    "width": width,
                    "serif": any(h in lower for h in SERIF_HINTS),
                    "italic": "italic" in lower or lower.endswith(" i") or lower.endswith("it"),
                }
            )
    return fonts


def match_font(font_class: str, fonts: list[dict], *, prefer: list[str] | None = None) -> dict | None:
    """Map a font class like 'condensed heavy sans' to the closest installed font."""
    words = font_class.casefold().split()
    weight = next((w for w in ("heavy", "bold", "regular", "light") if w in words), "bold")
    width = next((w for w in ("condensed", "wide") if w in words), "normal")
    serif = "serif" in words and "sans" not in words
    order = ["light", "regular", "bold", "heavy"]
    best, best_score = None, -1e9
    for font in fonts:
        if font["italic"]:
            continue
        score = 0.0
        score -= abs(order.index(font["weight"]) - order.index(weight)) * 1.0
        score -= 0.0 if font["width"] == width else 0.8
        score -= 0.0 if font["serif"] == serif else 1.5
        if prefer and any(p.casefold() in font["name"].casefold() for p in prefer):
            score += 2.0
        if any(k in font["name"].casefold() for k in ("montserrat", "inter", "bebas", "oswald", "anton", "impact", "arial", "segoe ui", "helvetica")):
            score += 0.3  # well-hinted display families
        if score > best_score:
            best, best_score = font, score
    return best


# ---------------------------------------------------------------- captions
@dataclass
class Chunk:
    text: str
    start_s: float
    end_s: float
    words: list[dict]


def chunk_words(words: list[dict], *, min_words: int = 2, max_words: int = 4, max_chars: int = 22, gap_break_s: float = 0.35) -> list[Chunk]:
    """Word-level karaoke chunks: 2-4 words, no orphans, break on pauses/punctuation."""
    chunks: list[list[dict]] = []
    current: list[dict] = []
    for i, word in enumerate(words):
        text = word["text"].strip()
        if not text:
            continue
        current.append(word)
        chars = sum(len(w["text"]) + 1 for w in current)
        nxt = words[i + 1] if i + 1 < len(words) else None
        pause = (nxt["start_s"] - word["end_s"]) if nxt else 1.0
        punct = text[-1] in ".!?,;:"
        if len(current) >= max_words or chars >= max_chars or (len(current) >= min_words and (punct or pause > gap_break_s)):
            chunks.append(current)
            current = []
    if current:
        if chunks and len(current) < min_words and len(chunks[-1]) + len(current) <= max_words + 1:
            chunks[-1].extend(current)
        else:
            chunks.append(current)
    return [
        Chunk(
            text=" ".join(w["text"].strip() for w in c),
            start_s=c[0]["start_s"],
            end_s=max(c[-1]["end_s"], c[0]["start_s"] + 0.3),
            words=[{"text": w["text"].strip(), "start_s": w["start_s"], "end_s": w["end_s"]} for w in c],
        )
        for c in chunks
    ]
