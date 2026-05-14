"""Tests for /api/sims lifecycle routes wired to SimulationManager (M3b-2).

These cover the manager-integration path added in M3b-2:

  * ``POST /api/sims`` with ``start=True`` spawns a run via the manager;
    ``start`` defaults to False (M3a contract: plain create does not spawn).
  * ``pause`` / ``resume`` / ``stop`` drive the manager's cooperative flags
    when the sim is actively running in this process, and fall back to a
    direct DB status flip otherwise.

The real ``stanford_town_runner`` is never invoked — a tiny fake runner that
honours pause/stop is injected into a fresh ``SimulationManager`` and patched
into ``app.routes.sims``. No simulator, no LLM.

An ``httpx.AsyncClient`` over ``ASGITransport`` is used (not ``TestClient``) so
the route handlers, the manager's spawned tasks, and the test body all share a
single asyncio event loop — letting the test deterministically observe and
clean up the spawned worker tasks.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.deps import get_db  # noqa: E402
from app.main import app  # noqa: E402
import app.routes.sims as sims_routes  # noqa: E402
from runner.events import EventBus  # noqa: E402
from runner.manager import RunContext, SimulationManager  # noqa: E402
from storage.db import Base  # noqa: E402
from storage.models import SimulationStatus  # noqa: E402
from storage.repos import make_repos  # noqa: E402


# --------------------------------------------------------------------- fixtures


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


async def _slow_runner(ctx: RunContext) -> None:
    """Runs until stopped — honours pause/stop cooperatively."""
    await ctx.emit_status("running")
    while not ctx.should_stop():
        await ctx.pause_event.wait()
        if ctx.should_stop():
            return
        await asyncio.sleep(0.01)


@pytest.fixture()
def manager(session_factory) -> SimulationManager:
    """A fresh manager with a fake slow runner."""
    return SimulationManager(
        event_bus=EventBus(),
        session_factory=session_factory,
        runner=_slow_runner,
    )


@pytest.fixture()
async def client(session_factory, manager, monkeypatch):
    def override_get_db():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    # The routes import manager_singleton by name — patch that reference.
    monkeypatch.setattr(sims_routes, "manager_singleton", manager)
    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def repos(session_factory):
    s = session_factory()
    try:
        yield make_repos(s)
    finally:
        s.close()


def _seed_sim(repos, sim_code="m_sim", status=SimulationStatus.IDLE):
    return repos.simulations.create(
        sim_code,
        fork_sim_code="base_the_ville_isabella_maria_klaus",
        status=status,
        start_time_iso="2023-02-13T07:00:00",
        curr_time_iso="2023-02-13T07:00:00",
        n_round=50,
    )


# ----------------------------------------------------------------- create+start


async def test_create_default_does_not_spawn(client, manager):
    resp = await client.post("/api/sims", json={"sim_code": "nostart", "n_round": 5})
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "idle"
    assert manager.list_running() == []


async def test_create_with_start_true_spawns_run(client, manager):
    resp = await client.post(
        "/api/sims", json={"sim_code": "withstart", "n_round": 5, "start": True}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    sim_id = body["id"]
    # Manager flips status to RUNNING before spawning the task.
    assert body["status"] == "running"
    assert manager.is_running(sim_id) is True

    # Clean up the spawned task.
    await manager.stop(sim_id)
    assert manager.is_running(sim_id) is False


async def test_create_with_start_records_persona_snapshot(
    client, manager, session_factory
):
    resp = await client.post(
        "/api/sims",
        json={
            "sim_code": "snap_sim",
            "n_round": 5,
            "personas": ["Isabella Rodriguez"],
        },
    )
    assert resp.status_code == 201
    sim_id = resp.json()["id"]
    from sqlalchemy import select

    from storage.models import SimulationConfigSnapshot

    with session_factory() as s:
        snap = s.scalar(
            select(SimulationConfigSnapshot).where(
                SimulationConfigSnapshot.sim_id == sim_id
            )
        )
        assert snap is not None
        assert snap.persona_filter_json["personas"] == ["Isabella Rodriguez"]


# ------------------------------------------------------- pause/resume/stop wiring


async def test_pause_resume_stop_drive_manager_when_running(client, manager, repos):
    sim = _seed_sim(repos, "running_sim")
    await manager.start(sim.id)
    assert manager.is_running(sim.id) is True

    # pause -> manager cooperative pause + DB status PAUSED.
    r = await client.post(f"/api/sims/{sim.id}/pause")
    assert r.status_code == 204
    got = await client.get(f"/api/sims/{sim.id}")
    assert got.json()["status"] == "paused"
    assert manager.is_running(sim.id) is True  # task alive, just blocked

    # resume -> RUNNING.
    r = await client.post(f"/api/sims/{sim.id}/resume")
    assert r.status_code == 204
    got = await client.get(f"/api/sims/{sim.id}")
    assert got.json()["status"] == "running"

    # stop -> task ends, DB status STOPPED.
    r = await client.post(f"/api/sims/{sim.id}/stop")
    assert r.status_code == 204
    assert manager.is_running(sim.id) is False
    got = await client.get(f"/api/sims/{sim.id}")
    assert got.json()["status"] == "stopped"


async def test_pause_resume_stop_fallback_when_not_running(client, manager, repos):
    """Idle/imported sims (not running in this manager) still respond via a
    direct DB status flip — preserves M3a behaviour."""
    sim = _seed_sim(repos, "idle_sim")
    assert manager.is_running(sim.id) is False

    r = await client.post(f"/api/sims/{sim.id}/pause")
    assert r.status_code == 204
    got = await client.get(f"/api/sims/{sim.id}")
    assert got.json()["status"] == "paused"

    r = await client.post(f"/api/sims/{sim.id}/resume")
    assert r.status_code == 204
    got = await client.get(f"/api/sims/{sim.id}")
    assert got.json()["status"] == "running"

    r = await client.post(f"/api/sims/{sim.id}/stop")
    assert r.status_code == 204
    got = await client.get(f"/api/sims/{sim.id}")
    assert got.json()["status"] == "stopped"


async def test_pause_unknown_sim_404(client):
    r = await client.post("/api/sims/12345/pause")
    assert r.status_code == 404
