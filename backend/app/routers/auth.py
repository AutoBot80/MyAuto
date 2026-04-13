"""Login and current-user endpoints."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.db import get_connection
from app.limiter import limiter
from app.security.deps import get_principal
from app.security.jwt_utils import create_access_token
from app.security.passwords import verify_password
from app.security.principal import Principal

logger = logging.getLogger(__name__)
audit = logging.getLogger("audit")

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    dealer_id: int = Field(..., description="Dealer scope for this session")
    login_id: str = Field(..., min_length=1, max_length=300)
    password: str = Field(..., min_length=1, max_length=300)


class MeResponse(BaseModel):
    login_id: str
    dealer_id: int
    name: str | None
    roles: list[str]
    admin: bool


def _load_roles_for_dealer(cur, login_id: str, dealer_id: int) -> tuple[list[str], bool]:
    cur.execute(
        """
        SELECT rr.role_name, rr.admin_flag
        FROM login_roles_ref lrr
        INNER JOIN roles_ref rr ON rr.role_id = lrr.role_id
        WHERE lrr.login_id = %s AND lrr.dealer_id = %s
        """,
        (login_id, dealer_id),
    )
    rows = cur.fetchall() or []
    names: list[str] = []
    admin = False
    for r in rows:
        rn = r.get("role_name")
        if rn:
            names.append(str(rn))
        if (r.get("admin_flag") or "").upper() == "Y":
            admin = True
    return names, admin


@router.post("/login")
@limiter.limit("30/minute")
def login(request: Request, payload: LoginRequest) -> dict[str, Any]:
    """Validate ``login_ref`` + ``login_roles_ref`` for dealer; return JWT."""
    lid = payload.login_id.strip()
    if not lid:
        raise HTTPException(status_code=400, detail="login_id is required")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pwd_hash, name, active_flag
                FROM login_ref
                WHERE login_id = %s
                """,
                (lid,),
            )
            row = cur.fetchone()
            if not row:
                audit.info(
                    "event=login_fail reason=no_user login_id=%s dealer_id=%s",
                    lid,
                    payload.dealer_id,
                )
                raise HTTPException(status_code=401, detail="Invalid login or password")

            if (row.get("active_flag") or "").upper() != "Y":
                audit.info(
                    "event=login_fail reason=inactive login_id=%s dealer_id=%s",
                    lid,
                    payload.dealer_id,
                )
                raise HTTPException(status_code=403, detail="Login is disabled")

            pwd_hash = row.get("pwd_hash") or ""
            if not verify_password(payload.password, str(pwd_hash)):
                audit.info(
                    "event=login_fail reason=bad_password login_id=%s dealer_id=%s",
                    lid,
                    payload.dealer_id,
                )
                raise HTTPException(status_code=401, detail="Invalid login or password")

            cur.execute("SELECT 1 FROM dealer_ref WHERE dealer_id = %s", (payload.dealer_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Dealer not found")

            roles, admin = _load_roles_for_dealer(cur, lid, int(payload.dealer_id))
            if not roles:
                audit.info(
                    "event=login_fail reason=no_roles login_id=%s dealer_id=%s",
                    lid,
                    payload.dealer_id,
                )
                raise HTTPException(
                    status_code=403,
                    detail="No role assigned for this dealer. Ask an administrator to assign roles.",
                )

            display_name = row.get("name")
            token = create_access_token(
                login_id=lid,
                dealer_id=int(payload.dealer_id),
                name=str(display_name) if display_name is not None else None,
                roles=roles,
                admin=admin,
            )
    finally:
        conn.close()

    audit.info("event=login_ok login_id=%s dealer_id=%s", lid, payload.dealer_id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "dealer_id": payload.dealer_id,
        "login_id": lid,
        "roles": roles,
        "admin": admin,
    }


@router.get("/me", response_model=MeResponse)
def me(principal: Principal = Depends(get_principal)) -> MeResponse:
    return MeResponse(
        login_id=principal.login_id,
        dealer_id=principal.dealer_id,
        name=principal.name,
        roles=list(principal.roles),
        admin=principal.admin,
    )
