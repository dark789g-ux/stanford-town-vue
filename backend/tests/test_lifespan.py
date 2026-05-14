"""Smoke test for the FastAPI lifespan handler.

``starlette.testclient.TestClient`` used as a context manager runs the
real lifespan startup/shutdown — unlike a bare ``ASGITransport`` client.
This test verifies the lifespan actually wires the live StanfordTown
runner into ``manager_singleton`` via ``bootstrap_runner()``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.main import app  # noqa: E402
from runner.manager import _default_noop_runner, manager_singleton  # noqa: E402


def test_lifespan_wires_real_runner():
    """Entering the TestClient context runs lifespan -> bootstrap_runner."""
    # Sanity: before the app starts, the manager holds the no-op default.
    # (Import order means this may already be swapped if another test ran
    # the lifespan first; assert the post-condition either way.)
    with TestClient(app) as client:
        # Lifespan has run. The manager must now hold the real runner.
        from runner.st_runner import stanford_town_runner

        assert manager_singleton.runner is stanford_town_runner
        assert manager_singleton.runner is not _default_noop_runner
        # A production session factory must be wired too.
        assert manager_singleton._session_factory is not None

        # The app is otherwise live.
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
