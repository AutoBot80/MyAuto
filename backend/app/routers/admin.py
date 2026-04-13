from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from psycopg2 import sql, IntegrityError
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.config import get_ocr_output_dir, get_uploads_dir
from app.db import get_connection
from app.security.deps import get_principal, require_admin, resolve_dealer_id
from app.security.principal import Principal

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _jsonable_value(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (bytes, memoryview)):
        return None
    return v


def _jsonable_row(d: dict) -> dict:
    return {k: _jsonable_value(v) for k, v in d.items()}


def _dealer_admin_detail_dict(cur, dealer_id: int) -> dict | None:
    """``dealer_ref`` joined to ``oem_name`` and parent row for name. Keeps ``parent_id`` for Admin UI (sub-dealer vs parent)."""
    cur.execute(
        """
        SELECT d.*, o.oem_name AS oem_name, p.dealer_name AS parent_name
        FROM dealer_ref d
        LEFT JOIN oem_ref o ON o.oem_id = d.oem_id
        LEFT JOIN dealer_ref p ON p.dealer_id = d.parent_id
        WHERE d.dealer_id = %s
        """,
        (dealer_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d.pop("dealer_id", None)
    d.pop("city", None)
    d.pop("state", None)
    d.pop("oem_id", None)
    return _jsonable_row(d)

# Truncate all public base tables except:
# - names ending with "ref" (SQL LIKE '%ref'), e.g. dealer_ref, roles_ref, login_ref
# - legacy reference tables that do not end in "ref"
PRESERVED_LIKE_SUFFIX = "%ref"
PRESERVED_EXTRA_TABLES = ("oem_service_schedule", "subdealer_discount_master")
CONFIRMATION_TEXT = "DELETE ALL DATA"


class ResetAllDataRequest(BaseModel):
    confirmation: str


class DataFoldersResponse(BaseModel):
    dealer_id: int
    upload_scans_path: str
    ocr_output_path: str


@router.get("/data-folders", response_model=DataFoldersResponse)
def get_data_folders(
    principal: Principal = Depends(get_principal),
    dealer_id: int | None = Query(None, description="Defaults to token dealer when omitted."),
) -> DataFoldersResponse:
    """Resolved absolute paths for dealer-scoped Upload Scans and ocr_output folders."""
    did = resolve_dealer_id(principal, dealer_id)
    uploads = get_uploads_dir(did).resolve()
    ocr = get_ocr_output_dir(did).resolve()
    return DataFoldersResponse(
        dealer_id=did,
        upload_scans_path=str(uploads),
        ocr_output_path=str(ocr),
    )


AdminFolderRoot = Literal["upload_scans", "ocr_output"]


def _admin_folder_base(root: AdminFolderRoot, did: int) -> Path:
    if root == "upload_scans":
        return get_uploads_dir(did)
    return get_ocr_output_dir(did)


def _resolve_under_dealer_root(base: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``base``; reject ``..`` and escapes."""
    base_resolved = base.resolve()
    rel = (rel or "").strip().replace("\\", "/")
    if not rel:
        return base_resolved
    parts = [p for p in rel.split("/") if p and p != "."]
    for p in parts:
        if p == "..":
            raise HTTPException(status_code=400, detail="Invalid path")
    target = base_resolved.joinpath(*parts) if parts else base_resolved
    target = target.resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid path") from e
    return target


class FolderEntry(BaseModel):
    name: str
    kind: Literal["file", "dir"]
    size: int | None = None
    modified_at: str = Field(..., description="UTC ISO 8601 from filesystem mtime")


class FolderListResponse(BaseModel):
    root: str
    rel_path: str
    dealer_id: int
    current_folder_abs: str
    items: list[FolderEntry]


@router.get("/folder-contents", response_model=FolderListResponse)
def list_admin_folder_contents(
    principal: Principal = Depends(get_principal),
    root: AdminFolderRoot = Query(..., description="upload_scans or ocr_output"),
    rel_path: str = Query("", description="Path under the dealer folder, / separators"),
    dealer_id: int | None = Query(None, description="Defaults to token dealer when omitted."),
) -> FolderListResponse:
    """List files and subfolders under the dealer Upload Scans or ocr_output tree."""
    did = resolve_dealer_id(principal, dealer_id)
    base = _admin_folder_base(root, did)
    folder = _resolve_under_dealer_root(base, rel_path)
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")

    rows: list[tuple[Path, float, int | None]] = []
    for f in folder.iterdir():
        if f.name.startswith("."):
            continue
        st = f.stat()
        size = None if f.is_dir() else st.st_size
        rows.append((f, st.st_mtime, size))
    rows.sort(key=lambda x: x[1], reverse=True)

    items: list[FolderEntry] = []
    for f, mtime_ts, size in rows:
        mt = datetime.fromtimestamp(mtime_ts, tz=timezone.utc).isoformat()
        if f.is_dir():
            items.append(FolderEntry(name=f.name, kind="dir", modified_at=mt))
        else:
            items.append(FolderEntry(name=f.name, kind="file", size=size, modified_at=mt))
    rel_norm = rel_path.strip().replace("\\", "/")
    return FolderListResponse(
        root=root,
        rel_path=rel_norm,
        dealer_id=did,
        current_folder_abs=str(folder.resolve()),
        items=items,
    )


@router.get("/folder-file")
def get_admin_folder_file(
    principal: Principal = Depends(get_principal),
    root: AdminFolderRoot = Query(...),
    path: str = Query(..., description="File path relative to dealer upload/ocr root"),
    dealer_id: int | None = Query(None, description="Defaults to token dealer when omitted."),
) -> FileResponse:
    """Serve a file from under the dealer Upload Scans or ocr_output tree (read-only)."""
    did = resolve_dealer_id(principal, dealer_id)
    base = _admin_folder_base(root, did)
    file_path = _resolve_under_dealer_root(base, path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, filename=file_path.name, content_disposition_type="inline")


@router.post("/reset-all-data")
def reset_all_data(payload: ResetAllDataRequest) -> dict:
    """Delete all public base-table rows except *ref tables (LIKE '%ref') and PRESERVED_EXTRA_TABLES."""
    if payload.confirmation != CONFIRMATION_TEXT:
        raise HTTPException(status_code=400, detail="Invalid confirmation text")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                  AND NOT (
                    table_name LIKE %s
                    OR table_name = ANY(%s::text[])
                  )
                ORDER BY table_name
                """,
                (PRESERVED_LIKE_SUFFIX, list(PRESERVED_EXTRA_TABLES)),
            )
            table_names = [row["table_name"] for row in cur.fetchall()]

            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                  AND (
                    table_name LIKE %s
                    OR table_name = ANY(%s::text[])
                  )
                ORDER BY table_name
                """,
                (PRESERVED_LIKE_SUFFIX, list(PRESERVED_EXTRA_TABLES)),
            )
            preserved_names = [row["table_name"] for row in cur.fetchall()]

            if table_names:
                truncate_sql = sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(
                    sql.SQL(", ").join(sql.Identifier(table_name) for table_name in table_names)
                )
                cur.execute(truncate_sql)

        conn.commit()
        return {
            "ok": True,
            "message": f"Deleted data from {len(table_names)} table(s).",
            "truncated_count": len(table_names),
            "truncated_tables": table_names,
            "preserved_tables": preserved_names,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- Admin Saathi: dealers (dealer_ref, login_roles_ref, subdealer_discount_master) ---


class CreateDealerRequest(BaseModel):
    dealer_name: str = Field(..., min_length=1, max_length=255)
    oem_id: int | None = None
    parent_id: int | None = None


class UpdateDealerRefInsurerRequest(BaseModel):
    """Both fields required so a partial JSON body cannot clear ``prefer_insurer`` by omission."""

    prefer_insurer: str | None
    hero_cpi: Literal["Y", "N"]


class LoginActiveFlagRequest(BaseModel):
    active_flag: Literal["Y", "N"]


class AddDealerLoginRoleRequest(BaseModel):
    login_id: str = Field(..., min_length=1, max_length=128)
    role_id: int


class CreateDiscountRequest(BaseModel):
    model: str = Field(..., min_length=1, max_length=64)
    discount: float | None = None
    create_date: str | None = Field(None, max_length=20)
    valid_flag: Literal["Y", "N"] = "Y"


class LoginAssignmentUpsertItem(BaseModel):
    """``login_roles_ref_id`` null = insert; else update role for that row. ``active_flag`` updates ``login_ref``."""

    login_roles_ref_id: int | None = None
    login_id: str = Field(..., min_length=1, max_length=128)
    role_id: int
    active_flag: Literal["Y", "N"]


class LoginAssignmentsUpsertRequest(BaseModel):
    rows: list[LoginAssignmentUpsertItem]


@router.get("/dealers")
def list_dealers_for_admin() -> list[dict]:
    """All dealers for Admin dropdown: ``dealer_id``, ``dealer_name``."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dealer_id, dealer_name
                FROM dealer_ref
                ORDER BY dealer_name ASC
                """
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    return [_jsonable_row(dict(r)) for r in rows]


@router.post("/dealers")
def create_dealer(payload: CreateDealerRequest) -> dict:
    """Insert a minimal ``dealer_ref`` row (``hero_cpi`` default ``N``)."""
    name = payload.dealer_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="dealer_name is required")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if payload.oem_id is not None:
                cur.execute("SELECT 1 FROM oem_ref WHERE oem_id = %s", (payload.oem_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=400, detail="oem_id does not exist in oem_ref")
            if payload.parent_id is not None:
                cur.execute("SELECT 1 FROM dealer_ref WHERE dealer_id = %s", (payload.parent_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=400, detail="parent_id does not exist in dealer_ref")

            cur.execute(
                """
                INSERT INTO dealer_ref (dealer_name, oem_id, parent_id, hero_cpi)
                VALUES (%s, %s, %s, 'N')
                RETURNING *
                """,
                (name, payload.oem_id, payload.parent_id),
            )
            row = cur.fetchone()
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return _jsonable_row(dict(row))


@router.get("/dealers/{dealer_id:int}")
def get_dealer_ref_full(dealer_id: int) -> dict:
    """``dealer_ref`` for Admin UI: ``oem_name`` instead of ``oem_id``; no ``dealer_id`` / ``city`` / ``state``."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            out = _dealer_admin_detail_dict(cur, dealer_id)
    finally:
        conn.close()
    if not out:
        raise HTTPException(status_code=404, detail="Dealer not found")
    return out


@router.patch("/dealers/{dealer_id:int}")
def patch_dealer_ref_insurer_cpi(dealer_id: int, payload: UpdateDealerRefInsurerRequest) -> dict:
    """Update ``prefer_insurer`` and ``hero_cpi`` on ``dealer_ref``. Send both fields (current values)."""
    pi = payload.prefer_insurer
    if pi is not None:
        pi = pi.strip() or None
        if pi is not None and len(pi) > 255:
            raise HTTPException(status_code=400, detail="prefer_insurer must be at most 255 characters")

    conn = get_connection()
    out: dict | None = None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM dealer_ref WHERE dealer_id = %s", (dealer_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Dealer not found")
            cur.execute(
                """
                UPDATE dealer_ref
                SET prefer_insurer = %s, hero_cpi = %s
                WHERE dealer_id = %s
                """,
                (pi, payload.hero_cpi, dealer_id),
            )
        conn.commit()
        with conn.cursor() as cur:
            out = _dealer_admin_detail_dict(cur, dealer_id)
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if not out:
        raise HTTPException(status_code=500, detail="Dealer row missing after update")
    return out


@router.get("/dealers/{dealer_id:int}/logins")
def list_dealer_login_assignments(dealer_id: int) -> list[dict]:
    """
    ``login_ref`` joined with ``login_roles_ref`` and ``roles_ref`` where
    ``login_roles_ref.dealer_id`` matches *dealer_id*. Password hash is omitted.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM dealer_ref WHERE dealer_id = %s", (dealer_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Dealer not found")

            cur.execute(
                """
                SELECT
                    lr.login_id,
                    lr.name AS login_display_name,
                    lr.phone AS login_phone,
                    lr.email AS login_email,
                    lr.active_flag AS login_active_flag,
                    lrr.login_roles_ref_id,
                    lrr.role_id,
                    rr.role_name
                FROM login_ref lr
                INNER JOIN login_roles_ref lrr ON lrr.login_id = lr.login_id
                LEFT JOIN roles_ref rr ON rr.role_id = lrr.role_id
                WHERE lrr.dealer_id = %s
                ORDER BY lr.login_id, lrr.login_roles_ref_id
                """,
                (dealer_id,),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()

    return [_jsonable_row(dict(r)) for r in rows]


@router.put("/dealers/{dealer_id:int}/login-assignments/upsert")
def upsert_login_assignments(dealer_id: int, payload: LoginAssignmentsUpsertRequest) -> list[dict]:
    """Insert or update ``login_roles_ref`` rows for this dealer and sync ``login_ref.active_flag`` per row."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM dealer_ref WHERE dealer_id = %s", (dealer_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Dealer not found")

            for item in payload.rows:
                lid = item.login_id.strip()
                if not lid:
                    raise HTTPException(status_code=400, detail="login_id is required")

                cur.execute("SELECT 1 FROM login_ref WHERE login_id = %s", (lid,))
                if not cur.fetchone():
                    raise HTTPException(status_code=400, detail=f"login_id not in login_ref: {lid!r}")

                cur.execute("SELECT 1 FROM roles_ref WHERE role_id = %s", (item.role_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=400, detail="role_id does not exist in roles_ref")

                if item.login_roles_ref_id is None:
                    try:
                        cur.execute(
                            """
                            INSERT INTO login_roles_ref (login_id, role_id, dealer_id)
                            VALUES (%s, %s, %s)
                            """,
                            (lid, int(item.role_id), dealer_id),
                        )
                    except IntegrityError as exc:
                        if getattr(exc, "pgcode", None) == "23505":
                            raise HTTPException(
                                status_code=409,
                                detail="This login already has this role for this dealer.",
                            ) from exc
                        raise
                else:
                    cur.execute(
                        """
                        UPDATE login_roles_ref
                        SET role_id = %s
                        WHERE login_roles_ref_id = %s AND dealer_id = %s
                        """,
                        (int(item.role_id), int(item.login_roles_ref_id), dealer_id),
                    )
                    if cur.rowcount == 0:
                        raise HTTPException(
                            status_code=404,
                            detail=f"login_roles_ref_id {item.login_roles_ref_id} not found for this dealer",
                        )

                cur.execute(
                    "UPDATE login_ref SET active_flag = %s WHERE login_id = %s",
                    (item.active_flag, lid),
                )

        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return list_dealer_login_assignments(dealer_id)


@router.get("/roles")
def list_roles_for_admin() -> list[dict]:
    """All ``roles_ref`` rows for Admin pickers."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT role_id, role_name
                FROM roles_ref
                ORDER BY role_name ASC
                """
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    return [_jsonable_row(dict(r)) for r in rows]


@router.get("/login-catalog")
def list_logins_for_admin() -> list[dict]:
    """All ``login_ref`` login ids and display names for Admin pickers."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT login_id, name AS display_name
                FROM login_ref
                ORDER BY login_id ASC
                """
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    return [_jsonable_row(dict(r)) for r in rows]


@router.patch("/logins/{login_id}/active-flag")
def patch_login_active_flag(login_id: str, payload: LoginActiveFlagRequest) -> dict:
    """Update ``login_ref.active_flag``."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE login_ref
                SET active_flag = %s
                WHERE login_id = %s
                RETURNING login_id, name, phone, email, active_flag
                """,
                (payload.active_flag, login_id),
            )
            row = cur.fetchone()
        if not row:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Login not found")
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return _jsonable_row(dict(row))


@router.post("/dealers/{dealer_id:int}/login-roles", status_code=201)
def add_dealer_login_role(dealer_id: int, payload: AddDealerLoginRoleRequest) -> dict:
    """Insert ``login_roles_ref`` for this dealer (``login_id``, ``role_id``, ``dealer_id``)."""
    lid = payload.login_id.strip()
    if not lid:
        raise HTTPException(status_code=400, detail="login_id is required")

    conn = get_connection()
    ins = None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM dealer_ref WHERE dealer_id = %s", (dealer_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Dealer not found")
            cur.execute("SELECT 1 FROM login_ref WHERE login_id = %s", (lid,))
            if not cur.fetchone():
                raise HTTPException(status_code=400, detail="login_id does not exist in login_ref")
            cur.execute("SELECT 1 FROM roles_ref WHERE role_id = %s", (payload.role_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=400, detail="role_id does not exist in roles_ref")

            try:
                cur.execute(
                    """
                    INSERT INTO login_roles_ref (login_id, role_id, dealer_id)
                    VALUES (%s, %s, %s)
                    RETURNING login_roles_ref_id, login_id, role_id, dealer_id
                    """,
                    (lid, int(payload.role_id), dealer_id),
                )
                ins = cur.fetchone()
            except IntegrityError as exc:
                conn.rollback()
                if getattr(exc, "pgcode", None) == "23505":
                    raise HTTPException(
                        status_code=409,
                        detail="This login already has this role for this dealer.",
                    ) from exc
                raise
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if not ins:
        raise HTTPException(status_code=500, detail="Insert did not return a row")
    return _jsonable_row(dict(ins))


@router.get("/dealers/{dealer_id:int}/discounts")
def list_dealer_discounts(dealer_id: int) -> list[dict]:
    """Per-dealer model discounts from ``subdealer_discount_master`` (no separate ``discounts`` table in schema)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM dealer_ref WHERE dealer_id = %s", (dealer_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Dealer not found")

            cur.execute(
                """
                SELECT subdealer_discount_id, dealer_id, model, discount, create_date, valid_flag
                FROM subdealer_discount_master
                WHERE dealer_id = %s
                ORDER BY model ASC
                """,
                (dealer_id,),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()

    return [_jsonable_row(dict(r)) for r in rows]


@router.post("/dealers/{dealer_id:int}/discounts", status_code=201)
def create_dealer_discount(dealer_id: int, payload: CreateDiscountRequest) -> dict:
    """Insert one ``subdealer_discount_master`` row for the dealer."""
    m = payload.model.strip()
    if not m:
        raise HTTPException(status_code=400, detail="model is required")

    cd = payload.create_date
    if cd is not None:
        cd = cd.strip() or None

    vf = payload.valid_flag
    if vf not in ("Y", "N"):
        raise HTTPException(status_code=400, detail="valid_flag must be Y or N")

    disc = payload.discount
    if disc is not None and disc < 0:
        raise HTTPException(status_code=400, detail="discount must be non-negative")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM dealer_ref WHERE dealer_id = %s", (dealer_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Dealer not found")

            cur.execute(
                """
                INSERT INTO subdealer_discount_master (dealer_id, model, discount, create_date, valid_flag)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING subdealer_discount_id, dealer_id, model, discount, create_date, valid_flag
                """,
                (dealer_id, m, disc, cd, vf),
            )
            row = cur.fetchone()
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=500, detail="Insert did not return a row")
    return _jsonable_row(dict(row))
