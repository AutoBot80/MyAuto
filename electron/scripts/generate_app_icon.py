"""
Generate Dealer Saathi Electron app icon (1024 master -> icon.png + multi-size icon.ico).
Document with bottom-right dog-ear fold; "AI" wordmark top-left on blue field.
Requires: pip install pillow
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Install Pillow: pip install pillow", file=sys.stderr)
    sys.exit(1)

SIZE = 1024


def lerp(a: float, b: float, t: float) -> int:
    return int(a + (b - a) * t)


def draw_vertical_gradient_rgba(img: Image.Image, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> None:
    w, h = img.size
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        r = lerp(top[0], bottom[0], t)
        g = lerp(top[1], bottom[1], t)
        b = lerp(top[2], bottom[2], t)
        for x in range(w):
            px[x, y] = (r, g, b, 255)


def try_load_bold_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
        Path(r"C:\Windows\Fonts\segoeuib.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    for p in candidates:
        if p.is_file():
            try:
                return ImageFont.truetype(str(p), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    out_dir = root / "resources"
    out_dir.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    # Blue field (dealer / professional)
    draw_vertical_gradient_rgba(img, (30, 58, 138), (59, 130, 246))
    draw = ImageDraw.Draw(img)

    doc_w, doc_h = 580, 700
    cx, cy = SIZE // 2 + 24, SIZE // 2 + 56
    x0 = cx - doc_w // 2
    y0 = cy - doc_h // 2
    x1 = cx + doc_w // 2
    y1 = cy + doc_h // 2
    radius = 22

    # Soft shadow
    shadow_off = 10
    draw.rounded_rectangle(
        [x0 + shadow_off, y0 + shadow_off, x1 + shadow_off, y1 + shadow_off],
        radius=radius,
        fill=(15, 23, 42, 90),
    )

    # Paper
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=(255, 255, 255, 255))

    # Dog-ear (bottom-right corner fold)
    fold = 76
    dog = [(x1 - fold, y1), (x1, y1), (x1, y1 - fold)]
    draw.polygon(dog, fill=(203, 213, 225, 255))
    draw.line([(x1 - fold, y1), (x1, y1 - fold)], fill=(148, 163, 184, 255), width=3)

    # Paper lines
    ly = y0 + 100
    for _ in range(5):
        draw.rounded_rectangle([x0 + 44, ly, x1 - 120, ly + 12], radius=4, fill=(226, 232, 240, 255))
        ly += 44

    # Simple "neural" motif (reads OK at small sizes)
    mx, my = cx, cy + 10
    r = 16
    for ox, oy in ((-44, 0), (44, 0), (0, -38)):
        draw.ellipse(
            [mx + ox - r, my + oy - r, mx + ox + r, my + oy + r],
            fill=(37, 99, 235, 255),
            outline=(29, 78, 216, 255),
            width=2,
        )
    draw.line([(mx - 44, my), (mx + 44, my)], fill=(29, 78, 216, 255), width=5)
    draw.line([(mx, my - 38), (mx, my)], fill=(29, 78, 216, 255), width=5)

    # "AI" top-left (amber on blue)
    font = try_load_bold_font(108)
    text = "AI"
    tx, ty = 32, 20
    if hasattr(draw, "textbbox"):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
    else:
        tw = draw.textlength(text, font=font)  # type: ignore[attr-defined]
    # Keep wordmark clear of document (document starts ~192px; cap width ~150)
    if tw > 150 and font != ImageFont.load_default():
        font = try_load_bold_font(88)
    draw.text((tx + 3, ty + 3), text, font=font, fill=(15, 23, 42, 220))
    draw.text((tx, ty), text, font=font, fill=(251, 191, 36, 255))

    png_path = out_dir / "icon.png"
    img.save(png_path, "PNG")

    ico_sizes = [16, 32, 48, 64, 128, 256]
    ico_images: list[Image.Image] = []
    for s in ico_sizes:
        ico_images.append(img.resize((s, s), Image.Resampling.LANCZOS))

    ico_path = out_dir / "icon.ico"
    first = ico_images[0].copy()
    rest = [im.copy() for im in ico_images[1:]]
    first.save(ico_path, format="ICO", sizes=[(s, s) for s in ico_sizes], append_images=rest)

    print(f"Wrote {png_path}")
    print(f"Wrote {ico_path}")


if __name__ == "__main__":
    main()
