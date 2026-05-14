"""Live StanfordTown runner — drives the vendored simulator step-by-step.

This is the real :data:`runner.manager.Runner` implementation. It is injected
into :class:`~runner.manager.SimulationManager` at app startup (see
:mod:`runner.bootstrap`).

Flow per run:

1. Read the ``Simulation`` row (and optional ``SimulationConfigSnapshot``).
2. :func:`materialize_fork` — copy/dump the fork's initial state into the
   simulator's working dir ``STORAGE_PATH/{sim_code}/`` and settle
   ``reverie/meta.json`` (persona subset + start time).
3. Build a :class:`~simulator.town.StanfordTown` + one ``STRole`` per persona,
   following the ``examples/stanford_town/run_st_game.py`` reference.
4. Drive the loop **one tick at a time** (``await town.env.run()``), instead of
   ``town.run(n_round)`` which loops internally with no hook. After each tick we
   :func:`~runner.step_sync.sync_step_to_db`, advance the ``simulations`` row and
   emit a ``step`` event on the EventBus.
5. Honour cooperative pause (``await ctx.pause_event.wait()``) and stop
   (``ctx.should_stop()``) between ticks.
6. After the loop, persist each persona's final memory state into the DB.

The simulator's own JSON I/O is left intact — we only *read* the files it
writes. The LLM is reached through ``core`` config; tests patch the LLM seam
(``core.llm.base_llm.BaseLLM.aask`` / a fake provider) so no real API calls
happen.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from config.settings import get_settings
from runner.llm_config import load_profile_context
from runner.manager import RunContext
from runner.step_sync import sync_step_to_db
from storage.repos import make_repos

# ---------------------------------------------------------------------------
# Fork lookup roots
# ---------------------------------------------------------------------------


def _example_storage_dirs() -> list[Path]:
    """On-disk fork bases: ``storage`` / ``compressed_storage`` under ``forks_dir``.

    Resolved lazily so the setting (and any ``STT_FORKS_DIR`` override) is
    honoured at call time. Defaults to ``~/.stanford-town-vue/forks``.
    """
    forks_dir = get_settings().expanded_forks_dir()
    return [forks_dir / "storage", forks_dir / "compressed_storage"]

_META_DT_FMT = "%B %d, %Y, %H:%M:%S"
_META_DATE_FMT = "%B %d, %Y"


class RunnerError(RuntimeError):
    """Hard runner failure — propagated so the manager records ``FAILED``."""


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _iso_to_meta_dt(iso: str) -> str:
    """``2023-02-13T07:00:00`` -> ``February 13, 2023, 07:00:00``."""
    s = iso.strip().replace("T", " ")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return dt.strftime(_META_DT_FMT)


def _iso_to_meta_date(iso: str) -> str:
    s = iso.strip().replace("T", " ")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return dt.strftime(_META_DATE_FMT)


def _meta_dt_to_iso(meta_dt: str) -> str:
    """``February 13, 2023, 07:00:00`` -> ``2023-02-13T07:00:00``."""
    try:
        dt = datetime.strptime(meta_dt.strip(), _META_DT_FMT)
    except ValueError:
        # bare date
        dt = datetime.strptime(meta_dt.strip(), _META_DATE_FMT)
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Fork materialisation
# ---------------------------------------------------------------------------


def _find_on_disk_fork(fork_sim_code: str) -> Path | None:
    """Return the path of an on-disk fork base, or ``None`` if not found."""
    for root in _example_storage_dirs():
        candidate = root / fork_sim_code
        if (candidate / "reverie" / "meta.json").is_file() or (
            candidate / "meta.json"
        ).is_file():
            return candidate
    return None


def materialize_fork(
    sim_code: str,
    fork_sim_code: str | None,
    storage_root: Path,
    *,
    session_factory=None,
    personas: list[str] | None = None,
    start_time_iso: str | None = None,
    sec_per_step: int | None = None,
) -> Path:
    """Copy/dump the fork's initial state into ``storage_root/{sim_code}/``.

    Resolution order for ``fork_sim_code``:

    1. An on-disk fork base under ``settings.forks_dir`` (``storage`` or
       ``compressed_storage`` subdir) -> ``shutil.copytree`` it.
    2. A DB simulation with that ``sim_code`` -> dump via
       :func:`storage.exporter.export_simulation` (``layout="live"``).
    3. A directory already present under ``storage_root`` -> copy it.

    Otherwise :class:`RunnerError` is raised.

    The freshly-copied ``reverie/meta.json`` is then settled: persona subset
    applied (when ``personas`` is given) and ``start_time`` / ``curr_time``
    overridden from ``start_time_iso`` (when given). Returns the working dir.
    """
    if not fork_sim_code:
        raise RunnerError(
            f"Simulation {sim_code!r} has no fork_sim_code; cannot bootstrap."
        )

    work_dir = storage_root / sim_code
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.parent.mkdir(parents=True, exist_ok=True)

    on_disk = _find_on_disk_fork(fork_sim_code)
    if on_disk is not None:
        shutil.copytree(on_disk, work_dir)
        logger.info("materialize_fork: copied on-disk fork {} -> {}", on_disk, work_dir)
    elif session_factory is not None and _dump_db_fork(
        fork_sim_code, work_dir, session_factory
    ):
        logger.info("materialize_fork: dumped DB fork {} -> {}", fork_sim_code, work_dir)
    elif (storage_root / fork_sim_code).is_dir():
        shutil.copytree(storage_root / fork_sim_code, work_dir)
        logger.info(
            "materialize_fork: copied storage fork {} -> {}",
            storage_root / fork_sim_code,
            work_dir,
        )
    else:
        raise RunnerError(
            f"fork_sim_code={fork_sim_code!r} not found: not an on-disk fork "
            f"base, not a DB simulation, and not present under {storage_root}."
        )

    _settle_meta(work_dir, personas=personas, start_time_iso=start_time_iso,
                 sec_per_step=sec_per_step)
    return work_dir


def _dump_db_fork(fork_sim_code: str, work_dir: Path, session_factory) -> bool:
    """Dump a DB simulation to ``work_dir`` in *live* layout. Returns success."""
    from storage.exporter import export_simulation

    with session_factory() as session:
        repos = make_repos(session)
        sim = repos.simulations.get_by_code(fork_sim_code)
        if sim is None:
            return False
        # exporter writes into target_dir / sim.sim_code; export to a parent
        # temp then rename into place.
        tmp_parent = work_dir.parent / f".__fork_dump_{fork_sim_code}"
        if tmp_parent.exists():
            shutil.rmtree(tmp_parent)
        export_simulation(sim.id, tmp_parent, session, layout="live")
        dumped = tmp_parent / fork_sim_code
        if work_dir.exists():
            shutil.rmtree(work_dir)
        shutil.move(str(dumped), str(work_dir))
        shutil.rmtree(tmp_parent, ignore_errors=True)
    return True


def _meta_path(work_dir: Path) -> Path:
    """Return the meta.json path, preferring reverie/meta.json."""
    rev = work_dir / "reverie" / "meta.json"
    if rev.is_file():
        return rev
    flat = work_dir / "meta.json"
    if flat.is_file():
        # normalise to reverie/meta.json so STRole.load_from finds it
        rev.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(flat, rev)
        return rev
    raise RunnerError(f"No meta.json found in fork copy at {work_dir}")


def _settle_meta(
    work_dir: Path,
    *,
    personas: list[str] | None,
    start_time_iso: str | None,
    sec_per_step: int | None,
) -> dict:
    """Apply persona subset + start time to the freshly-copied meta.json."""
    meta_path = _meta_path(work_dir)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    if personas:
        known = set(meta.get("persona_names", []))
        unknown = set(personas) - known
        if unknown:
            raise RunnerError(
                f"unknown personas {sorted(unknown)}; fork has {sorted(known)}"
            )
        meta["persona_names"] = list(personas)

    if start_time_iso:
        meta["start_date"] = _iso_to_meta_date(start_time_iso)
        full_dt = _iso_to_meta_dt(start_time_iso)
        meta["start_time"] = full_dt
        meta["curr_time"] = full_dt
    else:
        meta.setdefault("start_time", meta.get("curr_time"))

    if sec_per_step:
        meta["sec_per_step"] = int(sec_per_step)

    meta.setdefault("step", 0)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


# ---------------------------------------------------------------------------
# Town construction
# ---------------------------------------------------------------------------


def build_town(
    sim_code: str,
    work_dir: Path,
    *,
    inner_voice: str | None = None,
    context: Any = None,
):
    """Construct a ``StanfordTown`` + its ``STRole`` list from a working dir.

    Mirrors ``examples/stanford_town/run_st_game.py::startup`` but stops short
    of ``town.run`` — the runner drives ticks itself. Returns ``(town, roles)``.

    ``context`` (optional) is a vendored ``core.context.Context`` whose
    ``config.llm`` selects the LLM provider/model; when ``None`` the simulator
    falls back to ambient ``core`` config.
    """
    # Local imports keep ``runner.st_runner`` importable even when the heavy
    # simulator dependency graph is slow to load / partially configured.
    from simulator.roles.st_role import STRole
    from simulator.town import StanfordTown
    from simulator.utils import llm_logger
    from simulator.utils.mg_ga_transform import write_curr_sim_code, write_curr_step

    meta_path = _meta_path(work_dir)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    persona_names = meta["persona_names"]
    if not persona_names:
        raise RunnerError(f"fork {sim_code!r} has no persona_names")

    iv = inner_voice or persona_names[0]
    if iv not in persona_names:
        raise RunnerError(f"inner_voice {iv!r} not in personas {persona_names}")

    town = StanfordTown(context=context) if context is not None else StanfordTown()
    roles = []
    for role_name in persona_names:
        role = STRole(
            name=role_name,
            profile=role_name,
            sim_code=sim_code,
            step=meta.get("step", 0),
            start_time=meta.get("start_time", meta.get("start_date")),
            curr_time=meta.get("curr_time"),
            sec_per_step=meta.get("sec_per_step", 10),
            has_inner_voice=(role_name == iv),
        )
        roles.append(role)

    # init temp_storage so the simulator's frontend-sync writes don't blow up
    try:
        write_curr_sim_code({"sim_code": sim_code})
        llm_logger.set_sim_code(sim_code)
        write_curr_step({"step": meta.get("step", 0)})
    except Exception as exc:  # noqa: BLE001 — temp_storage is best-effort
        logger.warning("build_town: temp_storage init failed: {}", exc)

    return town, roles


# ---------------------------------------------------------------------------
# Runner entrypoint
# ---------------------------------------------------------------------------


def _load_sim_params(ctx: RunContext) -> dict[str, Any]:
    """Read everything the runner needs from the DB in one short session."""
    from sqlalchemy import select

    from storage.models import SimulationConfigSnapshot

    with ctx.session_factory() as session:
        repos = make_repos(session)
        sim = repos.simulations.get_by_id(ctx.sim_id)
        if sim is None:
            raise RunnerError(f"Simulation id={ctx.sim_id} not found")
        snapshot = session.scalar(
            select(SimulationConfigSnapshot).where(
                SimulationConfigSnapshot.sim_id == ctx.sim_id
            )
        )
        personas: list[str] = []
        llm_profile_id: int | None = None
        if snapshot is not None:
            pf = snapshot.persona_filter_json or {}
            personas = list(pf.get("personas") or [])
            lp = snapshot.llm_profile_json or {}
            llm_profile_id = lp.get("llm_profile_id")
        return {
            "sim_code": sim.sim_code,
            "fork_sim_code": sim.fork_sim_code,
            "n_round": sim.n_round,
            "sec_per_step": sim.sec_per_step,
            "maze_name": sim.maze_name,
            "start_time_iso": sim.start_time_iso,
            "inner_voice": sim.inner_voice,
            "idea": sim.idea,
            "investment": sim.investment,
            "step": sim.step,
            "personas": personas,
            "llm_profile_id": llm_profile_id,
        }


async def stanford_town_runner(ctx: RunContext) -> None:
    """The real :data:`Runner` — drives the vendored StanfordTown simulator.

    Raised exceptions propagate to :meth:`SimulationManager._run`, which records
    ``status=FAILED``.
    """
    from simulator.utils.const import STORAGE_PATH

    params = _load_sim_params(ctx)
    sim_code = params["sim_code"]
    n_round = params["n_round"]
    sec_per_step = params["sec_per_step"] or 10

    logger.info(
        "stanford_town_runner: sim_id={} sim_code={} n_round={}",
        ctx.sim_id,
        sim_code,
        n_round,
    )
    await ctx.emit_status("running")

    storage_root = Path(STORAGE_PATH)
    work_dir = materialize_fork(
        sim_code,
        params["fork_sim_code"],
        storage_root,
        session_factory=ctx.session_factory,
        personas=params["personas"] or None,
        start_time_iso=params["start_time_iso"],
        sec_per_step=sec_per_step,
    )

    # Import the persona subtree (bootstrap memory) into the DB up front, so the
    # personas stay queryable even if the run is interrupted or fails before
    # _persist_final_state runs. The end-of-run sync refreshes them with each
    # role's final memory state. Best-effort: a failure here must not abort the
    # run.
    try:
        _sync_personas_to_db(ctx, work_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "stanford_town_runner: initial persona sync failed for {}: {}",
            sim_code,
            exc,
        )

    # Resolve the LLM context from the simulation's chosen profile (if any).
    # Falls back to ambient `core` config when no profile is attached.
    sim_context = None
    profile_id = params.get("llm_profile_id")
    if profile_id:
        try:
            from config.settings import bootstrap_secret_key

            fernet_key = bootstrap_secret_key()
            sim_context = load_profile_context(
                ctx.session_factory, profile_id, fernet_key
            )
        except Exception as exc:  # noqa: BLE001 — profile is best-effort
            logger.warning(
                "stanford_town_runner: could not load llm_profile_id={}: {} — "
                "falling back to ambient config",
                profile_id,
                exc,
            )

    town, roles = build_town(
        sim_code,
        work_dir,
        inner_voice=params["inner_voice"],
        context=sim_context,
    )
    await town.hire(roles)

    # Kick the simulation off exactly like run_st_game.py's startup:
    # invest a budget and publish the seed `idea` message. Without
    # run_project() the roles stay idle and env.run() is a no-op.
    town.invest(params.get("investment") or 30.0)
    town.run_project(params.get("idea") or "")

    # Step loop — one simulator tick per iteration. We do NOT call
    # town.run(n_round): it loops internally with no per-step hook.
    start_iso = params["start_time_iso"]
    try:
        start_dt = datetime.fromisoformat(start_iso.replace("T", " "))
    except ValueError:
        start_dt = datetime.utcnow()

    for step in range(n_round):
        if ctx.should_stop():
            logger.info("stanford_town_runner: stop requested at step {}", step)
            break
        # Block here while paused; the manager sets the event on resume/stop.
        await ctx.pause_event.wait()
        if ctx.should_stop():
            break

        await town.env.run()  # one tick — roles write environment/N + movement/N

        # The roles increment their own ``step`` after writing files, so the
        # files for this iteration land at index ``step``.
        movement_rows = sync_step_to_db(
            ctx.session_factory, ctx.sim_id, step, work_dir
        )
        curr_dt = start_dt + timedelta(seconds=sec_per_step * (step + 1))
        curr_time_iso = curr_dt.isoformat(timespec="seconds")
        with ctx.session_factory() as session:
            make_repos(session).simulations.advance_step(
                ctx.sim_id, step, curr_time_iso
            )
        await ctx.emit_step(step, curr_time_iso, movement_rows)

    # Persist each persona's final memory state so the completed sim is fully
    # queryable. STRole.save_into writes JSON back into the working dir; we then
    # re-import that persona subtree into the DB.
    _persist_final_state(ctx, sim_code, work_dir, roles)
    logger.info("stanford_town_runner: sim_id={} loop finished", ctx.sim_id)


def _sync_personas_to_db(ctx: RunContext, work_dir: Path) -> None:
    """Re-import the persona subtree from ``work_dir`` into the DB for this sim.

    Drops any prior persona rows for ``ctx.sim_id`` (plus their memory), then
    re-imports them via the importer's per-persona helper. We reuse
    ``_import_personas`` rather than ``import_simulation`` — the latter would
    hard-delete and recreate the ``simulations`` row (new id), breaking the
    manager's ``ctx.sim_id`` handle and clobbering the live status / step
    counter. Here we keep the existing row and only refresh personas + their
    memory, scoped to ``ctx.sim_id``.

    Called twice per run: once up front (from bootstrap memory, so personas are
    queryable even if the run is interrupted before completion) and once at the
    end (to refresh them with each role's final memory state).
    """
    from sqlalchemy import delete, select

    from storage.importer import _import_personas
    from storage.models import (
        MemoryKeywordsToChat,
        MemoryKeywordsToEvent,
        MemoryKeywordsToThought,
        MemoryNode,
        Persona,
        SpatialMemoryTree,
    )

    with ctx.session_factory() as session:
        repos = make_repos(session)
        sim = repos.simulations.get_by_id(ctx.sim_id)
        if sim is None:
            return
        # Drop any prior persona rows for this sim so the refresh is clean.
        persona_ids = list(
            session.scalars(
                select(Persona.id).where(Persona.sim_id == ctx.sim_id)
            ).all()
        )
        if persona_ids:
            for tbl in (
                MemoryKeywordsToEvent,
                MemoryKeywordsToChat,
                MemoryKeywordsToThought,
                MemoryNode,
                SpatialMemoryTree,
            ):
                session.execute(
                    delete(tbl).where(tbl.persona_id.in_(persona_ids))
                )
            session.execute(delete(Persona).where(Persona.id.in_(persona_ids)))
            session.commit()

        try:
            start_dt = datetime.fromisoformat(
                sim.start_time_iso.replace("T", " ")
            )
        except ValueError:
            start_dt = datetime.utcnow()
        _import_personas(
            repos,
            ctx.sim_id,
            work_dir,
            start_dt,
            sim.sec_per_step or 10,
            log_progress=False,
        )


def _persist_final_state(ctx: RunContext, sim_code: str, work_dir: Path, roles) -> None:
    """Save role memory to disk, then sync the persona subtree into the DB."""
    for role in roles:
        try:
            role.save_into()
        except Exception as exc:  # noqa: BLE001 — defensive, keep going
            logger.warning("save_into failed for {}: {}", role.name, exc)

    try:
        _sync_personas_to_db(ctx, work_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("final-state persona sync failed for {}: {}", sim_code, exc)


__all__ = [
    "stanford_town_runner",
    "materialize_fork",
    "build_town",
    "RunnerError",
]
