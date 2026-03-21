"""Process AI reader queue with AWS Textract (forms mode). Details sheet only."""

import json
import re
from datetime import datetime
from pathlib import Path

from app.db import get_connection
from app.repositories.ai_reader_queue import AiReaderQueueRepository


def _aadhar_last4(aadhar_id: str | None) -> str | None:
    """Return only last 4 digits of Aadhar for compliance; never persist full 12 digits."""
    if not aadhar_id or not str(aadhar_id).strip():
        return None
    digits = "".join(c for c in str(aadhar_id) if c.isdigit())
    return digits[-4:] if len(digits) >= 4 else digits or None


def _safe_subfolder_name(subfolder: str) -> str:
    """Safe directory name under ocr_output (one segment, no path separators)."""
    return re.sub(r"[^\w\-]", "_", (subfolder or "").strip()) or "default"


def _safe_output_basename(subfolder: str, filename: str) -> str:
    """Build a safe filename for output: subfolder_filename.txt (no path chars)."""
    safe_sub = _safe_subfolder_name(subfolder)
    safe_name = Path(filename).stem
    safe_name = re.sub(r"[^\w\-.]", "_", safe_name)
    return f"{safe_sub}_{safe_name}.txt"


def _ocr_subfolder_path(output_dir: Path, subfolder: str) -> Path:
    """Path to per-customer subfolder under ocr_output: ocr_output/mobile_ddmmyyyy/."""
    return output_dir / _safe_subfolder_name(subfolder)


# OCR output JSON filename (in ocr_output subfolder only; input files stay as Details.jpg)
OCR_OUTPUT_JSON_STEM = "OCR_To_be_Used"


def _json_output_path(output_dir: Path, subfolder: str) -> Path:
    """Path for structured JSON output: ocr_output/mobile_ddmmyy/OCR_To_be_Used.json."""
    subfolder_name = _safe_subfolder_name(subfolder)
    return output_dir / subfolder_name / f"{OCR_OUTPUT_JSON_STEM}.json"


# Filename patterns for queue processing.
DETAILS_FILENAME_HINTS = (
    "details",
    "detail_sheet",
    "sales_detail",
    "sales detail",
    "a5 template",
)
AADHAR_FILENAME_CONTAINS = "aadhar"

# 15 Aadhar fields (display order and labels for ocr_output file).
AADHAR_15_FIELDS: list[tuple[str, str]] = [
    ("aadhar_id", "Aadhar ID"),
    ("name", "Name"),
    ("gender", "Gender"),
    ("year_of_birth", "Year of birth"),
    ("date_of_birth", "Date of birth"),
    ("care_of", "Care of"),
    ("house", "House"),
    ("street", "Street"),
    ("location", "Location"),
    ("city", "City"),
    ("post_office", "Post Office"),
    ("district", "District"),
    ("sub_district", "Sub District"),
    ("state", "State"),
    ("pin_code", "Pin Code"),
]

# Map Textract form keys (normalized) to our vehicle detail fields.
_VEHICLE_KEY_ALIASES = {
    # Many dealer PDFs use "Chassis Number" / "Engine Number" (not "Chassis No.")
    "frame_no": [
        "frame no",
        "frame no.",
        "frame number",
        "chassis",
        "chassis no",
        "chassis no.",
        "chassis number",
        "vin",
        "vehicle identification number",
    ],
    "engine_no": ["engine no", "engine no.", "engine number", "engine"],
    "model_colour": ["model & colour", "model and colour", "model/colour", "model", "colour", "color"],
    "key_no": ["key no", "key no.", "key number", "key"],
    "battery_no": ["battery no", "battery no.", "battery number", "battery"],
}

# Map Textract form keys to insurance/nominee fields (from Details sheet).
_INSURANCE_KEY_ALIASES = {
    "profession": [
        "profession",
        "profession:",
        "occupation",
        "customer profession",
        "employment",
        "nature of occupation",
        "job",
        "customer occupation",
    ],
    "financier": [
        "financier",
        "financier name",
        "finance company",
        "bank",
        "financer",
        "financing bank",
        "name of financier",
        "bank / financier",
        "financier / bank",
        "finance bank",
        "hypothecation",
        "loan from",
        "financing institution",
    ],
    "insurer": ["insurer", "insurer name", "insurance company", "insurance provider", "company"],
    "marital_status": ["marital status", "customer marital status", "married status"],
    "nominee_gender": ["nominee gender", "gender of nominee", "sex of nominee", "nominee sex"],
    "nominee_name": [
        "nominee name",
        "nominee name:",
        "nominee",
        "name of nominee",
        "name of the nominee",
        "nominee's name",
        "insurance nominee",
        "nominee details",
    ],
    "nominee_age": [
        "nominee age",
        "nominee age:",
        "age of nominee",
        "nominee age (years)",
        "age (years)",
    ],
    "nominee_relationship": [
        "nominee relationship",
        "nominee relationship:",
        "relationship with customer",
        "relation with proposer",
        "relation to insured",
        "nominee relation",
        "relationship",
        "relation",
    ],
}

# Map Textract form keys to customer name on Details sheet.
_DETAILS_CUSTOMER_NAME_ALIASES = [
    "customer name", "customer name:", "name", "buyer name", "buyer's name",
    "buyer name:", "name of customer", "customer",
    "full name", "full name:", "customer full name",
]

_DETAILS_CUSTOMER_KEY_ALIASES = {
    "name": ["full name", "customer name", "name of customer", "buyer name", "name"],
    "mobile_number": ["mobile number", "mobile no", "mobile", "contact number", "contact no"],
    "alt_phone_num": ["alternate", "alternate no", "alternate number", "alternate mobile", "alternate mobile number", "landline", "landline number"],
    "aadhar_id": ["aadhaar number", "aadhar number", "aadhaar no", "aadhar no", "aadhaar", "aadhar"],
    "profession": [
        "profession",
        "occupation",
        "customer profession",
        "employment",
        "nature of occupation",
        "customer occupation",
    ],
    "marital_status": ["marital status", "customer marital status"],
    "financier": [
        "financier name",
        "financier",
        "financer name",
        "financer",
        "finance company",
        "bank",
        "financing bank",
        "name of financier",
        "bank / financier",
    ],
    "nominee_name": ["nominee name", "name of nominee", "name of the nominee", "nominee's name"],
    "nominee_age": ["nominee age", "age of nominee", "age (years)"],
    "nominee_gender": ["nominee gender", "gender of nominee", "sex of nominee"],
    "nominee_relationship": [
        "nominee relationship",
        "nominee relation",
        "relationship with customer",
        "relation with proposer",
        "relationship",
        "relation",
    ],
}

