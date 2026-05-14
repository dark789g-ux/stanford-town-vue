"""Recover the ``personas`` table for simulations that ran but never finished.

A simulation that is *interrupted* (the backend process exited mid-run) or
*failed* before its step loop completes never reaches ``_persist_final_state``,
so its ``personas`` table stays empty even though ``step_movements`` already
accumulated live data. The UI agent list is sourced from ``step_movements``, so
it still shows agents — but their persona detail page 404s
("Persona '...' not found in sim N").

This script re-imports the persona subtree from each affected simulation's
on-disk working dir (``STORAGE_PATH/{sim_code}``), reusing the exact same
``_sync_personas_to_db`` path the runner now uses at run start and end.

Run from ``backend/``::

    python -m scripts.recover_personas                    # scan + recover all affected sims
    python -m scripts.recover_personas 24                 # recover sim id 24
    python -m scripts.recover_personas test-20260514-03   # recover by sim_code
    python -m scripts.recover_personas --dry-run          # report only, no writes
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from loguru import logger
from sqlalchemy import func, select

from runner.st_runner import _sync_personas_to_db
from simulator.utils.const import STORAGE_PATH
from storage.db import SessionLocal
from storage.models import Persona, Simulation, StepMovement


def _affected_sim_ids() -> list[int]:
    """Sim ids that have ``step_movements`` rows but zero ``personas`` rows."""
    with SessionLocal() as session:
        with_moves = set(session.scalars(select(StepMovement.sim_id).distinct()).all())
        with_personas = set(session.scalars(select(Persona.sim_id).distinct()).all())
    return sorted(with_moves - with_personas)


def _resolve(token: str) -> int | None:
    """Resolve a CLI token (numeric sim id or sim_code) to a sim id."""
    with SessionLocal() as session:
        if token.isdigit():
            sim = session.get(Simulation, int(token))
        else:
            sim = session.scalar(select(Simulation).where(Simulation.sim_code == token))
        return sim.id if sim else None


def recover(sim_id: int, *, dry_run: bool) -> bool:
    """Re-import personas for one sim from its on-disk work_dir. Returns success."""
    with SessionLocal() as session:
        sim = session.get(Simulation, sim_id)
        if sim is None:
            logger.error("sim id={} not found", sim_id)
            return False
        sim_code, status = sim.sim_code, sim.status
        existing = session.scalar(
            select(func.count(Persona.id)).where(Persona.sim_id == sim_id)
        )

    work_dir = Path(STORAGE_PATH) / sim_code
    if not (work_dir / "reverie" / "meta.json").is_file() or not (
        work_dir / "personas"
    ).is_dir():
        logger.error(
            "sim id={} sim_code={!r}: work_dir {} is missing personas/ or "
            "reverie/meta.json — cannot recover from disk",
            sim_id,
            sim_code,
            work_dir,
        )
        return False

    on_disk = [p.name for p in sorted((work_dir / "personas").iterdir()) if p.is_dir()]
    logger.info(
        "sim id={} sim_code={!r} status={} | personas in DB: {} | on disk: {}",
        sim_id,
        sim_code,
        status,
        existing,
        on_disk,
    )
    if status == "running":
        logger.warning(
            "  sim id={} is still RUNNING — its work_dir is being written; "
            "recovering it now may race with the live runner",
            sim_id,
        )
    if dry_run:
        logger.info("  [dry-run] would re-import {} persona(s)", len(on_disk))
        return True

    ctx = SimpleNamespace(session_factory=SessionLocal, sim_id=sim_id)
    _sync_personas_to_db(ctx, work_dir)

    with SessionLocal() as session:
        now = session.scalar(
            select(func.count(Persona.id)).where(Persona.sim_id == sim_id)
        )
    logger.info("  recovered: personas in DB now {}", now)
    return True


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    args = [a for a in argv if a != "--dry-run"]

    if args:
        sim_ids: list[int] = []
        for tok in args:
            sid = _resolve(tok)
            if sid is None:
                logger.error("could not resolve {!r} to a simulation", tok)
                return 1
            sim_ids.append(sid)
    else:
        sim_ids = _affected_sim_ids()
        if not sim_ids:
            logger.info("no affected simulations found (every sim with movements "
                        "already has personas)")
            return 0
        logger.info("affected simulations (movements but no personas): {}", sim_ids)

    ok = True
    for sid in sim_ids:
        ok = recover(sid, dry_run=dry_run) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
