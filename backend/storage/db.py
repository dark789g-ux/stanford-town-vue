"""SQLAlchemy engine + session bootstrap."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config.settings import get_settings

_settings = get_settings()

# SQLite needs check_same_thread=False so the FastAPI dependency injection
# (which may dispatch sessions across threadpool workers) works.
_engine_kwargs: dict = {"future": True}
if _settings.database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(_settings.database_url, **_engine_kwargs)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


@contextmanager
def get_session() -> Iterator[Session]:
    """Context-manager session helper for non-FastAPI callers."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