# Map Textract form keys for insurance policy document (Insurance.jpg).
_INSURANCE_POLICY_KEY_ALIASES = {
    "insurer": ["insurer", "insurance company", "company", "insurance provider", "name of insurer", "national insurance"],
    "policy_num": ["policy no", "policy no.", "policy number", "policy num", "cert. no", "cert no", "certificate no"],
    "policy_from": ["tp valid from", "valid from", "validity from", "policy from", "from date", "tp valid"],
    "policy_to": ["tp valid to", "valid to", "validity to", "policy to", "to date", "midnight of"],
    "premium": ["gross premium", "premium", "total premium", "gross premium amount", "premium of rs"],
}


def _normalize_key_for_match(key: str) -> str:
    """Lowercase and collapse spaces for key matching."""
    return re.sub(r"\s+", " ", (key or "").lower().strip())


def _details_input_format(path: Path) -> str:
    """
    Detect real file type for the sales detail upload. V2 always saves as Details.jpg
    even when the user uploads .docx or PDF, so we must sniff magic bytes.
    """
    try:
        head = path.read_bytes()[:12]
    except OSError:
        return "unknown"
    if len(head) >= 2 and head[:2] == b"\xff\xd8":
        return "jpeg"
    if len(head) >= 4 and head[:4] == b"\x89PNG":
        return "png"
    if len(head) >= 4 and head[:4] == b"%PDF":
        return "pdf"
    if len(head) >= 2 and head[:2] == b"PK":
        return "docx"
    return "unknown"


def _key_value_pairs_from_docx(doc_path: Path) -> tuple[list[dict[str, str]], str]:
    """
    Parse Word Sales Detail Sheet (.docx) into Textract-like key/value pairs.
    AWS Textract does not accept .docx bytes; this path is used for native Word uploads.
    """
    try:
        from docx import Document
    except ImportError:
        return [], ""

    doc = Document(str(doc_path))
    pairs: list[dict[str, str]] = []
    text_lines: list[str] = []

    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if not t:
            continue
        text_lines.append(t)
        if ":" in t:
            left, right = t.split(":", 1)
            lk, rv = left.strip(), right.strip()
            if lk and rv and len(lk) <= 160 and len(rv) <= 600:
                pairs.append({"key": lk, "value": rv})
        elif "\t" in t:
            parts = re.split(r"\t+", t, maxsplit=1)
            if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                pairs.append({"key": parts[0].strip(), "value": parts[1].strip()})

    for table in doc.tables:
        for row in table.rows:
            cells = [(c.text or "").strip() for c in row.cells]
            if not any(cells):
                continue
            n = len(cells)
            if n >= 2:
                for i in range(0, n - 1, 2):
                    key, val = cells[i], cells[i + 1]
                    if key and val and key.casefold() != val.casefold():
                        pairs.append({"key": key, "value": val})

    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for kv in pairs:
        k = (kv.get("key") or "").strip()
        v = (kv.get("value") or "").strip()
        if not k or not v:
            continue
        sig = (k.casefold(), v.casefold())
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append({"key": k, "value": v})

    full_text = "\n".join(text_lines)
    for table in doc.tables:
        for row in table.rows:
            row_txt = " | ".join((c.text or "").strip() for c in row.cells)
            if row_txt.strip():
                full_text += "\n" + row_txt
    return deduped, full_text


def _sanitize_nominee_age(val: str | None) -> str | None:
    """Extract numeric part from nominee age (e.g. '30/m' -> '30'). Returns None if no digits."""
    if not val or not str(val).strip():
        return None
    s = str(val).strip()
    m = re.match(r"^(\d{1,3})", s)
    return m.group(1) if m and 1 <= int(m.group(1)) <= 150 else None


def _normalize_name_for_match(name: str | None) -> str:
    """Normalize name for matching: lowercase, strip, collapse spaces."""
    if not name or not str(name).strip():
        return ""
    return " ".join(str(name).lower().strip().split())


def _names_match(name1: str | None, name2: str | None) -> bool:
    """Return True if names likely refer to same person. Handles OCR variations (spacing, case)."""
    n1 = _normalize_name_for_match(name1)
    n2 = _normalize_name_for_match(name2)
    if not n1 or not n2:
        return True  # Can't compare if one missing; allow
    if n1 == n2:
        return True
    # One name contains the other (e.g. "john doe" in "john doe kumar")
    if n1 in n2 or n2 in n1:
        return True
    # Stricter: no first-word-only fallback; require substantial match
    return False


def _validate_name_match(aadhar_name: str | None, details_name: str | None, insurance_name: str | None) -> str | None:
    """Return error message if names from Aadhar, Details and Insurance do not match. None if OK."""
    names = [
        ("Aadhar", aadhar_name),
        ("Details sheet", details_name),
        ("Insurance", insurance_name),
    ]
    present = [(label, n) for label, n in names if n and str(n).strip()]
    if len(present) < 2:
        return None  # Need at least 2 to compare
    first_label, first_val = present[0]
    for label, val in present[1:]:
        if not _names_match(first_val, val):
            return (
                f"Name mismatch: '{first_label}' has a different name than '{label}'. "
                "Ensure the name on Aadhar front, Details sheet and Insurance document match."
            )
    return None


def _map_key_value_pairs_to_vehicle(pairs: list[dict]) -> dict[str, str]:
    """Map key_value_pairs from Textract to structured vehicle fields (frame_no, engine_no, model_colour, key_no, battery_no)."""
    out: dict[str, str] = {}
    key_norm_to_value: dict[str, str] = {}
    for kv in pairs:
        k = (kv.get("key") or "").strip()
        v = (kv.get("value") or "").strip()
        if not k:
            continue
        key_norm = _normalize_key_for_match(k)
        key_norm_to_value[key_norm] = v
        if ":" in key_norm:
            key_norm_to_value[key_norm.replace(":", "").strip()] = v

    for field, aliases in _VEHICLE_KEY_ALIASES.items():
        if field in out:
            continue
        for alias in aliases:
            anorm = _normalize_key_for_match(alias)
            if anorm in key_norm_to_value:
                out[field] = key_norm_to_value[anorm]
                break
            for kn, v in key_norm_to_value.items():
                if anorm in kn or kn in anorm:
                    out[field] = v
                    break
            if field in out:
                break

    # Combine Model and Colour into model_colour if we have them separately
    model_val = key_norm_to_value.get(_normalize_key_for_match("model"), "").strip() or next(
        (
            v.strip()
            for kn, v in key_norm_to_value.items()
            if "model" in kn and "colour" not in kn and "color" not in kn and v.strip()
        ),
        "",
    )
    colour_val = key_norm_to_value.get(_normalize_key_for_match("colour"), "").strip() or key_norm_to_value.get(
        _normalize_key_for_match("color"), ""
    ).strip()
    if model_val or colour_val:
        combined = ", ".join(filter(None, [model_val, colour_val]))
        if combined:
            out["model_colour"] = combined

    return out


