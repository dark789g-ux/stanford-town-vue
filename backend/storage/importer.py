"""Importer: read an original Stanford Town JSON storage directory into SQLite.

This module is the M2 wave 2 / Agent D piece. It reads a directory laid out per
``docs/json_format.md`` (either a live simulation tree with
``reverie/meta.json`` + per-step ``movement/{N}.json`` + ``environment/{N}.json``
files, OR a compressed archive with a single ``meta.json`` and
``master_movement.json``) and ingests every row into SQLite via the
:mod:`storage.repos` layer.

The importer is *idempotent* under ``on_conflict="replace"`` — running it twice
yields the same final DB state. It does NOT touch the simulator or core
packages; it only depends on :mod:`storage` siblings.

Embeddings (``embeddings.json``) and aggregate keyword strength counts
(``kw_strength.json``) are intentionally **skipped** — the SQLite schema does
not persist them. The exporter rebuilds ``kw_strength`` by walking
``memory_nodes``; embeddings can be regenerated on demand by callers that need
them.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from storage.json_schemas import (
    AssociativeMemoryNode,
    LlmLogLine,
    parse_environment_snapshot,
    parse_master_movement,
    parse_movement_snapshot,
    parse_nodes,
    parse_reverie_meta,
    parse_scratch,
    parse_spatial_memory,
)
from storage.models import (
    LlmCall,
    MemoryKeywordsToChat,
    MemoryKeywordsToEvent,
    MemoryKeywordsToThought,
    MemoryNode,
    MemoryNodeType,
    Persona,
    Simulation,
    SimulationConfigSnapshot,
    SimulationStatus,
    SpatialMemoryTree,
    StepEnvironment,
    StepMovement,
)
from storage.repos import make_repos

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SimulationExists(Exception):
    """Raised when on_conflict='fail' and a sim with the same sim_code exists."""

    def __init__(self, sim_code: str, sim_id: int) -> None:
        super().__init__(
            f"Simulation with sim_code={sim_code!r} already exists (id={sim_id})."
        )
        self.sim_code = sim_code
        self.sim_id = sim_id


class ImportError_(Exception):
    """Generic importer failure (malformed source tree, etc.)."""


# ---------------------------------------------------------------------------
# Time conversion helpers
# ---------------------------------------------------------------------------

_META_START_DATE_FMT = "%B %d, %Y"
_META_CURR_TIME_FMT = "%B %d, %Y, %H:%M:%S"
_NODE_TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"


def _parse_meta_start_date(s: str) -> datetime:
    """Parse `reverie/meta.json#start_date` ('%B %d, %Y') into a datetime at 00:00:00."""
    return datetime.strptime(s.strip(), _META_START_DATE_FMT)


def _parse_meta_curr_time(s: str) -> datetime:
    """Parse `reverie/meta.json#curr_time` ('%B %d, %Y, %H:%M:%S')."""
    return datetime.strptime(s.strip(), _META_CURR_TIME_FMT)


def _parse_node_timestamp(s: str) -> datetime:
    """Parse a `nodes.json` `created` / `expiration` ('%Y-%m-%d %H:%M:%S')."""
    return datetime.strptime(s.strip(), _NODE_TIMESTAMP_FMT)


def _to_iso(dt: datetime) -> str:
    """Format a naive datetime to ISO 8601 (no timezone, second precision)."""
    return dt.isoformat(timespec="seconds")


def _game_time_to_step(ts_str: str, start_dt: datetime, sec_per_step: int) -> int:
    """Convert a `%Y-%m-%d %H:%M:%S` game-time string to a step index.

    Negative result means the event happened before sim start (e.g. bootstrap
    seed memories). The DB column accepts negative values and they are
    semantically distinct from `0` (which is sim start).
    """
    try:
        ts = _parse_node_timestamp(ts_str)
    except ValueError:
        # If the timestamp is malformed, use -1 as a sentinel rather than
        # blowing up the whole import. The exporter can detect this on round
        # trip but legacy seed data rarely carries malformed values.
        return -1
    delta = (ts - start_dt).total_seconds()
    step_size = sec_per_step if sec_per_step > 0 else 10
    return int(delta // step_size)


# ---------------------------------------------------------------------------
# Layout detection
# ---------------------------------------------------------------------------


def _find_meta_path(source_dir: Path) -> Path:
    """Locate the meta.json file. Tries `reverie/meta.json` then `meta.json`."""
    candidate = source_dir / "reverie" / "meta.json"
    if candidate.is_file():
        return candidate
    candidate = source_dir / "meta.json"
    if candidate.is_file():
        return candidate
    raise ImportError_(
        f"No meta.json found in {source_dir} "
        "(tried reverie/meta.json and meta.json at root)."
    )


def _iter_persona_dirs(source_dir: Path) -> Iterable[Path]:
    """Yield each persona directory under `source_dir/personas/`.

    A persona directory is any direct child of `personas/` that exists; we
    don't filter by inner contents — the per-file readers tolerate missing
    files.
    """
    personas_root = source_dir / "personas"
    if not personas_root.is_dir():
        return
    for child in sorted(personas_root.iterdir()):
        if child.is_dir():
            yield child


# ---------------------------------------------------------------------------
# Provider inference for llm_logs
# ---------------------------------------------------------------------------


def _infer_provider(model: str | None) -> str:
    """Infer the provider name from a model identifier."""
    if not model:
        return "unknown"
    m = model.lower()
    if m.startswith("gpt") or m.startswith("o1") or m.startswith("text-embedding"):
        return "openai"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("deepseek"):
        return "deepseek"
    return "unknown"


# ---------------------------------------------------------------------------
# Per-section importers (private)
# ---------------------------------------------------------------------------


_STEP_FILENAME_RE = re.compile(r"^(\d+)\.json$")


def _read_json_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _import_personas(
    repos,
    sim_id: int,
    source_dir: Path,
    start_dt: datetime,
    sec_per_step: int,
    log_progress: bool,
) -> tuple[int, int]:
    """Import all personas + their bootstrap memory.

    Returns ``(persona_count, total_nodes_inserted)``.
    """
    persona_count = 0
    nodes_total = 0

    for persona_dir in _iter_persona_dirs(source_dir):
        name = persona_dir.name
        bootstrap_dir = persona_dir / "bootstrap_memory"

        # --- scratch (optional)
        scratch_path = bootstrap_dir / "scratch.json"
        scratch_dict: dict[str, Any] | None = None
        age: int | None = None
        plan_text: str | None = None
        if scratch_path.is_file():
            try:
                scratch_model = parse_scratch(_read_json_text(scratch_path))
                scratch_dict = scratch_model.model_dump()
                age = scratch_dict.get("age")
                plan_text = scratch_dict.get("daily_plan_req")
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("Failed to parse scratch for %s: %s", name, exc)

        persona = repos.personas.create(
            sim_id,
            name=name,
            age=age,
            plan_text=plan_text,
        )
        persona_count += 1
        if log_progress:
            logger.info("Imported persona %s (id=%s)", name, persona.id)

        if scratch_dict is not None:
            repos.personas.save_scratch(persona.id, scratch_dict)

        # --- spatial memory (optional)
        spatial_path = bootstrap_dir / "spatial_memory.json"
        if spatial_path.is_file():
            try:
                tree = parse_spatial_memory(_read_json_text(spatial_path))
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("Failed to parse spatial_memory for %s: %s", name, exc)
                tree = {}
            repos.personas.save_spatial_memory(persona.id, tree)

        # --- associative memory nodes
        nodes_path = bootstrap_dir / "associative_memory" / "nodes.json"
        if nodes_path.is_file():
            try:
                nodes_map = parse_nodes(_read_json_text(nodes_path))
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("Failed to parse nodes.json for %s: %s", name, exc)
                nodes_map = {}

            node_rows: list[dict[str, Any]] = []
            keyword_buckets: dict[str, list[tuple[str, str]]] = {
                MemoryNodeType.EVENT.value: [],
                MemoryNodeType.CHAT.value: [],
                MemoryNodeType.THOUGHT.value: [],
            }

            for node_id, node in nodes_map.items():
                row = _node_to_row(node_id, node, start_dt, sec_per_step)
                node_rows.append(row)
                node_type_val = row["node_type"]
                for kw in node.keywords:
                    keyword_buckets[node_type_val].append((kw, node_id))

            if node_rows:
                inserted = repos.memory.add_nodes_bulk(persona.id, node_rows)
                nodes_total += inserted

            for node_type_val, items in keyword_buckets.items():
                if items:
                    repos.memory.add_keywords_bulk(persona.id, node_type_val, items)

        # --- kw_strength.json and embeddings.json are intentionally skipped.
        # kw_strength is rebuilt by the exporter from memory_nodes.keywords_json;
        # embeddings are never persisted in this fork.

    return persona_count, nodes_total


def _node_to_row(
    node_id: str,
    node: AssociativeMemoryNode,
    start_dt: datetime,
    sec_per_step: int,
) -> dict[str, Any]:
    """Translate one parsed ``AssociativeMemoryNode`` into a MemoryNode row dict."""
    created_step = _game_time_to_step(node.created, start_dt, sec_per_step)
    expiration_step: int | None
    if node.expiration is None:
        expiration_step = None
    else:
        expiration_step = _game_time_to_step(node.expiration, start_dt, sec_per_step)

    # filling can be: None, [], list[str], list[list[str]], or a bare string.
    # The SQLite column is JSON nullable. Preserve the value verbatim except a
    # bare string is wrapped into a single-element list for downstream sanity.
    filling: Any = node.filling
    if isinstance(filling, str):
        filling = [filling]

    return {
        "node_id": node_id,
        "node_type": node.type,
        "node_count": node.node_count,
        "type_count": node.type_count,
        "depth": node.depth,
        "created": created_step,
        "expiration_step": expiration_step,
        "subject": node.subject,
        "predicate": node.predicate,
        "object": node.object,
        "description": node.description,
        "poignancy": node.poignancy,
        "keywords_json": list(node.keywords),
        "filling_json": filling,
    }


def _import_environment_files(repos, sim_id: int, source_dir: Path) -> int:
    """Import every ``environment/{N}.json`` into ``step_environments``."""
    env_dir = source_dir / "environment"
    if not env_dir.is_dir():
        return 0
    count = 0
    for path in sorted(env_dir.iterdir(), key=_step_sort_key):
        m = _STEP_FILENAME_RE.match(path.name)
        if not m:
            continue
        step = int(m.group(1))
        try:
            payload = parse_environment_snapshot(_read_json_text(path))
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Skipping malformed env file %s: %s", path, exc)
            continue
        # Serialise PersonaPosition models back to plain dicts for JSON column.
        plain = {name: pos.model_dump() for name, pos in payload.items()}
        repos.steps.upsert_environment(sim_id, step, plain)
        count += 1
    return count


def _step_sort_key(path: Path) -> tuple[int, str]:
    m = _STEP_FILENAME_RE.match(path.name)
    if m:
        return (int(m.group(1)), path.name)
    return (10**12, path.name)


def _import_movement_files(repos, sim_id: int, source_dir: Path) -> int:
    """Import per-step ``movement/{N}.json`` files. Returns rows inserted."""
    move_dir = source_dir / "movement"
    if not move_dir.is_dir():
        return 0
    rows_inserted = 0
    for path in sorted(move_dir.iterdir(), key=_step_sort_key):
        m = _STEP_FILENAME_RE.match(path.name)
        if not m:
            continue
        step = int(m.group(1))
        try:
            snap = parse_movement_snapshot(_read_json_text(path))
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Skipping malformed movement file %s: %s", path, exc)
            continue
        movement_rows = _personas_to_movement_rows(snap.persona)
        if movement_rows:
            repos.steps.upsert_movements_for_step(sim_id, step, movement_rows)
            rows_inserted += len(movement_rows)
    return rows_inserted


def _import_master_movement(repos, sim_id: int, source_dir: Path) -> int:
    """Import compressed-archive ``master_movement.json`` into ``step_movements``."""
    path = source_dir / "master_movement.json"
    if not path.is_file():
        return 0
    try:
        master = parse_master_movement(_read_json_text(path))
    except Exception as exc:
        raise ImportError_(f"Failed to parse master_movement.json: {exc}") from exc

    rows_inserted = 0
    for step_str, per_persona in master.items():
        try:
            step = int(step_str)
        except ValueError:
            logger.warning("Skipping non-integer step key %r in master_movement", step_str)
            continue
        movement_rows = _personas_to_movement_rows(per_persona)
        if movement_rows:
            repos.steps.upsert_movements_for_step(sim_id, step, movement_rows)
            rows_inserted += len(movement_rows)
    return rows_inserted


def _personas_to_movement_rows(personas_map: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert ``{name: PersonaMovement}`` into row dicts for ``StepRepo``."""
    rows: list[dict[str, Any]] = []
    for name, pm in personas_map.items():
        # Tolerate both pydantic models and raw dicts.
        if hasattr(pm, "model_dump"):
            d = pm.model_dump()
        else:
            d = dict(pm)
        movement = d.get("movement") or [0, 0]
        if not isinstance(movement, (list, tuple)) or len(movement) < 2:
            movement = [0, 0]
        description = d.get("description")
        location_path: str | None = None
        if isinstance(description, str) and " @ " in description:
            try:
                location_path = description.split(" @ ", 1)[1]
            except IndexError:
                location_path = None
        rows.append(
            {
                "persona_name": name,
                "x": int(movement[0]),
                "y": int(movement[1]),
                "description": description,
                "pronunciatio": d.get("pronunciatio"),
                # StepRepo.upsert_movements_for_step reads the "chat" key.
                "chat": d.get("chat"),
                "location_path": location_path,
            }
        )
    return rows


