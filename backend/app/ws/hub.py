"""WebSocket hub for ``/ws/sim/{sim_id}``.

Protocol (frozen for M3b):

1. Client connects, server accepts.
2. Client sends ``{"action":"subscribe","since_step":N}``.
3. Server validates ``sim_id`` against the DB; if missing, closes with
   code ``4404``.
4. Server sends a ``snapshot`` event carrying the sim row + the highest
   step persisted so far (``current_step``).
5. Server replays history: every persisted ``step_movements`` row with
   ``from_step = N+1 .. current_step`` is grouped per step and sent as
   a ``step`` event.
6. Server subscribes to the per-sim :class:`EventBus` and forwards live
   events (``step`` / ``status`` / ``llm_call`` / ``error`` / ``pong``)
   until the client disconnects.

The hub subscribes to the shared :class:`EventBus` — by default the one
owned by :data:`runner.manager.manager_singleton`, so live events the
runner publishes reach WS clients. ``get_event_bus()`` resolves it;
tests inject their own bus via ``_set_event_bus()``.

``curr_time`` in replayed ``step`` events is best-effort: ``step_movements``
does not currently store a timestamp, so we derive it from
``sim.start_time_iso + step * sim.sec_per_step``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Iterator

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from loguru import logger
from sqlalchemy.orm import Session

from app.deps import get_db
from runner.events import EventBus, EventType, SimEvent, Subscription
from storage.models import Simulation, StepMovement
from storage.repos import make_repos

router = APIRouter()


# ---------------------------------------------------------------------------
# Singleton accessor — tests override via _set_event_bus()
# ---------------------------------------------------------------------------

_event_bus_singleton: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return the process-wide :class:`EventBus` instance.

    Defaults to the :class:`~runner.manager.SimulationManager` singleton's bus
    so the WS hub and the live runner share **one** bus — otherwise the runner
    publishes ``step`` events to a bus the hub never subscribes to. Tests
    override with their own bus via :func:`_set_event_bus`.
    """
    if _event_bus_singleton is not None:
        return _event_bus_singleton
    from runner.manager import manager_singleton

    return manager_singleton.event_bus


def _set_event_bus(bus: EventBus | None) -> None:
    """Test hook: replace (or reset to ``None`` to recreate) the singleton."""
    global _event_bus_singleton
    _event_bus_singleton = bus


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


_SIM_COLUMNS = (
    "id",
    "sim_code",
    "fork_sim_code",
    "status",
    "start_time_iso",
    "curr_time_iso",
    "sec_per_step",
    "step",
    "maze_name",
    "idea",
    "inner_voice",
    "n_round",
    "investment",
    "error_message",
    "deleted",
)


def _serialize_sim(sim: Simulation) -> dict[str, Any]:
    """Serialize a Simulation ORM row to a JSON-safe dict.

    ``created_at`` is converted to ISO format; everything else is a primitive
    that ``json.dumps`` already handles.
    """
    out: dict[str, Any] = {col: getattr(sim, col) for col in _SIM_COLUMNS}
    out["created_at"] = (
        sim.created_at.isoformat() if sim.created_at is not None else None
    )
    return out


def _movement_to_dict(mv: StepMovement) -> dict[str, Any]:
    return {
        "persona_name": mv.persona_name,
        "x": mv.x,
        "y": mv.y,
        "description": mv.description,
        "pronunciatio": mv.pronunciatio,
        "chat": mv.chat_json,
        "location_path": mv.location_path,
    }


def _approx_curr_time(start_iso: str, step: int, sec_per_step: int) -> str:
    """Best-effort ISO timestamp for a step row.

    ``step_movements`` does not currently persist a per-step timestamp; we
    therefore approximate it as ``start_time + step * sec_per_step`` seconds.
    """
    try:
        base = datetime.fromisoformat(start_iso)
    except (TypeError, ValueError):
        return start_iso
    return (base + timedelta(seconds=step * max(0, sec_per_step))).isoformat()


def _build_replay_events(
    sim: Simulation,
    rows: list[StepMovement],
) -> Iterator[dict[str, Any]]:
    """Group movement rows by step into outbound ``step`` event dicts."""
    if not rows:
        return iter(())
    rows_sorted = sorted(rows, key=lambda r: (r.step, r.persona_name))
    sec_per_step = sim.sec_per_step or 10
    out: list[dict[str, Any]] = []
    current_step = rows_sorted[0].step
    bucket: list[dict[str, Any]] = []
    for r in rows_sorted:
        if r.step != current_step:
            out.append(
                {
                    "event": "step",
                    "step": current_step,
                    "curr_time": _approx_curr_time(
                        sim.start_time_iso, current_step, sec_per_step
                    ),
                    "movements": bucket,
                }
            )
            bucket = []
            current_step = r.step
        bucket.append(_movement_to_dict(r))
    out.append(
        {
            "event": "step",
            "step": current_step,
            "curr_time": _approx_curr_time(
                sim.start_time_iso, current_step, sec_per_step
            ),
            "movements": bucket,
        }
    )
    return iter(out)


