"""Config-related endpoints.

Currently exposes a single read-only ``GET /api/config/effective`` summary
of the resolved runtime configuration. Future write endpoints will live in
this module too.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import get_db, get_settings
from config.settings import Settings, redact_db_url
from storage.models import LlmProfile

router = APIRouter(prefix="/api/config", tags=["config"])


class EffectiveConfigOut(BaseModel):
    database_url: str
    assets_dir: str
    logs_dir: str
    frontend_dev_origin: str
    secret_key_present: bool
    llm_profiles_count: int


@router.get("/effective", response_model=EffectiveConfigOut)
def effective_config(
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> EffectiveConfigOut:
    """Return the resolved runtime configuration with secrets redacted."""
    try:
        count = int(db.scalar(select(func.count()).select_from(LlmProfile)) or 0)
    except Exception:  # noqa: BLE001 - table may not exist yet; count as 0
        count = 0

    return EffectiveConfigOut(
        database_url=redact_db_url(settings.database_url),
        assets_dir=settings.assets_dir,
        logs_dir=settings.logs_dir,
        frontend_dev_origin=settings.frontend_dev_origin,
        secret_key_present=settings.expanded_secret_key_path().exists(),
        llm_profiles_count=count,
    )
