"""Per-step JSON -> SQLite sync for the live StanfordTown runner.

The vendored simulator writes one ``environment/{step}.json`` and one
``movement/{step}.json`` file per tick under its working directory
(``STORAGE_PATH/{sim_code}/``). This module reads those files back and
upserts them into the DB via the M2 :mod:`storage.repos` layer, reusing
the original-JSON parsers from :mod:`storage.json_schemas` and the
row-shaping helper from :mod:`storage.importer`.

It deliberately does **not** touch the simulator's JSON I/O — it only
consumes the files the simulator already wrote.

A sibling function, :func:`sync_personas_step`, is the "live object"
counterpart of :func:`sync_step_to_db`: instead of reading files it reads
the in-process ``STRole`` objects and incrementally upserts each persona's
associative memory / scratch / spatial memory so a running (or interrupted)
simulation stays queryable in the persona detail view.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

from storage.importer import _node_to_row, _personas_to_movement_rows
from storage.json_schemas import (
    AssociativeMemoryNode,
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


def _basic_memory_to_row(bm, start_dt: datetime, sec_per_step: int) -> dict:
    """Translate one live ``BasicMemory`` into a ``MemoryNode`` row dict.

    Routes the final shaping through the importer's :func:`_node_to_row` so the
    row shape stays single-sourced with the start-of-run / end-of-run imports.

    ``BasicMemory.save_to_dict()`` returns ``{node_id: node_dict}`` where
    ``node_dict`` carries the canonical GA ``nodes.json`` keys (``node_count`` /
    ``type`` / ``created`` already serialized to ``"%Y-%m-%d %H:%M:%S"`` / ...).
    Its key set is a superset of :class:`AssociativeMemoryNode`'s required
    fields; the extra ``cause_by`` is accepted by ``ConfigDict(extra="allow")``.
    """
    dumped = bm.save_to_dict()
    node_id, node_dict = next(iter(dumped.items()))
    amn = AssociativeMemoryNode(**node_dict)
    return _node_to_row(node_id, amn, start_dt, sec_per_step)


def sync_personas_step(
    session_factory,
    sim_id: int,
    roles,
    start_dt: datetime,
    sec_per_step: int,
) -> None:
    """Incrementally sync each live ``STRole``'s persona state into the DB.

    The "live object" counterpart of :func:`sync_step_to_db`: where that
    function ingests the per-step JSON files, this one reads the in-process
    ``STRole`` objects and upserts each persona's associative memory, scratch
    and spatial memory. Called once per tick, right after
    :func:`sync_step_to_db`.

    Parameters
    ----------
    session_factory:
        A ``sessionmaker``; a short-lived session is opened and closed here.
    sim_id:
        Target ``simulations.id``.
    roles:
        The live ``STRole`` objects (each exposes ``.name``,
        ``.a_mem.storage``, ``.scratch`` and ``.s_mem.tree``).
    start_dt:
        Simulation start time — used to translate game-time node timestamps
        into step indices.
    sec_per_step:
        Seconds of game time per step.

    Each role is processed inside its own ``try``/``except`` so one bad role
    cannot block its siblings. A missing persona row (the rare case where the
    start-of-run ``_sync_personas_to_db`` failed) is skipped with a debug log;
    the end-of-run ``_persist_final_state`` is the backstop there.
    """
    with session_factory() as session:
        repos = make_repos(session)

        for role in roles:
            try:
                persona = repos.personas.get(sim_id, role.name)
                if persona is None:
                    logger.debug(
                        "sync_personas_step: no persona row for {!r} in sim {} "
                        "— skipping",
                        role.name,
                        sim_id,
                    )
                    continue

                # --- incremental associative memory nodes + keywords --------
                watermark = repos.memory.get_max_node_count(persona.id)
                new_mems = [
                    bm
                    for bm in role.a_mem.storage
                    if bm.memory_count > watermark
                ]
                if new_mems:
                    node_rows = [
                        _basic_memory_to_row(bm, start_dt, sec_per_step)
                        for bm in new_mems
                    ]
                    repos.memory.add_nodes_bulk(persona.id, node_rows)

                    keyword_buckets: dict[str, list[tuple[str, str]]] = {}
                    for row in node_rows:
                        node_type_val = row["node_type"]
                        node_id = row["node_id"]
                        for kw in row["keywords_json"]:
                            keyword_buckets.setdefault(node_type_val, []).append(
                                (kw, node_id)
                            )
                    for node_type_val, items in keyword_buckets.items():
                        if items:
                            repos.memory.add_keywords_bulk(
                                persona.id, node_type_val, items
                            )

                # --- scratch (single row, idempotent overwrite) -------------
                repos.personas.save_scratch(
                    persona.id, role.scratch.model_dump()
                )

                # --- spatial memory (single row, idempotent overwrite) ------
                repos.personas.save_spatial_memory(
                    persona.id, role.s_mem.tree
                )
            except Exception as exc:  # noqa: BLE001 — one bad role must not
                # block its siblings; log and carry on.
                logger.warning(
                    "sync_personas_step: failed to sync persona {!r}: {}",
                    getattr(role, "name", "?"),
                    exc,
                )


__all__ = ["sync_step_to_db", "sync_personas_step"]