def _clean_sales_sheet_scalar(value: str) -> str:
    """Strip placeholder underscores / blanks from a field value."""
    s = (value or "").strip()
    if not s:
        return ""
    if re.match(r"^[\s_.,-]+$", s):
        return ""
    return s.strip()


def _parse_vehicle_from_full_text(full_text: str) -> dict[str, str]:
    """
    Fallback when Textract FORMS misses pairs (common on some PDFs): parse LINE layout
    like 'Chassis Number: 59324 Engine Number: 50581'.
    """
    out: dict[str, str] = {}
    if not full_text or not isinstance(full_text, str):
        return out
    text = full_text.replace("\r", "\n")

    m = re.search(r"(?i)Model\s*:\s*([^\n]+?)(?=\s+Colour\s*:)", text)
    if m:
        mv = _clean_sales_sheet_scalar(m.group(1))
        if mv:
            out["model_colour"] = mv
    m = re.search(r"(?i)Colour\s*:\s*([^\n]+)", text)
    if m:
        cv = _clean_sales_sheet_scalar(m.group(1))
        if cv:
            if out.get("model_colour"):
                out["model_colour"] = f"{out['model_colour']}, {cv}"
            else:
                out["model_colour"] = cv

    m = re.search(r"(?i)Chassis\s+Number\s*:\s*([^\n]+?)(?=\s+Engine\s+Number\s*:|\n|$)", text)
    if m:
        fv = _clean_sales_sheet_scalar(m.group(1))
        if fv:
            out["frame_no"] = fv
    m = re.search(r"(?i)Engine\s+Number\s*:\s*([^\n]+?)(?=\s+Key\s+Number\s*:|\n|$)", text)
    if m:
        ev = _clean_sales_sheet_scalar(m.group(1))
        if ev:
            out["engine_no"] = ev
    m = re.search(r"(?i)Key\s+Number\s*:\s*([^\n]+?)(?=\s+Battery\s+Number\s*:|\n|$)", text)
    if m:
        kv = _clean_sales_sheet_scalar(m.group(1))
        if kv:
            out["key_no"] = kv
    m = re.search(r"(?i)Battery\s+Number\s*:\s*([^\n]+)", text)
    if m:
        bv = _clean_sales_sheet_scalar(m.group(1))
        if bv:
            out["battery_no"] = bv

    # Alternate labels
    if "frame_no" not in out:
        m = re.search(r"(?i)(?:Frame|Chassis)\s+No\.?\s*:\s*([^\n]+?)(?=\s+Engine|\n|$)", text)
        if m:
            fv = _clean_sales_sheet_scalar(m.group(1))
            if fv:
                out["frame_no"] = fv
    return out


def _extract_details_customer_name(pairs: list[dict]) -> str | None:
    """Extract customer name from Details sheet key-value pairs."""
    key_lower_to_value: dict[str, str] = {}
    for kv in pairs:
        k = (kv.get("key") or "").strip()
        v = (kv.get("value") or "").strip()
        if not k or not v:
            continue
        key_norm = _normalize_key_for_match(k)
        key_lower_to_value[key_norm] = v
        if ":" in key_norm:
            key_lower_to_value[key_norm.replace(":", "").strip()] = v
    for alias in _DETAILS_CUSTOMER_NAME_ALIASES:
        anorm = _normalize_key_for_match(alias)
        if anorm in key_lower_to_value:
            val = key_lower_to_value[anorm].strip()
            if len(val) >= 2 and len(val) <= 80:
                return val
        for k, v in key_lower_to_value.items():
            if anorm in k or k in anorm:
                val = v.strip()
                if len(val) >= 2 and len(val) <= 80:
                    return val
    return None


def _map_key_value_pairs_to_insurance(pairs: list[dict]) -> dict[str, str]:
    """Map key_value_pairs from Textract to insurance/nominee fields."""
    out: dict[str, str] = {}
    key_lower_to_value: dict[str, str] = {}
    for kv in pairs:
        k = (kv.get("key") or "").strip()
        v = (kv.get("value") or "").strip()
        if not k:
            continue
        key_norm = _normalize_key_for_match(k)
        key_lower_to_value[key_norm] = v
        # Also store with colon stripped for "Nominee Name:" style
        if ":" in key_norm:
            key_lower_to_value[key_norm.replace(":", "").strip()] = v

    for field, aliases in _INSURANCE_KEY_ALIASES.items():
        if field in out:
            continue
        for alias in aliases:
            anorm = _normalize_key_for_match(alias)
            if anorm in key_lower_to_value:
                out[field] = key_lower_to_value[anorm]
                break
            # Match if key contains alias (e.g. "nominee name" in "Nominee Name")
            for k, v in key_lower_to_value.items():
                if anorm in k or k in anorm:
                    out[field] = v
                    break
            if field in out:
                break
    return out


def _map_key_value_pairs_to_details_customer(pairs: list[dict]) -> dict[str, str]:
    """Map Details sheet key-value pairs to customer-side fields from new A5 template."""
    out: dict[str, str] = {}
    key_lower_to_value: dict[str, str] = {}
    for kv in pairs:
        k = (kv.get("key") or "").strip()
        v = (kv.get("value") or "").strip()
        if not k:
            continue
        key_norm = _normalize_key_for_match(k)
        key_lower_to_value[key_norm] = v
        if ":" in key_norm:
            key_lower_to_value[key_norm.replace(":", "").strip()] = v

    for field, aliases in _DETAILS_CUSTOMER_KEY_ALIASES.items():
        if field in out:
            continue
        for alias in aliases:
            anorm = _normalize_key_for_match(alias)
            if anorm in key_lower_to_value and key_lower_to_value[anorm].strip():
                out[field] = key_lower_to_value[anorm].strip()
                break
            for k, v in key_lower_to_value.items():
                if (anorm in k or k in anorm) and v.strip():
                    out[field] = v.strip()
                    break
            if field in out:
                break

    if out.get("mobile_number"):
        digits = "".join(ch for ch in out["mobile_number"] if ch.isdigit())
        out["mobile_number"] = digits[-10:] if digits else ""
    if out.get("alt_phone_num"):
        digits = "".join(ch for ch in out["alt_phone_num"] if ch.isdigit())
        out["alt_phone_num"] = digits[-10:] if digits else ""
    if out.get("aadhar_id"):
        out["aadhar_id"] = _aadhar_last4(out.get("aadhar_id")) or ""
    if out.get("nominee_age"):
        out["nominee_age"] = _sanitize_nominee_age(out["nominee_age"]) or ""
    return out


