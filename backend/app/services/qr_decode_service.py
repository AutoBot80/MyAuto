"""
Decode QR code from a scan image and parse payload (no signature verification).
Handles UIDAI-style payloads: raw XML or base64+zlib compressed XML.
"""

import base64
import zlib
import xml.etree.ElementTree as ET
from typing import Any

import cv2
import numpy as np


def _decode_image(image_bytes: bytes) -> np.ndarray | None:
    """Decode image bytes to OpenCV BGR array."""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


def _decode_qr_from_image(img: np.ndarray) -> list[str]:
    """Detect and decode QR code(s) in image. Tries single-QR path first (faster, less prone to hang)."""
    detector = cv2.QRCodeDetector()
    decoded: list[str] = []

    # Try single QR first — faster and avoids known hangs with detectAndDecodeMulti on some images
    text, _, _ = detector.detectAndDecode(img)
    if text and isinstance(text, str) and text.strip():
        decoded.append(text.strip())
        return decoded

    # Fallback: multi (only if single found nothing)
    try:
        ret, texts, _points, _ = detector.detectAndDecodeMulti(img)
        if ret and texts is not None:
            for t in texts:
                if t and isinstance(t, str) and t.strip():
                    decoded.append(t.strip())
    except Exception:
        pass
    return decoded


def _decompress_payload(raw: str) -> str:
    """If payload is base64-encoded zlib, decode and decompress. Else return as-is."""
    raw = raw.strip()
    if not raw:
        return raw
    try:
        decoded = base64.b64decode(raw, validate=True)
        return zlib.decompress(decoded).decode("utf-8", errors="replace")
    except Exception:
        pass
    try:
        decoded = base64.b64decode(raw + "==", validate=False)
        return zlib.decompress(decoded).decode("utf-8", errors="replace")
    except Exception:
        pass
    return raw


def _parse_uidai_xml(xml_str: str) -> dict[str, Any]:
    """Parse UIDAI-style XML (PrintLetterBarcodeData or Offline e-KYC) into a flat dict.
    Extracts both element text and attributes (PrintLetterBarcodeData often has uid, name, etc. as attributes)."""
    out: dict[str, Any] = {}
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return out

    def add_attrs(element: ET.Element, prefix: str = "") -> None:
        for attr_name, attr_val in element.attrib.items():
            key = attr_name.split("}")[-1] if "}" in attr_name else attr_name
            full_key = f"{prefix}{key}" if prefix else key
            if attr_val and str(attr_val).strip():
                out[full_key] = str(attr_val).strip()
        for child in element:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            child_prefix = f"{prefix}{tag}." if prefix else f"{tag}."
            add_attrs(child, prefix=child_prefix)

    # Root attributes first (PrintLetterBarcodeData has uid, name, gender, yob, co, house, street, loc, vtc, po, dist, subdist, state, pc, dob on the root)
    add_attrs(root)

    # Flatten element text (for nested elements)
    def add_text(parent: ET.Element, prefix: str = "") -> None:
        for child in parent:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            key = f"{prefix}{tag}" if prefix else tag
            if child.text and child.text.strip():
                out[key] = child.text.strip()
            add_text(child, prefix=f"{key}.")

    add_text(root)

    if root.text and root.text.strip():
        out["_root_text"] = root.text.strip()

    return out


def _parse_xml_like(text: str) -> dict[str, Any]:
    """If text looks like XML, parse and return dict; else return empty dict."""
    text = text.strip()
    if not text.startswith("<"):
        return {}
    return _parse_uidai_xml(text)


