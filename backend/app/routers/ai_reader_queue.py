from fastapi import APIRouter

from app.db import get_connection
from app.repositories.ai_reader_queue import AiReaderQueueRepository

router = APIRouter(prefix="/ai-reader-queue", tags=["ai-reader-queue"])


@router.get("")
def list_ai_reader_queue(limit: int = 200) -> list[dict]:
    with get_connection() as conn:
        AiReaderQueueRepository.ensure_table(conn)
        return AiReaderQueueRepository.list_all(conn, limit=limit)
