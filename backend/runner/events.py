"""In-memory pub/sub event bus for simulation events.

Multi-subscriber per ``sim_id``. Each :class:`Subscription` is backed by an
``asyncio.Queue`` with bounded capacity (``maxsize=1024``). When the queue
fills up the oldest event is dropped so a slow consumer cannot block the
producer side.

This module is intentionally provider-agnostic: it knows nothing about
WebSockets, simulators, or HTTP — it just routes :class:`SimEvent` values
from publishers to subscribers within a single asyncio event loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, Literal

from loguru import logger

EventType = Literal["snapshot", "step", "status", "llm_call", "error", "pong"]

_QUEUE_MAXSIZE = 1024


@dataclass(slots=True)
class SimEvent:
    """A single simulation event addressed to subscribers of ``sim_id``."""

    sim_id: int
    event_type: EventType
    payload: dict[str, Any]
    ts: datetime = field(default_factory=datetime.utcnow)


class Subscription:
    """Async-iterable subscription handle returned by :meth:`EventBus.subscribe`.

    Iterating yields :class:`SimEvent` instances until :meth:`close` is called.
    After close, ``__anext__`` raises ``StopAsyncIteration``.
    """

    __slots__ = ("sim_id", "queue", "_bus", "_closed")

    def __init__(self, sim_id: int, bus: "EventBus") -> None:
        self.sim_id = sim_id
        self.queue: asyncio.Queue[SimEvent] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._bus = bus
        self._closed = False

    def __aiter__(self) -> AsyncIterator[SimEvent]:
        return self

    async def __anext__(self) -> SimEvent:
        if self._closed and self.queue.empty():
            raise StopAsyncIteration
        # Race-safe: a close() while we're awaiting will enqueue a sentinel.
        item = await self.queue.get()
        if item is _CLOSE_SENTINEL:  # type: ignore[comparison-overlap]
            raise StopAsyncIteration
        return item

    async def close(self) -> None:
        """Unsubscribe from the bus and wake any pending iterator. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self._bus._unregister(self)
        # Wake up an awaiter on __anext__ so it can exit cleanly.
        try:
            self.queue.put_nowait(_CLOSE_SENTINEL)  # type: ignore[arg-type]
        except asyncio.QueueFull:
            # Drop oldest then push sentinel so iterators terminate promptly.
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self.queue.put_nowait(_CLOSE_SENTINEL)  # type: ignore[arg-type]
            except asyncio.QueueFull:
                pass


# Module-private sentinel used to wake a blocked iterator on close().
_CLOSE_SENTINEL: Any = object()


class EventBus:
    """In-memory pub-sub keyed by ``sim_id``.

    Single asyncio event loop is assumed: no external locking is required.

    The bus opportunistically captures the running loop on the first
    :meth:`subscribe` call so synchronous producers (notably pytest's
    ``TestClient`` which runs the app in a portal thread) can use
    :meth:`publish_threadsafe` from non-async code.
    """

    def __init__(self) -> None:
        self._subs: dict[int, set[Subscription]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def publish(self, event: SimEvent) -> None:
        """Fan ``event`` out to all subscribers of ``event.sim_id``.

        If a subscriber's queue is full, the oldest event is dropped to make
        room. Publishing to a sim_id with no subscribers is a no-op.
        """
        subs = self._subs.get(event.sim_id)
        if not subs:
            return
        # Iterate over a snapshot — a subscriber may close() during fan-out.
        for sub in list(subs):
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop oldest to make room. Log once per overflow.
                try:
                    sub.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    sub.queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning(
                        "EventBus: dropping event for sim_id={} after overflow",
                        event.sim_id,
                    )

    def subscribe(self, sim_id: int) -> Subscription:
        """Create and register a fresh :class:`Subscription` for ``sim_id``."""
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # Subscribed from synchronous test code before any loop existed;
            # publish_threadsafe will raise if invoked in that state.
            pass
        sub = Subscription(sim_id, self)
        self._subs.setdefault(sim_id, set()).add(sub)
        return sub

    def publish_threadsafe(self, event: SimEvent, timeout: float = 1.0) -> None:
        """Schedule :meth:`publish` on the bus's owning loop from any thread.

        Useful when a synchronous test driver (``fastapi.testclient.TestClient``)
        needs to inject events into a hub running inside Starlette's portal
        thread. Raises ``RuntimeError`` if the bus has not yet observed a
        running loop via :meth:`subscribe`.
        """
        loop = self._loop
        if loop is None:
            raise RuntimeError(
                "EventBus.publish_threadsafe called before any async subscriber"
            )
        fut = asyncio.run_coroutine_threadsafe(self.publish(event), loop)
        fut.result(timeout=timeout)

    def subscriber_count(self, sim_id: int) -> int:
        return len(self._subs.get(sim_id, ()))

    # Internal hook used by Subscription.close().
    def _unregister(self, sub: Subscription) -> None:
        bucket = self._subs.get(sub.sim_id)
        if not bucket:
            return
        bucket.discard(sub)
        if not bucket:
            self._subs.pop(sub.sim_id, None)


__all__ = ["EventBus", "EventType", "SimEvent", "Subscription"]
