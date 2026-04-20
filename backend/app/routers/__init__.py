from .health import router as health_router
from .uploads import router as uploads_router
from .ai_reader_queue import router as ai_reader_queue_router
from .vision import router as vision_router
from .dealers import router as dealers_router
from .settings import router as settings_router
from .textract_router import router as textract_router
from .qr_decode import router as qr_decode_router
from .submit_info import router as submit_info_router
from .fill_forms_router import router as fill_forms_router
from .rto_payment_details import router as rto_payment_details_router
from .customer_search import router as customer_search_router
from .vehicle_search import router as vehicle_search_router
from .documents import router as documents_router
from .bulk_loads import router as bulk_loads_router
from .admin import router as admin_router
from .add_sales import router as add_sales_router
from .subdealer_challan import router as subdealer_challan_router
from .sidecar_proxy import router as sidecar_proxy_router

__all__ = [
    "health_router",
    "uploads_router",
    "ai_reader_queue_router",
    "vision_router",
    "dealers_router",
    "settings_router",
    "textract_router",
    "qr_decode_router",
    "submit_info_router",
    "fill_forms_router",
    "rto_payment_details_router",
    "customer_search_router",
    "vehicle_search_router",
    "documents_router",
    "bulk_loads_router",
    "admin_router",
    "add_sales_router",
    "subdealer_challan_router",
    "sidecar_proxy_router",
]
