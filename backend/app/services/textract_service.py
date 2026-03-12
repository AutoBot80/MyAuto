"""AWS Textract: extract text from document images (e.g. Sales Detail Sheet)."""

from pathlib import Path
from typing import Any

from app.config import AWS_REGION


def extract_text_from_bytes(document_bytes: bytes) -> dict[str, Any]:
    """
    Call AWS Textract DetectDocumentText on raw document bytes (JPEG/PNG).
    Returns dict with:
      - full_text: str (all LINE text joined)
      - blocks: list of {BlockType, Text, Confidence} for inspection
      - raw_response: summary of API response (e.g. block count)
    Raises or returns error dict if AWS credentials/config missing or API fails.
    """
    try:
        import boto3
    except ImportError:
        return {
            "full_text": "",
            "blocks": [],
            "raw_response": None,
            "error": "boto3 not installed. pip install boto3",
        }

    if not document_bytes or len(document_bytes) > 5 * 1024 * 1024:
        return {
            "full_text": "",
            "blocks": [],
            "raw_response": None,
            "error": "Document empty or larger than 5 MB.",
        }

    try:
        client = boto3.client("textract", region_name=AWS_REGION)
        response = client.detect_document_text(Document={"Bytes": document_bytes})
    except Exception as e:
        return {
            "full_text": "",
            "blocks": [],
            "raw_response": None,
            "error": str(e),
        }

    blocks = response.get("Blocks") or []
    lines = [
        b.get("Text", "").strip()
        for b in blocks
        if b.get("BlockType") == "LINE" and b.get("Text")
    ]
    full_text = "\n".join(lines)

    # Expose a subset of block data for "see the output"
    block_summary = [
        {
            "BlockType": b.get("BlockType"),
            "Text": b.get("Text", ""),
            "Confidence": round(b.get("Confidence", 0), 2),
        }
        for b in blocks
    ]

    return {
        "full_text": full_text,
        "blocks": block_summary,
        "raw_response": {
            "BlockCount": len(blocks),
            "DocumentMetadata": response.get("DocumentMetadata"),
        },
        "error": None,
    }


def extract_text_from_path(file_path: Path) -> dict[str, Any]:
    """Read file from path and run Textract. For JPEG/PNG."""
    if not file_path.exists():
        return {"full_text": "", "blocks": [], "raw_response": None, "error": "File not found."}
    return extract_text_from_bytes(file_path.read_bytes())
