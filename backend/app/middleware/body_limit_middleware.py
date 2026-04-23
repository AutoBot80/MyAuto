"""Reject oversized bodies using Content-Length (multipart may omit it; uvicorn still streams)."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import (
    MAX_JSON_BODY_BYTES,
    MAX_SINGLE_UPLOAD_BODY_BYTES,
    MAX_UPLOAD_ROUTE_BODY_BYTES,
)


def _path_segments(path: str) -> list[str]:
    return [p for p in path.split("/") if p]


def _limit_for_path(path: str) -> int:
    # Do not use path.startswith("/uploads") only — behind a prefix (e.g. /api) the path is
    # /api/.../uploads/... and would incorrectly get MAX_JSON_BODY_BYTES (2 MiB).
    segs = _path_segments(path)
    if "uploads" in segs:
        return MAX_UPLOAD_ROUTE_BODY_BYTES
    if "subdealer-challan" in segs and "parse-scan" in segs:
        return MAX_SINGLE_UPLOAD_BODY_BYTES
    if "qr-decode" in segs:
        return MAX_SINGLE_UPLOAD_BODY_BYTES
    if "textract" in segs:
        return MAX_SINGLE_UPLOAD_BODY_BYTES
    if "vision" in segs:
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
