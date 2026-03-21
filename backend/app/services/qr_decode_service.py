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


def _decode_qr_pyzbar_strings(img: np.ndarray) -> list[str]:
    """Optional zbar backend — often reads glossy / skewed UIDAI QR better than OpenCV alone."""
    try:
        from pyzbar.pyzbar import decode as pyzbar_decode
    except ImportError:
        return []
    out: list[str] = []
    try:
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img
        for sym in pyzbar_decode(gray):
            raw = sym.data
            if isinstance(raw, (bytes, bytearray)):
                try:
                    s = raw.decode("utf-8", errors="replace").strip()
                except Exception:
                    s = str(raw).strip()
            else:
                s = str(raw).strip()
            if s:
                out.append(s)
    except Exception:
        return []
    return out


def _decode_qr_from_image(img: np.ndarray) -> list[str]:
    """
    Decode all QR payloads in the image (single + multi), deduplicated.
    Important when multiple QRs exist: the UIDAI barcode may not be the first one OpenCV decodes.
    """
    detector = cv2.QRCodeDetector()
    decoded: list[str] = []
    seen: set[str] = set()

    def add_strings(strings: list[str]) -> None:
        for raw in strings:
            if not raw or not str(raw).strip():
                continue
            s = str(raw).strip()
            if s not in seen:
                seen.add(s)
                decoded.append(s)

    text, _, _ = detector.detectAndDecode(img)
    if text and isinstance(text, str) and text.strip():
        add_strings([text])

    try:
        ret, texts, _points, _ = detector.detectAndDecodeMulti(img)
        if ret and texts is not None:
            add_strings([t for t in texts if isinstance(t, str)])
    except Exception:
        pass

    try:
        _r, curved, _p = detector.detectAndDecodeCurved(img)
        if isinstance(curved, str) and curved.strip():
            add_strings([curved])
    except Exception:
        pass

    add_strings(_decode_qr_pyzbar_strings(img))
    return decoded


def _rotated_variants(img: np.ndarray) -> list[np.ndarray]:
    """Original + 90/180/270° — back-of-card phone photos are often rotated."""
    out = [img]
    try:
        for rot in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE):
            out.append(cv2.rotate(img, rot))
    except Exception:
        pass
    return out


def _qr_preprocess_variants(img: np.ndarray) -> list[np.ndarray]:
    """
    BGR images to try for OpenCV QR decode (noisy / low-contrast back scans).
    Keeps 3-channel BGR for QRCodeDetector.
    """
    variants: list[np.ndarray] = [img]
    try:
        if len(img.shape) == 2:
            gray = img
        else:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        variants.append(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
        h, w = gray.shape[:2]
        max_side = max(h, w)
        scale = min(2.0, 2000.0 / float(max_side)) if max_side > 0 else 2.0
        if scale > 1.01:
            nh, nw = int(h * scale), int(w * scale)
            up = cv2.resize(gray, (nw, nh), interpolation=cv2.INTER_CUBIC)
            variants.append(cv2.cvtColor(up, cv2.COLOR_GRAY2BGR))
        _, thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(cv2.cvtColor(thr, cv2.COLOR_GRAY2BGR))
        variants.append(cv2.cvtColor(cv2.bitwise_not(gray), cv2.COLOR_GRAY2BGR))
        variants.append(cv2.cvtColor(cv2.bitwise_not(thr), cv2.COLOR_GRAY2BGR))
        adp = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 5
        )
        variants.append(cv2.cvtColor(adp, cv2.COLOR_GRAY2BGR))
    except Exception:
        pass
    return variants


def _aadhar_dense_qr_roi_variants(img: np.ndarray) -> list[np.ndarray]:
    """
    UIDAI secure QR is very dense; on low-res full-card photos OpenCV often fails unless the
    right-hand QR region is cropped and upscaled strongly. Keep this list small — each variant
    runs a full detector pass.
    """
    out: list[np.ndarray] = []
    if img is None or len(img.shape) < 2:
        return out
    h, w = img.shape[:2]
    if h < 24 or w < 24:
        return out
    for x0_ratio in (0.42, 0.46, 0.50):
        x0 = min(int(w * x0_ratio), w - 32)
        crop = img[:, x0:]
        ch, cw = crop.shape[:2]
        if cw < 32 or ch < 24:
            continue
        for scale in (4, 5, 6):
            nw, nh = cw * scale, ch * scale
            if max(nw, nh) > 4200:
                continue
            try:
                out.append(cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_CUBIC))
            except Exception:
                pass
    return out


