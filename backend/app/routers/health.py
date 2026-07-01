from fastapi import APIRouter

from app.db import DATABASE_URL, get_connection
from app.version import BACKEND_SEMVER, GIT_COMMIT_SHORT

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict:
    checks: dict[str, object] = {}
    if DATABASE_URL:
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT COUNT(*)::int AS n
                        FROM insurance_addon_ref
                        WHERE active_flag = 'Y'
                        """
                    )
                    row = cur.fetchone()
                    n = int(row["n"] if isinstance(row, dict) else row[0]) if row else 0
                checks["insurance_addon_ref"] = {
                    "ok": n > 0,
                    "active_preset_count": n,
                }
            finally:
                conn.close()
        except Exception as exc:
            checks["insurance_addon_ref"] = {"ok": False, "error": str(exc)}
    return {
        "status": "ok",
        "version": BACKEND_SEMVER,
        "git_commit": GIT_COMMIT_SHORT,
        "checks": checks or None,
    }
