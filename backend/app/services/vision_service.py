"""Call ChatGPT (OpenAI) vision on document images: identify document, locate photo, extract customer fields."""

import base64
import json
import re
from pathlib import Path
from typing import Any

# Prompt for extracting structured customer fields from Aadhar (Name, Address, City, State, PIN, Aadhar ID)
AADHAR_EXTRACT_FIELDS_PROMPT = """Look at this Indian Aadhar (Aadhaar) card image. Extract the following fields and return ONLY a single JSON object, no other text. Use these exact keys: "name", "address", "city", "state", "pin", "aadhar_id". For "name" use the person's full name as shown. For "address" use the full address line(s). For "city", "state", "pin" use the place, state, and PIN code. For "aadhar_id" use the 12-digit Aadhar number (format nnnn nnnn nnnn or 12 consecutive digits) - it may appear on front or back. If something is not visible or not in English/Hindi, use empty string "" for that key. Example format: {"name":"John Doe","address":"123 Street, Area","city":"Mumbai","state":"Maharashtra","pin":"400001","aadhar_id":"1234 5678 9012"}"""

# Default prompt for Aadhar scan (document type + photo region)
AADHAR_VISION_PROMPT = """Look at this document image.

1) IDENTIFY THE DOCUMENT: What type of document is this? (e.g. Aadhar card, Aadhaar, driving licence, etc.) Reply in one short line.

2) CUSTOMER PHOTO: On Indian Aadhar cards there is usually a portrait/photo of the person on the LEFT side. Describe where that photo is:
   - If you see a person's photo on the left, give its approximate bounding box as percentages of the image size: left_pct, top_pct, width_pct, height_pct (each 0-100). Format: "Photo region: left_pct=X, top_pct=Y, width_pct=W, height_pct=H"
   - If there is no clear photo or you're unsure, say "Photo region: not found" or describe what you see.

Be concise. Use the exact "Photo region:" format if you can give numbers."""


def _image_to_base64_data_uri(image_path: Path) -> str:
    """Read image file and return base64 data URI for OpenAI API."""
    raw = image_path.read_bytes()
    b64 = base64.standard_b64encode(raw).decode("ascii")
    # Detect media type from suffix
    suffix = image_path.suffix.lower()
    if suffix in (".png",):
        media_type = "image/png"
    elif suffix in (".jpg", ".jpeg",):
        media_type = "image/jpeg"
    elif suffix in (".gif", ".webp",):
        media_type = f"image/{suffix[1:]}"
    else:
        media_type = "image/jpeg"
    return f"data:{media_type};base64,{b64}"


def analyze_aadhar_image(
    image_path: Path | None = None,
    image_base64: str | None = None,
    *,
    api_key: str | None = None,
    model: str = "gpt-4o-mini",
) -> dict[str, Any]:
    """
    Send Aadhar scan image to ChatGPT vision. Returns API response and parsed content.

    Args:
        image_path: Path to image file (used if image_base64 is not set).
        image_base64: Optional base64 data URI (e.g. from upload). Overrides image_path.
        api_key: OpenAI API key (defaults to OPENAI_API_KEY from config).
        model: Vision model (gpt-4o, gpt-4o-mini, gpt-4-turbo, etc.).

    Returns:
        dict with:
          - content: str (what ChatGPT returned, plain text)
          - document_type: str | None (first line or summary, if we want to parse later)
          - raw_response: full API response (choices, usage, etc.) for inspection
          - error: str | None if something failed
    """
    from app.config import OPENAI_API_KEY

    key = api_key or OPENAI_API_KEY
    if not key:
        return {
            "content": None,
            "document_type": None,
            "raw_response": None,
            "error": "OPENAI_API_KEY is not set",
        }

    if image_base64:
        image_url = image_base64 if image_base64.startswith("data:") else f"data:image/jpeg;base64,{image_base64}"
    elif image_path and image_path.exists():
        image_url = _image_to_base64_data_uri(image_path)
    else:
        return {
            "content": None,
            "document_type": None,
            "raw_response": None,
            "error": "No image provided (image_path or image_base64 required)",
        }

    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": AADHAR_VISION_PROMPT,
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url},
                        },
                    ],
                }
            ],
            max_tokens=500,
        )
    except Exception as e:
        return {
            "content": None,
            "document_type": None,
            "raw_response": None,
            "error": str(e),
        }

    choice = response.choices[0] if response.choices else None
    content = choice.message.content if choice and choice.message else None

    # Build raw response for "show what it returns" (strip non-serializable if any)
    raw = {
        "id": getattr(response, "id", None),
        "model": getattr(response, "model", None),
        "choices": [
            {
                "index": getattr(c, "index", None),
                "message": {
                    "role": getattr(c.message, "role", None),
                    "content": getattr(c.message, "content", None),
                },
                "finish_reason": getattr(c, "finish_reason", None),
            }
            for c in (response.choices or [])
        ],
        "usage": (
            {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
            if response.usage
            else None
        ),
    }

    # Simple heuristic: first line as document_type
    document_type = None
    if content:
        first_line = content.strip().split("\n")[0].strip()
        if first_line and not first_line.lower().startswith("photo"):
            document_type = first_line

    return {
        "content": content,
        "document_type": document_type,
        "raw_response": raw,
        "error": None,
    }


def extract_aadhar_customer_fields(
    image_path: Path | None = None,
    image_bytes: bytes | None = None,
    *,
    api_key: str | None = None,
    model: str = "gpt-4o-mini",
) -> dict[str, Any]:
    """
    Extract structured customer fields (name, address, city, state, pin) from an Aadhar card image using OpenAI Vision.
    Returns dict with keys: name, address, city, state, pin (all strings), and optionally "error" if something failed.
    """
    from app.config import OPENAI_API_KEY

    key = api_key or OPENAI_API_KEY
    if not key:
        return {"name": "", "address": "", "city": "", "state": "", "pin": "", "aadhar_id": "", "error": "OPENAI_API_KEY is not set"}

    if image_bytes:
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        image_url = f"data:image/jpeg;base64,{b64}"
    elif image_path and image_path.exists():
        image_url = _image_to_base64_data_uri(image_path)
    else:
        return {"name": "", "address": "", "city": "", "state": "", "pin": "", "aadhar_id": "", "error": "No image provided"}

    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": AADHAR_EXTRACT_FIELDS_PROMPT},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            max_tokens=400,
        )
    except Exception as e:
        return {"name": "", "address": "", "city": "", "state": "", "pin": "", "error": str(e)}

    choice = response.choices[0] if response.choices else None
    content = (choice.message.content if choice and choice.message else None) or ""

    # Parse JSON from response (model might wrap in markdown code block)
    out = {"name": "", "address": "", "city": "", "state": "", "pin": "", "aadhar_id": ""}
    content = content.strip()
    # Remove optional markdown code fence
    if content.startswith("```"):
        content = re.sub(r"^```\w*\n?", "", content)
        content = re.sub(r"\n?```\s*$", "", content)
    start = content.find("{")
    if start >= 0:
        depth = 0
        end = -1
        for i in range(start, len(content)):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end >= start:
            try:
                parsed = json.loads(content[start : end + 1])
                if isinstance(parsed, dict):
                    for k in out:
                        if k in parsed and isinstance(parsed[k], (str, int, float)):
                            out[k] = str(parsed[k]).strip()
            except json.JSONDecodeError:
                pass
    return out
