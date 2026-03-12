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
    """Detect and decode all QR codes in image. Returns list of decoded strings."""
    detector = cv2.QRCodeDetector()
    decoded: list[str] = []

    # Try multi first to get all QRs
    ret, texts, _points, _ = detector.detectAndDecodeMulti(img)
    if ret and texts is not None:
        for t in texts:
            if t and isinstance(t, str) and t.strip():
                decoded.append(t.strip())
    if decoded:
        return decoded

    # Single QR
    text, _, _ = detector.detectAndDecode(img)
    if text and isinstance(text, str) and text.strip():
        decoded.append(text.strip())
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
    """Parse UIDAI-style XML (PrintLetterBarcodeData or Offline e-KYC) into a flat dict."""
    out: dict[str, Any] = {}
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return out

    # Flatten common tag names (strip namespace)
    def add_text(parent: ET.Element, prefix: str = "") -> None:
        for child in parent:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            key = f"{prefix}{tag}" if prefix else tag
            if child.text and child.text.strip():
                out[key] = child.text.strip()
            add_text(child, prefix=f"{key}.")

    add_text(root)

    # Also handle direct text at root level
    if root.text and root.text.strip():
        out["_root_text"] = root.text.strip()

    return out


def _parse_xml_like(text: str) -> dict[str, Any]:
    """If text looks like XML, parse and return dict; else return empty dict."""
    text = text.strip()
    if not text.startswith("<"):
        return {}
    return _parse_uidai_xml(text)


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
        entry: dict[str, Any] = {"raw": raw, "parsed": {}}
        # Try decompress (base64+zlib) then parse
        expanded = _decompress_payload(raw)
        if expanded != raw:
            entry["expanded"] = expanded
        parsed = _parse_xml_like(expanded)
        if parsed:
            entry["parsed"] = parsed
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
        result["decoded"].append(entry)

    return result
