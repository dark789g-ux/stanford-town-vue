"""Unit tests for runner.events.EventBus + Subscription."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Make backend/ importable when pytest is run from the repo root.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from runner.events import EventBus, SimEvent  # noqa: E402


def _ev(sim_id: int = 1, n: int = 0) -> SimEvent:
    return SimEvent(sim_id=sim_id, event_type="step", payload={"n": n})


async def test_publish_to_single_subscriber():
    bus = EventBus()
    sub = bus.subscribe(1)
    await bus.publish(_ev(1, 7))

    got = await asyncio.wait_for(sub.__anext__(), timeout=1.0)
    assert got.sim_id == 1
    assert got.payload == {"n": 7}

    await sub.close()


async def test_multi_subscriber_fanout():
    bus = EventBus()
    a = bus.subscribe(42)
    b = bus.subscribe(42)
    assert bus.subscriber_count(42) == 2

    await bus.publish(_ev(42, 1))
    await bus.publish(_ev(42, 2))

    got_a = [await asyncio.wait_for(a.__anext__(), timeout=1.0) for _ in range(2)]
    got_b = [await asyncio.wait_for(b.__anext__(), timeout=1.0) for _ in range(2)]
    assert [e.payload["n"] for e in got_a] == [1, 2]
    assert [e.payload["n"] for e in got_b] == [1, 2]

    await a.close()
    await b.close()


async def test_close_unsubscribes():
    bus = EventBus()
    sub = bus.subscribe(7)
    assert bus.subscriber_count(7) == 1

    await sub.close()
    assert bus.subscriber_count(7) == 0

    # Idempotent.
    await sub.close()
    assert bus.subscriber_count(7) == 0

    # After close, iteration terminates.
    with pytest.raises(StopAsyncIteration):
        await sub.__anext__()


async def test_close_wakes_pending_iterator():
    bus = EventBus()
    sub = bus.subscribe(1)

    async def consume():
        items = []
        async for ev in sub:
            items.append(ev)
        return items

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)  # let consumer block on queue.get()
    await bus.publish(_ev(1, 1))
    await asyncio.sleep(0)
    await sub.close()

    result = await asyncio.wait_for(task, timeout=1.0)
    assert [e.payload["n"] for e in result] == [1]


async def test_queue_overflow_drops_oldest():
    bus = EventBus()
    sub = bus.subscribe(9)

    # Force the bounded queue to its maxsize then push one more.
    maxsize = sub.queue.maxsize
    for i in range(maxsize):
        await bus.publish(_ev(9, i))
    assert sub.queue.qsize() == maxsize

    # Overflow: oldest (n=0) should be dropped, newest (n=maxsize) kept.
    await bus.publish(_ev(9, maxsize))
    assert sub.queue.qsize() == maxsize

    # Pop everything and verify n=0 is gone but n=maxsize is present.
    ns: list[int] = []
    while not sub.queue.empty():
        ns.append((await sub.__anext__()).payload["n"])
    assert 0 not in ns
    assert maxsize in ns
    assert ns[-1] == maxsize

    await sub.close()


async def test_publish_no_subscribers_is_noop():
    bus = EventBus()
    # Should not raise, should not create state.
    await bus.publish(_ev(123, 0))
    assert bus.subscriber_count(123) == 0


async def test_publish_isolated_by_sim_id():
    bus = EventBus()
    sub_a = bus.subscribe(1)
    sub_b = bus.subscribe(2)

    await bus.publish(_ev(1, 100))

    got = await asyncio.wait_for(sub_a.__anext__(), timeout=1.0)
    assert got.payload["n"] == 100
    assert sub_b.queue.empty()

    await sub_a.close()
    await sub_b.close()
