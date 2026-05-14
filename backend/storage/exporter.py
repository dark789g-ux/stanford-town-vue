"""JSON exporter — reads a SQLite simulation back into Stanford Town's
on-disk JSON layout.

Two output layouts are supported (see ``docs/json_format.md``):

* ``"live"`` — the shape the runtime simulator emits: a per-step
  ``environment/{N}.json`` + ``movement/{N}.json`` envelope, ``reverie/meta.json``,
  plus an append-only ``llm_logs.jsonl``.
* ``"compressed"`` — the archived shape used by the replay frontend: a single
  ``meta.json`` at the sim root and one ``master_movement.json`` aggregating
  every step (no per-step environment files, no llm log file).

Embeddings are never persisted in this fork, so the exporter always writes
``embeddings.json`` as ``{}`` (see docs §embeddings.json).

Public API
----------

.. autofunction:: export_simulation

CLI: ``python -m storage.exporter <sim_code_or_id> <target_dir> [--layout live|compressed]``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.orm import Session

from storage.json_schemas import (
    LlmLogLine,
    LlmUsage,
    PersonaMovement,
    ReverieMeta,
)
from storage.models import MemoryNodeType, Simulation
from storage.repos import make_repos

Layout = Literal["live", "compressed"]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SimulationNotFound(LookupError):
    """Raised when ``export_simulation`` cannot find the target sim row."""


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

# ``simulations.start_time_iso`` / ``curr_time_iso`` use ISO-8601 ("2023-02-13T00:00:00"
# or "2023-02-13 00:00:00"). Original JSON formats:
#   * meta.start_date:  "%B %d, %Y"               e.g. "February 13, 2023"
#   * meta.curr_time:   "%B %d, %Y, %H:%M:%S"     e.g. "February 14, 2023, 00:02:30"
#   * scratch.curr_time: same as meta.curr_time
#   * memory_nodes.created / expiration: "%Y-%m-%d %H:%M:%S"

_META_CURR_TIME_FMT = "%B %d, %Y, %H:%M:%S"
_META_START_DATE_FMT = "%B %d, %Y"
_NODE_TIME_FMT = "%Y-%m-%d %H:%M:%S"


def _parse_iso(iso: str) -> datetime:
    """Parse an ISO-ish string written by the SQLite layer back to a datetime.

    Accepts both ``"2023-02-13T00:00:00"`` and ``"2023-02-13 00:00:00"`` plus
    optional trailing timezone offsets / fractional seconds.
    """
    s = iso.strip()
    # ``fromisoformat`` in 3.11+ handles "Z" and offsets, but be defensive.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # Fallback for "2023-02-13 00:00:00" without ISO 'T' separator.
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def _iso_to_meta_start_date(iso: str) -> str:
    return _parse_iso(iso).strftime(_META_START_DATE_FMT)


def _iso_to_meta_curr_time(iso: str) -> str:
    return _parse_iso(iso).strftime(_META_CURR_TIME_FMT)


def _step_to_node_time(start_iso: str, sec_per_step: int, step: int | None) -> str | None:
    """Convert a memory-node step integer back to ``"%Y-%m-%d %H:%M:%S"``.

    Returns ``None`` when ``step`` is ``None`` (used for nullable expirations).
    """
    if step is None:
        return None
    dt = _parse_iso(start_iso) + timedelta(seconds=int(sec_per_step) * int(step))
    return dt.strftime(_NODE_TIME_FMT)


# ---------------------------------------------------------------------------
# JSON dump helper
# ---------------------------------------------------------------------------


def _dump_json(path: Path, obj: Any) -> None:
    """Write ``obj`` as indented JSON, parents auto-created."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _build_meta(sim: Simulation, persona_names: list[str]) -> dict[str, Any]:
    """Build the ``meta.json`` dict (validates through :class:`ReverieMeta`)."""
    meta = ReverieMeta(
        fork_sim_code=sim.fork_sim_code,
        start_date=_iso_to_meta_start_date(sim.start_time_iso),
        curr_time=_iso_to_meta_curr_time(sim.curr_time_iso),
        sec_per_step=int(sim.sec_per_step),
        maze_name=sim.maze_name,
        persona_names=persona_names,
        step=int(sim.step),
    )
    return meta.model_dump()


