"""Smoke tests for the backend skeleton."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Ensure `app`, `storage`, etc. are importable when tests run from the repo root.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.main import app  # noqa: E402


def test_app_is_fastapi_instance() -> None:
    assert isinstance(app, FastAPI)


def test_health_endpoint_returns_ok() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "ok"
    assert isinstance(body.get("version"), str) and body["version"]
