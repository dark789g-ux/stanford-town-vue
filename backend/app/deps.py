"""FastAPI dependency providers."""

from __future__ import annotations

from typing import Iterator

from fastapi import Depends
from sqlalchemy.orm import Session

from config.settings import Settings, get_settings as _get_settings
from runner.manager import SimulationManager, manager_singleton
from storage.db import SessionLocal
from storage.repos import Repos, make_repos


def get_settings() -> Settings:
    """Cached Settings dependency."""
    return _get_settings()


def get_db() -> Iterator[Session]:
    """Yield a SQLAlchemy session scoped to a single request."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_repos(db: Session = Depends(get_db)) -> Repos:
    """Build the per-request Repos bundle from a Session.

    ``fernet_key`` is intentionally ``None`` here — the LLM profile agent
    injects the real key via override when encryption is needed.
    """
    return make_repos(db)


def get_manager() -> SimulationManager:
    """Return the SimulationManager singleton.

    During Wave 1 this is a stub manager; M3 replaces the singleton with the
    real instance backed by worker threads.
    """
    return manager_singleton