def _build_node_record(
    node, start_iso: str, sec_per_step: int
) -> dict[str, Any]:
    """Render one ``MemoryNode`` row as an on-disk node dict.

    ``embedding_key`` is emitted as an empty string — this fork drops embedding
    storage entirely (see docs/json_format.md §embeddings.json). Downstream
    consumers that don't index ``embeddings.json`` ignore the value; importers
    that re-read this file simply set the in-memory key to "" as well.
    """
    return {
        "node_count": int(node.node_count),
        "type_count": int(node.type_count),
        "type": node.node_type,
        "depth": int(node.depth),
        "created": _step_to_node_time(start_iso, sec_per_step, node.created),
        "expiration": _step_to_node_time(start_iso, sec_per_step, node.expiration_step),
        "subject": node.subject,
        "predicate": node.predicate,
        "object": node.object,
        "description": node.description,
        # No embeddings are persisted; the on-disk schema requires the key to
        # exist as a string. Empty string is safe — it never matches an entry
        # in the (empty) embeddings.json we emit.
        "embedding_key": "",
        "poignancy": int(node.poignancy),
        "keywords": list(node.keywords_json or []),
        "filling": node.filling_json,
    }


def _build_nodes_file(
    nodes: list, start_iso: str, sec_per_step: int
) -> dict[str, dict[str, Any]]:
    """Build ``nodes.json`` as ``{node_id: node_record}`` ordered newest-first.

    The original simulator writes node files newest-first (it iterates the
    in-memory ``seq_event/thought/chat`` lists in reverse). We mirror that so
    re-importers and the replay frontend see the same key ordering.
    """
    sorted_nodes = sorted(nodes, key=lambda n: n.node_count, reverse=True)
    out: dict[str, dict[str, Any]] = {}
    for n in sorted_nodes:
        out[n.node_id] = _build_node_record(n, start_iso, sec_per_step)
    return out


def _build_kw_strength(memory_repo, persona_id: int) -> dict[str, dict[str, int]]:
    """Reconstruct ``kw_strength.json`` by counting keyword occurrences.

    The original simulator persists explicit per-keyword counters; this fork
    rebuilds them from the ``memory_keywords_to_*`` join tables. Each
    ``list_keywords`` row is a ``(keyword, node_id)`` pair, so the count for a
    keyword is the number of distinct nodes that mention it (which matches the
    original strength semantics — see docs §kw_strength.json).
    """
    event_rows = memory_repo.list_keywords(persona_id, MemoryNodeType.EVENT)
    thought_rows = memory_repo.list_keywords(persona_id, MemoryNodeType.THOUGHT)
    event_counter: Counter[str] = Counter(kw for kw, _ in event_rows)
    thought_counter: Counter[str] = Counter(kw for kw, _ in thought_rows)
    return {
        "kw_strength_event": dict(event_counter),
        "kw_strength_thought": dict(thought_counter),
    }


def _build_movement_record(mv) -> dict[str, Any]:
    """Render one ``StepMovement`` ORM row as a ``PersonaMovement`` dict."""
    pm = PersonaMovement(
        movement=[int(mv.x), int(mv.y)],
        pronunciatio=mv.pronunciatio,
        description=mv.description,
        chat=mv.chat_json,
    )
    # ``model_dump`` keeps None fields; the on-disk format always emits them.
    return pm.model_dump()


