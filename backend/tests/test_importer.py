"""Integration tests for :mod:`storage.importer`.

Each test gets its own in-memory SQLite engine + fresh session so we can run
in any order without leakage. The "demo" sim is synthesised in a temp dir —
the original Stanford Town demo data is not bundled in this repository, and
the importer is data-agnostic so a synthesised fixture still exercises the
live + compressed branches.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

# Make ``import storage.*`` work when pytest runs from the repo root.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from storage import Base  # noqa: E402
from storage.importer import (  # noqa: E402
    SimulationExists,
    import_simulation,
)
from storage.models import (  # noqa: E402
    LlmCall,
    MemoryNode,
    Persona,
    Simulation,
    StepMovement,
)


# ---------------------------------------------------------------------------
# Engine / session helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def session():
    """A clean in-memory SQLite DB with the full schema, per-test."""
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )

    # Enable ON DELETE CASCADE for SQLite so on_conflict=replace works.
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # pragma: no cover
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True
    )
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Synthesised "compressed_storage" demo
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_compressed_demo(root: Path) -> Path:
    """Build a 3-persona compressed-format sim under ``root/<sim_code>/``.

    Mirrors the original Stanford Town compressed on-disk layout: ``meta.json``
    at the root, ``master_movement.json``, and
    ``personas/<Name>/bootstrap_memory/...``.
    """
    sim_code = "July1_the_ville_isabella_maria_klaus-step-3-20"
    sim_dir = root / sim_code

    # --- meta.json at root (compressed flavour) ---------------------------
    _write_json(
        sim_dir / "meta.json",
        {
            "fork_sim_code": "July1_the_ville_isabella_maria_klaus-step-3-19",
            "start_date": "February 13, 2023",
            "curr_time": "February 14, 2023, 00:02:30",
            "sec_per_step": 10,
            "maze_name": "the_ville",
            "persona_names": ["Isabella Rodriguez", "Maria Lopez", "Klaus Mueller"],
            "step": 8655,
        },
    )

    # --- master_movement.json (3 steps, 3 personas) -----------------------
    persona_names = ["Isabella Rodriguez", "Maria Lopez", "Klaus Mueller"]
    master = {}
    for step in range(3):
        master[str(step)] = {
            name: {
                "movement": [10 + step, 20 + i],
                "pronunciatio": "😴",
                "description": f"sleeping @ the Ville:{name}'s apartment:main room:bed",
                "chat": None,
            }
            for i, name in enumerate(persona_names)
        }
    _write_json(sim_dir / "master_movement.json", master)

    # --- minimal personas (only Isabella has memory nodes) ----------------
    for name in persona_names:
        bm = sim_dir / "personas" / name / "bootstrap_memory"
        _write_json(
            bm / "scratch.json",
            {
                "name": name,
                "first_name": name.split()[0],
                "last_name": name.split()[-1],
                "age": 30,
                "daily_plan_req": f"{name}'s daily plan placeholder.",
            },
        )
        _write_json(bm / "spatial_memory.json", {"the Ville": {}})
        _write_json(bm / "associative_memory" / "nodes.json", {})

    # Give Isabella one event + one thought to assert node insertion paths.
    isabella_nodes = {
        "node_2": {
            "node_count": 2,
            "type_count": 2,
            "type": "event",
            "depth": 0,
            "created": "2023-02-13 06:00:00",
            "expiration": None,
            "subject": "Isabella Rodriguez",
            "predicate": "wake",
            "object": "up",
            "description": "Isabella Rodriguez wakes up",
            "embedding_key": "Isabella Rodriguez wakes up",
            "poignancy": 2,
            "keywords": ["wake", "morning"],
            "filling": [],
        },
        "node_1": {
            "node_count": 1,
            "type_count": 1,
            "type": "thought",
            "depth": 1,
            "created": "2023-02-13 00:00:00",
            "expiration": "2023-03-15 00:00:00",
            "subject": "Isabella Rodriguez",
            "predicate": "plan",
            "object": "open cafe",
            "description": "This is Isabella's plan for the day.",
            "embedding_key": "This is Isabella's plan for the day.",
            "poignancy": 5,
            "keywords": ["plan"],
            "filling": None,
        },
    }
    _write_json(
        sim_dir / "personas" / "Isabella Rodriguez" / "bootstrap_memory" / "associative_memory" / "nodes.json",
        isabella_nodes,
    )

    # --- llm_logs.jsonl (3 lines) -----------------------------------------
    lines = [
        {
            "seq": 0,
            "ts": "2026-05-12T19:21:21.376+08:00",
            "step": 0,
            "persona": "Klaus Mueller",
            "action": "WakeUp",
            "model": "deepseek-v4-flash",
            "params": {"temperature": 0.0, "max_tokens": 64},
            "prompt": "Name: Klaus Mueller...",
            "response": "7",
            "usage": {"prompt_tokens": 205, "completion_tokens": 1, "total_tokens": 206},
            "cost_usd": 2.9e-05,
            "latency_ms": 735,
            "retry_idx": 0,
            "used_fail_default": False,
            "error": None,
        },
        {
            "seq": 1,
            "ts": "2026-05-12T19:21:22.000+08:00",
            "step": 0,
            "persona": "Isabella Rodriguez",
            "action": "WakeUp",
            "model": "gpt-4o-mini",
            "params": {},
            "prompt": "Name: Isabella Rodriguez...",
            "response": "6",
            "usage": {"prompt_tokens": 180, "completion_tokens": 1, "total_tokens": 181},
            "cost_usd": 1.5e-05,
            "latency_ms": 412,
            "retry_idx": 0,
            "used_fail_default": False,
            "error": None,
        },
        {
            "seq": 2,
            "ts": "2026-05-12T19:21:23.000+08:00",
            "step": 1,
            "persona": "Maria Lopez",
            "action": None,
            "model": "claude-3-5-sonnet",
            "params": {},
            "prompt": "Name: Maria Lopez...",
            "response": None,
            "usage": None,
            "cost_usd": None,
            "latency_ms": 100,
            "retry_idx": 0,
            "used_fail_default": False,
            "error": "rate-limited",
        },
    ]
    jsonl_path = sim_dir / "llm_logs.jsonl"
    jsonl_path.write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n",
        encoding="utf-8",
    )
    return sim_dir


@pytest.fixture
def compressed_demo_dir(tmp_path: Path) -> Path:
    """A synthesised compressed-format demo simulation."""
    return _build_compressed_demo(tmp_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_imports_compressed_demo(session, compressed_demo_dir: Path):
    sim_id = import_simulation(compressed_demo_dir, session)
    assert sim_id is not None and sim_id > 0

    # Simulation row sanity ------------------------------------------------
    sim = session.scalar(select(Simulation).where(Simulation.id == sim_id))
    assert sim is not None
    assert sim.sim_code == "July1_the_ville_isabella_maria_klaus-step-3-20"
    assert sim.fork_sim_code == "July1_the_ville_isabella_maria_klaus-step-3-19"
    assert sim.maze_name == "the_ville"
    assert sim.sec_per_step == 10
    assert sim.step == 8655
    assert sim.status == "completed"
    assert sim.start_time_iso.startswith("2023-02-13")
    assert sim.curr_time_iso.startswith("2023-02-14")

    # Personas -------------------------------------------------------------
    personas = session.scalars(
        select(Persona).where(Persona.sim_id == sim_id).order_by(Persona.name)
    ).all()
    persona_names = [p.name for p in personas]
    assert persona_names == ["Isabella Rodriguez", "Klaus Mueller", "Maria Lopez"]
    for p in personas:
        assert p.age == 30
        assert p.plan_text and "placeholder" in p.plan_text
        assert isinstance(p.scratch_json, dict)

    # Memory nodes (only Isabella has nodes in fixture) --------------------
    isabella = next(p for p in personas if p.name == "Isabella Rodriguez")
    nodes = session.scalars(
        select(MemoryNode).where(MemoryNode.persona_id == isabella.id)
    ).all()
    assert len(nodes) == 2
    by_id = {n.node_id: n for n in nodes}
    assert by_id["node_1"].node_type == "thought"
    assert by_id["node_2"].node_type == "event"
    # node_1 created at start_date 00:00:00 -> step 0; node_2 at 06:00 same day
    assert by_id["node_1"].created == 0
    assert by_id["node_2"].created == 6 * 3600 // 10  # 2160
    assert by_id["node_1"].expiration_step is not None
    assert by_id["node_2"].expiration_step is None

    # Step movements -------------------------------------------------------
    moves = session.scalars(select(StepMovement).where(StepMovement.sim_id == sim_id)).all()
    # 3 steps x 3 personas = 9 rows
    assert len(moves) == 9
    # Spot-check description / location_path split
    first = moves[0]
    assert first.description is not None
    assert first.location_path is not None
    assert "apartment" in first.location_path

    # LLM logs -------------------------------------------------------------
    llm_count = session.scalar(
        select(func.count(LlmCall.id)).where(LlmCall.sim_id == sim_id)
    )
    assert llm_count == 3

    # Provider inference
    providers = {
        r.persona_name: r.provider
        for r in session.scalars(select(LlmCall).where(LlmCall.sim_id == sim_id)).all()
    }
    assert providers["Klaus Mueller"] == "deepseek"
    assert providers["Isabella Rodriguez"] == "openai"
    assert providers["Maria Lopez"] == "anthropic"


def test_on_conflict_fail(session, compressed_demo_dir: Path):
    import_simulation(compressed_demo_dir, session)
    with pytest.raises(SimulationExists):
        import_simulation(compressed_demo_dir, session, on_conflict="fail")


def test_on_conflict_replace_is_idempotent(session, compressed_demo_dir: Path):
    first_id = import_simulation(compressed_demo_dir, session)
    counts_before = _row_counts(session, first_id)

    second_id = import_simulation(compressed_demo_dir, session, on_conflict="replace")
    counts_after = _row_counts(session, second_id)
    assert counts_before == counts_after

    # Exactly one row left with this sim_code.
    sims = session.scalars(
        select(Simulation).where(Simulation.sim_code == "July1_the_ville_isabella_maria_klaus-step-3-20")
    ).all()
    assert len(sims) == 1
    assert sims[0].id == second_id


def test_on_conflict_skip_returns_existing_id(session, compressed_demo_dir: Path):
    first_id = import_simulation(compressed_demo_dir, session)
    counts_before = _row_counts(session, first_id)

    second_id = import_simulation(compressed_demo_dir, session, on_conflict="skip")
    assert second_id == first_id

    counts_after = _row_counts(session, first_id)
    assert counts_before == counts_after


def test_imports_bootstrap_only(session, tmp_path: Path):
    """Bootstrap seed under backend/assets — 25 personas, no environment data."""
    source = _BACKEND_DIR / "assets" / "personas" / "base_the_ville_n25"
    if not source.is_dir():  # pragma: no cover — assets always shipped
        pytest.skip(f"Bundled seed missing at {source}")

    # The seed lacks a reverie/meta.json — copy minimally into tmp_path with
    # a synthesised meta and re-point.
    staging = tmp_path / "base_the_ville_n25"
    staging.mkdir()
    # Symlink personas/ if possible; otherwise we still iterate the original
    # via a directory reference. For simplicity, link the personas dir.
    import shutil

    shutil.copytree(source / "personas", staging / "personas")
    _write_json(
        staging / "reverie" / "meta.json",
        {
            "fork_sim_code": None,
            "start_date": "February 13, 2023",
            "curr_time": "February 13, 2023, 00:00:00",
            "sec_per_step": 10,
            "maze_name": "the_ville",
            "persona_names": [],  # not load-bearing for the importer
            "step": 0,
        },
    )

    sim_id = import_simulation(staging, session)
    personas = session.scalars(
        select(Persona).where(Persona.sim_id == sim_id)
    ).all()
    assert len(personas) == 25
    move_count = session.scalar(
        select(func.count(StepMovement.id)).where(StepMovement.sim_id == sim_id)
    )
    assert move_count == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_counts(session, sim_id: int) -> dict[str, int]:
    """Return per-table row counts scoped to a single simulation."""
    return {
        "personas": session.scalar(
            select(func.count(Persona.id)).where(Persona.sim_id == sim_id)
        ),
        "memory_nodes": session.scalar(
            select(func.count(MemoryNode.id)).join(Persona, Persona.id == MemoryNode.persona_id)
            .where(Persona.sim_id == sim_id)
        ),
        "step_movements": session.scalar(
            select(func.count(StepMovement.id)).where(StepMovement.sim_id == sim_id)
        ),
        "llm_calls": session.scalar(
            select(func.count(LlmCall.id)).where(LlmCall.sim_id == sim_id)
        ),
    }