def _parse_insurance_from_full_text(full_text: str) -> dict[str, str]:
    """Try to extract insurance/customer extras from full text (fallback when not in key-value pairs)."""
    out: dict[str, str] = {}
    if not full_text or not isinstance(full_text, str):
        return out
    text = full_text.strip()
    # Patterns: "Label" or "Label:" followed by value on same or next line
    patterns = [
        ("customer profession", "profession"),
        ("profession of customer", "profession"),
        ("profession", "profession"),
        ("occupation", "profession"),
        ("nature of occupation", "profession"),
        ("name of financier", "financier"),
        ("financing bank", "financier"),
        ("finance company", "financier"),
        ("financier / bank", "financier"),
        ("bank / financier", "financier"),
        ("financier", "financier"),
        ("marital status", "marital_status"),
        ("nominee gender", "nominee_gender"),
        ("sex of nominee", "nominee_gender"),
        ("name of the nominee", "nominee_name"),
        ("name of nominee", "nominee_name"),
        ("nominee name", "nominee_name"),
        ("nominee's name", "nominee_name"),
        ("nominee age", "nominee_age"),
        ("age of nominee", "nominee_age"),
        ("nominee relationship", "nominee_relationship"),
        ("relationship with customer", "nominee_relationship"),
        ("relation with proposer", "nominee_relationship"),
        ("customer name", "customer_name"),
        ("buyer name", "customer_name"),
        ("buyer's name", "customer_name"),
    ]
    for label, key in patterns:
        if key in out:
            continue
        pat = re.compile(
            rf"(?im){re.escape(label)}\s*[:\t]?\s*\n?\s*([^\n]+)",
        )
        m = pat.search(text)
        if m:
            val = m.group(1).strip()
            if val and len(val) < 200:
                out[key] = val

    # Word / table layouts: label alone on one line, value on the next (e.g. "Profession" then "Farmer")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        low = _normalize_key_for_match(ln)
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if not nxt or len(nxt) > 180:
            continue
        if "profession" not in out and low in ("profession", "occupation", "customer profession", "employment"):
            out["profession"] = nxt
        if "financier" not in out and low in (
            "financier",
            "financing bank",
            "name of financier",
            "finance company",
            "bank name",
        ):
            out["financier"] = nxt
        if "nominee_name" not in out and ("nominee" in low and "name" in low) and "age" not in low:
            out["nominee_name"] = nxt
        if "nominee_age" not in out and low in ("nominee age", "age of nominee", "nominee age (years)"):
            out["nominee_age"] = _sanitize_nominee_age(nxt) or nxt
        if "nominee_gender" not in out and low in ("nominee gender", "gender of nominee", "sex of nominee"):
            out["nominee_gender"] = nxt
        if "nominee_relationship" not in out and low in (
            "nominee relationship",
            "relationship with customer",
            "relation with proposer",
            "relation to insured",
        ):
            out["nominee_relationship"] = nxt
        same = re.match(r"(?i)^(profession|occupation|customer profession)\s+(.{1,120})$", ln)
        if same and "profession" not in out:
            out["profession"] = same.group(2).strip()
        same_f = re.match(
            r"(?i)^(financier|financing bank|name of financier)\s+(.{1,160})$",
            ln,
        )
        if same_f and "financier" not in out:
            out["financier"] = same_f.group(2).strip()

    return out


def _map_key_value_pairs_to_insurance_policy(pairs: list[dict]) -> dict[str, str]:
    """Map key_value_pairs from Textract to insurance policy fields (insurer, policy_from, policy_to, premium)."""
    out: dict[str, str] = {}
    key_lower_to_value: dict[str, str] = {}
    for kv in pairs:
        k = (kv.get("key") or "").strip()
        v = (kv.get("value") or "").strip()
        if not k:
            continue
        key_norm = _normalize_key_for_match(k)
        key_lower_to_value[key_norm] = v
        if ":" in key_norm:
            key_lower_to_value[key_norm.replace(":", "").strip()] = v

    for field, aliases in _INSURANCE_POLICY_KEY_ALIASES.items():
        if field in out:
            continue
        for alias in aliases:
            anorm = _normalize_key_for_match(alias)
            if anorm in key_lower_to_value:
                out[field] = key_lower_to_value[anorm]
                break
            for k, v in key_lower_to_value.items():
                if anorm in k or k in anorm:
                    out[field] = v
                    break
            if field in out:
                break
    return out