def _build_llm_log_line(call) -> dict[str, Any]:
    """Render one ``LlmCall`` ORM row as an `llm_logs.jsonl` record.

    The SQLite ``llm_calls`` table drops a handful of fields the JSONL carries
    (``seq``, ``action``, ``params``, ``cost_usd``, ``retry_idx``,
    ``used_fail_default``). We synthesise plausible defaults per the mapping
    table in ``docs/json_format.md``.
    """
    ts: datetime = call.ts
    if ts.tzinfo is None:
        # The SQLite column is naive; emit a naive ISO string. The original
        # writer uses an offset-aware timestamp but the importer tolerates both.
        ts_str = ts.isoformat(timespec="milliseconds")
    else:
        ts_str = ts.isoformat(timespec="milliseconds")

    usage = LlmUsage(
        prompt_tokens=int(call.prompt_tokens or 0),
        completion_tokens=int(call.completion_tokens or 0),
        total_tokens=int((call.prompt_tokens or 0) + (call.completion_tokens or 0)),
    )
    line = LlmLogLine(
        seq=int(call.id),  # id is monotonic per-sim, good enough as a seq stand-in
        ts=ts_str,
        step=call.step,
        persona=call.persona_name,
        action=None,  # not persisted in this fork
        model=call.model,
        params={},  # not persisted in this fork
        prompt=call.prompt,
        response=call.response,
        usage=usage,
        cost_usd=None,
        latency_ms=int(call.latency_ms or 0),
        retry_idx=0,
        used_fail_default=False,
        error=call.error,
    )
    return line.model_dump()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def export_simulation(
    sim_id: int,
    target_dir: Path | str,
    session: Session,
    *,
    layout: Layout = "compressed",
    log_progress: bool = False,
) -> Path:
    """Export a SQLite simulation back to original Stanford Town JSON layout.

    Parameters
    ----------
    sim_id:
        The ``simulations.id`` to export. (CLI also accepts a ``sim_code``;
        callers using the Python API should resolve the code via
        ``repos.simulations.get_by_code`` first.)
    target_dir:
        Output base directory. The export is written to
        ``target_dir / sim.sim_code``.
    session:
        Open SQLAlchemy session. The exporter only reads; it does not commit.
    layout:
        ``"compressed"`` (default) — bundles all steps into
        ``master_movement.json``, omits per-step environment files and the
        LLM log. ``"live"`` — writes ``reverie/meta.json`` plus per-step
        ``environment/{N}.json`` / ``movement/{N}.json`` files and emits
        ``llm_logs.jsonl``.
    log_progress:
        When true, print one line per major export phase to stdout (used by
        the CLI).

    Returns the path to the directory that was created.

    Raises
    ------
    SimulationNotFound:
        If ``sim_id`` has no matching row.
    """
    if layout not in ("live", "compressed"):
        raise ValueError(f"layout must be 'live' or 'compressed', got {layout!r}")

    repos = make_repos(session)
    sim = repos.simulations.get_by_id(sim_id)
    if sim is None:
        raise SimulationNotFound(f"Simulation id={sim_id} not found")

    out_dir = Path(target_dir) / sim.sim_code
    out_dir.mkdir(parents=True, exist_ok=True)

    personas = repos.personas.list_by_sim(sim.id)
    persona_names = [p.name for p in personas]

    if log_progress:
        print(f"[exporter] sim={sim.sim_code} layout={layout} -> {out_dir}")

    # ------------------------------------------------------------------ meta
    meta = _build_meta(sim, persona_names)
    if layout == "live":
        _dump_json(out_dir / "reverie" / "meta.json", meta)
    else:
        _dump_json(out_dir / "meta.json", meta)
    if log_progress:
        print(f"[exporter] wrote meta.json ({len(persona_names)} personas)")

    # ------------------------------------------------------ per-persona files
    for persona in personas:
        pdir = out_dir / "personas" / persona.name / "bootstrap_memory"

        # scratch.json
        scratch = repos.personas.load_scratch(persona.id) or {}
        _dump_json(pdir / "scratch.json", scratch)

        # spatial_memory.json
        spatial = repos.personas.load_spatial_memory(persona.id) or {}
        _dump_json(pdir / "spatial_memory.json", spatial)

        # associative_memory/*
        am_dir = pdir / "associative_memory"
        nodes = repos.memory.get_all_nodes(persona.id)
        nodes_json = _build_nodes_file(nodes, sim.start_time_iso, sim.sec_per_step)
        _dump_json(am_dir / "nodes.json", nodes_json)

        kw_strength = _build_kw_strength(repos.memory, persona.id)
        _dump_json(am_dir / "kw_strength.json", kw_strength)

        # Per the spec we never persist embeddings; an empty placeholder keeps
        # downstream tools that expect the file present from crashing.
        _dump_json(am_dir / "embeddings.json", {})

        if log_progress:
            print(
                f"[exporter] persona={persona.name} nodes={len(nodes)} "
                f"kw_event={len(kw_strength['kw_strength_event'])} "
                f"kw_thought={len(kw_strength['kw_strength_thought'])}"
            )

    # ----------------------------------------------------- per-step / aggregate
    max_step = repos.steps.get_max_step(sim.id)
    if layout == "live":
        # Iterate up to max(max_step, sim.step) — the simulator's step counter
        # can outrun the rows on disk during a paused tick; cover both.
        last = max(max_step, int(sim.step or 0))
        for step in range(0, last + 1):
            env_payload = repos.steps.get_environment(sim.id, step)
            if env_payload is not None:
                _dump_json(out_dir / "environment" / f"{step}.json", env_payload)

            mv_rows = repos.steps.list_movements_range(sim.id, step, step)
            if mv_rows:
                personas_block: dict[str, Any] = {}
                for mv in mv_rows:
                    personas_block[mv.persona_name] = _build_movement_record(mv)
                snapshot = {
                    "persona": personas_block,
                    "meta": {"curr_time": _iso_to_meta_curr_time(sim.curr_time_iso)},
                }
                _dump_json(out_dir / "movement" / f"{step}.json", snapshot)
        if log_progress:
            print(f"[exporter] wrote per-step files for steps 0..{last}")
    else:
        # Compressed: one master_movement.json keyed by stringified step.
        master: dict[str, dict[str, Any]] = {}
        if max_step >= 0:
            all_movements = repos.steps.list_movements_range(sim.id, 0, max_step)
            grouped: dict[int, dict[str, Any]] = {}
            for mv in all_movements:
                grouped.setdefault(mv.step, {})[mv.persona_name] = (
                    _build_movement_record(mv)
                )
            # Emit ALL steps in range (empty {} for ones without rows — matches
            # docs/json_format.md §master_movement.json).
            for step in range(0, max_step + 1):
                master[str(step)] = grouped.get(step, {})
        _dump_json(out_dir / "master_movement.json", master)
        if log_progress:
            print(f"[exporter] wrote master_movement.json (max_step={max_step})")

    # ------------------------------------------------------------- LLM logs
    if layout == "live":
        log_path = out_dir / "llm_logs.jsonl"
        # Stream paginated so we never load the full table at once.
        total = repos.llm_logs.count(sim.id)
        page_size = 500
        # ``LlmLogRepo.list`` orders by id DESC; reverse each page to keep the
        # final JSONL chronological.
        with log_path.open("w", encoding="utf-8") as fh:
            collected: list[dict[str, Any]] = []
            offset = 0
            while offset < total:
                rows = repos.llm_logs.list(sim.id, offset=offset, limit=page_size)
                if not rows:
                    break
                for call in rows:
                    collected.append(_build_llm_log_line(call))
                offset += len(rows)
            # ``list`` returns newest-first; flip to write oldest-first.
            for line in reversed(collected):
                fh.write(json.dumps(line, ensure_ascii=False))
                fh.write("\n")
        if log_progress:
            print(f"[exporter] wrote llm_logs.jsonl ({total} rows)")

    return out_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_sim_id(session: Session, ref: str) -> int:
    repos = make_repos(session)
    if ref.isdigit():
        sim = repos.simulations.get_by_id(int(ref))
        if sim is None:
            raise SimulationNotFound(f"No simulation with id={ref}")
        return sim.id
    sim = repos.simulations.get_by_code(ref)
    if sim is None:
        raise SimulationNotFound(f"No simulation with sim_code={ref!r}")
    return sim.id


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m storage.exporter",
        description="Export a SQLite simulation to Stanford Town JSON format.",
    )
    p.add_argument("sim", help="Simulation id (int) or sim_code (str).")
    p.add_argument("target_dir", help="Output directory (sim_code/ is created under it).")
    p.add_argument(
        "--layout",
        choices=("live", "compressed"),
        default="compressed",
        help="Output layout (default: compressed).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    from storage.db import SessionLocal

    with SessionLocal() as session:
        try:
            sim_id = _resolve_sim_id(session, args.sim)
        except SimulationNotFound as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        out = export_simulation(
            sim_id,
            args.target_dir,
            session,
            layout=args.layout,
            log_progress=True,
        )
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["export_simulation", "SimulationNotFound", "Layout"]
