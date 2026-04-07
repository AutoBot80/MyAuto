"""AWS Textract: extract text and forms from document images (e.g. Sales Detail Sheet)."""

from pathlib import Path
from typing import Any

from app.config import AWS_REGION

# Note: Textract supports English, French, German, Italian, Portuguese, Spanish only. Hindi is not supported.


def _get_text_from_block(block: dict, block_map: dict[str, dict]) -> str:
    """Get concatenated text from a block via CHILD WORD blocks."""
    text_parts = []
    for rel in block.get("Relationships") or []:
        if rel.get("Type") != "CHILD":
            continue
        for child_id in rel.get("Ids") or []:
            child = block_map.get(child_id)
            if not child:
                continue
            if child.get("BlockType") == "WORD":
                text_parts.append(child.get("Text", ""))
            if child.get("BlockType") == "SELECTION_ELEMENT" and child.get("SelectionStatus") == "SELECTED":
                text_parts.append("X")
    return " ".join(text_parts).strip()


def _parse_key_value_pairs(blocks: list[dict]) -> list[dict[str, str]]:
    """Parse KEY_VALUE_SET blocks into key-value pairs. Returns list of {key, value}."""
    block_map = {b["Id"]: b for b in blocks}
    key_blocks = [
        b for b in blocks
        if b.get("BlockType") == "KEY_VALUE_SET" and "KEY" in (b.get("EntityTypes") or [])
    ]
    value_map = {
        b["Id"]: b for b in blocks
        if b.get("BlockType") == "KEY_VALUE_SET" and "VALUE" in (b.get("EntityTypes") or [])
    }
    pairs = []
    for key_block in key_blocks:
        key_text = _get_text_from_block(key_block, block_map)
        value_block = None
        for rel in key_block.get("Relationships") or []:
            if rel.get("Type") == "VALUE":
                for vid in rel.get("Ids") or []:
                    if vid in value_map:
                        value_block = value_map[vid]
                        break
                break
        value_text = _get_text_from_block(value_block, block_map) if value_block else ""
        if key_text or value_text:
            pairs.append({"key": key_text, "value": value_text})
    return pairs


def _parse_tables_from_blocks(blocks: list[dict]) -> list[list[list[str]]]:
    """
    Reconstruct tables from Textract AnalyzeDocument TABLE/CELL blocks.
    Returns list of tables; each table is a list of rows; each row is a list of cell strings.
    """
    block_map = {b["Id"]: b for b in blocks if b.get("Id")}
    tables_out: list[list[list[str]]] = []
    for block in blocks:
        if block.get("BlockType") != "TABLE":
            continue
        cells: dict[tuple[int, int], str] = {}
        max_r, max_c = 0, 0
        for rel in block.get("Relationships") or []:
            if rel.get("Type") != "CHILD":
                continue
            for cid in rel.get("Ids") or []:
                cell = block_map.get(cid)
                if not cell or cell.get("BlockType") != "CELL":
                    continue
                r = int(cell.get("RowIndex") or 1)
                c = int(cell.get("ColumnIndex") or 1)
                max_r = max(max_r, r)
                max_c = max(max_c, c)
                text = _get_text_from_block(cell, block_map)
                cells[(r, c)] = text.strip()
        if max_r == 0 or max_c == 0:
            continue
        grid: list[list[str]] = []
        for ri in range(1, max_r + 1):
            row: list[str] = []
            for ci in range(1, max_c + 1):
                row.append(cells.get((ri, ci), ""))
            grid.append(row)
        tables_out.append(grid)
    return tables_out


def analyze_document_forms_and_tables(document_bytes: bytes) -> dict[str, Any]:
    """
    Single AWS Textract AnalyzeDocument call with FORMS + TABLES.
    Returns:
      - full_text, key_value_pairs, tables (list of row grids), raw_response, error
    """
    try:
        import boto3
    except ImportError:
        return {
            "full_text": "",
            "key_value_pairs": [],
            "tables": [],
            "raw_response": None,
            "error": "boto3 not installed. pip install boto3",
        }

    if not document_bytes or len(document_bytes) > 5 * 1024 * 1024:
        return {
            "full_text": "",
            "key_value_pairs": [],
            "tables": [],
            "raw_response": None,
            "error": "Document empty or larger than 5 MB.",
        }

    try:
        client = boto3.client("textract", region_name=AWS_REGION)
        response = client.analyze_document(
            Document={"Bytes": document_bytes},
            FeatureTypes=["FORMS", "TABLES"],
        )
    except Exception as e:
        return {
            "full_text": "",
            "key_value_pairs": [],
            "tables": [],
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
    key_value_pairs = _parse_key_value_pairs(blocks)
    tables = _parse_tables_from_blocks(blocks)

    return {
        "full_text": full_text,
        "key_value_pairs": key_value_pairs,
        "tables": tables,
        "raw_response": {
            "BlockCount": len(blocks),
            "DocumentMetadata": response.get("DocumentMetadata"),
        },
        "error": None,
    }


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


def extract_forms_from_bytes(document_bytes: bytes) -> dict[str, Any]:
    """
    Call AWS Textract AnalyzeDocument with FORMS (and TABLES) to get key-value pairs.
    Returns dict with:
      - full_text: str (all LINE text)
      - key_value_pairs: list of {key, value}
      - tables: list of tables (each table = list of rows of cell strings); same AnalyzeDocument call
      - raw_response: summary
      - error: if any
    """
    result = analyze_document_forms_and_tables(document_bytes)
    err = result.get("error")
    if err:
        return {
            "full_text": "",
            "key_value_pairs": [],
            "tables": [],
            "raw_response": result.get("raw_response"),
            "error": err,
        }
    return {
        "full_text": result.get("full_text") or "",
        "key_value_pairs": result.get("key_value_pairs") or [],
        "tables": result.get("tables") or [],
        "raw_response": result.get("raw_response"),
        "error": None,
    }


def extract_text_from_path(file_path: Path) -> dict[str, Any]:
    """Read file from path and run Textract. For JPEG/PNG."""
    if not file_path.exists():
        return {"full_text": "", "blocks": [], "raw_response": None, "error": "File not found."}
    return extract_text_from_bytes(file_path.read_bytes())