def _live_event_to_wire(ev: SimEvent) -> dict[str, Any]:
    """Convert an EventBus :class:`SimEvent` into the documented wire shape."""
    base = {"event": ev.event_type}
    payload = ev.payload or {}
    if ev.event_type == "pong":
        base["ts"] = payload.get("ts") or ev.ts.isoformat()
        return base
    # For all other event types we let the payload supply the documented
    # fields verbatim; this keeps the producer free to add extras without
    # the hub needing to know about them.
    base.update(payload)
    return base


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/sim/{sim_id}")
async def sim_socket(
    websocket: WebSocket,
    sim_id: int,
    db: Session = Depends(get_db),
) -> None:
    """Per-sim subscription endpoint — see module docstring for the protocol."""
    await websocket.accept()
    logger.info("WS connected sim_id={}", sim_id)

    # ----- look up the sim row up front (cheap; fail fast if missing) ---
    repos = make_repos(db)
    sim = repos.simulations.get_by_id(sim_id)
    if sim is None:
        logger.info("WS sim_id={} not found — closing 4404", sim_id)
        await websocket.close(code=4404, reason="sim not found")
        return

    # ----- await the subscribe handshake --------------------------------
    try:
        raw = await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info("WS sim_id={} disconnected before subscribe", sim_id)
        return

    try:
        msg = json.loads(raw)
        if not isinstance(msg, dict) or msg.get("action") != "subscribe":
            raise ValueError("first message must be subscribe")
        since_step = int(msg.get("since_step", 0) or 0)
        if since_step < 0:
            raise ValueError("since_step must be >= 0")
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        await websocket.send_json(
            {"event": "error", "detail": f"bad subscribe: {exc}"}
        )
        await websocket.close(code=4400, reason="bad subscribe")
        return

    # ----- snapshot -----------------------------------------------------
    current_step = repos.steps.get_max_step(sim_id)
    await websocket.send_json(
        {
            "event": "snapshot",
            "sim": _serialize_sim(sim),
            "current_step": current_step,
        }
    )

    # ----- history replay ----------------------------------------------
    # `since_step` is "the last step the client already has". A fresh client
    # sends 0, but it genuinely has *nothing* — so 0 means "replay from step 0
    # inclusive". Any positive N means "I have through N, send N+1 onward".
    from_step = 0 if since_step == 0 else since_step + 1
    if current_step >= from_step:
        rows = repos.steps.list_movements_range(
            sim_id=sim_id,
            from_step=from_step,
            to_step=current_step,
        )
        for ev in _build_replay_events(sim, rows):
            await websocket.send_json(ev)

    # ----- live subscription -------------------------------------------
    bus = get_event_bus()
    sub = bus.subscribe(sim_id)

    receive_task = asyncio.create_task(_client_loop(websocket, sub))
    forward_task = asyncio.create_task(_forward_loop(websocket, sub))

    try:
        done, pending = await asyncio.wait(
            {receive_task, forward_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    finally:
        await sub.close()
        # Best-effort drain — already handled by forward_task ending; the
        # underlying WS may already be closed so swallow any errors here.
        try:
            await websocket.close()
        except RuntimeError:
            pass
        logger.info("WS sim_id={} closed", sim_id)


# ---------------------------------------------------------------------------
# Inner loops
# ---------------------------------------------------------------------------


async def _client_loop(websocket: WebSocket, sub: Subscription) -> None:
    """Read client messages: ``ping`` -> pong, ``unsubscribe`` -> exit."""
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"event": "error", "detail": "invalid json"}
                )
                continue
            if not isinstance(msg, dict):
                await websocket.send_json(
                    {"event": "error", "detail": "message must be a json object"}
                )
                continue
            action = msg.get("action")
            if action == "ping":
                await websocket.send_json(
                    {"event": "pong", "ts": datetime.utcnow().isoformat()}
                )
            elif action == "unsubscribe":
                return
            else:
                await websocket.send_json(
                    {"event": "error", "detail": f"unknown action: {action!r}"}
                )
    except WebSocketDisconnect:
        return


async def _forward_loop(websocket: WebSocket, sub: Subscription) -> None:
    """Pump events from the bus subscription out to the WS client."""
    try:
        async for ev in sub:
            await websocket.send_json(_live_event_to_wire(ev))
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("ws.hub forward loop crashed: {}", exc)


__all__ = [
    "router",
    "EventBus",
    "Subscription",
    "SimEvent",
    "EventType",
    "get_event_bus",
]
