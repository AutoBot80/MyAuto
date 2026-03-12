from fastapi import APIRouter, HTTPException

from app.db import get_connection
from app.repositories.ai_reader_queue import AiReaderQueueRepository
from app.schemas.ocr import ExtractionItem, ProcessNextResponse, ProcessStatusResponse
from app.services.ocr_service import OcrService
from app.services.queue_processor import get_status, start_process_all

router = APIRouter(prefix="/ai-reader-queue", tags=["ai-reader-queue"])


@router.get("")
def list_ai_reader_queue(limit: int = 200) -> list[dict]:
    with get_connection() as conn:
        AiReaderQueueRepository.ensure_table(conn)
        return AiReaderQueueRepository.list_all(conn, limit=limit)


@router.post("/process-next", response_model=ProcessNextResponse | None)
def process_next_ocr() -> ProcessNextResponse | None:
    """Process the oldest queued document with Tesseract; write text to a flat file and update status."""
    service = OcrService()
    result = service.process_next()
    if result is None:
        return None
    return ProcessNextResponse(**result)


@router.get("/extractions", response_model=list[ExtractionItem])
def list_extractions(limit: int = 200) -> list[ExtractionItem]:
    """List queue items (oldest first) with extracted text read from the flat files."""
    service = OcrService()
    items = service.list_extractions(limit=limit)
    return [ExtractionItem(**item) for item in items]


@router.get("/process-status", response_model=ProcessStatusResponse)
def process_status() -> ProcessStatusResponse:
    """Return status of the background process: waiting, running, or sleeping."""
    return ProcessStatusResponse(**get_status())


@router.post("/empty")
def empty_queue() -> dict:
    """Remove all items from the AI reader queue."""
    with get_connection() as conn:
        AiReaderQueueRepository.ensure_table(conn)
        n = AiReaderQueueRepository.delete_all(conn)
        conn.commit()
    return {"ok": True, "deleted": n}


@router.post("/process-all")
def process_all() -> dict:
    """Start background process that reads all queued documents until the queue is empty."""
    started, message = start_process_all()
    return {"started": started, "message": message}


@router.post("/{item_id:int}/reprocess")
def reprocess_item(item_id: int) -> dict:
    """Reset a queue item to 'queued' and clear classification so it will be processed again."""
    with get_connection() as conn:
        AiReaderQueueRepository.ensure_table(conn)
        n = AiReaderQueueRepository.reset_for_reprocess(conn, item_id)
        conn.commit()
    if n == 0:
        raise HTTPException(status_code=404, detail="Queue item not found")
    return {"ok": True, "id": item_id, "message": "Queued for re-processing"}
