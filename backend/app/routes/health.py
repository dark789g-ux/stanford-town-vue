"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

try:  # importlib.metadata is stdlib on 3.8+; resolve the installed version if any
    from importlib.metadata import PackageNotFoundError, version as _pkg_version
except ImportError:  # pragma: no cover - defensive
    _pkg_version = None  # type: ignore[assignment]
    PackageNotFoundError = Exception  # type: ignore[assignment,misc]


def _resolve_version() -> str:
    if _pkg_version is None:
        return "dev"
    try:
        return _pkg_version("stanford-town-vue-backend")
    except PackageNotFoundError:
        return "dev"
    except Exception:  # noqa: BLE001 - any metadata error -> dev
        return "dev"


router = APIRouter(prefix="/api", tags=["health"])


class HealthOut(BaseModel):
    status: str = "ok"
    version: str


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok", version=_resolve_version())
