#!/usr/bin/env python3
"""
Combine Cust 2 files in Shashank Documents folder into a single PDF (scan 2.pdf).
Run from project root: python scripts/combine_cust2_to_scan2.py
"""
import sys
from pathlib import Path

# Shashank Documents: inside My Auto.AI project
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SHASHANK_DOCS = _PROJECT_ROOT / "Shashank Documents"

# Order: Aadhar first, then Details, then Insurance (docx last if included)
CUST2_ORDER = [
    "Cust 2 - Aadhar front and back.jpg",
    "Cust 2 - Details Sheet.pdf",
    "Cust 2 - Insurance.jpg",
    "Cust 2 - Details Sheet.docx",
]


def main() -> int:
    if not _SHASHANK_DOCS.is_dir():
        print(f"Shashank Documents folder not found: {_SHASHANK_DOCS}")
        return 1

    import fitz

    files_to_merge: list[Path] = []
    for name in CUST2_ORDER:
        p = _SHASHANK_DOCS / name
        if p.exists():
            files_to_merge.append(p)
        else:
            print(f"  Skip (not found): {name}")

    if not files_to_merge:
        print("No Cust 2 files found.")
        return 1

    out_path = _SHASHANK_DOCS / "scan 2.pdf"
    merged = fitz.open()
    try:
        for f in files_to_merge:
            if f.suffix.lower() == ".docx":
                # fitz doesn't support docx; skip or use docx2pdf if available
                print(f"  Skip docx (use PDF version): {f.name}")
                continue
            doc = fitz.open(str(f))
            try:
                if doc.is_pdf:
                    merged.insert_pdf(doc, from_page=0, to_page=-1)
                else:
                    pdf_bytes = doc.convert_to_pdf()
                    img_pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
                    merged.insert_pdf(img_pdf, from_page=0, to_page=-1)
                    img_pdf.close()
            finally:
                doc.close()
        merged.save(str(out_path))
        print(f"Created: {out_path} ({len(merged)} pages from {len(files_to_merge)} files)")
        return 0
    finally:
        merged.close()


if __name__ == "__main__":
    sys.exit(main())
