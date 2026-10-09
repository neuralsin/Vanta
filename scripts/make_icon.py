"""Render VANTA's application icon (assets/vanta.ico + vanta.png) without external assets."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
AMBER = (255, 176, 32, 255)
CHARCOAL = (16, 17, 21, 255)


def draw(size: int) -> Image.Image:
    s = 4  # supersample
    big = size * s
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(big * 0.22)
    d.rounded_rectangle([0, 0, big - 1, big - 1], radius=r, fill=CHARCOAL)
    # subtle amber glow
    glow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([big * 0.15, big * 0.2, big * 0.85, big * 0.9], fill=(255, 176, 32, 60))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(big * 0.08)))
    d = ImageDraw.Draw(img)
    # stylised V made of two film strips meeting at a point
    w = big * 0.13
    top = big * 0.22
    bottom = big * 0.80
    left, right, mid = big * 0.20, big * 0.80, big * 0.50
    d.line([(left, top), (mid, bottom)], fill=AMBER, width=int(w), joint="curve")
    d.line([(right, top), (mid, bottom)], fill=AMBER, width=int(w), joint="curve")
    # sprocket holes on the strips
    for t in (0.18, 0.42, 0.66):
        for x0, x1 in ((left, mid), (right, mid)):
            x = x0 + (x1 - x0) * t
            y = top + (bottom - top) * t
            hole = big * 0.022
            d.rounded_rectangle([x - hole, y - hole, x + hole, y + hole], radius=hole * 0.4, fill=CHARCOAL)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [draw(size) for size in sizes]
    images[-1].save(assets / "vanta.png")
    images[-1].save(assets / "vanta.ico", sizes=[(s, s) for s in sizes], append_images=images[:-1])
    print(f"wrote {assets / 'vanta.ico'}")


if __name__ == "__main__":
    main()
