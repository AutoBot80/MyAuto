"""Login and current-user endpoints."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import SKIP_PASSWORD_VERIFICATION
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
    dealer_id: int | None = Field(
        None,
        description=(
            "Omit when this login has a single dealer in login_roles_ref (server derives dealer_id). "
            "Required when the login has multiple dealers — must be one of those dealer_ids."
        ),
    )
    login_id: str = Field(..., min_length=1, max_length=300)
    password: str = Field(..., min_length=1, max_length=300)


class MeResponse(BaseModel):
    login_id: str
    dealer_id: int
    name: str | None
    roles: list[str]
    admin: bool
    # roles_ref: OR of flags across roles for this login at this dealer (home tiles).
    tile_pos: bool = Field(..., description="Sales Window — roles_ref.pos_flag")
    tile_rto: bool = Field(..., description="RTO Desk — roles_ref.rto_flag")
    tile_service: bool = Field(..., description="Service Saathi — roles_ref.service_flag")
    tile_dealer: bool = Field(..., description="Dealer Saathi — roles_ref.dealer_flag")


def _dealer_ids_for_login(cur, login_id: str) -> list[int]:
    """Distinct dealer_ids from ``login_roles_ref`` for this login (existing dealers only)."""
    cur.execute(
        """
        SELECT DISTINCT lrr.dealer_id
        FROM login_roles_ref lrr
        INNER JOIN dealer_ref dr ON dr.dealer_id = lrr.dealer_id
        WHERE lrr.login_id = %s
        ORDER BY lrr.dealer_id
        """,
        (login_id,),
    )
    rows = cur.fetchall() or []
    return [int(r["dealer_id"]) for r in rows if r.get("dealer_id") is not None]


def _resolve_dealer_id(
    allowed: list[int],
    requested: int | None,
) -> int:
    """
    Pick session dealer_id from ``login_roles_ref`` membership.
    Single dealer -> that id (client ``dealer_id`` ignored).
    Multiple -> ``requested`` must be present and in ``allowed``.
    """
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="No role assigned for any dealer. Ask an administrator to assign roles.",
        )
    if len(allowed) == 1:
        return allowed[0]
    if requested is None:
        raise HTTPException(
            status_code=400,
            detail="This login has access to multiple dealers; specify dealer_id.",
        )
    rid = int(requested)
    if rid not in allowed:
        raise HTTPException(
            status_code=403,
            detail="No role assigned for the chosen dealer_id.",
        )
    return rid


def _load_roles_and_tile_flags(
    cur, login_id: str, dealer_id: int
) -> tuple[list[str], bool, bool, bool, bool, bool]:
    """
    Role names and OR of ``roles_ref`` flags for this login at this dealer.
    Maps: pos_flag → Sales Window, rto_flag → RTO Desk, service_flag → Service,
    dealer_flag → Dealer, admin_flag → Admin (JWT ``admin`` + Admin tile).
    """
    cur.execute(
        """
        SELECT rr.role_name, rr.admin_flag, rr.pos_flag, rr.rto_flag, rr.service_flag, rr.dealer_flag
        FROM login_roles_ref lrr
        INNER JOIN roles_ref rr ON rr.role_id = lrr.role_id
        WHERE lrr.login_id = %s AND lrr.dealer_id = %s
        """,
        (login_id, dealer_id),
    )
    rows = cur.fetchall() or []
    names: list[str] = []
    admin = False
    tile_pos = tile_rto = tile_service = tile_dealer = False
    for r in rows:
        rn = r.get("role_name")
        if rn:
            names.append(str(rn))
        if (r.get("admin_flag") or "").upper() == "Y":
            admin = True
        if (r.get("pos_flag") or "").upper() == "Y":
            tile_pos = True
        if (r.get("rto_flag") or "").upper() == "Y":
            tile_rto = True
        if (r.get("service_flag") or "").upper() == "Y":
            tile_service = True
        if (r.get("dealer_flag") or "").upper() == "Y":
            tile_dealer = True
    return names, admin, tile_pos, tile_rto, tile_service, tile_dealer


@router.post("/login")
@limiter.limit("30/minute")
def login(request: Request, payload: LoginRequest) -> dict[str, Any]:
    """
    Validate ``login_ref`` + ``login_roles_ref``; return JWT.

    Session ``dealer_id`` is taken from ``login_roles_ref`` for this login: if exactly one
    distinct dealer is assigned, that id is used; if several, the client must send ``dealer_id``
    and it must appear in ``login_roles_ref`` for this login.
    """
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
                audit.info("event=login_fail reason=no_user login_id=%s", lid)
                raise HTTPException(status_code=401, detail="Invalid login or password")

            if (row.get("active_flag") or "").upper() != "Y":
                audit.info("event=login_fail reason=inactive login_id=%s", lid)
                raise HTTPException(status_code=403, detail="Login is disabled")

            if not SKIP_PASSWORD_VERIFICATION:
                pwd_hash = row.get("pwd_hash") or ""
                if not verify_password(payload.password, str(pwd_hash)):
                    audit.info("event=login_fail reason=bad_password login_id=%s", lid)
                    raise HTTPException(status_code=401, detail="Invalid login or password")

            allowed_dealers = _dealer_ids_for_login(cur, lid)
            dealer_id = _resolve_dealer_id(allowed_dealers, payload.dealer_id)

            roles, admin, tile_pos, tile_rto, tile_service, tile_dealer = _load_roles_and_tile_flags(
                cur, lid, dealer_id
            )
            if not roles:
                audit.info(
                    "event=login_fail reason=no_roles login_id=%s dealer_id=%s",
                    lid,
                    dealer_id,
                )
                raise HTTPException(
                    status_code=403,
                    detail="No role assigned for this dealer. Ask an administrator to assign roles.",
                )

            display_name = row.get("name")
            name_out: str | None = None
            if display_name is not None:
                stripped = str(display_name).strip()
                name_out = stripped if stripped else None
            token = create_access_token(
                login_id=lid,
                dealer_id=int(dealer_id),
                name=name_out,
                roles=roles,
                admin=admin,
                tile_pos=tile_pos,
                tile_rto=tile_rto,
                tile_service=tile_service,
                tile_dealer=tile_dealer,
            )
            audit.info("event=login_ok login_id=%s dealer_id=%s", lid, dealer_id)
            return {
                "access_token": token,
                "token_type": "bearer",
                "dealer_id": dealer_id,
                "login_id": lid,
                "name": name_out,
                "roles": roles,
                "admin": admin,
                "tile_pos": tile_pos,
                "tile_rto": tile_rto,
                "tile_service": tile_service,
                "tile_dealer": tile_dealer,
            }
    finally:
        conn.close()


@router.get("/me", response_model=MeResponse)
def me(principal: Principal = Depends(get_principal)) -> MeResponse:
    return MeResponse(
        login_id=principal.login_id,
        dealer_id=principal.dealer_id,
        name=principal.name,
        roles=list(principal.roles),
        admin=principal.admin,
        tile_pos=principal.tile_pos,
        tile_rto=principal.tile_rto,
        tile_service=principal.tile_service,
        tile_dealer=principal.tile_dealer,
    )
