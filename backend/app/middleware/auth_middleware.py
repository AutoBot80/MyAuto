"""JWT validation; optional AUTH_DISABLED dev principal."""

from __future__ import annotations

from jose import JWTError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import AUTH_DISABLED, DEALER_ID, JWT_SECRET
from app.security.jwt_utils import decode_token, payload_to_claims
from app.security.principal import Principal

PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/auth/login",
        "/settings/site-urls",
    }
)


def _is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    if path.startswith("/docs/") or path.startswith("/redoc"):
        return True
    return False


def _dev_principal() -> Principal:
    return Principal(
        login_id="dev",
        dealer_id=DEALER_ID,
        name="Dev",
        roles=("Dev",),
        admin=True,
    )


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if _is_public_path(path):
            return await call_next(request)
        if AUTH_DISABLED:
            request.state.principal = _dev_principal()
            return await call_next(request)
        if not JWT_SECRET or len(JWT_SECRET) < 32:
            return JSONResponse(
                status_code=503,
                content={"detail": "Server misconfiguration: JWT_SECRET not set (min 32 chars)."},
            )
        auth = request.headers.get("authorization") or ""
        if not auth.lower().startswith("bearer "):
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
        token = auth[7:].strip()
        if not token:
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
        try:
            raw = decode_token(token)
            claims = payload_to_claims(raw)
            request.state.principal = Principal(
                login_id=claims["login_id"],
                dealer_id=int(claims["dealer_id"]),
                name=claims["name"],
                roles=tuple(claims["roles"]),
                admin=bool(claims["admin"]),
            )
        except JWTError:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})
        return await call_next(request)