def _strings_include_uidai_payload(strings: list[str]) -> bool:
    """True if any decoded string expands to UIDAI XML with at least uid or name (not just a random URL QR)."""
    for raw in strings:
        expanded = _decompress_payload(raw)
        parsed = _parse_xml_like(expanded)
        if not parsed:
            continue
        fields = _extract_uidai_fields(parsed)
        if fields.get("aadhar_id") or (fields.get("name") and len(str(fields["name"]).strip()) > 2):
            return True
    return False


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
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff").strip()
    if not text.startswith("<"):
        return {}
    return _parse_uidai_xml(text)


# Map display field name -> list of possible XML tag names (any case) or dotted paths
# PrintLetterBarcodeData / Offline e-KYC use various tag names; dotted = nested e.g. PrintLetterBarcodeData.Name
UIDAI_FIELD_MAP: dict[str, list[str]] = {
    "aadhar_id": ["uid", "aadhaarno", "aadhar", "printletterbarcodedata.uid"],
    "name": ["name", "poi.name", "printletterbarcodedata.name"],
    # UIDAI XML often uses gnd (not gndr) on PrintLetterBarcodeData root
    "gender": [
        "gnd",
        "gndr",
        "gender",
        "gen",
        "sex",
        "poi.gnd",
        "poi.gndr",
        "poi.gender",
        "poi.sex",
        "printletterbarcodedata.gnd",
        "printletterbarcodedata.gndr",
        "printletterbarcodedata.gender",
    ],
    "year_of_birth": ["yob", "yearofbirth", "poi.yob", "printletterbarcodedata.yob"],
    "date_of_birth": [
        "dob",
        "dateofbirth",
        "date_of_birth",
        "birthdate",
        "birth_date",
        "dobon",
        "poi.dob",
        "poi.dateofbirth",
        "poi.date_of_birth",
        "printletterbarcodedata.dob",
        "printletterbarcodedata.dateofbirth",
    ],
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
        elif "/" in dob:
            # DD/MM/YYYY or MM/DD/YYYY — take 4-digit year token
            for p in dob.replace(".", "/").split("/"):
                p = p.strip()
                if len(p) == 4 and p.isdigit():
                    out["year_of_birth"] = p
                    break
    # Many cards only encode YOB in QR; copy to date_of_birth so downstream (submit_info / DMS) gets a value
    if "date_of_birth" not in out and "year_of_birth" in out:
        y = out["year_of_birth"].strip()
        if y.isdigit() and len(y) == 4:
            out["date_of_birth"] = y
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

    # Upscale very small photos — module size becomes readable for dense UIDAI QR.
    try:
        h0, w0 = img.shape[:2]
        min_side = min(h0, w0)
        if min_side > 0 and min_side < 960:
            scale = 960.0 / float(min_side)
            nw, nh = int(w0 * scale), int(h0 * scale)
            img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_CUBIC)
    except Exception:
        pass

    strings: list[str] = []
    seen_raw: set[str] = set()

    def add_from_variant(variant: np.ndarray) -> None:
        for s in _decode_qr_from_image(variant):
            if s not in seen_raw:
                seen_raw.add(s)
                strings.append(s)

    # Fast path: rotations only (phone photos of back often need 90°/270°).
    for variant in _rotated_variants(img):
        add_from_variant(variant)

    # Slow path: grayscale / threshold / upscale — glossy back scans, weak contrast.
    if not strings or not _strings_include_uidai_payload(strings):
        for variant in _rotated_variants(img):
            prepped = _qr_preprocess_variants(variant)
            for p in prepped[1:]:
                add_from_variant(p)

    # Last resort: crop right side (QR on Aadhaar back) + heavy upscale (few rotations).
    if not strings or not _strings_include_uidai_payload(strings):
        for variant in (img, cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)):
            for roi in _aadhar_dense_qr_roi_variants(variant):
                add_from_variant(roi)
                if strings and _strings_include_uidai_payload(strings):
                    break
            if strings and _strings_include_uidai_payload(strings):
                break

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
