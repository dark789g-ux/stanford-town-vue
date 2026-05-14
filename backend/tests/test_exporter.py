"""Integration tests for ``storage.exporter``.

We do not yet have the importer (Wave 2 Agent D, parallel) nor the demo data
checked in, so the seed data is built directly via the repo layer. The
round-trip test is gated on the importer becoming importable.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# Ensure the backend root is on sys.path so `storage.*` imports resolve when
# pytest is invoked from the repo top.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from storage.db import Base  # noqa: E402
from storage.exporter import (  # noqa: E402
    SimulationNotFound,
    export_simulation,
)
from storage.json_schemas import (  # noqa: E402
    parse_master_movement,
    parse_movement_snapshot,
    parse_nodes,
    parse_reverie_meta,
    parse_scratch,
    parse_spatial_memory,
)
from storage.models import MemoryNodeType  # noqa: E402
from storage.repos import make_repos  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _seed_full_sim(session: Session) -> int:
    """Seed a sim with two personas, memory, environment + movement, and LLM logs.

    Returns the sim id. The sim is intentionally tiny so tests stay fast.
    """
    repos = make_repos(session)
    sim = repos.simulations.create(
        sim_code="test_export_sim",
        fork_sim_code="seed_sim",
        start_time_iso="2023-02-13T00:00:00",
        curr_time_iso="2023-02-13T00:00:20",
        sec_per_step=10,
        step=2,
        maze_name="the_ville",
        n_round=100,
    )

    p1 = repos.personas.create(
        sim.id,
        "Isabella Rodriguez",
        age=34,
        scratch_json={
            "name": "Isabella Rodriguez",
            "first_name": "Isabella",
            "last_name": "Rodriguez",
            "age": 34,
            "vision_r": 4,
            "att_bandwidth": 3,
            "retention": 5,
            "daily_req": [],
            "f_daily_schedule": [],
            "f_daily_schedule_hourly_org": [],
            "act_event": ["Isabella Rodriguez", None, None],
            "act_obj_event": [None, None, None],
            "chatting_with_buffer": {},
            "planned_path": [],
        },
    )
    p2 = repos.personas.create(
        sim.id,
        "Klaus Mueller",
        age=20,
        scratch_json={
            "name": "Klaus Mueller",
            "first_name": "Klaus",
            "last_name": "Mueller",
            "age": 20,
            "vision_r": 8,
            "att_bandwidth": 8,
            "retention": 8,
            "daily_req": [],
            "f_daily_schedule": [],
            "f_daily_schedule_hourly_org": [],
            "act_event": ["Klaus Mueller", None, None],
            "act_obj_event": [None, None, None],
            "chatting_with_buffer": {},
            "planned_path": [],
        },
    )

    repos.personas.save_spatial_memory(
        p1.id,
        {"the Ville": {"Hobbs Cafe": {"cafe": ["counter", "register"]}}},
    )
    repos.personas.save_spatial_memory(
        p2.id, {"the Ville": {"Dorm": {"Klaus's room": ["bed", "desk"]}}}
    )

    # Memory nodes for persona 1 (1 event + 1 thought).
    repos.memory.add_node(
        p1.id,
        "node_1",
        MemoryNodeType.EVENT,
        node_count=1,
        type_count=1,
        depth=0,
        created=0,
        expiration_step=None,
        subject="the Ville:Hobbs Cafe:cafe:counter",
        predicate="be",
        object="idle",
        description="counter is idle",
        poignancy=1,
        keywords_json=["counter", "idle"],
        filling_json=[],
    )
    repos.memory.add_node(
        p1.id,
        "node_2",
        MemoryNodeType.THOUGHT,
        node_count=2,
        type_count=1,
        depth=1,
        created=1,
        expiration_step=100,
        subject="Isabella Rodriguez",
        predicate="plan",
        object="day",
        description="plan for the day",
        poignancy=5,
        keywords_json=["plan"],
        filling_json=["node_1"],
    )
    repos.memory.add_keywords_bulk(
        p1.id,
        MemoryNodeType.EVENT,
        [("counter", "node_1"), ("idle", "node_1")],
    )
    repos.memory.add_keywords_bulk(
        p1.id, MemoryNodeType.THOUGHT, [("plan", "node_2")]
    )

    # Environment + movement at step 0 and 1.
    repos.steps.upsert_environment(
        sim.id,
        0,
        {
            "Isabella Rodriguez": {"maze": "the_ville", "x": 72, "y": 14},
            "Klaus Mueller": {"maze": "the_ville", "x": 50, "y": 30},
        },
    )
    repos.steps.upsert_movements_for_step(
        sim.id,
        0,
        [
            {
                "persona_name": "Isabella Rodriguez",
                "x": 72,
                "y": 14,
                "description": "idle @ the Ville:Hobbs Cafe:cafe",
                "pronunciatio": "🧍",
                "chat": None,
                "location_path": "the Ville:Hobbs Cafe:cafe",
            },
            {
                "persona_name": "Klaus Mueller",
                "x": 50,
                "y": 30,
                "description": "sleeping @ the Ville:Dorm:Klaus's room",
                "pronunciatio": "😴",
                "chat": None,
                "location_path": "the Ville:Dorm:Klaus's room",
            },
        ],
    )
    repos.steps.upsert_environment(
        sim.id,
        1,
        {
            "Isabella Rodriguez": {"maze": "the_ville", "x": 73, "y": 14},
            "Klaus Mueller": {"maze": "the_ville", "x": 50, "y": 30},
        },
    )
    repos.steps.upsert_movements_for_step(
        sim.id,
        1,
        [
            {
                "persona_name": "Isabella Rodriguez",
                "x": 73,
                "y": 14,
                "description": "walking @ the Ville:Hobbs Cafe:cafe",
                "pronunciatio": "🚶",
                "chat": [["Isabella Rodriguez", "hello"]],
                "location_path": "the Ville:Hobbs Cafe:cafe",
            },
        ],
    )

    # LLM logs (sanity 3 rows).
    for i in range(3):
        repos.llm_logs.add(
            sim.id,
            persona_name="Isabella Rodriguez",
            step=i,
            ts=datetime(2026, 5, 12, 19, 21, 21 + i),
            model="deepseek-chat",
            provider="deepseek",
            prompt=f"prompt-{i}",
            response=f"response-{i}",
            prompt_tokens=10,
            completion_tokens=2,
            latency_ms=100 + i,
            error=None,
        )

    return sim.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_export_compressed_layout(session: Session, tmp_path: Path) -> None:
    sim_id = _seed_full_sim(session)

    out = export_simulation(sim_id, tmp_path, session, layout="compressed")

    assert out == tmp_path / "test_export_sim"
    assert out.is_dir()

    # Top-level meta.
    meta_path = out / "meta.json"
    assert meta_path.is_file()
    meta = parse_reverie_meta(meta_path.read_text(encoding="utf-8"))
    assert meta.start_date == "February 13, 2023"
    assert meta.curr_time == "February 13, 2023, 00:00:20"
    assert meta.sec_per_step == 10
    assert meta.maze_name == "the_ville"
    assert meta.step == 2
    assert meta.fork_sim_code == "seed_sim"
    assert set(meta.persona_names) == {"Isabella Rodriguez", "Klaus Mueller"}

    # No live-layout artifacts.
    assert not (out / "reverie").exists()
    assert not (out / "environment").exists()
    assert not (out / "movement").exists()
    assert not (out / "llm_logs.jsonl").exists()

    # master_movement.json — both steps present.
    master_path = out / "master_movement.json"
    assert master_path.is_file()
    master = parse_master_movement(master_path.read_text(encoding="utf-8"))
    assert set(master.keys()) == {"0", "1"}
    assert set(master["0"].keys()) == {"Isabella Rodriguez", "Klaus Mueller"}
    isa_step0 = master["0"]["Isabella Rodriguez"]
    assert isa_step0.movement == [72, 14]
    assert isa_step0.pronunciatio == "🧍"
    # Chat at step 1 round-trips through JSON.
    assert master["1"]["Isabella Rodriguez"].chat == [["Isabella Rodriguez", "hello"]]

    # Per-persona bootstrap_memory files.
    for name in ("Isabella Rodriguez", "Klaus Mueller"):
        pdir = out / "personas" / name / "bootstrap_memory"
        assert (pdir / "scratch.json").is_file()
        assert (pdir / "spatial_memory.json").is_file()
        am = pdir / "associative_memory"
        assert (am / "nodes.json").is_file()
        assert (am / "kw_strength.json").is_file()
        assert (am / "embeddings.json").is_file()

        scratch = parse_scratch((pdir / "scratch.json").read_text(encoding="utf-8"))
        assert scratch.name == name
        spatial = parse_spatial_memory(
            (pdir / "spatial_memory.json").read_text(encoding="utf-8")
        )
        assert "the Ville" in spatial
        # embeddings.json is the empty placeholder.
        assert json.loads((am / "embeddings.json").read_text(encoding="utf-8")) == {}

    # Memory + kw_strength for Isabella.
    isa_dir = out / "personas" / "Isabella Rodriguez" / "bootstrap_memory" / "associative_memory"
    nodes = parse_nodes((isa_dir / "nodes.json").read_text(encoding="utf-8"))
    assert set(nodes.keys()) == {"node_1", "node_2"}
    # Newest-first ordering — node_2 must come first in the file.
    file_keys = list(
        json.loads((isa_dir / "nodes.json").read_text(encoding="utf-8")).keys()
    )
    assert file_keys[0] == "node_2"
    # Step-to-timestamp conversion: created=0 → start time.
    assert nodes["node_1"].created == "2023-02-13 00:00:00"
    # node_2 created=1 step (10s later).
    assert nodes["node_2"].created == "2023-02-13 00:00:10"
    # expiration_step=100 → 100 * 10s = 1000s after start = +16min40s.
    assert nodes["node_2"].expiration == "2023-02-13 00:16:40"
    # embedding_key is the empty-string placeholder.
    assert nodes["node_1"].embedding_key == ""

    kw_strength = json.loads(
        (isa_dir / "kw_strength.json").read_text(encoding="utf-8")
    )
    assert kw_strength["kw_strength_event"] == {"counter": 1, "idle": 1}
    assert kw_strength["kw_strength_thought"] == {"plan": 1}


def test_export_live_layout(session: Session, tmp_path: Path) -> None:
    sim_id = _seed_full_sim(session)

    out = export_simulation(sim_id, tmp_path, session, layout="live")

    # Live-layout artifacts present.
    assert (out / "reverie" / "meta.json").is_file()
    assert (out / "environment" / "0.json").is_file()
    assert (out / "environment" / "1.json").is_file()
    assert (out / "movement" / "0.json").is_file()
    assert (out / "movement" / "1.json").is_file()
    assert (out / "llm_logs.jsonl").is_file()
    assert not (out / "master_movement.json").exists()
    assert not (out / "meta.json").exists()

    meta = parse_reverie_meta(
        (out / "reverie" / "meta.json").read_text(encoding="utf-8")
    )
    assert meta.start_date == "February 13, 2023"

    env0 = json.loads((out / "environment" / "0.json").read_text(encoding="utf-8"))
    assert env0["Isabella Rodriguez"]["x"] == 72
    assert env0["Klaus Mueller"]["maze"] == "the_ville"

    mv0 = parse_movement_snapshot(
        (out / "movement" / "0.json").read_text(encoding="utf-8")
    )
    assert set(mv0.persona.keys()) == {"Isabella Rodriguez", "Klaus Mueller"}
    assert mv0.persona["Klaus Mueller"].movement == [50, 30]
    assert mv0.meta.curr_time == "February 13, 2023, 00:00:20"

    # llm_logs.jsonl: line count equals row count, chronological.
    lines = [
        line for line in (out / "llm_logs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    # Original insertion order (oldest first): step 0, 1, 2.
    assert [r["step"] for r in parsed] == [0, 1, 2]
    assert parsed[0]["model"] == "deepseek-chat"
    assert parsed[0]["usage"]["prompt_tokens"] == 10
    assert parsed[0]["usage"]["total_tokens"] == 12


def test_export_empty_sim(session: Session, tmp_path: Path) -> None:
    """A sim with no personas, no steps, no LLM logs still produces meta.json."""
    repos = make_repos(session)
    sim = repos.simulations.create(
        sim_code="empty_sim",
        start_time_iso="2024-01-01T00:00:00",
        curr_time_iso="2024-01-01T00:00:00",
        sec_per_step=10,
        step=0,
        n_round=10,
    )

    out_compressed = export_simulation(sim.id, tmp_path / "c", session, layout="compressed")
    assert (out_compressed / "meta.json").is_file()
    # master_movement.json is an empty dict (no movement rows).
    assert json.loads((out_compressed / "master_movement.json").read_text(encoding="utf-8")) == {}
    # No persona directories.
    assert not (out_compressed / "personas").exists()

    out_live = export_simulation(sim.id, tmp_path / "l", session, layout="live")
    assert (out_live / "reverie" / "meta.json").is_file()
    # No movement or environment files since there's no step data.
    assert not (out_live / "environment").exists()
    assert not (out_live / "movement").exists()
    # llm_logs.jsonl is always created in live mode, possibly empty.
    log_path = out_live / "llm_logs.jsonl"
    assert log_path.is_file()
    assert log_path.read_text(encoding="utf-8").strip() == ""


def test_export_unknown_sim_id_raises(session: Session, tmp_path: Path) -> None:
    with pytest.raises(SimulationNotFound):
        export_simulation(99999, tmp_path, session, layout="compressed")


def test_export_invalid_layout_raises(session: Session, tmp_path: Path) -> None:
    sim_id = _seed_full_sim(session)
    with pytest.raises(ValueError):
        export_simulation(sim_id, tmp_path, session, layout="weird")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Round-trip test — depends on the importer (Wave 2 Agent D, parallel)
# ---------------------------------------------------------------------------


def _importer_available() -> bool:
    try:
        from storage import importer  # noqa: F401
        return hasattr(importer, "import_simulation")
    except Exception:
        return False


@pytest.mark.skipif(
    not _importer_available(),
    reason="storage.importer.import_simulation is not yet implemented "
    "(Wave 2 Agent D runs in parallel; round-trip test will activate once it lands).",
)
def test_round_trip_idempotent(session: Session, tmp_path: Path) -> None:
    """Import a sim → export → re-import → row counts must match.

    Lossy timestamp conversions are accepted; the assertion is on cardinalities,
    not byte-equality.
    """
    from storage.importer import import_simulation  # type: ignore

    sim_id = _seed_full_sim(session)
    out = export_simulation(sim_id, tmp_path, session, layout="live")

    # Re-import into a fresh DB.
    engine2 = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine2)
    with Session(engine2, future=True) as s2:
        new_sim_id = import_simulation(out, s2)
        repos2 = make_repos(s2)

        original_repos = make_repos(session)
        orig_personas = original_repos.personas.list_by_sim(sim_id)
        new_personas = repos2.personas.list_by_sim(new_sim_id)
        assert len(new_personas) == len(orig_personas)

        for orig_p in orig_personas:
            new_p = next((p for p in new_personas if p.name == orig_p.name), None)
            assert new_p is not None, f"Persona {orig_p.name} missing after round trip"
            orig_nodes = original_repos.memory.get_all_nodes(orig_p.id)
            new_nodes = repos2.memory.get_all_nodes(new_p.id)
            assert len(new_nodes) == len(orig_nodes), (
                f"Memory node count mismatch for {orig_p.name}: "
                f"{len(orig_nodes)} vs {len(new_nodes)}"
            )
