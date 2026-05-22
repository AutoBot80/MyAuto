"""
Test pencil mark extraction on a Sales Detail Sheet.

Usage:
    python test_pencil_mark_extraction.py <path_to_sales_detail_sheet.pdf>
    python test_pencil_mark_extraction.py <path_to_sales_detail_sheet.jpg>

Output:
    Creates pencil_mark_test.jpeg in the same folder as the input file.
"""

import sys
import io
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print("Usage: python test_pencil_mark_extraction.py <path_to_pdf_or_jpg>")
        print("Example: python test_pencil_mark_extraction.py 'd:\\Saathi\\Uploaded scans\\100001\\9057397169_210526\\Sales_Detail_Sheet.pdf'")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f"File not found: {input_path}")
        sys.exit(1)

    from app.services.pre_ocr_service import (
        extract_details_chassis_pencil_mark_jpeg,
        _detect_paper_bounds,
    )
    from PIL import Image
    import cv2
    import numpy as np

    # Load image
    if input_path.suffix.lower() == ".pdf":
        import fitz
        doc = fitz.open(str(input_path))
        page = doc[0]
        pix = page.get_pixmap(dpi=200)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        doc.close()
        print(f"Loaded PDF, rasterized at 200 DPI: {img.width} x {img.height}")
    else:
        img = Image.open(input_path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        print(f"Loaded image: {img.width} x {img.height}")

    # Convert to JPEG bytes
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    jpeg_bytes = buf.getvalue()

    # Check paper detection
    nparr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img_cv = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    paper = _detect_paper_bounds(img_cv)
    
    if paper:
        px, py, pw, ph = paper
        pct = (pw * ph) / (img.width * img.height) * 100
        print(f"Paper detected: x={px}, y={py}, w={pw}, h={ph} ({pct:.1f}% of image)")
    else:
        print("Paper NOT detected (treating full image as paper)")

    # Extract pencil mark
    result = extract_details_chassis_pencil_mark_jpeg(jpeg_bytes)

    if result:
        output_path = input_path.parent / "pencil_mark_test.jpeg"
        output_path.write_bytes(result)
        
        result_img = Image.open(io.BytesIO(result))
        print(f"Extracted pencil mark: {result_img.width} x {result_img.height}")
        print(f"Saved to: {output_path}")
    else:
        print("Extraction failed - returned None")


if __name__ == "__main__":
    main()
