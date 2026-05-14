"""Unit tests for storage.repos — in-memory SQLite, fresh session per test."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# Make backend/ importable when pytest is run from the repo root.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from storage.db import Base  # noqa: E402
from storage.models import (  # noqa: E402
    MemoryKeywordsToChat,
    MemoryKeywordsToEvent,
    MemoryKeywordsToThought,
    MemoryNodeType,
    SimulationStatus,
)
from storage.repos import (  # noqa: E402
    LlmLogRepo,
    LlmProfileRepo,
    MemoryRepo,
    PersonaRepo,
    Repos,
    SimulationRepo,
    StepRepo,
    make_repos,
)


# ---------------------------------------------------------------------- helpers


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


@pytest.fixture()
def repos(session: Session) -> Repos:
    return make_repos(session, fernet_key=Fernet.generate_key())


def _make_sim(repo: SimulationRepo, code: str = "sim_001"):
    return repo.create(
        code,
        status=SimulationStatus.IDLE,
        start_time_iso="2025-01-01T00:00:00",
        curr_time_iso="2025-01-01T00:00:00",
        n_round=100,
    )


# -------------------------------------------------------------------- factory


def test_make_repos_returns_all_six_repos(session: Session) -> None:
    bundle = make_repos(session)
    assert isinstance(bundle.simulations, SimulationRepo)
    assert isinstance(bundle.personas, PersonaRepo)
    assert isinstance(bundle.memory, MemoryRepo)
    assert isinstance(bundle.steps, StepRepo)
    assert isinstance(bundle.llm_logs, LlmLogRepo)
    assert isinstance(bundle.llm_profiles, LlmProfileRepo)


def test_make_repos_without_fernet_key_blocks_encryption(session: Session) -> None:
    bundle = make_repos(session)  # no key
    with pytest.raises(RuntimeError):
        bundle.llm_profiles.create(
            name="x", provider="openai", model="gpt-4o-mini", api_key="sk-123"
        )


# ----------------------------------------------------------------- simulations


def test_simulation_lifecycle(repos: Repos) -> None:
    sim = _make_sim(repos.simulations, "lifecycle")
    assert sim.id is not None
    assert sim.status == "idle"

    repos.simulations.set_status(sim.id, SimulationStatus.RUNNING)
    assert repos.simulations.get_by_id(sim.id).status == "running"

    repos.simulations.advance_step(sim.id, 5, "2025-01-01T00:05:00")
    refreshed = repos.simulations.get_by_code("lifecycle")
    assert refreshed.step == 5
    assert refreshed.curr_time_iso == "2025-01-01T00:05:00"

    repos.simulations.update(sim.id, idea="new idea", investment=12.5)
    again = repos.simulations.get_by_id(sim.id)
    assert again.idea == "new idea"
    assert again.investment == 12.5

    repos.simulations.set_status(sim.id, "failed", error_message="boom")
    failed = repos.simulations.get_by_id(sim.id)
    assert failed.status == "failed"
    assert failed.error_message == "boom"


def test_simulation_list_and_soft_delete(repos: Repos) -> None:
    a = _make_sim(repos.simulations, "a")
    b = _make_sim(repos.simulations, "b")
    repos.simulations.set_status(b.id, SimulationStatus.RUNNING)

    all_default = repos.simulations.list()
    assert {s.sim_code for s in all_default} == {"a", "b"}

    running = repos.simulations.list(status=SimulationStatus.RUNNING)
    assert [s.sim_code for s in running] == ["b"]

    repos.simulations.soft_delete(a.id)
    visible = repos.simulations.list()
    assert {s.sim_code for s in visible} == {"b"}

    with_deleted = repos.simulations.list(include_deleted=True)
    assert {s.sim_code for s in with_deleted} == {"a", "b"}


# -------------------------------------------------------------------- personas


def test_persona_create_is_upsert(repos: Repos) -> None:
    sim = _make_sim(repos.simulations, "p_sim")
    p1 = repos.personas.create(sim.id, "Isabella Rodriguez", age=34)
    assert p1.age == 34

    # Same (sim_id, name) → update + return existing row (id preserved).
    p2 = repos.personas.create(sim.id, "Isabella Rodriguez", plan_text="Open the cafe")
    assert p2.id == p1.id
    assert p2.age == 34  # not nulled out by missing field
    assert p2.plan_text == "Open the cafe"

    fetched = repos.personas.get(sim.id, "Isabella Rodriguez")
    assert fetched.id == p1.id
    listed = repos.personas.list_by_sim(sim.id)
    assert len(listed) == 1


def test_persona_scratch_and_spatial_memory_upsert(repos: Repos) -> None:
    sim = _make_sim(repos.simulations, "p_sim2")
    p = repos.personas.create(sim.id, "Klaus Mueller")

    repos.personas.save_scratch(p.id, {"curr_tile": [10, 20]})
    assert repos.personas.load_scratch(p.id) == {"curr_tile": [10, 20]}

    repos.personas.save_spatial_memory(p.id, {"the Ville": {"Cafe": {}}})
    assert repos.personas.load_spatial_memory(p.id) == {"the Ville": {"Cafe": {}}}

    # upsert overwrites
    repos.personas.save_spatial_memory(p.id, {"the Ville": {"Park": {}}})
    assert repos.personas.load_spatial_memory(p.id) == {"the Ville": {"Park": {}}}


# --------------------------------------------------------------------- memory


def test_memory_add_node_auto_assigns_count(repos: Repos) -> None:
    sim = _make_sim(repos.simulations, "m_sim")
    p = repos.personas.create(sim.id, "Maria Lopez")

    n1 = repos.memory.add_node(
        p.id,
        "node_001",
        MemoryNodeType.EVENT,
        type_count=1,
        created=0,
        subject="Maria",
        predicate="is",
        object="idle",
        description="idle",
        keywords_json=["idle"],
    )
    assert n1.node_count == 1
    n2 = repos.memory.add_node(p.id, "node_002", "event")
    assert n2.node_count == 2
    assert repos.memory.get_max_node_count(p.id) == 2


def test_memory_bulk_insert_and_filters(repos: Repos) -> None:
    sim = _make_sim(repos.simulations, "m_sim2")
    p = repos.personas.create(sim.id, "Maria Lopez")

    rows = [
        {
            "node_id": f"n_{i}",
            "node_type": "event" if i % 2 == 0 else "thought",
            "node_count": i,
            "type_count": i,
            "depth": 0,
            "created": i,
            "subject": "s",
            "predicate": "p",
            "object": "o",
            "description": f"d{i}",
            "poignancy": 1,
            "keywords_json": ["kw"],
        }
        for i in range(1, 6)
    ]
    inserted = repos.memory.add_nodes_bulk(p.id, rows)
    assert inserted == 5

    # Re-inserting same node_ids is a no-op due to ON CONFLICT DO NOTHING.
    again = repos.memory.add_nodes_bulk(p.id, rows)
    assert again == 0

    events = repos.memory.list_nodes(p.id, node_type=MemoryNodeType.EVENT)
    assert {n.node_id for n in events} == {"n_2", "n_4"}

    early = repos.memory.list_nodes(p.id, before_step=3)
    assert {n.node_id for n in early} == {"n_1", "n_2"}

    limited = repos.memory.list_nodes(p.id, limit=2)
    assert len(limited) == 2

    assert repos.memory.get_node(p.id, "n_3").description == "d3"
    assert len(repos.memory.get_all_nodes(p.id)) == 5


def test_memory_keyword_routing_to_correct_table(
    repos: Repos, session: Session
) -> None:
    sim = _make_sim(repos.simulations, "kw_sim")
    p = repos.personas.create(sim.id, "Maria")

    repos.memory.add_keyword(p.id, MemoryNodeType.EVENT, "coffee", "n_1")
    repos.memory.add_keyword(p.id, "chat", "coffee", "n_2")
    repos.memory.add_keyword(p.id, "thought", "coffee", "n_3")
    # Re-inserting same triple is a no-op.
    repos.memory.add_keyword(p.id, MemoryNodeType.EVENT, "coffee", "n_1")

    n_event = session.query(MemoryKeywordsToEvent).count()
    n_chat = session.query(MemoryKeywordsToChat).count()
    n_thought = session.query(MemoryKeywordsToThought).count()
    assert (n_event, n_chat, n_thought) == (1, 1, 1)

    bulk = repos.memory.add_keywords_bulk(
        p.id, "event", [("coffee", "n_4"), ("tea", "n_5")]
    )
    assert bulk == 2

    coffee_events = repos.memory.list_keywords(p.id, "event", keyword="coffee")
    assert sorted(coffee_events) == [("coffee", "n_1"), ("coffee", "n_4")]

    all_event_kw = repos.memory.list_keywords(p.id, MemoryNodeType.EVENT)
    assert len(all_event_kw) == 3

    with pytest.raises(ValueError):
        repos.memory.add_keyword(p.id, "bogus", "x", "y")


def test_memory_delete_nodes_for_persona_wipes_keywords(
    repos: Repos, session: Session
) -> None:
    sim = _make_sim(repos.simulations, "wipe_sim")
    p = repos.personas.create(sim.id, "Maria")
    repos.memory.add_node(p.id, "n_1", "event")
    repos.memory.add_keyword(p.id, "event", "coffee", "n_1")

    removed = repos.memory.delete_nodes_for_persona(p.id)
    assert removed == 1
    assert repos.memory.get_all_nodes(p.id) == []
    assert session.query(MemoryKeywordsToEvent).count() == 0


# ----------------------------------------------------------------------- steps


def test_step_environment_upsert_and_get(repos: Repos) -> None:
    sim = _make_sim(repos.simulations, "env_sim")
    repos.steps.upsert_environment(sim.id, 0, {"Isabella": {"x": 1, "y": 2}})
    assert repos.steps.get_environment(sim.id, 0) == {
        "Isabella": {"x": 1, "y": 2}
    }
    # Upsert overwrites prior payload.
    repos.steps.upsert_environment(sim.id, 0, {"Isabella": {"x": 9, "y": 9}})
    assert repos.steps.get_environment(sim.id, 0) == {
        "Isabella": {"x": 9, "y": 9}
    }
    assert repos.steps.get_environment(sim.id, 99) is None


def test_step_movements_upsert_and_range(repos: Repos) -> None:
    sim = _make_sim(repos.simulations, "mv_sim")
    repos.steps.upsert_movements_for_step(
        sim.id,
        0,
        [
            {
                "persona_name": "Isabella",
                "x": 1,
                "y": 2,
                "description": "idle",
                "pronunciatio": "x",
                "chat": None,
                "location_path": "Ville:Cafe",
            },
            {
                "persona_name": "Klaus",
                "x": 3,
                "y": 4,
                "description": "walking",
                "pronunciatio": "y",
                "chat": [["Klaus", "hi"]],
                "location_path": None,
            },
        ],
    )
    repos.steps.upsert_movements_for_step(
        sim.id,
        1,
        [{"persona_name": "Isabella", "x": 5, "y": 6, "description": None,
          "pronunciatio": None, "chat": None, "location_path": None}],
    )
    step0 = repos.steps.get_movements(sim.id, 0)
    assert {m.persona_name for m in step0} == {"Isabella", "Klaus"}

    # Replace-style upsert.
    repos.steps.upsert_movements_for_step(
        sim.id,
        0,
        [{"persona_name": "Solo", "x": 0, "y": 0, "description": None,
          "pronunciatio": None, "chat": None, "location_path": None}],
    )
    step0_again = repos.steps.get_movements(sim.id, 0)
    assert [m.persona_name for m in step0_again] == ["Solo"]

    rng = repos.steps.list_movements_range(sim.id, 0, 1)
    assert len(rng) == 2

    assert repos.steps.get_max_step(sim.id) == 1


def test_step_delete_for_sim(repos: Repos) -> None:
    sim = _make_sim(repos.simulations, "del_sim")
    repos.steps.upsert_environment(sim.id, 0, {"a": 1})
    repos.steps.upsert_movements_for_step(
        sim.id, 0,
        [{"persona_name": "Isabella", "x": 1, "y": 2, "description": None,
          "pronunciatio": None, "chat": None, "location_path": None}],
    )
    deleted = repos.steps.delete_steps_for_sim(sim.id)
    assert deleted == 2
    assert repos.steps.get_max_step(sim.id) == -1


# -------------------------------------------------------------------- llm logs


def test_llm_log_add_filter_and_pagination(repos: Repos) -> None:
    sim = _make_sim(repos.simulations, "log_sim")
    base_ts = datetime(2025, 1, 1, 0, 0, 0)

    rows = [
        {
            "persona_name": "Isabella" if i % 2 == 0 else "Klaus",
            "step": i,
            "ts": base_ts,
            "model": "deepseek-chat" if i < 5 else "gpt-4o-mini",
            "provider": "deepseek" if i < 5 else "openai",
            "prompt": f"p{i}",
            "response": f"r{i}",
            "prompt_tokens": i,
            "completion_tokens": i,
            "latency_ms": 10 * i,
            "error": None,
        }
        for i in range(10)
    ]
    n = repos.llm_logs.add_bulk(sim.id, rows)
    assert n == 10
    assert repos.llm_logs.count(sim.id) == 10

    # Single add too.
    extra = repos.llm_logs.add(
        sim.id,
        persona_name="Maria",
        step=99,
        ts=base_ts,
        model="deepseek-chat",
        provider="deepseek",
        prompt="q",
        response="a",
    )
    assert extra.id is not None
    assert repos.llm_logs.count(sim.id) == 11

    isabellas = repos.llm_logs.list(sim.id, persona="Isabella", limit=100)
    assert all(c.persona_name == "Isabella" for c in isabellas)

    openai_calls = repos.llm_logs.list(sim.id, model="gpt-4o-mini", limit=100)
    assert {c.model for c in openai_calls} == {"gpt-4o-mini"}

    # Pagination — most recent first.
    page1 = repos.llm_logs.list(sim.id, offset=0, limit=5)
    page2 = repos.llm_logs.list(sim.id, offset=5, limit=5)
    assert len(page1) == 5 and len(page2) == 5
    assert {p.id for p in page1}.isdisjoint({p.id for p in page2})

    fetched = repos.llm_logs.get_by_id(sim.id, extra.id)
    assert fetched is not None and fetched.persona_name == "Maria"
    assert repos.llm_logs.get_by_id(sim.id, 999_999) is None


# ----------------------------------------------------------------- llm profile


def test_llm_profile_encryption_round_trip(repos: Repos) -> None:
    prof = repos.llm_profiles.create(
        name="prod",
        provider="deepseek",
        model="deepseek-chat",
        api_key="sk-supersecret",
        max_tokens=8000,
        temperature=0.3,
    )
    # api_key on the row must NOT be plaintext.
    assert prof.api_key != b"sk-supersecret"
    assert isinstance(prof.api_key, (bytes, bytearray))

    plaintext = repos.llm_profiles.get_decrypted_key(prof.id)
    assert plaintext == "sk-supersecret"

    repos.llm_profiles.update(prof.id, api_key="sk-rotated", temperature=0.9)
    assert repos.llm_profiles.get_decrypted_key(prof.id) == "sk-rotated"
    assert repos.llm_profiles.get(prof.id).temperature == 0.9

    listed = repos.llm_profiles.list()
    assert [p.id for p in listed] == [prof.id]

    repos.llm_profiles.delete(prof.id)
    assert repos.llm_profiles.get(prof.id) is None
