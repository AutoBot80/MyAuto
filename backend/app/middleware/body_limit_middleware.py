"""Reject oversized bodies using Content-Length (multipart may omit it; uvicorn still streams)."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import (
    MAX_JSON_BODY_BYTES,
    MAX_SINGLE_UPLOAD_BODY_BYTES,
    MAX_UPLOAD_ROUTE_BODY_BYTES,
)


def _limit_for_path(path: str) -> int:
    if path.startswith("/uploads"):
        return MAX_UPLOAD_ROUTE_BODY_BYTES
    if path.startswith("/subdealer-challan/parse-scan"):
        return MAX_SINGLE_UPLOAD_BODY_BYTES
    if path.startswith("/qr-decode"):
        return MAX_SINGLE_UPLOAD_BODY_BYTES
    if path.startswith("/textract"):
        return MAX_SINGLE_UPLOAD_BODY_BYTES
    if path.startswith("/vision"):
        return MAX_SINGLE_UPLOAD_BODY_BYTES
    return MAX_JSON_BODY_BYTES


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method not in ("POST", "PUT", "PATCH"):
            return await call_next(request)
        cl = request.headers.get("content-length")
        if cl is None:
            return await call_next(request)
        try:
            n = int(cl)
        except ValueError:
            return await call_next(request)
        limit = _limit_for_path(request.url.path)
        if n > limit:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Payload too large (max {limit} bytes for this route)."},
            )
        return await call_next(request)
