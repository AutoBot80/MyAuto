from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from app.config import JWT_ALGORITHM, JWT_EXPIRE_MINUTES, JWT_SECRET


def create_access_token(
    *,
    login_id: str,
    dealer_id: int,
    name: str | None,
    roles: list[str],
    admin: bool,
) -> str:
    now = datetime.now(timezone.utc)
    exp_at = now + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload: dict[str, Any] = {
        "sub": login_id,
        "dealer_id": dealer_id,
        "name": name,
        "roles": roles,
        "admin": admin,
        "iat": int(now.timestamp()),
        "exp": int(exp_at.timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


def payload_to_claims(data: dict[str, Any]) -> dict[str, Any]:
    """Validate required JWT claims."""
    sub = data.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        raise JWTError("missing sub")
    did = data.get("dealer_id")
    if not isinstance(did, int):
        raise JWTError("missing dealer_id")
    roles = data.get("roles")
    if not isinstance(roles, list):
        roles = []
    roles_str = [str(r) for r in roles if r is not None]
    admin = bool(data.get("admin"))
    name = data.get("name")
    name_str = str(name) if isinstance(name, str) else None
    return {
        "login_id": sub.strip(),
        "dealer_id": did,
        "name": name_str,
        "roles": roles_str,
        "admin": admin,
    }
