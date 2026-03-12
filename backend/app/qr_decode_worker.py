"""
Run QR decode in a separate process so the main server can kill it on timeout.
Reads image path from argv[1], writes JSON result to stdout.
"""
import json
import sys
from pathlib import Path

# Ensure app is importable when run as __main__
if __name__ == "__main__":
    from app.services.qr_decode_service import decode_qr_from_image_bytes

    try:
        if len(sys.argv) < 2:
            print(json.dumps({"decoded": [], "error": "Missing image path"}))
            sys.exit(1)
        path = Path(sys.argv[1])
        if not path.exists():
            print(json.dumps({"decoded": [], "error": "File not found"}))
            sys.exit(1)
        raw = path.read_bytes()
        result = decode_qr_from_image_bytes(raw)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"decoded": [], "error": f"Decode failed: {e}"}))
        sys.exit(1)
