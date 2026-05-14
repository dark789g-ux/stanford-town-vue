"""Integration tests for the ``/ws/sim/{sim_id}`` WebSocket hub.

Uses Starlette's :class:`TestClient` WebSocket support. The hub's EventBus
singleton is reset per-test and we publish onto it via
:meth:`EventBus.publish_threadsafe`, which schedules onto the app loop
that the WS handler is actually running on.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.deps import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.ws import hub as ws_hub  # noqa: E402
from app.ws.hub import EventBus, SimEvent  # noqa: E402
from storage.db import Base  # noqa: E402
from storage.repos import make_repos  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
def event_bus() -> Iterator[EventBus]:
    bus = EventBus()
    ws_hub._set_event_bus(bus)
    try:
        yield bus
    finally:
        ws_hub._set_event_bus(None)


@pytest.fixture()
def client(session_factory, event_bus) -> Iterator[TestClient]:
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


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_sim(session_factory, sim_code: str = "sim_ws") -> int:
    s = session_factory()
    try:
        repos = make_repos(s)
        sim = repos.simulations.create(
            sim_code,
            start_time_iso="2025-01-01T07:00:00",
            curr_time_iso="2025-01-01T07:00:00",
            n_round=200,
            sec_per_step=10,
        )
        return sim.id
    finally:
        s.close()


def _seed_movements(session_factory, sim_id: int, step: int, personas: list[str]) -> None:
    s = session_factory()
    try:
        repos = make_repos(s)
        repos.steps.upsert_movements_for_step(
            sim_id,
            step,
            [
                {
                    "persona_name": name,
                    "x": idx,
                    "y": idx * 2,
                    "description": f"{name}-desc-{step}",
                    "pronunciatio": "🙂",
                    "chat": None,
                    "location_path": f"the_ville:house:{name}",
                }
                for idx, name in enumerate(personas)
            ],
        )
    finally:
        s.close()


def _wait_for_subscriber(bus: EventBus, sim_id: int, timeout: float = 2.0) -> None:
    """Block until at least one Subscription exists on the bus for sim_id."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if bus.subscriber_count(sim_id) > 0 and bus._loop is not None:
            return
        time.sleep(0.01)
    raise AssertionError(
        f"timed out waiting for ws subscriber on sim_id={sim_id} "
        f"(count={bus.subscriber_count(sim_id)}, loop={bus._loop!r})"
    )


