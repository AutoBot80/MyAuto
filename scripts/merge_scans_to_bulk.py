#!/usr/bin/env python3
"""
Merge raw scans (Aadhar back, Insurance, Aadhar front, Details sheet) into combined PDFs.
Order: Aadhar_back.jpg, Insurance.jpg, Aadhar.jpg, Details.jpg
Output: My Auto.AI/Bulk Upload/Scans/<subfolder>/Scans.pdf

Run from project root: python scripts/merge_scans_to_bulk.py
Or: python -m scripts.merge_scans_to_bulk (from project root)
"""
import sys
from pathlib import Path

# Add backend to path so app.config and app.services are importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "backend"))

from app.config import UPLOADS_DIR, BULK_UPLOAD_DIR
from app.services.merge_scans_service import merge_all_scans


def main() -> int:
    if not UPLOADS_DIR.is_dir():
        print(f"Uploads dir not found: {UPLOADS_DIR}")
        return 1
    results = merge_all_scans(UPLOADS_DIR, BULK_UPLOAD_DIR)
    ok = sum(1 for r in results if r.get("ok"))
    for r in results:
        if r.get("ok"):
            print(f"  OK {r['subfolder']} -> {r['output_path']}")
        else:
            print(f"  SKIP {r['subfolder']}: {r.get('error', 'no files')}")
    print(f"\nDone: {ok}/{len(results)} subfolders merged into {BULK_UPLOAD_DIR / 'Scans'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
