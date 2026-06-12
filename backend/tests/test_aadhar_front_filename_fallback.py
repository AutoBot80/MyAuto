"""Raw OCR fallback must parse Aadhar_front.jpg sections (not only legacy Aadhar.jpg)."""

import json
from pathlib import Path

from app.services.sales_ocr_service import _apply_aadhar_textract_fallbacks_from_parts


def test_apply_aadhar_fallbacks_parses_aadhar_front_jpg(tmp_path: Path) -> None:
    subfolder = "7240275304_120626"
    sale_dir = tmp_path / subfolder
    sale_dir.mkdir()
    json_path = sale_dir / "OCR_To_be_Used.json"
    json_path.write_text(
        json.dumps({"vehicle": {}, "customer": {"name": "Devendra Singh"}, "insurance": {}}),
        encoding="utf-8",
    )

    parts = [
        (
            "for_OCR/Aadhar_front.jpg",
            "Devendra Singh\n315H AR / DOB 10/03/2001\n309 / Male\n",
        ),
        (
            "for_OCR/Aadhar_back.jpg",
            "Address S/O: Mormukat Singh, Barakhur,\nBharatpur, Rajasthan 321021\n",
        ),
    ]
    _apply_aadhar_textract_fallbacks_from_parts(tmp_path, subfolder, parts)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    customer = data["customer"]
    assert customer.get("date_of_birth") == "10/03/2001"
    assert customer.get("gender") == "Male"
    assert customer.get("care_of")
    assert customer.get("address")
    assert "321021" in (customer.get("pin_code") or customer.get("address") or "")
