"""Backfill ``memory_nodes.embedding_key`` for nodes imported before the column existed.

``embedding_key`` holds a memory node's canonical natural-language text — what
the simulator feeds into reflection/planning/conversation prompts. The original
``memory_nodes`` schema had no column for it, so the importer silently dropped
it and the exporter could only emit ``""``. Any fork descended from a DB-only
simulation therefore loaded personas with blank memory text (and, before the
loader was hardened, crashed outright with ``KeyError: ''``).

The migration that adds the column defaults existing rows to ``""``. This
script recovers the lost text from each simulation's on-disk working dir
(``STORAGE_PATH/{sim_code}/.../associative_memory/nodes.json``), which — when it
was written by a real simulator run — still carries the original
``embedding_key`` values. Nodes are matched by ``node_id``. Sims whose on-disk
file is missing or itself empty (e.g. written by the old exporter) are left
untouched; re-running those forks regenerates them from a now-fixed source.

Run from ``backend/``::

    python -m scripts.recover_embedding_keys              # scan + backfill all sims
    python -m scripts.recover_embedding_keys 23           # backfill sim id 23
    python -m scripts.recover_embedding_keys test-20260514-02   # backfill by sim_code
    python -m scripts.recover_embedding_keys --dry-run    # report only, no writes
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from loguru import logger
from sqlalchemy import func, select

from simulator.utils.const import STORAGE_PATH
from storage.db import SessionLocal
from storage.models import MemoryNode, Persona, Simulation


def _sims_with_empty_keys() -> list[int]:
    """Sim ids that have at least one ``memory_nodes`` row with an empty key."""
    with SessionLocal() as session:
        rows = session.execute(
            select(Persona.sim_id)
            .join(MemoryNode, MemoryNode.persona_id == Persona.id)
            .where(MemoryNode.embedding_key == "")
            .distinct()
        ).scalars()
        return sorted(set(rows))


def _resolve(token: str) -> int | None:
    """Resolve a CLI token (numeric sim id or sim_code) to a sim id."""
    with SessionLocal() as session:
        if token.isdigit():
            sim = session.get(Simulation, int(token))
        else:
            sim = session.scalar(
                select(Simulation).where(Simulation.sim_code == token)
            )
        return sim.id if sim else None


def _disk_embedding_keys(work_dir: Path, persona_name: str) -> dict[str, str]:
    """Read ``{node_id: embedding_key}`` from a persona's on-disk nodes.json."""
    nodes_path = (
        work_dir
        / "personas"
        / persona_name
        / "bootstrap_memory"
        / "associative_memory"
        / "nodes.json"
    )
    if not nodes_path.is_file():
        return {}
    try:
        raw = json.loads(nodes_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("  could not read {}: {}", nodes_path, exc)
        return {}
    return {
        node_id: (rec.get("embedding_key") or "")
        for node_id, rec in raw.items()
        if isinstance(rec, dict)
    }


def recover(sim_id: int, *, dry_run: bool) -> bool:
    """Backfill empty embedding_keys for one sim from its on-disk work_dir."""
    with SessionLocal() as session:
        sim = session.get(Simulation, sim_id)
        if sim is None:
            logger.error("sim id={} not found", sim_id)
            return False
        sim_code = sim.sim_code
        personas = session.scalars(
            select(Persona).where(Persona.sim_id == sim_id)
        ).all()
        persona_names = {p.id: p.name for p in personas}

    work_dir = Path(STORAGE_PATH) / sim_code
    total_filled = 0
    total_empty = 0

    with SessionLocal() as session:
        for persona_id, name in persona_names.items():
            nodes = session.scalars(
                select(MemoryNode).where(
                    MemoryNode.persona_id == persona_id,
                    MemoryNode.embedding_key == "",
                )
            ).all()
            if not nodes:
                continue
            total_empty += len(nodes)
            disk_keys = _disk_embedding_keys(work_dir, name)
            filled = 0
            for node in nodes:
                key = disk_keys.get(node.node_id, "")
                if key:
                    if not dry_run:
                        node.embedding_key = key
                    filled += 1
            total_filled += filled
            logger.info(
                "  sim id={} persona={!r}: {}/{} empty keys recoverable from disk",
                sim_id,
                name,
                filled,
                len(nodes),
            )
        if not dry_run:
            session.commit()

    if total_empty == 0:
        logger.info("sim id={} sim_code={!r}: no empty keys", sim_id, sim_code)
    else:
        verb = "would recover" if dry_run else "recovered"
        logger.info(
            "sim id={} sim_code={!r}: {} {}/{} embedding_key(s){}",
            sim_id,
            sim_code,
            verb,
            total_filled,
            total_empty,
            "" if total_filled == total_empty
            else " (rest unrecoverable — on-disk source missing or empty)",
        )
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
        sim_ids = _sims_with_empty_keys()
        if not sim_ids:
            logger.info("no simulations have empty embedding_key rows")
            return 0
        logger.info("simulations with empty embedding_key rows: {}", sim_ids)

    with SessionLocal() as session:
        before = session.scalar(
            select(func.count(MemoryNode.id)).where(MemoryNode.embedding_key == "")
        )
    logger.info("empty embedding_key rows in DB: {}", before)

    ok = True
    for sid in sim_ids:
        ok = recover(sid, dry_run=dry_run) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
