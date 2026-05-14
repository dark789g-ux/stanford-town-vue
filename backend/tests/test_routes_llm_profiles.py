"""Integration tests for /api/llm-profiles routes."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

# Make backend/ importable when pytest runs from the repo root.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.deps import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.routes.llm_profiles import get_llm_profile_repo  # noqa: E402
from storage.db import Base  # noqa: E402
from storage.repos.llm_profiles import LlmProfileRepo  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fernet_key() -> bytes:
    return Fernet.generate_key()


@pytest.fixture()
def session_factory():
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    yield SessionLocal
    engine.dispose()


class _Ctx:
    """Holds the persistent session + key shared between client and repo fixtures."""

    def __init__(self, session: Session, fernet_key: bytes) -> None:
        self.session = session
        self.fernet_key = fernet_key

    def repo(self) -> LlmProfileRepo:
        return LlmProfileRepo(self.session, self.fernet_key)


@pytest.fixture()
def ctx(session_factory, fernet_key) -> Iterator[_Ctx]:
    persistent_session = session_factory()
    try:
        yield _Ctx(persistent_session, fernet_key)
    finally:
        persistent_session.close()


@pytest.fixture()
def client(ctx) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        try:
            yield ctx.session
        finally:
            pass

    def override_get_llm_profile_repo() -> LlmProfileRepo:
        return ctx.repo()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_llm_profile_repo] = override_get_llm_profile_repo
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_llm_profile_repo, None)


@pytest.fixture()
def client_repo(client, ctx) -> LlmProfileRepo:
    """Repo bound to the SAME session as the client's override."""
    return ctx.repo()


# ---------------------------------------------------------------------------
# CRUD tests
# ---------------------------------------------------------------------------


def _create_payload(**overrides):
    base = {
        "name": "default-openai",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key": "sk-test-123",
        "max_tokens": 256,
        "temperature": 0.2,
    }
    base.update(overrides)
    return base


def test_create_and_list_profile(client):
    r = client.post("/api/llm-profiles", json=_create_payload())
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["name"] == "default-openai"
    assert "api_key" not in created  # redacted

    lst = client.get("/api/llm-profiles")
    assert lst.status_code == 200
    rows = lst.json()
    assert len(rows) == 1
    assert rows[0]["id"] == created["id"]
    assert "api_key" not in rows[0]


def test_duplicate_name_returns_409(client):
    r1 = client.post("/api/llm-profiles", json=_create_payload())
    assert r1.status_code == 201
    r2 = client.post("/api/llm-profiles", json=_create_payload())
    assert r2.status_code == 409


def test_list_redacts_api_key(client):
    client.post("/api/llm-profiles", json=_create_payload())
    body = client.get("/api/llm-profiles").json()
    for row in body:
        assert "api_key" not in row


def test_update_profile_fields(client, client_repo):
    created = client.post("/api/llm-profiles", json=_create_payload()).json()
    pid = created["id"]

    r = client.put(
        f"/api/llm-profiles/{pid}",
        json={"name": "renamed", "temperature": 0.9, "api_key": "sk-new-456"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "renamed"
    assert body["temperature"] == 0.9

    # Verify the key was re-encrypted via the repo (not exposed in API).
    decrypted = client_repo.get_decrypted_key(pid)
    assert decrypted == "sk-new-456"


def test_update_missing_returns_404(client):
    r = client.put("/api/llm-profiles/9999", json={"name": "x"})
    assert r.status_code == 404


def test_delete_removes_profile(client):
    created = client.post("/api/llm-profiles", json=_create_payload()).json()
    pid = created["id"]
    r = client.delete(f"/api/llm-profiles/{pid}")
    assert r.status_code == 204
    assert client.get("/api/llm-profiles").json() == []


def test_delete_missing_returns_404(client):
    assert client.delete("/api/llm-profiles/9999").status_code == 404


# ---------------------------------------------------------------------------
# /test endpoint with mocked OpenAI client
# ---------------------------------------------------------------------------


def _mock_openai_response(text: str = "pong") -> MagicMock:
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_test_endpoint_success(client):
    created = client.post("/api/llm-profiles", json=_create_payload()).json()
    pid = created["id"]

    with patch("openai.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response("pong")
        mock_cls.return_value = mock_client

        r = client.post(f"/api/llm-profiles/{pid}/test")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["sample_response"] == "pong"
    assert body["error"] is None
    assert body["model"] == "gpt-4o-mini"


def test_test_endpoint_failure(client):
    created = client.post("/api/llm-profiles", json=_create_payload()).json()
    pid = created["id"]

    with patch("openai.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("boom")
        mock_cls.return_value = mock_client

        r = client.post(f"/api/llm-profiles/{pid}/test")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["error"] is not None
    assert "boom" in body["error"]


def test_test_endpoint_unknown_profile_returns_404(client):
    r = client.post("/api/llm-profiles/9999/test")
    assert r.status_code == 404