def _import_llm_logs(repos, sim_id: int, source_dir: Path) -> int:
    """Bulk-import an ``llm_logs.jsonl`` file (if present). Returns row count."""
    path = source_dir / "llm_logs.jsonl"
    if not path.is_file():
        return 0
    text = _read_json_text(path)
    rows: list[dict[str, Any]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            line = LlmLogLine.model_validate_json(raw)
        except Exception as exc:  # noqa: BLE001 — tolerate malformed log lines
            logger.warning(
                "Skipping malformed llm_logs.jsonl line %d in %s: %s",
                lineno,
                path,
                exc,
            )
            continue
        rows.append(_llm_log_line_to_row(line))
    if not rows:
        return 0
    return repos.llm_logs.add_bulk(sim_id, rows)


def _llm_log_line_to_row(line: LlmLogLine) -> dict[str, Any]:
    """Translate one parsed ``LlmLogLine`` into an ``llm_calls`` row dict."""
    usage = line.usage
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    # Timestamp: parse ISO-8601 with timezone offset; fall back to wall clock.
    try:
        ts = datetime.fromisoformat(line.ts)
    except (ValueError, TypeError):
        ts = datetime.utcnow()
    return {
        "persona_name": line.persona,
        "step": line.step,
        "ts": ts,
        "model": line.model or "",
        "provider": _infer_provider(line.model),
        "prompt": line.prompt,
        "response": line.response or "",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_ms": line.latency_ms,
        "error": line.error,
    }


# ---------------------------------------------------------------------------
# Cascade-delete helper (database-agnostic)
# ---------------------------------------------------------------------------


def _hard_delete_sim(session: Session, sim_id: int) -> None:
    """Delete a simulation row and all rows that reference it.

    We do this explicitly (per-table DELETE statements) rather than relying on
    ``ON DELETE CASCADE`` because SQLite only enforces FK constraints when the
    connection has ``PRAGMA foreign_keys=ON`` and the production
    ``storage.db.engine`` does not set that pragma by default.
    """
    # Gather persona ids first; some child tables key off persona_id, not sim_id.
    persona_ids = list(
        session.scalars(select(Persona.id).where(Persona.sim_id == sim_id)).all()
    )

    if persona_ids:
        # Order matters: drop indices that reference Persona before personas.
        for tbl in (
            MemoryKeywordsToEvent,
            MemoryKeywordsToChat,
            MemoryKeywordsToThought,
            MemoryNode,
            SpatialMemoryTree,
        ):
            session.execute(delete(tbl).where(tbl.persona_id.in_(persona_ids)))
        session.execute(delete(Persona).where(Persona.id.in_(persona_ids)))

    for tbl in (StepEnvironment, StepMovement, LlmCall, SimulationConfigSnapshot):
        session.execute(delete(tbl).where(tbl.sim_id == sim_id))

    session.execute(delete(Simulation).where(Simulation.id == sim_id))
    session.commit()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def import_simulation(
    source_dir: Path | str,
    session: Session,
    *,
    sim_code_override: str | None = None,
    on_conflict: Literal["fail", "replace", "skip"] = "fail",
    log_progress: bool = False,
) -> int:
    """Import an original-format storage directory into SQLite.

    See the module docstring for the on-disk layout this handles. Returns the
    primary-key ``sim_id`` of the (new or existing) simulation row.

    Parameters
    ----------
    source_dir
        Path to the simulation directory. Must contain either
        ``reverie/meta.json`` (live-runner format) or ``meta.json`` at the root
        (compressed archive format).
    session
        Open SQLAlchemy session bound to the target database. The importer
        manages its own commits via the Repo layer.
    sim_code_override
        Optional sim_code to use; otherwise derived from the source directory
        name. The original ``meta.json`` does not carry its own ``sim_code``
        field — directory name is canonical.
    on_conflict
        Behaviour when a row with the same sim_code already exists.

        * ``"fail"`` — raise :class:`SimulationExists`.
        * ``"replace"`` — hard-delete the existing sim (and everything it
          owns via FK cascade) and re-import.
        * ``"skip"`` — leave the existing row untouched and return its id.
    log_progress
        If True, emit INFO-level log messages as personas / steps are loaded.

    Raises
    ------
    ImportError_
        On any malformed input (missing meta.json, unparseable JSON, etc.).
    SimulationExists
        Only when ``on_conflict="fail"`` and a clash exists.
    """
    src = Path(source_dir).resolve()
    if not src.is_dir():
        raise ImportError_(f"Source directory does not exist: {src}")

    # --- meta.json ---------------------------------------------------------
    meta_path = _find_meta_path(src)
    meta = parse_reverie_meta(_read_json_text(meta_path))

    sim_code = sim_code_override or src.name
    if not sim_code:
        raise ImportError_("Could not determine sim_code (override and source dir name both empty).")

    repos = make_repos(session)

    # --- conflict handling -------------------------------------------------
    existing = repos.simulations.get_by_code(sim_code)
    if existing is not None:
        if on_conflict == "fail":
            raise SimulationExists(sim_code, existing.id)
        if on_conflict == "skip":
            if log_progress:
                logger.info("Skipping import; sim_code=%s already exists.", sim_code)
            return existing.id
        if on_conflict == "replace":
            _hard_delete_sim(session, existing.id)
        else:  # pragma: no cover — argparse / typing prevents this
            raise ValueError(f"Unknown on_conflict mode: {on_conflict!r}")

    # --- time normalisation -----------------------------------------------
    try:
        start_dt = _parse_meta_start_date(meta.start_date)
    except ValueError as exc:
        raise ImportError_(
            f"Could not parse meta.start_date={meta.start_date!r}: {exc}"
        ) from exc
    try:
        curr_dt = _parse_meta_curr_time(meta.curr_time)
    except ValueError:
        # Some bootstrap sims set curr_time == start_date (no HH:MM:SS); fall
        # back to start_dt rather than failing the whole import.
        curr_dt = start_dt

    # --- create simulation row --------------------------------------------
    sim = repos.simulations.create(
        sim_code=sim_code,
        fork_sim_code=meta.fork_sim_code,
        status=SimulationStatus.COMPLETED.value,
        start_time_iso=_to_iso(start_dt),
        curr_time_iso=_to_iso(curr_dt),
        sec_per_step=meta.sec_per_step,
        step=meta.step or 0,
        maze_name=meta.maze_name,
        n_round=200,  # spec default for imported (completed) sims
    )
    sim_id = sim.id
    if log_progress:
        logger.info("Created simulation row sim_code=%s id=%s", sim_code, sim_id)

    # --- personas + memory -------------------------------------------------
    persona_count, node_count = _import_personas(
        repos, sim_id, src, start_dt, meta.sec_per_step, log_progress
    )
    if log_progress:
        logger.info("Imported %d personas, %d memory nodes", persona_count, node_count)

    # --- environment & movement -------------------------------------------
    env_count = _import_environment_files(repos, sim_id, src)
    move_count = _import_movement_files(repos, sim_id, src)
    if move_count == 0:
        # No per-step movement files; try compressed master_movement.json
        move_count = _import_master_movement(repos, sim_id, src)
    if log_progress:
        logger.info(
            "Imported %d environment snapshots, %d movement rows",
            env_count,
            move_count,
        )

    # --- llm logs ----------------------------------------------------------
    log_count = _import_llm_logs(repos, sim_id, src)
    if log_progress:
        logger.info("Imported %d llm_calls", log_count)

    return sim_id


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m storage.importer",
        description="Import an original Stanford Town JSON storage directory into SQLite.",
    )
    parser.add_argument("source_dir", help="Path to the storage/<sim_code>/ directory.")
    parser.add_argument(
        "--sim-code",
        dest="sim_code",
        default=None,
        help="Override the target sim_code (default: source dir name).",
    )
    parser.add_argument(
        "--on-conflict",
        choices=("fail", "replace", "skip"),
        default="fail",
        help="What to do if a sim with the same sim_code already exists.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-section progress logs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — opens a SessionLocal from storage.db and imports.

    Exits with status 0 on success, non-zero on importer error.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    # Local import so the module is testable without a real DB.
    from storage.db import SessionLocal  # noqa: WPS433

    session = SessionLocal()
    try:
        sim_id = import_simulation(
            args.source_dir,
            session,
            sim_code_override=args.sim_code,
            on_conflict=args.on_conflict,
            log_progress=not args.quiet,
        )
    except SimulationExists as exc:
        logger.error("%s", exc)
        return 2
    except ImportError_ as exc:
        logger.error("Import failed: %s", exc)
        return 1
    finally:
        session.close()

    print(json.dumps({"sim_id": sim_id, "sim_code": args.sim_code or Path(args.source_dir).name}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "import_simulation",
    "SimulationExists",
    "ImportError_",
    "main",
]
