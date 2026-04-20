from fastapi import APIRouter

from app.version import BACKEND_SEMVER, GIT_COMMIT_SHORT

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict:
    return {
        "status": "ok",
        "version": BACKEND_SEMVER,
        "git_commit": GIT_COMMIT_SHORT,
    }