def _subscribe(ws, since_step: int = 0) -> dict:
    """Send the subscribe handshake and return the snapshot event."""
    ws.send_json({"action": "subscribe", "since_step": since_step})
    return ws.receive_json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_unknown_sim_closes_4404(client):
    """Connecting to a sim_id that doesn't exist closes with 4404."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/sim/999") as ws:
            # Server should close immediately; trying to read raises.
            ws.receive_text()
    assert exc_info.value.code == 4404


def test_snapshot_first_message(client, session_factory):
    """First server message after subscribe is a snapshot for this sim_id."""
    sim_id = _seed_sim(session_factory)
    with client.websocket_connect(f"/ws/sim/{sim_id}") as ws:
        snap = _subscribe(ws, since_step=0)
        assert snap["event"] == "snapshot"
        assert snap["sim"]["id"] == sim_id
        assert snap["sim"]["sim_code"] == "sim_ws"
        assert snap["current_step"] == -1  # no movements yet


def test_history_replay_skips_old_steps(client, session_factory):
    """Pre-seed steps 1..5; since_step=2 -> only 3, 4, 5 are replayed."""
    sim_id = _seed_sim(session_factory)
    for step in range(1, 6):
        _seed_movements(session_factory, sim_id, step, ["alice"])
    with client.websocket_connect(f"/ws/sim/{sim_id}") as ws:
        snap = _subscribe(ws, since_step=2)
        assert snap["event"] == "snapshot"
        assert snap["current_step"] == 5
        seen_steps = []
        for _ in range(3):
            msg = ws.receive_json()
            assert msg["event"] == "step"
            seen_steps.append(msg["step"])
        assert seen_steps == [3, 4, 5]


def test_live_step_forwarding(client, session_factory, event_bus):
    """An event published on the bus is forwarded to the WS client."""
    sim_id = _seed_sim(session_factory)
    with client.websocket_connect(f"/ws/sim/{sim_id}") as ws:
        _subscribe(ws, since_step=0)
        _wait_for_subscriber(event_bus, sim_id)
        event_bus.publish_threadsafe(
            SimEvent(
                sim_id=sim_id,
                event_type="step",
                payload={
                    "step": 1,
                    "curr_time": "2025-01-01T07:00:10",
                    "movements": [
                        {
                            "persona_name": "alice",
                            "x": 1,
                            "y": 2,
                            "description": "walking",
                            "pronunciatio": "🚶",
                            "chat": None,
                            "location_path": "the_ville",
                        }
                    ],
                },
            )
        )
        msg = ws.receive_json()
        assert msg["event"] == "step"
        assert msg["step"] == 1
        assert msg["movements"][0]["persona_name"] == "alice"


def test_multi_subscriber_fanout(client, session_factory, event_bus):
    """Two connections on the same sim both receive a single published event."""
    sim_id = _seed_sim(session_factory)
    with client.websocket_connect(f"/ws/sim/{sim_id}") as ws_a:
        _subscribe(ws_a, since_step=0)
        _wait_for_subscriber(event_bus, sim_id)
        with client.websocket_connect(f"/ws/sim/{sim_id}") as ws_b:
            _subscribe(ws_b, since_step=0)
            # Wait until both have registered.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if event_bus.subscriber_count(sim_id) >= 2:
                    break
                time.sleep(0.01)
            assert event_bus.subscriber_count(sim_id) == 2

            event_bus.publish_threadsafe(
                SimEvent(
                    sim_id=sim_id,
                    event_type="status",
                    payload={"status": "running", "error_message": None},
                )
            )
            msg_a = ws_a.receive_json()
            msg_b = ws_b.receive_json()
            assert msg_a["event"] == "status"
            assert msg_b["event"] == "status"
            assert msg_a["status"] == "running"
            assert msg_b["status"] == "running"


def test_ping_pong(client, session_factory):
    """``ping`` from client elicits a ``pong`` with an ISO ts."""
    sim_id = _seed_sim(session_factory)
    with client.websocket_connect(f"/ws/sim/{sim_id}") as ws:
        _subscribe(ws, since_step=0)
        ws.send_json({"action": "ping"})
        msg = ws.receive_json()
        assert msg["event"] == "pong"
        assert isinstance(msg["ts"], str) and "T" in msg["ts"]


def test_replay_then_live_ordering(client, session_factory, event_bus):
    """Three replayed steps then two live steps arrive in order."""
    sim_id = _seed_sim(session_factory)
    for step in (1, 2, 3):
        _seed_movements(session_factory, sim_id, step, ["alice"])
    with client.websocket_connect(f"/ws/sim/{sim_id}") as ws:
        snap = _subscribe(ws, since_step=0)
        assert snap["event"] == "snapshot"
        assert snap["current_step"] == 3
        replayed = [ws.receive_json() for _ in range(3)]
        assert [m["step"] for m in replayed] == [1, 2, 3]
        for m in replayed:
            assert m["event"] == "step"

        _wait_for_subscriber(event_bus, sim_id)
        for step in (4, 5):
            event_bus.publish_threadsafe(
                SimEvent(
                    sim_id=sim_id,
                    event_type="step",
                    payload={
                        "step": step,
                        "curr_time": f"2025-01-01T07:0{step}:00",
                        "movements": [],
                    },
                )
            )
        live = [ws.receive_json() for _ in range(2)]
        assert [m["step"] for m in live] == [4, 5]


def test_multi_movement_grouping(client, session_factory):
    """Three movements at step=2 are delivered as one step event with all 3."""
    sim_id = _seed_sim(session_factory)
    _seed_movements(
        session_factory, sim_id, step=2, personas=["alice", "bob", "carol"]
    )
    with client.websocket_connect(f"/ws/sim/{sim_id}") as ws:
        snap = _subscribe(ws, since_step=0)
        assert snap["current_step"] == 2
        msg = ws.receive_json()
        assert msg["event"] == "step"
        assert msg["step"] == 2
        names = sorted(m["persona_name"] for m in msg["movements"])
        assert names == ["alice", "bob", "carol"]
        # curr_time approximation: start_time + step*sec_per_step = +20s
        assert msg["curr_time"].endswith("T07:00:20")


def test_replay_curr_time_uses_sec_per_step(client, session_factory):
    """Replayed step events compute curr_time as start + step*sec_per_step."""
    sim_id = _seed_sim(session_factory)
    _seed_movements(session_factory, sim_id, step=6, personas=["alice"])
    with client.websocket_connect(f"/ws/sim/{sim_id}") as ws:
        _subscribe(ws, since_step=0)
        msg = ws.receive_json()
        # 6 steps * 10s/step = 60s -> 07:01:00
        assert msg["curr_time"].endswith("T07:01:00")


def test_get_event_bus_defaults_to_manager_bus():
    """With no test override, the hub shares the manager's EventBus.

    Regression: the hub used to lazily create its *own* EventBus, so live
    ``step`` events the runner published on ``manager_singleton.event_bus``
    never reached WS subscribers.
    """
    from runner.manager import manager_singleton

    ws_hub._set_event_bus(None)
    try:
        assert ws_hub.get_event_bus() is manager_singleton.event_bus
    finally:
        ws_hub._set_event_bus(None)


def test_bad_first_message_closes_4400(client, session_factory):
    """A non-subscribe first frame triggers a 4400 close with an error event."""
    sim_id = _seed_sim(session_factory)
    with client.websocket_connect(f"/ws/sim/{sim_id}") as ws:
        ws.send_json({"action": "ping"})
        err = ws.receive_json()
        assert err["event"] == "error"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == 4400
