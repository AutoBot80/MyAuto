"""
Convert a custom PNG image to app icons (icon.png + multi-size icon.ico).
Usage: python convert_custom_icon.py <path_to_source_png>
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("Install Pillow: pip install pillow", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python convert_custom_icon.py <path_to_source_png>", file=sys.stderr)
        sys.exit(1)

    source_path = Path(sys.argv[1])
    if not source_path.exists():
        print(f"Source file not found: {source_path}", file=sys.stderr)
        sys.exit(1)

    root = Path(__file__).resolve().parent.parent
    out_dir = root / "resources"
    out_dir.mkdir(parents=True, exist_ok=True)

    img = Image.open(source_path).convert("RGBA")
    
    # Resize to 1024x1024 for high-res master (if not already)
    if img.size != (1024, 1024):
        img = img.resize((1024, 1024), Image.Resampling.LANCZOS)
    
    # Save PNG
    png_path = out_dir / "icon.png"
    img.save(png_path, "PNG")
    print(f"Wrote {png_path}")

    # Create multi-size ICO
    ico_sizes = [16, 32, 48, 64, 128, 256]
    ico_images: list[Image.Image] = []
    for s in ico_sizes:
        ico_images.append(img.resize((s, s), Image.Resampling.LANCZOS))

    ico_path = out_dir / "icon.ico"
    first = ico_images[0].copy()
    rest = [im.copy() for im in ico_images[1:]]
    first.save(ico_path, format="ICO", sizes=[(s, s) for s in ico_sizes], append_images=rest)
    print(f"Wrote {ico_path}")
    
    print("\nIcon files updated! Rebuild the Electron app to use the new icons.")


if __name__ == "__main__":
    main()