def _parse_insurance_policy_from_full_text(full_text: str) -> dict[str, str]:
    """Extract policy number, insurer, policy from/to, gross premium from insurance policy full text.
    Document layout:
      - Policy / Cert. No. -> policy number (e.g. 39010231216200202663)
      - Right after -> insurance provider (e.g. NATIONAL INSURANCE COMPANY LIMITED)
      - Policy Period -> two dates: first=from, second=to (e.g. 15-06-2022, 15-06-2023)
      - Gross Premium -> premium amount (e.g. 5291.00)
    """
    out: dict[str, str] = {}
    if not full_text or not isinstance(full_text, str):
        return out
    text = full_text.strip()

    # 1. Policy number: "Policy\nCert. No.\n39010231216200202663" - number after Cert. No.
    if "policy_num" not in out:
        m = re.search(
            r"(?:Policy\s+)?Cert\.?\s*No\.?\s*:?\s*\n\s*(\d{12,})",
            text,
            re.IGNORECASE,
        )
        if m:
            out["policy_num"] = m.group(1).strip()

    # 2. Insurer: right after policy number - "NATIONAL INSURANCE COMPANY LIMITED"
    if "insurer" not in out:
        m = re.search(
            r"(\d{12,})\s*\n\s*([A-Z][A-Za-z\s]+(?:INSURANCE\s+COMPANY\s+LIMITED|INSURANCE\s+COMPANY|INSURANCE))",
            text,
        )
        if m:
            out["insurer"] = m.group(2).strip()
        else:
            # Fallback: known insurer names
            m = re.search(
                r"([A-Z][A-Za-z\s]+INSURANCE[A-Za-z\s]*(?:COMPANY\s+)?(?:LIMITED|LTD\.?)?)",
                text,
            )
            if m:
                out["insurer"] = m.group(1).strip()

    # 3. Policy from & to:
    #    - policy_from: Date in "dd-mm-yyyy To" block before "OD Policy Period" (e.g. 16-06-2021 near UIN block)
    #    - policy_to: First dd-mm-yyyy after "OD Policy Period" (e.g. 15-06-2022)
    if "policy_from" not in out or "policy_to" not in out:
        date_pat = re.compile(r"\b(\d{1,2}-\d{1,2}-\d{4})\b")
        od_match = re.search(r"OD\s+Policy\s+Period\s*:?\s*\n", text, re.IGNORECASE)
        if od_match:
            before_od = text[: od_match.start()]
            after_od = text[od_match.end() :]
            if "policy_from" not in out:
                # Prefer date in "dd-mm-yyyy To" format (policy period start); else first date before OD
                m_from = re.search(r"(\d{1,2}-\d{1,2}-\d{4})\s+To", before_od)
                if m_from:
                    out["policy_from"] = m_from.group(1)
                else:
                    dates_before = date_pat.findall(before_od)
                    if dates_before:
                        out["policy_from"] = dates_before[0]
            if "policy_to" not in out:
                dates_after = date_pat.findall(after_od)
                if dates_after:
                    out["policy_to"] = dates_after[0]  # First date after OD = policy end (e.g. 15-06-2022)
        if "policy_from" not in out or "policy_to" not in out:
            # Fallback: Policy Period with two dates
            for label in [r"Policy\s+Period", r"OD\s+Policy\s+Period"]:
                m = re.search(
                    rf"{label}\s*:?\s*\n\s*(\d{{1,2}}-\d{{1,2}}-\d{{4}})\s*\n\s*(\d{{1,2}}-\d{{1,2}}-\d{{4}})",
                    text,
                    re.IGNORECASE,
                )
                if m:
                    if "policy_from" not in out:
                        out["policy_from"] = m.group(1)
                    if "policy_to" not in out:
                        out["policy_to"] = m.group(2)
                    break

    # 4. Gross Premium: "Gross Premium\n5291.00"
    if "premium" not in out:
        m = re.search(r"Gross\s+Premium\s*:?\s*\n\s*([\d,]+\.?\d*)", text, re.IGNORECASE)
        if m:
            out["premium"] = m.group(1).strip().replace(",", "")
        else:
            m = re.search(r"Premium\s+of\s+Rs\.?\s*:?\s*\n\s*([\d,]+\.?\d*)", text, re.IGNORECASE)
            if m:
                out["premium"] = m.group(1).strip().replace(",", "")

    # 5. Policy holder / insured name (multiple patterns for different document formats)
    if "policy_holder_name" not in out:
        for label in [
            r"Name\s+of\s+(?:the\s+)?(?:insured|proposer|policy\s*holder)",
            r"Policy\s+holder\s*:?",
            r"Registered\s+Owner\s*:?",
            r"Name\s+of\s+insured\s*:?",
            r"Proposer['\u2019]?s?\s+name\s*:?",
            r"Name\s+of\s+proposer\s*:?",
            r"Insured\s+name\s*:?",
            r"(?:Name|Proposer)\s*:?\s*\n",  # "Name:" or "Proposer:" on own line
        ]:
            m = re.search(rf"{label}\s*\n?\s*([A-Za-z][A-Za-z\s\.]{1,79})", text, re.IGNORECASE)
            if m:
                val = m.group(1).strip()
                # Exclude values that look like insurer names or numbers
                if len(val) >= 2 and len(val) <= 80 and not re.match(r"^\d+$", val):
                    if "insurance" not in val.lower() and "company" not in val.lower() and "ltd" not in val.lower():
                        out["policy_holder_name"] = val
                        break

    return out