# Map display field name -> list of possible XML tag names (any case) or dotted paths
# PrintLetterBarcodeData / Offline e-KYC use various tag names; dotted = nested e.g. PrintLetterBarcodeData.Name
UIDAI_FIELD_MAP: dict[str, list[str]] = {
    "aadhar_id": ["uid", "aadhaarno", "aadhar", "printletterbarcodedata.uid"],
    "name": ["name", "poi.name", "printletterbarcodedata.name"],
    "gender": ["gndr", "gender", "poi.gndr", "printletterbarcodedata.gndr"],
    "year_of_birth": ["yob", "yearofbirth", "poi.yob", "printletterbarcodedata.yob"],
    "date_of_birth": ["dob", "dateofbirth", "poi.dob", "printletterbarcodedata.dob"],
    "care_of": ["careof", "co", "care_of", "poa.careof", "printletterbarcodedata.co"],
    "house": ["house", "poa.house", "printletterbarcodedata.house"],
    "street": ["street", "poa.street", "printletterbarcodedata.street", "street2"],
    "location": ["lmt", "loc", "locality", "poa.lmt", "location", "landmark", "printletterbarcodedata.loc"],
    "city": ["vtc", "lgc", "city", "poa.vtc", "poa.lgc", "town", "village", "printletterbarcodedata.vtc", "printletterbarcodedata.lgc"],
    "post_office": ["po", "postoffice", "poa.po", "post_office", "printletterbarcodedata.po"],
    "district": ["dist", "district", "poa.dist", "lgc", "printletterbarcodedata.dist", "printletterbarcodedata.lgc"],
    "sub_district": ["subdist", "subdistrict", "poa.subdist", "sub_district", "tehsil", "printletterbarcodedata.subdist"],
    "state": ["state", "st", "poa.state", "printletterbarcodedata.state"],
    "pin_code": ["pc", "pincode", "pin", "poa.pc", "pin_code", "printletterbarcodedata.pc"],
    "mobile": ["mobile", "mobileno", "phone", "tel", "ph", "mobile_number", "contact", "m"],
}


def _normalize_key(k: str) -> str:
    """Lowercase and strip for matching."""
    return k.lower().strip()


def _extract_uidai_fields(parsed: dict[str, Any]) -> dict[str, str]:
    """Map raw parsed XML keys to the 15 display fields. Returns only non-empty values."""
    out: dict[str, str] = {}
    # Build normalized key -> original value
    norm_to_val: dict[str, str] = {}
    for k, v in parsed.items():
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        n = _normalize_key(k)
        norm_to_val[n] = str(v).strip()
        # If key is dotted (e.g. PrintLetterBarcodeData.Name), also register leaf name for matching
        if "." in n:
            leaf = n.split(".")[-1]
            if leaf and leaf not in norm_to_val:
                norm_to_val[leaf] = str(v).strip()
    for display_key, candidates in UIDAI_FIELD_MAP.items():
        for cand in candidates:
            n = _normalize_key(cand)
            if n in norm_to_val:
                out[display_key] = norm_to_val[n]
                break
    # Year of birth: if we have DOB but not YOB, try to take year from DOB
    if "year_of_birth" not in out and "date_of_birth" in out:
        dob = out["date_of_birth"]
        if len(dob) >= 4 and dob[:4].isdigit():
            out["year_of_birth"] = dob[:4]
        elif "-" in dob:
            parts = dob.split("-")
            for p in parts:
                if len(p) == 4 and p.isdigit():
                    out["year_of_birth"] = p
                    break
    return out


def decode_qr_from_image_bytes(image_bytes: bytes) -> dict[str, Any]:
    """
    Decode all QR codes in the image and parse their payloads.
    Returns:
        {
          "decoded": [{"raw": str, "parsed": dict}, ...],
          "error": str | None
        }
    """
    result: dict[str, Any] = {"decoded": [], "error": None}
    if not image_bytes or len(image_bytes) == 0:
        result["error"] = "Empty image"
        return result

    img = _decode_image(image_bytes)
    if img is None:
        result["error"] = "Could not decode image (unsupported format or corrupt)"
        return result

    strings = _decode_qr_from_image(img)
    if not strings:
        result["error"] = "No QR code found in image"
        return result

    for raw in strings:
        entry: dict[str, Any] = {"raw": raw, "parsed": {}, "fields": {}}
        # Try decompress (base64+zlib) then parse
        expanded = _decompress_payload(raw)
        if expanded != raw:
            entry["expanded"] = expanded
        parsed = _parse_xml_like(expanded)
        if parsed:
            entry["parsed"] = parsed
            entry["fields"] = _extract_uidai_fields(parsed)
        else:
            # Not XML: treat as key-value lines if possible (e.g. "key:value")
            lines = [s.strip() for s in expanded.splitlines() if s.strip()]
            if lines:
                for line in lines:
                    if ":" in line:
                        k, _, v = line.partition(":")
                        entry["parsed"][k.strip()] = v.strip()
                    else:
                        entry["parsed"][f"_line_{len(entry['parsed'])}"] = line
                entry["fields"] = _extract_uidai_fields(entry["parsed"])
        result["decoded"].append(entry)

    return result
