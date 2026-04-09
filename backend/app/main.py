from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import (
    CHALLANS_DIR,
    DEALER_ID,
    get_bulk_input_scans_dir,
    get_bulk_processing_dir,
    get_bulk_queue_dir,
    get_bulk_upload_dir,
    get_ocr_output_dir,
    get_uploads_dir,
    validate_external_site_urls,
)
from app.routers import (
    health_router,
    uploads_router,
    ai_reader_queue_router,
    vision_router,
    dealers_router,
    settings_router,
    textract_router,
    qr_decode_router,
    submit_info_router,
    fill_forms_router,
    rto_payment_details_router,
    customer_search_router,
    vehicle_search_router,
    documents_router,
    bulk_loads_router,
    admin_router,
    add_sales_router,
    subdealer_challan_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_external_site_urls()
    yield


app = FastAPI(title="Auto Dealer Server", version="0.1.0", lifespan=lifespan)

# Create dealer-scoped dirs for app's DEALER_ID
get_uploads_dir(DEALER_ID).mkdir(parents=True, exist_ok=True)
get_ocr_output_dir(DEALER_ID).mkdir(parents=True, exist_ok=True)
get_bulk_input_scans_dir(DEALER_ID).mkdir(parents=True, exist_ok=True)
get_bulk_queue_dir(DEALER_ID).mkdir(parents=True, exist_ok=True)
get_bulk_processing_dir(DEALER_ID).mkdir(parents=True, exist_ok=True)
(get_bulk_upload_dir(DEALER_ID) / "Success").mkdir(parents=True, exist_ok=True)
(get_bulk_upload_dir(DEALER_ID) / "Error").mkdir(parents=True, exist_ok=True)
(get_bulk_upload_dir(DEALER_ID) / "Rejected scans").mkdir(parents=True, exist_ok=True)
CHALLANS_DIR.mkdir(parents=True, exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    # Dev: Vite on LAN IP (e.g. http://192.168.x.x:5173) when VITE_API_URL points at :8000
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1|192\.168\.\d{1,3}\.\d{1,3})(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(settings_router)
app.include_router(uploads_router)
app.include_router(ai_reader_queue_router)
app.include_router(vision_router)
app.include_router(dealers_router)
app.include_router(textract_router)
app.include_router(qr_decode_router)
app.include_router(submit_info_router)
app.include_router(fill_forms_router)
app.include_router(rto_payment_details_router)
app.include_router(customer_search_router)
app.include_router(vehicle_search_router)
app.include_router(documents_router)
app.include_router(bulk_loads_router)
app.include_router(admin_router)
app.include_router(add_sales_router)
app.include_router(subdealer_challan_router)
