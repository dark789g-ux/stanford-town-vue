"""Integration tests for /api/sims/{sim_id}/llm-logs."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.deps import get_db  # noqa: E402
from app.main import app  # noqa: E402
from storage.db import Base  # noqa: E402
from storage.models import SimulationStatus  # noqa: E402
from storage.repos import make_repos  # noqa: E402


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    yield SessionLocal
    engine.dispose()


@pytest.fixture()
def client(session_factory) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def seeded(session_factory):
    """Seed a simulation with 10 LLM calls; return (sim_id, call_ids)."""
    s = session_factory()
    try:
        repos = make_repos(s)
        sim = repos.simulations.create(
            "log_sim",
            status=SimulationStatus.IDLE,
            start_time_iso="2025-01-01T00:00:00",
            curr_time_iso="2025-01-01T00:00:00",
            n_round=10,
        )
        base_ts = datetime(2025, 1, 1, 0, 0, 0)
        rows = [
            {
                "persona_name": "Isabella" if i % 2 == 0 else "Klaus",
                "step": i,
                "ts": base_ts,
                "model": "deepseek-chat" if i < 5 else "gpt-4o-mini",
                "provider": "deepseek" if i < 5 else "openai",
                "prompt": f"prompt-{i}",
                "response": f"response-{i}",
                "prompt_tokens": i,
                "completion_tokens": i,
                "latency_ms": 10 * i,
                "error": None,
            }
            for i in range(10)
        ]
        repos.llm_logs.add_bulk(sim.id, rows)
        # Re-list to capture IDs
        all_rows = repos.llm_logs.list(sim.id, limit=100)
        yield sim.id, [r.id for r in all_rows]
    finally:
        s.close()


def test_list_pagination(client, seeded):
    sim_id, _ = seeded
    r = client.get(f"/api/sims/{sim_id}/llm-logs", params={"limit": 5, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 10
    assert body["limit"] == 5
    assert body["offset"] == 0
    assert len(body["items"]) == 5

    r2 = client.get(f"/api/sims/{sim_id}/llm-logs", params={"limit": 5, "offset": 5})
    body2 = r2.json()
    assert len(body2["items"]) == 5
    ids1 = {it["id"] for it in body["items"]}
    ids2 = {it["id"] for it in body2["items"]}
    assert ids1.isdisjoint(ids2)


def test_list_filter_by_persona(client, seeded):
    sim_id, _ = seeded
    r = client.get(
        f"/api/sims/{sim_id}/llm-logs", params={"persona": "Isabella", "limit": 100}
    )
    body = r.json()
    assert all(it["persona_name"] == "Isabella" for it in body["items"])
    assert len(body["items"]) == 5  # i=0,2,4,6,8


def test_list_filter_by_model(client, seeded):
    sim_id, _ = seeded
    r = client.get(
        f"/api/sims/{sim_id}/llm-logs", params={"model": "gpt-4o-mini", "limit": 100}
    )
    body = r.json()
    assert {it["model"] for it in body["items"]} == {"gpt-4o-mini"}
    assert len(body["items"]) == 5


def test_get_detail_returns_prompt_and_response(client, seeded):
    sim_id, call_ids = seeded
    cid = call_ids[0]
    r = client.get(f"/api/sims/{sim_id}/llm-logs/{cid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == cid
    assert "prompt" in body and body["prompt"].startswith("prompt-")
    assert "response" in body and body["response"].startswith("response-")


def test_get_detail_missing_call_404(client, seeded):
    sim_id, _ = seeded
    r = client.get(f"/api/sims/{sim_id}/llm-logs/9999999")
    assert r.status_code == 404


def test_list_sim_missing_404(client):
    r = client.get("/api/sims/9999/llm-logs")
    assert r.status_code == 404
