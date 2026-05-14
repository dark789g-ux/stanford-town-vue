"""Tests for /api/config/effective."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.main import app  # noqa: E402
from config.settings import get_settings, redact_db_url  # noqa: E402


def test_effective_config_returns_expected_fields() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/config/effective")
    assert resp.status_code == 200
    body = resp.json()

    expected_keys = {
        "database_url",
        "assets_dir",
        "logs_dir",
        "frontend_dev_origin",
        "secret_key_present",
        "llm_profiles_count",
    }
    assert expected_keys.issubset(body.keys())

    settings = get_settings()
    assert body["assets_dir"] == settings.assets_dir
    assert body["logs_dir"] == settings.logs_dir
    assert body["frontend_dev_origin"] == settings.frontend_dev_origin
    assert isinstance(body["secret_key_present"], bool)
    assert isinstance(body["llm_profiles_count"], int)
    assert body["llm_profiles_count"] >= 0
    # main.py lifespan generates the key on startup; TestClient's `with` block
    # invokes lifespan, so the file should exist now.
    assert body["secret_key_present"] is True


def test_effective_config_redacts_password() -> None:
    raw = "postgresql://alice:hunter2@db.example.com:5432/town"
    assert redact_db_url(raw) == "postgresql://alice:***@db.example.com:5432/town"
    # No credentials -> untouched
    plain = "sqlite:///./data/stanford_town.db"
    assert redact_db_url(plain) == plain
