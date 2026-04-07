"""Background process that reads all documents in ai_reader_queue until empty."""

import threading
from typing import Literal

from app.services.sales_ocr_service import OcrService

ProcessStatus = Literal["waiting", "running", "sleeping"]

_state: dict = {
    "status": "waiting",
    "processed_count": 0,
    "last_error": None,
}
_lock = threading.Lock()


def get_status() -> dict:
    with _lock:
        return {
            "status": _state["status"],
            "processed_count": _state["processed_count"],
            "last_error": _state["last_error"],
        }


def _run_process_all() -> None:
    service = OcrService()
    with _lock:
        _state["status"] = "running"
        _state["processed_count"] = 0
        _state["last_error"] = None
    count = 0
    try:
        while True:
            result = service.process_next()
            if result is None:
                break
            count += 1
            with _lock:
                _state["processed_count"] = count
                if result.get("status") == "failed" and result.get("error"):
                    _state["last_error"] = result["error"]
    finally:
        with _lock:
            _state["status"] = "sleeping"


def start_process_all() -> tuple[bool, str]:
    """
    Start the background process if not already running.
    Returns (started: bool, message: str).
    """
    with _lock:
        if _state["status"] == "running":
            return False, "Process is already running."
        thread = threading.Thread(target=_run_process_all, daemon=True)
        thread.start()
        return True, "Process started."
