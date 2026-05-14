"""Session-factory builders for the runner package.

The :class:`SimulationManager` is constructed with an injected
``sessionmaker`` so it can be unit-tested against in-memory SQLite. This
module provides the production-default factory wired to settings.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config.settings import get_settings


def build_session_factory(database_url: str | None = None) -> sessionmaker:
    """Return a ``sessionmaker`` bound to a fresh engine for ``database_url``.

    When ``database_url`` is ``None`` the URL is taken from the active
    :class:`config.settings.Settings`. SQLite gets the standard
    ``check_same_thread=False`` connect arg so sessions can be used from
    threadpool workers.
    """
    if database_url is None:
        database_url = get_settings().database_url

    engine_kwargs: dict = {"future": True}
    if database_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(database_url, **engine_kwargs)
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


__all__ = ["build_session_factory"]
