"""SimulationManager — orchestrates per-simulation asyncio worker tasks.

The manager owns the lifecycle of each simulation: it spawns an asyncio
task per ``sim_id``, exposes cooperative ``pause``/``resume``/``stop``
controls via two :class:`asyncio.Event` flags per task, and reconciles
the persisted ``status`` column accordingly.

The actual stepping loop is supplied by an injected :data:`Runner`
callable so the manager can be unit-tested in isolation from the real
StanfordTown simulator. The default no-op runner emits a couple of
status events and exits, which is enough to keep the surface importable
in environments where the simulator is not wired up yet.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from sqlalchemy.orm import sessionmaker

from runner.events import EventBus, SimEvent
from storage.models import SimulationStatus
from storage.repos import make_repos

_STOP_GRACE_SECONDS = 30.0


@dataclass(slots=True)
class RunContext:
    """Per-run mutable handle passed to the injected :data:`Runner`.

    A Runner is expected to:
      * read its starting parameters from the fields below,
      * call :meth:`emit_step` / :meth:`emit_status` / :meth:`emit_llm_call`
        to broadcast progress on the shared :class:`EventBus`,
      * write progress to the DB via short-lived sessions opened from
        :attr:`session_factory`,
      * cooperatively honour pause/stop: ``await pause_event.wait()`` near
        the top of its loop and break when ``stop_event.is_set()``.
    """

    sim_id: int
    sim_code: str
    n_round: int
    session_factory: sessionmaker
    event_bus: EventBus
    pause_event: asyncio.Event
    stop_event: asyncio.Event
    extra: dict[str, Any] = field(default_factory=dict)

    # --------------------------------------------------------------- helpers
    def is_paused(self) -> bool:
        """``True`` when the pause flag is *not* set (i.e. paused)."""
        return not self.pause_event.is_set()

    def should_stop(self) -> bool:
        return self.stop_event.is_set()

    async def emit_step(
        self,
        step: int,
        curr_time_iso: str,
        movements: list[dict],
    ) -> None:
        await self.event_bus.publish(
            SimEvent(
                sim_id=self.sim_id,
                event_type="step",
                # Wire key is ``curr_time`` — matches the hub's replay events
                # and the documented WS protocol the frontend consumes.
                payload={
                    "step": step,
                    "curr_time": curr_time_iso,
                    "movements": movements,
                },
            )
        )

    async def emit_status(
        self,
        status: str,
        error_message: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"status": status}
        if error_message is not None:
            payload["error_message"] = error_message
        await self.event_bus.publish(
            SimEvent(
                sim_id=self.sim_id,
                event_type="status",
                payload=payload,
            )
        )

    async def emit_llm_call(self, summary: dict) -> None:
        await self.event_bus.publish(
            SimEvent(
                sim_id=self.sim_id,
                event_type="llm_call",
                payload=summary,
            )
        )


Runner = Callable[[RunContext], Awaitable[None]]


async def _default_noop_runner(ctx: RunContext) -> None:
    """Trivial runner used when no real runner is injected.

    Emits a single ``status=running`` event then a ``status=completed``
    event and returns. The Manager wraps this and sets DB status anyway,
    so the bus events are mainly here to keep tests of the event flow
    honest.
    """
    await ctx.emit_status("running")
    # Honour pause/stop even in the no-op path so it can be exercised.
    if not ctx.should_stop():
        await ctx.pause_event.wait()
    await ctx.emit_status("completed")


class SimulationManager:
    """Singleton-friendly orchestrator for in-process simulation runs.

    Lifecycle:
      * :meth:`start` flips DB status to ``RUNNING`` and spawns a task.
      * :meth:`pause` clears the pause event (runner blocks on
        ``pause_event.wait()``) and writes ``PAUSED`` to the DB.
      * :meth:`resume` sets the pause event and writes ``RUNNING``.
      * :meth:`stop` sets the stop event; after the cooperative grace
        period (30 s) the task is hard-cancelled. Final status:
        ``STOPPED``.
      * Natural completion → ``COMPLETED``. Uncaught exception in the
        runner → ``FAILED`` with ``error_message`` populated.
    """

    def __init__(
        self,
        event_bus: EventBus | None = None,
        session_factory: sessionmaker | None = None,
        runner: Runner | None = None,
    ) -> None:
        self._event_bus = event_bus if event_bus is not None else EventBus()
        self._session_factory = session_factory  # may be None until lazy init
        self._runner: Runner = runner if runner is not None else _default_noop_runner
        self._tasks: dict[int, asyncio.Task] = {}
        self._pause_events: dict[int, asyncio.Event] = {}
        self._stop_events: dict[int, asyncio.Event] = {}

    # ----------------------------------------------------------------- props
    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def runner(self) -> Runner:
        return self._runner

    def set_runner(self, runner: Runner) -> None:
        """Swap the injected runner. Used by Wave-3 wiring."""
        self._runner = runner

    def set_session_factory(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    # -------------------------------------------------------------- internal
    def _require_session_factory(self) -> sessionmaker:
        if self._session_factory is None:
            # Lazy-build from settings so import order stays cheap.
            from runner.sessions import build_session_factory

            self._session_factory = build_session_factory()
        return self._session_factory

    def _set_status(
        self,
        sim_id: int,
        status: SimulationStatus,
        error_message: str | None = None,
    ) -> None:
        sf = self._require_session_factory()
        with sf() as s:
            repos = make_repos(s)
            repos.simulations.set_status(sim_id, status, error_message=error_message)

    def _load_sim(self, sim_id: int) -> tuple[str, int]:
        """Return ``(sim_code, n_round)`` for ``sim_id``; raise if missing."""
        sf = self._require_session_factory()
        with sf() as s:
            repos = make_repos(s)
            sim = repos.simulations.get_by_id(sim_id)
            if sim is None:
                raise ValueError(f"Simulation id={sim_id} not found")
            return sim.sim_code, sim.n_round

    # -------------------------------------------------------------- lifecycle
    async def start(self, sim_id: int) -> None:
        """Spawn a worker task for ``sim_id``.

        Raises ``ValueError`` if the sim does not exist or is already
        running in this manager.
        """
        if self.is_running(sim_id):
            raise ValueError(f"Simulation id={sim_id} is already running")

        sim_code, n_round = self._load_sim(sim_id)

        # Flip status to RUNNING *before* spawning the task so callers
        # that immediately read the DB see the right value.
        self._set_status(sim_id, SimulationStatus.RUNNING)

        pause_event = asyncio.Event()
        pause_event.set()  # start in the "resumed" state.
        stop_event = asyncio.Event()
        self._pause_events[sim_id] = pause_event
        self._stop_events[sim_id] = stop_event

        ctx = RunContext(
            sim_id=sim_id,
            sim_code=sim_code,
            n_round=n_round,
            session_factory=self._require_session_factory(),
            event_bus=self._event_bus,
            pause_event=pause_event,
            stop_event=stop_event,
        )

        task = asyncio.create_task(self._run(ctx), name=f"sim-{sim_id}")
        self._tasks[sim_id] = task
        logger.info("SimulationManager.start: spawned task for sim_id={}", sim_id)

    async def _run(self, ctx: RunContext) -> None:
        sim_id = ctx.sim_id
        final_status: SimulationStatus = SimulationStatus.COMPLETED
        error_message: str | None = None
        try:
            await self._runner(ctx)
            if ctx.should_stop():
                final_status = SimulationStatus.STOPPED
            else:
                final_status = SimulationStatus.COMPLETED
        except asyncio.CancelledError:
            final_status = SimulationStatus.STOPPED
            error_message = "cancelled"
            logger.warning("SimulationManager: task for sim_id={} cancelled", sim_id)
            # Don't re-raise — we want to record the status and exit cleanly.
        except Exception as exc:  # noqa: BLE001
            final_status = SimulationStatus.FAILED
            error_message = f"{type(exc).__name__}: {exc}"
            logger.exception("SimulationManager: runner for sim_id={} failed", sim_id)
        finally:
            try:
                self._set_status(sim_id, final_status, error_message=error_message)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "SimulationManager: failed to write final status for sim_id={}: {}",
                    sim_id,
                    exc,
                )
            try:
                await ctx.emit_status(final_status.value, error_message=error_message)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "SimulationManager: failed to emit final status for sim_id={}",
                    sim_id,
                )
            self._tasks.pop(sim_id, None)
            self._pause_events.pop(sim_id, None)
            self._stop_events.pop(sim_id, None)

    async def pause(self, sim_id: int) -> None:
        pause_event = self._pause_events.get(sim_id)
        if pause_event is None:
            raise ValueError(f"Simulation id={sim_id} is not running")
        pause_event.clear()
        self._set_status(sim_id, SimulationStatus.PAUSED)
        logger.info("SimulationManager.pause: sim_id={}", sim_id)

    async def resume(self, sim_id: int) -> None:
        pause_event = self._pause_events.get(sim_id)
        if pause_event is None:
            raise ValueError(f"Simulation id={sim_id} is not running")
        pause_event.set()
        self._set_status(sim_id, SimulationStatus.RUNNING)
        logger.info("SimulationManager.resume: sim_id={}", sim_id)

    async def stop(self, sim_id: int) -> None:
        """Cooperatively stop ``sim_id``; escalate to cancel after 30 s."""
        stop_event = self._stop_events.get(sim_id)
        pause_event = self._pause_events.get(sim_id)
        task = self._tasks.get(sim_id)
        if stop_event is None or task is None:
            raise ValueError(f"Simulation id={sim_id} is not running")

        stop_event.set()
        # Unblock any pause wait so the runner can observe stop_event.
        if pause_event is not None:
            pause_event.set()

        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_STOP_GRACE_SECONDS)
        except asyncio.TimeoutError:
            logger.warning(
                "SimulationManager.stop: sim_id={} did not exit within {}s; cancelling",
                sim_id,
                _STOP_GRACE_SECONDS,
            )
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        except asyncio.CancelledError:
            # The caller cancelled us — propagate.
            raise

    # ------------------------------------------------------------------ info
    def is_running(self, sim_id: int) -> bool:
        task = self._tasks.get(sim_id)
        return task is not None and not task.done()

    def list_running(self) -> list[int]:
        return [sid for sid, t in self._tasks.items() if not t.done()]

    # ------------------------------------------------------------ reconcile
    def scan_interrupted(self) -> None:
        """Flip any DB rows still marked ``RUNNING`` to ``INTERRUPTED``.

        Called from the FastAPI lifespan handler at startup to clean up
        rows left over from a previous crash. Synchronous on purpose —
        the lifespan handler treats this as a best-effort cleanup.
        """
        try:
            sf = self._require_session_factory()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SimulationManager.scan_interrupted: no session factory ({}); skipping",
                exc,
            )
            return

        try:
            with sf() as s:
                repos = make_repos(s)
                running = repos.simulations.list(status=SimulationStatus.RUNNING)
                for sim in running:
                    repos.simulations.set_status(
                        sim.id,
                        SimulationStatus.INTERRUPTED,
                        error_message="Process exited while simulation was running",
                    )
                if running:
                    logger.info(
                        "SimulationManager.scan_interrupted: marked {} row(s) as INTERRUPTED",
                        len(running),
                    )
        except Exception as exc:  # noqa: BLE001
            logger.exception("SimulationManager.scan_interrupted failed: {}", exc)


# ---------------------------------------------------------------------------
# Module-level singleton — lazily-initialized so importing this module is
# side-effect-free. The FastAPI lifespan handler calls
# ``manager_singleton.scan_interrupted()`` at startup.
# ---------------------------------------------------------------------------


manager_singleton = SimulationManager()


__all__ = [
    "Runner",
    "RunContext",
    "SimulationManager",
    "manager_singleton",
]
