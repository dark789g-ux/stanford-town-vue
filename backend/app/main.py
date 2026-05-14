"""FastAPI application entrypoint for stanford-town-vue."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

# Make `app`, `storage`, etc. importable when running `uvicorn app.main:app`
# from the `backend/` directory without an editable install.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.routes import all_routers  # noqa: E402
from app.ws import router as ws_router  # noqa: E402
from config.settings import bootstrap_secret_key, get_settings  # noqa: E402
from runner.manager import manager_singleton  # noqa: E402


def _configure_logging() -> None:
    """Configure loguru sinks. Idempotent across hot-reloads."""
    settings = get_settings()
    logs_dir = settings.expanded_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    logger.add(
        logs_dir / "backend.log",
        rotation="10 MB",
        retention="14 days",
        level="DEBUG",
        enqueue=True,
    )


def _run_alembic_upgrade() -> None:
    """Apply pending Alembic migrations on startup."""
    try:
        from alembic import command
        from alembic.config import Config

        ini_path = _BACKEND_DIR / "alembic.ini"
        if not ini_path.exists():
            logger.warning("alembic.ini not found at {}; skipping migrations", ini_path)
            return
        cfg = Config(str(ini_path))
        # Ensure script_location resolves relative to backend/, not cwd.
        cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
        command.upgrade(cfg, "head")
        logger.info("Alembic upgrade head: OK")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Alembic upgrade failed: {}", exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _configure_logging()
    logger.info("stanford-town-vue starting")
    _run_alembic_upgrade()
    try:
        bootstrap_secret_key(get_settings().expanded_secret_key_path())
    except Exception as exc:  # noqa: BLE001
        logger.exception("bootstrap_secret_key failed: {}", exc)
    try:
        from runner.bootstrap import bootstrap_runner

        bootstrap_runner()
    except Exception as exc:  # noqa: BLE001
        logger.exception("runner bootstrap failed: {}", exc)
    try:
        manager_singleton.scan_interrupted()
    except Exception as exc:  # noqa: BLE001
        logger.exception("scan_interrupted failed: {}", exc)
    yield
    logger.info("stanford-town-vue shutting down")


app = FastAPI(
    title="stanford-town-vue",
    version="0.0.1",
    description="Backend for the Vue3 + AntD reimplementation of Stanford Town generative agents.",
    lifespan=lifespan,
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        _settings.frontend_dev_origin,
        "http://localhost:5173",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount HTTP routers (each module owns its own prefix).
for _router in all_routers:
    app.include_router(_router)

# Mount the WebSocket hub.
app.include_router(ws_router)

# Optional static mounts — only enabled if the directories exist so test
# runs in a fresh checkout don't fail.
_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

_assets_dir = _BACKEND_DIR / "assets"
if _assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")
