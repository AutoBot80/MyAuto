#!/usr/bin/env python3
"""
Combine Scan 1 and Scan 2 into a single PDF (scan 3.pdf).
Output: Shashank Documents/scan 3.pdf
Run from project root: python scripts/combine_scan1_scan2_to_scan3.py
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SHASHANK_DOCS = _PROJECT_ROOT / "Shashank Documents"

SCAN1 = _PROJECT_ROOT / "Scan1.pdf"
SCAN2 = _SHASHANK_DOCS / "scan 2.pdf"


def main() -> int:
    if not SCAN1.exists():
        print(f"Scan 1 not found: {SCAN1}")
        return 1
    if not SCAN2.exists():
        print(f"Scan 2 not found: {SCAN2}")
        return 1

    import fitz

    out_path = _SHASHANK_DOCS / "scan 3.pdf"
    merged = fitz.open()
    try:
        for pdf_path in [SCAN1, SCAN2]:
            doc = fitz.open(str(pdf_path))
            merged.insert_pdf(doc, from_page=0, to_page=-1)
            doc.close()
        merged.save(str(out_path))
        print(f"Created: {out_path} ({len(merged)} pages)")
        return 0
    finally:
        merged.close()


if __name__ == "__main__":
    sys.exit(main())
