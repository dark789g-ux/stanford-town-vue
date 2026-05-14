"""Per-step JSON -> SQLite sync for the live StanfordTown runner.

The vendored simulator writes one ``environment/{step}.json`` and one
``movement/{step}.json`` file per tick under its working directory
(``STORAGE_PATH/{sim_code}/``). This module reads those files back and
upserts them into the DB via the M2 :mod:`storage.repos` layer, reusing
the original-JSON parsers from :mod:`storage.json_schemas` and the
row-shaping helper from :mod:`storage.importer`.

It deliberately does **not** touch the simulator's JSON I/O — it only
consumes the files the simulator already wrote.
"""

from __future__ import annotations

from pathlib import Path

from storage.importer import _personas_to_movement_rows
from storage.json_schemas import (
    parse_environment_snapshot,
    parse_movement_snapshot,
)
from storage.repos import make_repos


def _env_file(storage_dir: Path, step: int) -> Path:
    return storage_dir / "environment" / f"{step}.json"


def _movement_file(storage_dir: Path, step: int) -> Path:
    return storage_dir / "movement" / f"{step}.json"


def sync_step_to_db(
    session_factory,
    sim_id: int,
    step: int,
    storage_dir: Path | str,
) -> list[dict]:
    """Read ``environment/{step}.json`` + ``movement/{step}.json`` and persist.

    Parameters
    ----------
    session_factory:
        A ``sessionmaker``; a short-lived session is opened and closed here.
    sim_id:
        Target ``simulations.id``.
    step:
        The step index whose JSON files should be ingested.
    storage_dir:
        The simulator's per-sim working directory (``STORAGE_PATH/{sim_code}``).

    Returns
    -------
    list[dict]
        The movement rows that were written (the same dicts passed to
        :meth:`StepRepo.upsert_movements_for_step`). Useful for the runner to
        forward into ``ctx.emit_step``. Empty list when no movement file exists.
    """
    storage_dir = Path(storage_dir)
    env_path = _env_file(storage_dir, step)
    move_path = _movement_file(storage_dir, step)

    movement_rows: list[dict] = []
    with session_factory() as session:
        repos = make_repos(session)

        # --- environment/{step}.json -> step_environments -----------------
        if env_path.is_file():
            payload = parse_environment_snapshot(env_path.read_text(encoding="utf-8"))
            plain = {name: pos.model_dump() for name, pos in payload.items()}
            repos.steps.upsert_environment(sim_id, step, plain)

        # --- movement/{step}.json -> step_movements -----------------------
        if move_path.is_file():
            snap = parse_movement_snapshot(move_path.read_text(encoding="utf-8"))
            movement_rows = _personas_to_movement_rows(snap.persona)
            if movement_rows:
                repos.steps.upsert_movements_for_step(sim_id, step, movement_rows)

    return movement_rows


__all__ = ["sync_step_to_db"]