class OcrService:
    """Process AI reader queue with Textract (forms mode); write results to flat files. Details sheet only."""

    def __init__(
        self,
        uploads_dir: Path | None = None,
        ocr_output_dir: Path | None = None,
    ):
        from app.config import OCR_OUTPUT_DIR, UPLOADS_DIR

        self.uploads_dir = Path(uploads_dir or UPLOADS_DIR).resolve()
        self.ocr_output_dir = Path(ocr_output_dir or OCR_OUTPUT_DIR).resolve()

    def _ensure_ocr_output_dir(self) -> None:
        self.ocr_output_dir.mkdir(parents=True, exist_ok=True)

    def get_output_path(self, subfolder: str, filename: str) -> Path:
        """Path where extracted text is written: ocr_output/mobile_ddmmyy/filename_stem.txt."""
        self._ensure_ocr_output_dir()
        stem = Path(filename).stem
        safe_stem = re.sub(r"[^\w\-.]", "_", stem)
        # Always use a subfolder (mobile_ddmmyy); never write at ocr_output root
        subfolder_name = _safe_subfolder_name(subfolder)
        subfolder_path = self.ocr_output_dir / subfolder_name
        subfolder_path.mkdir(parents=True, exist_ok=True)
        return subfolder_path / f"{safe_stem}.txt"

    def read_extraction_file(self, subfolder: str, filename: str) -> str | None:
        """Read extracted text from the flat file if it exists."""
        path = self.get_output_path(subfolder, filename)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8", errors="replace")

    def process_next(self) -> dict | None:
        """
        Process the oldest queued item: Details sheet (Textract) or Aadhar (Vision).
        Picks any queued item; branches on filename (details vs aadhar). Returns result dict or None if none queued.
        """
        with get_connection() as conn:
            AiReaderQueueRepository.ensure_table(conn)
            row = AiReaderQueueRepository.get_oldest_queued(conn)  # any queued item
            if not row:
                return None

            qid = row["id"]
            subfolder = row["subfolder"]
            filename = row["filename"]

            AiReaderQueueRepository.update_status(conn, qid, "processing")
            conn.commit()

        input_path = self.uploads_dir / subfolder / filename
        if not input_path.exists():
            with get_connection() as conn:
                AiReaderQueueRepository.update_status(conn, qid, "failed")
                conn.commit()
            return {
                "id": qid,
                "subfolder": subfolder,
                "filename": filename,
                "status": "failed",
                "error": "File not found",
                "extracted_text": None,
                "output_path": None,
                "document_type": None,
                "classification_confidence": None,
            }

        fn_lower = filename.lower()
        if any(hint in fn_lower for hint in DETAILS_FILENAME_HINTS):
            return self._process_details_sheet(qid, subfolder, filename, input_path)
        if AADHAR_FILENAME_CONTAINS in fn_lower:
            return self._process_aadhar(qid, subfolder, filename, input_path)
        # Unknown type: mark done so queue advances
        with get_connection() as conn:
            AiReaderQueueRepository.update_classification(conn, qid, "Other", 0.0)
            AiReaderQueueRepository.update_status(conn, qid, "done")
            conn.commit()
        return {
            "id": qid,
            "subfolder": subfolder,
            "filename": filename,
            "status": "done",
            "error": None,
            "extracted_text": None,
            "output_path": None,
            "document_type": "Other",
            "classification_confidence": 0.0,
        }

    def process_uploaded_subfolder(self, subfolder: str) -> dict:
        """
        Run extraction directly on uploaded files (no queue).
        Order: Aadhar.jpg (customer/QR), Details.jpg (vehicle + insurance; format sniffed),
        Insurance.jpg, then raw text for Aadhar_back/Financing.
        Collects raw OCR text from all processed files and writes Raw_OCR.txt.
        Returns summary of what was processed.
        """
        subdir = self.uploads_dir / subfolder
        if not subdir.exists() or not subdir.is_dir():
            return {"error": f"Subfolder not found: {subfolder}", "processed": []}

        processed: list[str] = []
        errors: list[str] = []
        self._raw_ocr_parts: list[tuple[str, str]] = []

        # 1. Aadhar.jpg first (QR is fast; populates customer in JSON immediately)
        aadhar_path = subdir / "Aadhar.jpg"
        if aadhar_path.exists():
            try:
                self._process_aadhar(None, subfolder, "Aadhar.jpg", aadhar_path)
                processed.append("Aadhar.jpg")
            except Exception as e:
                errors.append(f"Aadhar.jpg: {e}")

        # 2. Details.jpg (may be JPEG/PNG/PDF or .docx bytes under .jpg name — see _details_input_format)
        details_path = subdir / "Details.jpg"
        if details_path.exists():
            try:
                self._process_details_sheet(None, subfolder, "Details.jpg", details_path)
                processed.append("Details.jpg")
            except Exception as e:
                errors.append(f"Details.jpg: {e}")

        # 3. Insurance.jpg (extract insurer, TP valid from/to, gross premium; merge into ocr_output/.../OCR_To_be_Used.json)
        insurance_path = subdir / "Insurance.jpg"
        if insurance_path.exists():
            try:
                self._process_insurance_sheet(subfolder, "Insurance.jpg", insurance_path)
                processed.append("Insurance.jpg")
            except Exception as e:
                errors.append(f"Insurance.jpg: {e}")

        # 4. Aadhar_back.jpg and Financing.jpg: add raw text for Raw_OCR (no structured extraction)
        for extra_file in ["Aadhar_back.jpg", "Financing.jpg"]:
            extra_path = subdir / extra_file
            if extra_path.exists():
                try:
                    from app.services.textract_service import extract_text_from_bytes

                    result = extract_text_from_bytes(extra_path.read_bytes())
                    if not result.get("error") and result.get("full_text"):
                        self._raw_ocr_parts.append((extra_file, result["full_text"]))
                except Exception:
                    pass

        # 5. Write Raw_OCR.txt with raw text from all processed files
        if self._raw_ocr_parts:
            self._ensure_ocr_output_dir()
            subfolder_name = _safe_subfolder_name(subfolder)
            subfolder_path = self.ocr_output_dir / subfolder_name
            subfolder_path.mkdir(parents=True, exist_ok=True)
            raw_lines = []
            for filename, text in self._raw_ocr_parts:
                raw_lines.append(f"--- {filename} ---")
                raw_lines.append(text.strip() if text else "")
                raw_lines.append("")
            (subfolder_path / "Raw_OCR.txt").write_text("\n".join(raw_lines), encoding="utf-8")

        self._raw_ocr_parts = None

        result: dict = {"processed": processed}
        if errors:
            result["errors"] = errors
        return result

    def _process_details_sheet(self, qid: int | None, subfolder: str, filename: str, input_path: Path) -> dict:
        """Run Textract (forms) on Details sheet; write text + JSON; optionally update queue."""
        if qid is not None:
            with get_connection() as conn:
                AiReaderQueueRepository.update_classification(conn, qid, "Details sheet", 1.0)
                conn.commit()
        try:
            fmt = _details_input_format(input_path)
            if fmt == "docx":
                key_value_pairs, docx_full = _key_value_pairs_from_docx(input_path)
                if not key_value_pairs and not (docx_full or "").strip():
                    raise RuntimeError(
                        "Could not read the Word document (.docx). Ensure it is a valid .docx Sales Detail Sheet."
                    )
                result = {
                    "error": None,
                    "full_text": docx_full,
                    "key_value_pairs": key_value_pairs,
                }
            elif fmt in ("jpeg", "png", "pdf"):
                from app.services.textract_service import extract_forms_from_bytes

                document_bytes = input_path.read_bytes()
                result = extract_forms_from_bytes(document_bytes)
                if result.get("error"):
                    raise RuntimeError(result["error"])
            else:
                raise RuntimeError(
                    f"Unsupported Details file format (detected={fmt!r}). "
                    "Use a JPEG/PNG scan, PDF export, or .docx Sales Detail Sheet."
                )

            key_value_pairs = result.get("key_value_pairs") or []
            lines = []
            lines.append("Document: Details sheet (Textract Forms)\n")
            for kv in key_value_pairs:
                lines.append(f"{kv.get('key', '')}: {kv.get('value', '')}")
            if result.get("full_text"):
                lines.append("\n--- Full text ---\n")
                lines.append(result["full_text"])
            text = "\n".join(lines)

            if hasattr(self, "_raw_ocr_parts") and self._raw_ocr_parts is not None:
                self._raw_ocr_parts.append((filename, result.get("full_text") or ""))

            self._ensure_ocr_output_dir()
            output_path = self.get_output_path(subfolder, filename)  # used for return value only; no separate .txt file

            vehicle = _map_key_value_pairs_to_vehicle(key_value_pairs)
            insurance = _map_key_value_pairs_to_insurance(key_value_pairs)
            details_customer = _map_key_value_pairs_to_details_customer(key_value_pairs)
            details_customer_name = _extract_details_customer_name(key_value_pairs)
            if result.get("full_text"):
                from_vehicle = _parse_vehicle_from_full_text(result["full_text"])
                for k, v in from_vehicle.items():
                    if v and not vehicle.get(k):
                        vehicle[k] = v
                from_full = _parse_insurance_from_full_text(result["full_text"])
                if from_full.get("customer_name") and not details_customer_name:
                    details_customer_name = from_full["customer_name"]
                for k, v in from_full.items():
                    if k == "customer_name":
                        continue  # Used for details_customer_name, not insurance
                    if v and not insurance.get(k):
                        insurance[k] = v
            json_path = _json_output_path(self.ocr_output_dir, subfolder)
            json_path.parent.mkdir(parents=True, exist_ok=True)
            customer = {}
            existing_insurance: dict = {}
            if json_path.exists():
                try:
                    existing = json.loads(json_path.read_text(encoding="utf-8"))
                    customer = existing.get("customer") or {}
                    if not isinstance(customer, dict):
                        customer = {}
                    # Compliance: never persist full Aadhar
                    if customer.get("aadhar_id"):
                        customer["aadhar_id"] = _aadhar_last4(customer["aadhar_id"]) or ""
                    existing_insurance = existing.get("insurance") or {}
                    if not isinstance(existing_insurance, dict):
                        existing_insurance = {}
                except Exception:
                    pass
            if details_customer.get("name") and not details_customer_name:
                details_customer_name = details_customer.get("name")

            customer_merged = dict(customer)
            for key, val in details_customer.items():
                if not val:
                    continue
                if key in (
                    "profession",
                    "marital_status",
                    "financier",
                    "nominee_name",
                    "nominee_age",
                    "nominee_gender",
                    "nominee_relationship",
                ):
                    continue
                if not customer_merged.get(key):
                    customer_merged[key] = val

            insurance_merged = {
                **existing_insurance,
                **{k: v for k, v in insurance.items() if v},
                **{
                    k: v
                    for k, v in details_customer.items()
                    if k
                    in (
                        "profession",
                        "marital_status",
                        "financier",
                        "nominee_name",
                        "nominee_age",
                        "nominee_gender",
                        "nominee_relationship",
                    )
                    and v
                },
            }
            details_json = {"vehicle": vehicle, "customer": customer_merged, "insurance": insurance_merged}
            if details_customer_name:
                details_json["details_customer_name"] = details_customer_name
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(details_json, indent=2), encoding="utf-8")

            if qid is not None:
                with get_connection() as conn:
                    AiReaderQueueRepository.update_status(conn, qid, "done")
                    conn.commit()

            return {
                "id": qid,
                "subfolder": subfolder,
                "filename": filename,
                "status": "done",
                "error": None,
                "extracted_text": text,
                "output_path": str(output_path),
                "document_type": "Details sheet",
                "classification_confidence": 1.0,
            }
        except Exception as e:
            if qid is not None:
                with get_connection() as conn:
                    AiReaderQueueRepository.update_status(conn, qid, "failed")
                    conn.commit()
                return {
                    "id": qid,
                    "subfolder": subfolder,
                    "filename": filename,
                    "status": "failed",
                    "error": str(e),
                    "extracted_text": None,
                    "output_path": None,
                    "document_type": None,
                    "classification_confidence": None,
                }
            raise

    def _process_aadhar(self, qid: int | None, subfolder: str, filename: str, input_path: Path) -> dict:
        """Run Aadhar extraction: try QR decode first, then fall back to OpenAI Vision; merge customer into subfolder JSON."""
        if qid is not None:
            with get_connection() as conn:
                AiReaderQueueRepository.update_classification(conn, qid, "Aadhar", 1.0)
                conn.commit()
        try:
            customer: dict[str, str] = {}
            raw_bytes = input_path.read_bytes()

            # Raw OCR: run Textract for raw text (for Raw_OCR.txt)
            if hasattr(self, "_raw_ocr_parts") and self._raw_ocr_parts is not None:
                try:
                    from app.services.textract_service import extract_text_from_bytes

                    txt_result = extract_text_from_bytes(raw_bytes)
                    if not txt_result.get("error") and txt_result.get("full_text"):
                        self._raw_ocr_parts.append((filename, txt_result["full_text"]))
                except Exception:
                    pass

            try:
                from app.services.qr_decode_service import decode_qr_from_image_bytes

                qr_result = decode_qr_from_image_bytes(raw_bytes)
                if qr_result.get("decoded") and qr_result["decoded"][0].get("fields"):
                    fields = qr_result["decoded"][0]["fields"]
                    for k in (
                        "aadhar_id", "name", "gender", "year_of_birth", "date_of_birth",
                        "care_of", "house", "street", "location", "city", "post_office",
                        "district", "sub_district", "state", "pin_code",
                    ):
                        v = fields.get(k)
                        if v and str(v).strip():
                            customer[k] = str(v).strip()
                    parts = [
                        customer.get("care_of"), customer.get("house"), customer.get("street"),
                        customer.get("location"), customer.get("state"), customer.get("pin_code"),
                    ]
                    address = ", ".join(p for p in parts if p)
                    if address:
                        customer["address"] = address
                else:
                    raise ValueError("No QR fields")
            except Exception:
                raise ValueError(
                    "QR code is not clear in scan. Re-scan the Aadhar card to ensure the QR code is readable - "
                    "it is required for all critical details."
                ) from None

            # Compliance: never persist full Aadhar; store only last 4 digits
            if customer.get("aadhar_id"):
                customer["aadhar_id"] = _aadhar_last4(customer["aadhar_id"]) or ""

            self._ensure_ocr_output_dir()
            json_path = _json_output_path(self.ocr_output_dir, subfolder)
            data = {"vehicle": {}, "customer": {}, "insurance": {}}
            if json_path.exists():
                try:
                    data = json.loads(json_path.read_text(encoding="utf-8"))
                    if not isinstance(data.get("vehicle"), dict):
                        data["vehicle"] = {}
                    if not isinstance(data.get("customer"), dict):
                        data["customer"] = {}
                    if not isinstance(data.get("insurance"), dict):
                        data["insurance"] = {}
                except Exception:
                    pass
            data["customer"] = customer
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

            # Write a file in ocr_output/subfolder listing the 15 extracted Aadhar fields (with display labels)
            output_path = self.get_output_path(subfolder, filename)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            lines = ["Aadhar scan – 15 extracted fields", ""]
            for key, label in AADHAR_15_FIELDS:
                value = customer.get(key)
                if value and str(value).strip():
                    lines.append(f"{label}: {value.strip()}")
            if customer.get("address") and str(customer["address"]).strip():
                lines.append(f"Address (constructed): {customer['address'].strip()}")
            summary = "\n".join(f"{k}: {v}" for k, v in customer.items() if v)
            output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            if qid is not None:
                with get_connection() as conn:
                    AiReaderQueueRepository.update_status(conn, qid, "done")
                    conn.commit()

            return {
                "id": qid,
                "subfolder": subfolder,
                "filename": filename,
                "status": "done",
                "error": None,
                "extracted_text": summary,
                "output_path": str(output_path),
                "document_type": "Aadhar",
                "classification_confidence": 1.0,
            }
        except Exception as e:
            if qid is not None:
                with get_connection() as conn:
                    AiReaderQueueRepository.update_status(conn, qid, "failed")
                    conn.commit()
                return {
                    "id": qid,
                    "subfolder": subfolder,
                    "filename": filename,
                    "status": "failed",
                    "error": str(e),
                    "extracted_text": None,
                    "output_path": None,
                    "document_type": None,
                    "classification_confidence": None,
                }
            raise

    def _process_insurance_sheet(self, subfolder: str, filename: str, input_path: Path) -> None:
        """Run Textract on Insurance.jpg (TEXT mode); extract insurer, policy from/to, premium via regex on full_text."""
        from app.services.textract_service import extract_text_from_bytes

        document_bytes = input_path.read_bytes()
        result = extract_text_from_bytes(document_bytes)

        if result.get("error"):
            raise RuntimeError(result["error"])

        full_text = result.get("full_text") or ""
        insurance_policy = _parse_insurance_policy_from_full_text(full_text)

        if hasattr(self, "_raw_ocr_parts") and self._raw_ocr_parts is not None:
            self._raw_ocr_parts.append(("Insurance.jpg", full_text))

        # Merge into Details.json insurance section
        json_path = _json_output_path(self.ocr_output_dir, subfolder)
        data: dict = {"vehicle": {}, "customer": {}, "insurance": {}}
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                if not isinstance(data.get("vehicle"), dict):
                    data["vehicle"] = {}
                if not isinstance(data.get("customer"), dict):
                    data["customer"] = {}
                if not isinstance(data.get("insurance"), dict):
                    data["insurance"] = {}
            except Exception:
                pass
        existing_insurance = data.get("insurance") or {}
        insurance_merged = {**existing_insurance, **{k: v for k, v in insurance_policy.items() if v}}
        data["insurance"] = insurance_merged
        # Compliance: sanitize customer aadhar before persisting
        customer = data.get("customer") or {}
        if customer.get("aadhar_id"):
            customer["aadhar_id"] = _aadhar_last4(customer["aadhar_id"]) or ""
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get_extracted_details(self, subfolder: str) -> dict | None:
        """
        Return structured extracted details (vehicle, customer) for a subfolder.
        Loads from JSON if present. If customer is missing/empty and Aadhar.jpg exists in the subfolder,
        runs Aadhar reading (OpenAI Vision), merges customer, persists JSON, and returns.
        """
        self._ensure_ocr_output_dir()
        json_path = _json_output_path(self.ocr_output_dir, subfolder)
        data = {"vehicle": {}, "customer": {}}
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                if not isinstance(data.get("vehicle"), dict):
                    data["vehicle"] = {}
                if not isinstance(data.get("customer"), dict):
                    data["customer"] = {}
                if not isinstance(data.get("insurance"), dict):
                    data["insurance"] = {}
            except Exception:
                pass
        if "insurance" not in data:
            data["insurance"] = {}

        customer = data.get("customer") or {}
        has_customer = any(customer.get(k) for k in ("name", "address", "aadhar_id", "city", "state", "pin", "pin_code"))
        aadhar_path = self.uploads_dir / subfolder / "Aadhar.jpg"
        if not has_customer and aadhar_path.exists():
            raw_bytes = aadhar_path.read_bytes()
            try:
                from app.services.qr_decode_service import decode_qr_from_image_bytes

                qr_result = decode_qr_from_image_bytes(raw_bytes)
                if qr_result.get("decoded") and qr_result["decoded"][0].get("fields"):
                    fields = qr_result["decoded"][0]["fields"]
                    for k in (
                        "aadhar_id", "name", "gender", "year_of_birth", "date_of_birth",
                        "care_of", "house", "street", "location", "city", "post_office",
                        "district", "sub_district", "state", "pin_code",
                    ):
                        v = fields.get(k)
                        if v and str(v).strip():
                            customer[k] = str(v).strip()
                    parts = [
                        customer.get("care_of"), customer.get("house"), customer.get("street"),
                        customer.get("location"), customer.get("state"), customer.get("pin_code"),
                    ]
                    address = ", ".join(p for p in parts if p)
                    if address:
                        customer["address"] = address
                    # Compliance: never persist full Aadhar; store only last 4 digits
                    if customer.get("aadhar_id"):
                        customer["aadhar_id"] = _aadhar_last4(customer["aadhar_id"]) or ""
                    data["customer"] = customer
                else:
                    raise ValueError("No QR fields")
            except Exception:
                # Return partial data (vehicle, insurance) so UI can show them; include error for customer
                data["extraction_error"] = (
                    "QR code is not clear in scan. Re-scan the Aadhar card to ensure the QR code is readable - "
                    "it is required for all critical details."
                )
            else:
                # QR succeeded: persist customer to JSON
                try:
                    json_path.parent.mkdir(parents=True, exist_ok=True)
                    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                except Exception:
                    pass

        # Compliance: sanitize customer aadhar on return (handles legacy JSON with full Aadhar)
        customer = data.get("customer") or {}
        if customer.get("aadhar_id"):
            customer["aadhar_id"] = _aadhar_last4(customer["aadhar_id"]) or ""
        # Sanitize nominee_age: extract digits only (e.g. "30/m" -> "30"); clear if invalid
        ins = data.get("insurance") or {}
        if ins.get("nominee_age"):
            sanitized = _sanitize_nominee_age(str(ins["nominee_age"]))
            ins["nominee_age"] = sanitized
        # Name match validation: Aadhar, Details sheet, Insurance
        aadhar_name = customer.get("name")
        details_name = data.get("details_customer_name")
        insurance_name = (data.get("insurance") or {}).get("policy_holder_name")
        name_err = _validate_name_match(aadhar_name, details_name, insurance_name)
        if name_err:
            data["name_mismatch_error"] = name_err
        return data

    def list_extractions(self, limit: int = 200) -> list[dict]:
        """List queue items (oldest first) with extracted text from flat files."""
        with get_connection() as conn:
            AiReaderQueueRepository.ensure_table(conn)
            rows = AiReaderQueueRepository.list_all(conn, limit=limit)

        rows = list(reversed(rows))
        result = []
        for row in rows:
            r = dict(row)
            for k in ("created_at", "updated_at"):
                if k in r and isinstance(r[k], datetime):
                    r[k] = r[k].isoformat()
            text = self.read_extraction_file(r["subfolder"], r["filename"])
            r["extracted_text"] = text
            r["output_path"] = str(self.get_output_path(r["subfolder"], r["filename"]))
            result.append(r)
        return result


def validate_name_match_for_subfolder(ocr_output_dir: Path, subfolder: str) -> str | None:
    """
    Load OCR JSON for subfolder and validate name match across Aadhar, Details, Insurance.
    Returns error message if mismatch, None if OK.
    """
    json_path = _json_output_path(ocr_output_dir, subfolder)
    if not json_path.exists():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    customer = data.get("customer") or {}
    aadhar_name = customer.get("name")
    details_name = data.get("details_customer_name")
    insurance_name = (data.get("insurance") or {}).get("policy_holder_name")
    return _validate_name_match(aadhar_name, details_name, insurance_name)
