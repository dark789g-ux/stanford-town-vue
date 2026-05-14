"""Integration tests for /api/sims routes — in-memory SQLite."""

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

# Make backend/ importable when pytest is run from the repo root.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.deps import get_db  # noqa: E402
from app.main import app  # noqa: E402
from storage.db import Base  # noqa: E402
from storage.models import MemoryNodeType, SimulationStatus  # noqa: E402
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
def repos(session_factory):
    s = session_factory()
    try:
        yield make_repos(s)
    finally:
        s.close()


def _seed_sim(repos, sim_code="sim_a", status=SimulationStatus.IDLE):
    return repos.simulations.create(
        sim_code,
        status=status,
        start_time_iso="2025-01-01T07:00:00",
        curr_time_iso="2025-01-01T07:00:00",
        n_round=200,
    )


# ---------------------------------------------------------------- lifecycle


def test_post_creates_simulation(client):
    resp = client.post(
        "/api/sims",
        json={
            "sim_code": "first",
            "n_round": 10,
            "start_hms": "08:30:00",
            "personas": ["Isabella"],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sim_code"] == "first"
    assert body["status"] == "idle"
    assert body["n_round"] == 10
    assert body["start_time_iso"].endswith("T08:30:00")


def test_post_duplicate_returns_409(client):
    payload = {"sim_code": "dup", "n_round": 5}
    r1 = client.post("/api/sims", json=payload)
    assert r1.status_code == 201
    r2 = client.post("/api/sims", json=payload)
    assert r2.status_code == 409


def test_post_invalid_hms_returns_400(client):
    r = client.post("/api/sims", json={"sim_code": "bad_hms", "start_hms": "99:99:99"})
    assert r.status_code == 400


def _seed_completed_fork(repos, sim_code="base_fork", personas=("Isabella", "Klaus")):
    """A completed sim with personas — resolvable by ``fork_persona_names``."""
    sim = _seed_sim(repos, sim_code, status=SimulationStatus.COMPLETED)
    for name in personas:
        repos.personas.create(sim.id, name)
    return sim


def test_post_rejects_unknown_persona(client, repos):
    _seed_completed_fork(repos)
    r = client.post(
        "/api/sims",
        json={
            "sim_code": "bad_personas",
            "fork_sim_code": "base_fork",
            "personas": ["Isabella", "Nobody"],
        },
    )
    assert r.status_code == 400
    assert "Nobody" in r.json()["detail"]


def test_post_rejects_inner_voice_not_in_personas(client, repos):
    _seed_completed_fork(repos)
    r = client.post(
        "/api/sims",
        json={
            "sim_code": "bad_iv",
            "fork_sim_code": "base_fork",
            "inner_voice": "我要吃饭",
        },
    )
    assert r.status_code == 400
    assert "inner_voice" in r.json()["detail"]


def test_post_rejects_inner_voice_outside_persona_subset(client, repos):
    """inner_voice must be in the *included* subset, not just the fork."""
    _seed_completed_fork(repos)
    r = client.post(
        "/api/sims",
        json={
            "sim_code": "iv_outside_subset",
            "fork_sim_code": "base_fork",
            "personas": ["Isabella"],
            "inner_voice": "Klaus",  # in fork, but not in the chosen subset
        },
    )
    assert r.status_code == 400


def test_post_accepts_valid_fork_config(client, repos):
    _seed_completed_fork(repos)
    r = client.post(
        "/api/sims",
        json={
            "sim_code": "good_config",
            "fork_sim_code": "base_fork",
            "personas": ["Isabella", "Klaus"],
            "inner_voice": "Klaus",
        },
    )
    assert r.status_code == 201, r.text


def test_get_list_returns_inserted(client, repos):
    _seed_sim(repos, "alpha")
    _seed_sim(repos, "beta")
    resp = client.get("/api/sims")
    assert resp.status_code == 200
    codes = {row["sim_code"] for row in resp.json()}
    assert codes == {"alpha", "beta"}


def test_get_list_filter_by_status(client, repos):
    _seed_sim(repos, "running_one", status=SimulationStatus.RUNNING)
    _seed_sim(repos, "idle_one", status=SimulationStatus.IDLE)
    resp = client.get("/api/sims", params={"status": "running"})
    assert resp.status_code == 200
    codes = [r["sim_code"] for r in resp.json()]
    assert codes == ["running_one"]


def test_get_list_rejects_bad_status(client):
    resp = client.get("/api/sims", params={"status": "bogus"})
    assert resp.status_code == 400


def test_get_by_id_returns_sim(client, repos):
    sim = _seed_sim(repos, "one")
    resp = client.get(f"/api/sims/{sim.id}")
    assert resp.status_code == 200
    assert resp.json()["sim_code"] == "one"


def test_get_by_id_404(client):
    resp = client.get("/api/sims/9999")
    assert resp.status_code == 404


def test_pause_resume_stop_transitions(client, repos):
    sim = _seed_sim(repos, "trans")

    r = client.post(f"/api/sims/{sim.id}/pause")
    assert r.status_code == 204
    assert client.get(f"/api/sims/{sim.id}").json()["status"] == "paused"

    r = client.post(f"/api/sims/{sim.id}/resume")
    assert r.status_code == 204
    assert client.get(f"/api/sims/{sim.id}").json()["status"] == "running"

    r = client.post(f"/api/sims/{sim.id}/stop")
    assert r.status_code == 204
    assert client.get(f"/api/sims/{sim.id}").json()["status"] == "stopped"


def test_pause_unknown_404(client):
    assert client.post("/api/sims/9999/pause").status_code == 404


def test_delete_soft_deletes(client, repos):
    sim = _seed_sim(repos, "delme")
    r = client.delete(f"/api/sims/{sim.id}")
    assert r.status_code == 204
    # Default list excludes soft-deleted.
    listing = client.get("/api/sims").json()
    assert all(row["id"] != sim.id for row in listing)
    # Include deleted reveals it.
    incl = client.get("/api/sims", params={"include_deleted": True}).json()
    assert any(row["id"] == sim.id and row["deleted"] for row in incl)


# ------------------------------------------------------------------- steps


def test_get_steps_range(client, repos):
    sim = _seed_sim(repos, "steps")
    repos.steps.upsert_movements_for_step(
        sim.id, 0,
        [{"persona_name": "Isabella", "x": 1, "y": 2, "description": "idle",
          "pronunciatio": "x", "chat": None, "location_path": None}],
    )
    repos.steps.upsert_movements_for_step(
        sim.id, 1,
        [{"persona_name": "Isabella", "x": 3, "y": 4, "description": "walk",
          "pronunciatio": None, "chat": [["I", "hi"]], "location_path": "Ville:Cafe"}],
    )
    r = client.get(f"/api/sims/{sim.id}/steps", params={"from": 0, "to": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["from_step"] == 0 and body["to_step"] == 1
    assert {item["step"] for item in body["items"]} == {0, 1}


def test_get_steps_out_of_bounds_empty(client, repos):
    sim = _seed_sim(repos, "empty_range")
    r = client.get(f"/api/sims/{sim.id}/steps", params={"from": 100, "to": 200})
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_get_steps_range_too_large(client, repos):
    sim = _seed_sim(repos, "big_range")
    r = client.get(
        f"/api/sims/{sim.id}/steps", params={"from": 0, "to": 5000}
    )
    assert r.status_code == 400


def test_get_step_snapshot(client, repos):
    sim = _seed_sim(repos, "snap")
    repos.steps.upsert_environment(sim.id, 5, {"world": "ok"})
    repos.steps.upsert_movements_for_step(
        sim.id, 5,
        [{"persona_name": "Klaus", "x": 7, "y": 8, "description": None,
          "pronunciatio": None, "chat": None, "location_path": None}],
    )
    r = client.get(f"/api/sims/{sim.id}/steps/5")
    assert r.status_code == 200
    body = r.json()
    assert body["step"] == 5
    assert body["environment"] == {"world": "ok"}
    assert len(body["movements"]) == 1
    assert body["movements"][0]["persona_name"] == "Klaus"


# ---------------------------------------------------------------- personas


def test_list_personas(client, repos):
    sim = _seed_sim(repos, "p_list")
    repos.personas.create(sim.id, "Isabella Rodriguez", age=34)
    repos.personas.create(sim.id, "Klaus Mueller", age=20)
    r = client.get(f"/api/sims/{sim.id}/personas")
    assert r.status_code == 200
    names = {row["name"] for row in r.json()}
    assert names == {"Isabella Rodriguez", "Klaus Mueller"}


def test_list_personas_sim_missing_404(client):
    assert client.get("/api/sims/9999/personas").status_code == 404


def test_persona_state(client, repos):
    sim = _seed_sim(repos, "p_state")
    p = repos.personas.create(sim.id, "Maria", age=25)
    repos.personas.save_scratch(p.id, {"curr_tile": [1, 2]})
    repos.personas.save_spatial_memory(p.id, {"Ville": {"Cafe": {}}})
    repos.memory.add_node(
        p.id, "n1", MemoryNodeType.EVENT,
        type_count=1, created=0, subject="s", predicate="p", object="o",
        description="d", poignancy=1, keywords_json=["coffee"],
    )
    r = client.get(f"/api/sims/{sim.id}/personas/Maria/state", params={"k": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["persona"]["name"] == "Maria"
    assert body["scratch"] == {"curr_tile": [1, 2]}
    assert body["spatial_memory"] == {"Ville": {"Cafe": {}}}
    assert len(body["recent_memory"]) == 1
    assert body["recent_memory"][0]["keywords"] == ["coffee"]


def test_persona_state_missing_persona_404(client, repos):
    sim = _seed_sim(repos, "no_persona")
    r = client.get(f"/api/sims/{sim.id}/personas/Nobody/state")
    assert r.status_code == 404


def test_persona_memory_filter_and_pagination(client, repos):
    sim = _seed_sim(repos, "mem_q")
    p = repos.personas.create(sim.id, "Maria")
    for i in range(5):
        repos.memory.add_node(
            p.id, f"n_{i}",
            MemoryNodeType.EVENT if i % 2 == 0 else MemoryNodeType.THOUGHT,
            type_count=i, created=i, subject="s", predicate="p", object="o",
            description=f"d{i}", poignancy=0, keywords_json=[],
        )
    r = client.get(
        f"/api/sims/{sim.id}/personas/Maria/memory",
        params={"type": "event", "limit": 10},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3  # 0,2,4
    assert {item["node_id"] for item in body["items"]} == {"n_0", "n_2", "n_4"}

    r2 = client.get(
        f"/api/sims/{sim.id}/personas/Maria/memory",
        params={"limit": 2, "offset": 1},
    )
    body2 = r2.json()
    assert body2["total"] == 5
    assert len(body2["items"]) == 2
