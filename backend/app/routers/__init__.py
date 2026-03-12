from .health import router as health_router
from .uploads import router as uploads_router
from .ai_reader_queue import router as ai_reader_queue_router
from .vision import router as vision_router

__all__ = ["health_router", "uploads_router", "ai_reader_queue_router", "vision_router"]
