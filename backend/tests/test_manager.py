"""Unit tests for runner.manager.SimulationManager — uses a FakeRunner."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Make backend/ importable when pytest is run from the repo root.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from runner.events import EventBus  # noqa: E402
from runner.manager import RunContext, SimulationManager  # noqa: E402
from storage.db import Base  # noqa: E402
from storage.models import SimulationStatus  # noqa: E402
from storage.repos import make_repos  # noqa: E402


# ---------------------------------------------------------------------- fixtures


@pytest.fixture()
def session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


@pytest.fixture()
def event_bus() -> EventBus:
    return EventBus()


def _seed_sim(
    session_factory: sessionmaker,
    sim_code: str = "sim_test",
    n_round: int = 3,
    status: SimulationStatus = SimulationStatus.IDLE,
) -> int:
    with session_factory() as s:
        repos = make_repos(s)
        sim = repos.simulations.create(
            sim_code,
            status=status,
            start_time_iso="2025-01-01T00:00:00",
            curr_time_iso="2025-01-01T00:00:00",
            sec_per_step=10,
            step=0,
            maze_name="the_ville",
            n_round=n_round,
            investment=0.0,
        )
        return sim.id


def _get_status(session_factory: sessionmaker, sim_id: int) -> str:
    with session_factory() as s:
        repos = make_repos(s)
        sim = repos.simulations.get_by_id(sim_id)
        assert sim is not None
        return sim.status


def _get_error(session_factory: sessionmaker, sim_id: int) -> str | None:
    with session_factory() as s:
        repos = make_repos(s)
        sim = repos.simulations.get_by_id(sim_id)
        assert sim is not None
        return sim.error_message


# ----------------------------------------------------------------- fake runners


def make_fake_runner(observed: dict | None = None):
    """A runner that emits n_round step events while honouring pause/stop."""

    async def runner(ctx: RunContext) -> None:
        if observed is not None:
            observed["started"] = True
            observed["sim_code"] = ctx.sim_code
        await ctx.emit_status("running")
        for step in range(ctx.n_round):
            # Block while paused; wakes promptly when pause_event.set().
            await ctx.pause_event.wait()
            if ctx.should_stop():
                return
            await ctx.emit_step(
                step=step + 1,
                curr_time_iso=f"2025-01-01T00:00:{step:02d}",
                movements=[],
            )
            # Yield so pause/stop can interleave.
            await asyncio.sleep(0.01)

    return runner


async def _slow_runner(ctx: RunContext) -> None:
    """Runs until stopped — used for pause/stop tests."""
    await ctx.emit_status("running")
    while not ctx.should_stop():
        await ctx.pause_event.wait()
        if ctx.should_stop():
            return
        await asyncio.sleep(0.01)


async def _failing_runner(ctx: RunContext) -> None:
    raise RuntimeError("boom from runner")


# ---------------------------------------------------------------------- tests


async def test_start_spawns_task_and_sets_running(session_factory, event_bus):
    sim_id = _seed_sim(session_factory)
    observed: dict = {}
    mgr = SimulationManager(
        event_bus=event_bus,
        session_factory=session_factory,
        runner=make_fake_runner(observed),
    )

    await mgr.start(sim_id)
    assert mgr.is_running(sim_id) is True
    assert sim_id in mgr.list_running()

    # Wait until task finishes naturally to keep the test deterministic.
    await asyncio.wait_for(mgr._tasks[sim_id], timeout=2.0)
    assert observed["started"] is True
    assert observed["sim_code"] == "sim_test"


async def test_natural_completion_sets_completed(session_factory, event_bus):
    sim_id = _seed_sim(session_factory, n_round=2)
    mgr = SimulationManager(
        event_bus=event_bus,
        session_factory=session_factory,
        runner=make_fake_runner(),
    )

    await mgr.start(sim_id)
    # Allow runner to finish.
    while mgr.is_running(sim_id):
        await asyncio.sleep(0.01)

    assert _get_status(session_factory, sim_id) == SimulationStatus.COMPLETED.value
    assert mgr.list_running() == []


async def test_pause_sets_paused_then_resume_sets_running(session_factory, event_bus):
    sim_id = _seed_sim(session_factory)
    mgr = SimulationManager(
        event_bus=event_bus,
        session_factory=session_factory,
        runner=_slow_runner,
    )

    await mgr.start(sim_id)
    assert _get_status(session_factory, sim_id) == SimulationStatus.RUNNING.value

    await mgr.pause(sim_id)
    assert _get_status(session_factory, sim_id) == SimulationStatus.PAUSED.value
    assert mgr.is_running(sim_id) is True  # task still alive, just blocked

    await mgr.resume(sim_id)
    assert _get_status(session_factory, sim_id) == SimulationStatus.RUNNING.value

    # Clean up.
    await mgr.stop(sim_id)
    assert mgr.is_running(sim_id) is False


async def test_stop_sets_stopped_and_task_ends(session_factory, event_bus):
    sim_id = _seed_sim(session_factory)
    mgr = SimulationManager(
        event_bus=event_bus,
        session_factory=session_factory,
        runner=_slow_runner,
    )

    await mgr.start(sim_id)
    await mgr.stop(sim_id)

    assert mgr.is_running(sim_id) is False
    assert _get_status(session_factory, sim_id) == SimulationStatus.STOPPED.value


async def test_stop_while_paused_still_terminates(session_factory, event_bus):
    sim_id = _seed_sim(session_factory)
    mgr = SimulationManager(
        event_bus=event_bus,
        session_factory=session_factory,
        runner=_slow_runner,
    )

    await mgr.start(sim_id)
    await mgr.pause(sim_id)
    # Stop should unblock the pause and let runner exit.
    await mgr.stop(sim_id)

    assert mgr.is_running(sim_id) is False
    assert _get_status(session_factory, sim_id) == SimulationStatus.STOPPED.value


async def test_runner_exception_sets_failed_with_error_message(
    session_factory, event_bus
):
    sim_id = _seed_sim(session_factory)
    mgr = SimulationManager(
        event_bus=event_bus,
        session_factory=session_factory,
        runner=_failing_runner,
    )

    await mgr.start(sim_id)
    while mgr.is_running(sim_id):
        await asyncio.sleep(0.01)

    assert _get_status(session_factory, sim_id) == SimulationStatus.FAILED.value
    err = _get_error(session_factory, sim_id)
    assert err is not None
    assert "boom from runner" in err


async def test_is_running_and_list_running_track_state(session_factory, event_bus):
    sim_a = _seed_sim(session_factory, sim_code="sim_a")
    sim_b = _seed_sim(session_factory, sim_code="sim_b")
    mgr = SimulationManager(
        event_bus=event_bus,
        session_factory=session_factory,
        runner=_slow_runner,
    )

    assert mgr.list_running() == []
    assert mgr.is_running(sim_a) is False

    await mgr.start(sim_a)
    await mgr.start(sim_b)
    assert sorted(mgr.list_running()) == sorted([sim_a, sim_b])

    await mgr.stop(sim_a)
    assert mgr.list_running() == [sim_b]
    assert mgr.is_running(sim_a) is False
    assert mgr.is_running(sim_b) is True

    await mgr.stop(sim_b)
    assert mgr.list_running() == []


async def test_start_missing_sim_raises(session_factory, event_bus):
    mgr = SimulationManager(
        event_bus=event_bus,
        session_factory=session_factory,
        runner=make_fake_runner(),
    )
    with pytest.raises(ValueError):
        await mgr.start(99999)


async def test_start_already_running_raises(session_factory, event_bus):
    sim_id = _seed_sim(session_factory)
    mgr = SimulationManager(
        event_bus=event_bus,
        session_factory=session_factory,
        runner=_slow_runner,
    )
    await mgr.start(sim_id)
    with pytest.raises(ValueError):
        await mgr.start(sim_id)
    await mgr.stop(sim_id)


async def test_scan_interrupted_marks_running_rows(session_factory):
    """A RUNNING row left from a previous process becomes INTERRUPTED."""
    sim_id = _seed_sim(session_factory, status=SimulationStatus.RUNNING)
    other_id = _seed_sim(
        session_factory, sim_code="sim_other", status=SimulationStatus.COMPLETED
    )

    mgr = SimulationManager(session_factory=session_factory)
    mgr.scan_interrupted()

    assert _get_status(session_factory, sim_id) == SimulationStatus.INTERRUPTED.value
    err = _get_error(session_factory, sim_id)
    assert err and "running" in err.lower()
    # Untouched.
    assert _get_status(session_factory, other_id) == SimulationStatus.COMPLETED.value


async def test_event_bus_receives_status_events(session_factory, event_bus):
    """Verify the manager emits status events on completion."""
    sim_id = _seed_sim(session_factory, n_round=1)
    mgr = SimulationManager(
        event_bus=event_bus,
        session_factory=session_factory,
        runner=make_fake_runner(),
    )

    sub = event_bus.subscribe(sim_id)
    await mgr.start(sim_id)
    while mgr.is_running(sim_id):
        await asyncio.sleep(0.01)

    received: list = []
    # Drain whatever's queued.
    while not sub.queue.empty():
        received.append(await sub.__anext__())
    await sub.close()

    types = [e.event_type for e in received]
    # Should include at least one "status" event and one "step" event.
    assert "status" in types
    assert "step" in types

    # Step events carry the timestamp under the wire key ``curr_time`` (the
    # hub forwards the payload verbatim and the frontend reads ``curr_time``).
    step_ev = next(e for e in received if e.event_type == "step")
    assert "curr_time" in step_ev.payload
    assert "curr_time_iso" not in step_ev.payload
