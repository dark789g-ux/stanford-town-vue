"""Unit tests for storage.models — insert + query against in-memory SQLite."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from storage.db import Base
from storage.models import (
    AppSetting,
    LlmCall,
    LlmProfile,
    MemoryKeywordsToEvent,
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


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def test_insert_and_query_each_major_table(session: Session) -> None:
    # 1. simulations
    sim = Simulation(
        sim_code="test_sim_001",
        status=SimulationStatus.IDLE.value,
        start_time_iso="2025-01-01T00:00:00",
        curr_time_iso="2025-01-01T00:00:00",
        n_round=100,
    )
    session.add(sim)
    session.flush()

    # 2. simulation_config_snapshots
    snap = SimulationConfigSnapshot(
        sim_id=sim.id,
        llm_profile_json={"provider": "deepseek", "model": "deepseek-chat"},
        persona_filter_json={"include": ["Isabella Rodriguez"]},
    )
    session.add(snap)

    # 3. personas
    persona = Persona(
        sim_id=sim.id,
        name="Isabella Rodriguez",
        age=34,
        plan_text="Open the cafe at 8am",
        scratch_json={"curr_tile": [72, 14]},
    )
    session.add(persona)
    session.flush()

    # 4. spatial_memory_trees
    tree = SpatialMemoryTree(
        persona_id=persona.id,
        tree_json={"the Ville": {"Hobbs Cafe": {"cafe": ["counter"]}}},
    )
    session.add(tree)

    # 5. memory_nodes
    node = MemoryNode(
        persona_id=persona.id,
        node_id="node_001",
        node_type=MemoryNodeType.EVENT.value,
        node_count=1,
        type_count=1,
        depth=0,
        created=0,
        subject="Isabella Rodriguez",
        predicate="is",
        object="idle",
        description="Isabella is idle",
        poignancy=2,
        keywords_json=["isabella", "idle"],
        filling_json=None,
    )
    session.add(node)

    # 6. memory_keywords_to_event
    kw = MemoryKeywordsToEvent(
        persona_id=persona.id, keyword="isabella", node_id="node_001"
    )
    session.add(kw)

    # 9. step_environments
    env = StepEnvironment(
        sim_id=sim.id,
        step=0,
        payload_json={"Isabella Rodriguez": {"x": 72, "y": 14, "maze": "the_ville"}},
    )
    session.add(env)

    # 10. step_movements
    mv = StepMovement(
        sim_id=sim.id,
        step=0,
        persona_name="Isabella Rodriguez",
        x=72,
        y=14,
        description="idle",
        pronunciatio="🧍",
        chat_json=None,
        location_path="the Ville:Hobbs Cafe:cafe",
    )
    session.add(mv)

    # 11. llm_calls
    call = LlmCall(
        sim_id=sim.id,
        persona_name="Isabella Rodriguez",
        step=0,
        ts=datetime.utcnow(),
        model="deepseek-chat",
        provider="deepseek",
        prompt="Hi",
        response="Hello",
        prompt_tokens=1,
        completion_tokens=1,
        latency_ms=42,
    )
    session.add(call)

    # 12. llm_profiles
    profile = LlmProfile(
        name="default",
        provider="deepseek",
        model="deepseek-chat",
        api_key=b"encrypted-bytes",
        max_tokens=4096,
        temperature=0.5,
    )
    session.add(profile)

    # 13. app_settings
    session.add(AppSetting(k="theme", v="dark"))

    session.commit()

    # --- query each back ---
    got_sim = session.scalar(select(Simulation).where(Simulation.sim_code == "test_sim_001"))
    assert got_sim is not None
    assert got_sim.status == "idle"
    assert got_sim.sec_per_step == 10  # default
    assert got_sim.maze_name == "the_ville"  # default
    assert got_sim.deleted is False

    got_snap = session.scalar(select(SimulationConfigSnapshot))
    assert got_snap.llm_profile_json["model"] == "deepseek-chat"

    got_persona = session.scalar(select(Persona).where(Persona.name == "Isabella Rodriguez"))
    assert got_persona is not None and got_persona.sim_id == sim.id

    got_tree = session.scalar(select(SpatialMemoryTree))
    assert "the Ville" in got_tree.tree_json

    got_node = session.scalar(
        select(MemoryNode).where(MemoryNode.persona_id == persona.id)
    )
    assert got_node.node_id == "node_001"
    assert got_node.node_type == "event"
    assert got_node.keywords_json == ["isabella", "idle"]

    got_kw = session.scalar(select(MemoryKeywordsToEvent))
    assert got_kw.keyword == "isabella"

    got_env = session.scalar(select(StepEnvironment))
    assert got_env.payload_json["Isabella Rodriguez"]["x"] == 72

    got_mv = session.scalar(select(StepMovement))
    assert got_mv.persona_name == "Isabella Rodriguez"
    assert got_mv.x == 72 and got_mv.y == 14

    got_call = session.scalar(select(LlmCall))
    assert got_call.provider == "deepseek"

    got_profile = session.scalar(select(LlmProfile).where(LlmProfile.name == "default"))
    assert got_profile.api_key == b"encrypted-bytes"

    got_setting = session.get(AppSetting, "theme")
    assert got_setting.v == "dark"


def test_simulation_status_check_constraint(session: Session) -> None:
    sim = Simulation(
        sim_code="bad_status",
        status="not_a_status",
        start_time_iso="2025-01-01T00:00:00",
        curr_time_iso="2025-01-01T00:00:00",
        n_round=10,
    )
    session.add(sim)
    with pytest.raises(Exception):
        session.commit()


def test_cascade_delete_persona_removes_memory_nodes(session: Session) -> None:
    sim = Simulation(
        sim_code="cascade_test",
        status=SimulationStatus.IDLE.value,
        start_time_iso="2025-01-01T00:00:00",
        curr_time_iso="2025-01-01T00:00:00",
        n_round=10,
    )
    session.add(sim)
    session.flush()
    persona = Persona(sim_id=sim.id, name="Foo")
    session.add(persona)
    session.flush()
    node = MemoryNode(
        persona_id=persona.id,
        node_id="node_001",
        node_type=MemoryNodeType.EVENT.value,
        node_count=1,
        type_count=1,
        created=0,
        subject="x",
        predicate="y",
        object="z",
        description="d",
        keywords_json=[],
    )
    session.add(node)
    session.commit()

    # Enable FK enforcement on this SQLite connection (off by default).
    session.execute(__import__("sqlalchemy").text("PRAGMA foreign_keys=ON"))
    session.delete(persona)
    session.commit()

    remaining = session.scalars(select(MemoryNode)).all()
    assert remaining == []
