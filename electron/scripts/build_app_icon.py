"""
Build two Dealer Saathi desktop icon options from source renders:
  - mesh: soft backdrop; document + cyan wireframe graphic scaled/centered on the sheet (navy keyed out) + subtle "AI".
  - silver: soft light backdrop; document + silver ring + subtle "AI".

Place sources under electron/resources/source/:
  - Mesh: mesh_ring.png **or** icon-mesh.png (first existing file is used).
  - Silver: silver_ring.png (optional — if missing, icon-silver.* duplicates the mesh option).

Run from repo root:
  pip install -r electron/scripts/requirements-icons.txt
  python electron/scripts/build_app_icon.py

Writes:
  electron/resources/icon-mesh.png / icon-mesh.ico
  electron/resources/icon-silver.png / icon-silver.ico
  electron/resources/icon.png / icon.ico  (copy of mesh option; default for builder)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
ELECTRON = REPO / "electron"
SRC = ELECTRON / "resources" / "source"
OUT_DIR = ELECTRON / "resources"

SIZE = 1024

# Subtle label: small, muted slate (readable on white, not loud)
AI_TEXT = "AI"
AI_FONT_SIZE = 58
AI_FILL = (148, 163, 184, 255)  # slate-400
AI_STROKE_W = 0


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        os.environ.get("SAATHI_ICON_FONT"),
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\segoeuisb.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    if bold:
        candidates = [
            os.environ.get("SAATHI_ICON_FONT"),
            r"C:\Windows\Fonts\segoeuisb.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\calibrib.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ] + candidates
    for p in candidates:
        if not p:
            continue
        try:
            return ImageFont.truetype(p, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def key_near_white_transparent(im: Image.Image, threshold: int = 8) -> Image.Image:
    """Drop pixels very close to pure white (keeps silver specular mostly; trims bg)."""
    rgba = im.convert("RGBA")
    px = rgba.load()
    w, h = rgba.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if r >= 255 - threshold and g >= 255 - threshold and b >= 255 - threshold:
                px[x, y] = (r, g, b, 0)
    return rgba


def key_mesh_navy_transparent(im: Image.Image, bg_tolerance: float = 42.0) -> Image.Image:
    """Remove dark navy backdrop from mesh render; keep bright cyan wireframe."""
    rgba = im.convert("RGBA")
    w, h = rgba.size
    samples = [
        rgba.getpixel((min(4, w - 1), min(4, h - 1))),
        rgba.getpixel((max(w - 5, 0), min(4, h - 1))),
        rgba.getpixel((min(4, w - 1), max(h - 5, 0))),
        rgba.getpixel((max(w - 5, 0), max(h - 5, 0))),
    ]
    br = sum(s[0] for s in samples) // len(samples)
    bg = sum(s[1] for s in samples) // len(samples)
    bb = sum(s[2] for s in samples) // len(samples)
    px = rgba.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            lum = (r + g + b) / 3.0
            dr, dg, db = r - br, g - bg, b - bb
            dist = (dr * dr + dg * dg + db * db) ** 0.5
            # Keep bright cyan lines; drop navy and dark interior of the torus hole
            if lum > 88:
                continue
            if dist < bg_tolerance or (lum < 62 and max(r, g, b) < 105):
                px[x, y] = (r, g, b, 0)
    return rgba


def silver_gradient_background() -> Image.Image:
    """Soft studio backdrop similar to the silver reference (white / cool gray)."""
    base = Image.new("RGB", (SIZE, SIZE), (241, 245, 249))
    px = base.load()
    for y in range(SIZE):
        t = y / max(SIZE - 1, 1)
        r = int(250 + (236 - 250) * t)
        g = int(252 + (242 - 252) * t)
        b = int(255 + (247 - 255) * t)
        for x in range(SIZE):
            px[x, y] = (r, g, b)
    return base


def draw_document_layer() -> Image.Image:
    """Rounded sheet + BR dog-ear; transparent elsewhere."""
    doc_x, doc_y = 96, 112
    doc_w, doc_h = 832, 800
    r = 28
    fold = 72

    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    sdraw.rounded_rectangle(
        (doc_x + 8, doc_y + 10, doc_x + doc_w + 8, doc_y + doc_h + 10),
        radius=r,
        fill=(15, 23, 42, 85),
    )

    paper = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(paper)
    draw.rounded_rectangle(
        (doc_x, doc_y, doc_x + doc_w, doc_y + doc_h),
        radius=r,
        fill=(252, 252, 253, 255),
        outline=(226, 232, 240, 255),
        width=2,
    )

    br_x, br_y = doc_x + doc_w, doc_y + doc_h
    fold_poly = [
        (br_x - fold, br_y),
        (br_x, br_y - fold),
        (br_x, br_y),
    ]
    draw.polygon(fold_poly, fill=(214, 221, 232, 255))
    draw.line(
        [(br_x - fold, br_y), (br_x, br_y - fold)],
        fill=(180, 190, 205, 255),
        width=1,
    )

    layer = Image.alpha_composite(shadow, paper)
    return layer


def document_box() -> tuple[int, int, int, int]:
    return 96, 112, 832, 800


def draw_subtle_ai() -> Image.Image:
    doc_x, doc_y, doc_w, doc_h = document_box()
    label = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(label)
    font = load_font(AI_FONT_SIZE, bold=False)
    tx, ty = doc_x + 40, doc_y + 32
    kwargs: dict = {"font": font, "fill": AI_FILL}
    if AI_STROKE_W > 0:
        kwargs["stroke_width"] = AI_STROKE_W
        kwargs["stroke_fill"] = (255, 255, 255, 120)
    tdraw.text((tx, ty), AI_TEXT, **kwargs)
    return label


def paste_silver_ring(canvas: Image.Image, silver_path: Path) -> Image.Image:
    doc_x, doc_y, doc_w, doc_h = document_box()
    silver = key_near_white_transparent(Image.open(silver_path))
    max_w = int(doc_w * 0.62)
    sw, sh = silver.size
    scale = min(max_w / sw, (doc_h * 0.58) / sh)
    nw, nh = int(sw * scale), int(sh * scale)
    silver = silver.resize((nw, nh), Image.Resampling.LANCZOS)

    cx = doc_x + doc_w // 2
    cy = doc_y + doc_h // 2 + 36
    paste_x = cx - nw // 2
    paste_y = cy - nh // 2
    canvas.paste(silver, (paste_x, paste_y), silver)
    return canvas


def paste_mesh_on_document(canvas: Image.Image, mesh_path: Path) -> Image.Image:
    """Same framing as silver: graphic scaled and centered on the document."""
    doc_x, doc_y, doc_w, doc_h = document_box()
    mesh = key_mesh_navy_transparent(Image.open(mesh_path))
    max_w = int(doc_w * 0.62)
    sw, sh = mesh.size
    scale = min(max_w / sw, (doc_h * 0.58) / sh)
    nw, nh = int(sw * scale), int(sh * scale)
    mesh = mesh.resize((nw, nh), Image.Resampling.LANCZOS)

    cx = doc_x + doc_w // 2
    cy = doc_y + doc_h // 2 + 36
    paste_x = cx - nw // 2
    paste_y = cy - nh // 2
    canvas.paste(mesh, (paste_x, paste_y), mesh)
    return canvas


def build_mesh_option(mesh_path: Path) -> Image.Image:
    base_rgb = silver_gradient_background()
    canvas = Image.new("RGBA", (SIZE, SIZE))
    canvas.paste(base_rgb)
    canvas = Image.alpha_composite(canvas, draw_document_layer())
    canvas = paste_mesh_on_document(canvas, mesh_path)
    canvas = Image.alpha_composite(canvas, draw_subtle_ai())
    return canvas


def build_silver_option(silver_path: Path) -> Image.Image:
    base_rgb = silver_gradient_background()
    canvas = Image.new("RGBA", (SIZE, SIZE))
    canvas.paste(base_rgb)
    canvas = Image.alpha_composite(canvas, draw_document_layer())
    canvas = paste_silver_ring(canvas, silver_path)
    canvas = Image.alpha_composite(canvas, draw_subtle_ai())
    return canvas


def save_png_ico(canvas: Image.Image, png_path: Path, ico_path: Path) -> None:
    canvas.convert("RGB").save(png_path, "PNG", optimize=True)
    im_ico = canvas.convert("RGBA")
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    imgs = [im_ico.resize(s, Image.Resampling.LANCZOS) for s in sizes]
    imgs[0].save(
        ico_path,
        format="ICO",
        sizes=sizes,
        append_images=imgs[1:],
    )


def resolve_mesh_source() -> Path | None:
    for name in ("mesh_ring.png", "icon-mesh.png"):
        p = SRC / name
        if p.is_file():
            return p
    return None


def main() -> int:
    mesh_path = resolve_mesh_source()
    if mesh_path is None:
        print(
            "Missing mesh source: add mesh_ring.png or icon-mesh.png to",
            SRC,
            file=sys.stderr,
        )
        return 1

    silver_path = SRC / "silver_ring.png"
    has_silver = silver_path.is_file()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    mesh_canvas = build_mesh_option(mesh_path)
    save_png_ico(mesh_canvas, OUT_DIR / "icon-mesh.png", OUT_DIR / "icon-mesh.ico")
    print("Wrote", OUT_DIR / "icon-mesh.png")
    print("Wrote", OUT_DIR / "icon-mesh.ico")

    if has_silver:
        silver_canvas = build_silver_option(silver_path)
    else:
        silver_canvas = mesh_canvas.copy()
        print("No silver_ring.png — icon-silver.* use the mesh option.", file=sys.stderr)
    save_png_ico(silver_canvas, OUT_DIR / "icon-silver.png", OUT_DIR / "icon-silver.ico")
    print("Wrote", OUT_DIR / "icon-silver.png")
    print("Wrote", OUT_DIR / "icon-silver.ico")

    # Default filenames expected by electron-builder / getAppIconPath
    save_png_ico(mesh_canvas, OUT_DIR / "icon.png", OUT_DIR / "icon.ico")
    print("Wrote", OUT_DIR / "icon.png", "(default, same as mesh option)")
    print("Wrote", OUT_DIR / "icon.ico", "(default, same as mesh option)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
