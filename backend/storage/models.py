"""SQLAlchemy 2.0 declarative ORM models for stanford-town-vue.

Thirteen tables covering simulation metadata, personas + spatial memory,
associative memory (nodes + keyword indices), per-step world state
(environment + movements), LLM call logs, and app-layer settings/profiles.

All tables are declared on the ``Base`` exported from ``storage.db``.
Repository classes (M2 wave 2) own joins and higher-level helpers; this
module deliberately keeps ``relationship()`` declarations to a minimum.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from storage.db import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SimulationStatus(str, enum.Enum):
    """Lifecycle status of a simulation row."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    STOPPED = "stopped"


class MemoryNodeType(str, enum.Enum):
    """Type discriminator for associative-memory nodes."""

    EVENT = "event"
    THOUGHT = "thought"
    CHAT = "chat"


_SIM_STATUS_VALUES = ", ".join(f"'{s.value}'" for s in SimulationStatus)
_MEMORY_NODE_TYPE_VALUES = ", ".join(f"'{t.value}'" for t in MemoryNodeType)


# ---------------------------------------------------------------------------
# 1. simulations
# ---------------------------------------------------------------------------


class Simulation(Base):
    __tablename__ = "simulations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sim_code: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    fork_sim_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SimulationStatus.IDLE.value,
    )
    start_time_iso: Mapped[str] = mapped_column(String(64), nullable=False)
    curr_time_iso: Mapped[str] = mapped_column(String(64), nullable=False)
    sec_per_step: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    maze_name: Mapped[str] = mapped_column(String(64), nullable=False, default="the_ville")
    idea: Mapped[str | None] = mapped_column(Text, nullable=True)
    inner_voice: Mapped[str | None] = mapped_column(Text, nullable=True)
    n_round: Mapped[int] = mapped_column(Integer, nullable=False)
    investment: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        CheckConstraint(
            f"status IN ({_SIM_STATUS_VALUES})",
            name="ck_simulations_status",
        ),
    )


# ---------------------------------------------------------------------------
# 2. simulation_config_snapshots
# ---------------------------------------------------------------------------


class SimulationConfigSnapshot(Base):
    __tablename__ = "simulation_config_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sim_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    llm_profile_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    persona_filter_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


# ---------------------------------------------------------------------------
# 3. personas
# ---------------------------------------------------------------------------


class Persona(Base):
    __tablename__ = "personas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sim_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    scratch_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("sim_id", "name", name="uq_personas_sim_name"),
    )


# ---------------------------------------------------------------------------
# 4. spatial_memory_trees
# ---------------------------------------------------------------------------


class SpatialMemoryTree(Base):
    __tablename__ = "spatial_memory_trees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    persona_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("personas.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    tree_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


# ---------------------------------------------------------------------------
# 5. memory_nodes
# ---------------------------------------------------------------------------


class MemoryNode(Base):
    __tablename__ = "memory_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    persona_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("personas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    node_type: Mapped[str] = mapped_column(String(16), nullable=False)
    node_count: Mapped[int] = mapped_column(Integer, nullable=False)
    type_count: Mapped[int] = mapped_column(Integer, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created: Mapped[int] = mapped_column(Integer, nullable=False)
    expiration_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    predicate: Mapped[str] = mapped_column(String(255), nullable=False)
    object: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    poignancy: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    keywords_json: Mapped[list] = mapped_column(JSON, nullable=False)
    filling_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        CheckConstraint(
            f"node_type IN ({_MEMORY_NODE_TYPE_VALUES})",
            name="ck_memory_nodes_node_type",
        ),
        UniqueConstraint("persona_id", "node_id", name="uq_memory_nodes_persona_node"),
        Index(
            "ix_memory_nodes_persona_type_created",
            "persona_id",
            "node_type",
            "created",
        ),
    )


# ---------------------------------------------------------------------------
# 6-8. memory_keywords_to_{event,chat,thought}
# ---------------------------------------------------------------------------


class MemoryKeywordsToEvent(Base):
    __tablename__ = "memory_keywords_to_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    persona_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("personas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    keyword: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "persona_id", "keyword", "node_id",
            name="uq_mkw_event_persona_kw_node",
        ),
    )


class MemoryKeywordsToChat(Base):
    __tablename__ = "memory_keywords_to_chat"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    persona_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("personas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    keyword: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "persona_id", "keyword", "node_id",
            name="uq_mkw_chat_persona_kw_node",
        ),
    )


class MemoryKeywordsToThought(Base):
    __tablename__ = "memory_keywords_to_thought"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    persona_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("personas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    keyword: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "persona_id", "keyword", "node_id",
            name="uq_mkw_thought_persona_kw_node",
        ),
    )


# ---------------------------------------------------------------------------
# 9. step_environments
# ---------------------------------------------------------------------------


class StepEnvironment(Base):
    __tablename__ = "step_environments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sim_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint("sim_id", "step", name="uq_step_environments_sim_step"),
        Index("ix_step_environments_sim_step", "sim_id", "step"),
    )


# ---------------------------------------------------------------------------
# 10. step_movements
# ---------------------------------------------------------------------------


class StepMovement(Base):
    __tablename__ = "step_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sim_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    persona_name: Mapped[str] = mapped_column(String(128), nullable=False)
    x: Mapped[int] = mapped_column(Integer, nullable=False)
    y: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    pronunciatio: Mapped[str | None] = mapped_column(String(32), nullable=True)
    chat_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    location_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "sim_id", "step", "persona_name",
            name="uq_step_movements_sim_step_persona",
        ),
        Index("ix_step_movements_sim_step", "sim_id", "step"),
    )


# ---------------------------------------------------------------------------
# 11. llm_calls
# ---------------------------------------------------------------------------


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sim_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
    )
    persona_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_llm_calls_sim_ts", "sim_id", "ts"),
    )


# ---------------------------------------------------------------------------
# 12. llm_profiles
# ---------------------------------------------------------------------------


class LlmProfile(Base):
    __tablename__ = "llm_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    api_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=4096)
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    extra_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "provider IN ('openai', 'deepseek', 'anthropic')",
            name="ck_llm_profiles_provider",
        ),
    )


# ---------------------------------------------------------------------------
# 13. app_settings
# ---------------------------------------------------------------------------


class AppSetting(Base):
    __tablename__ = "app_settings"

    k: Mapped[str] = mapped_column(String(128), primary_key=True)
    v: Mapped[str] = mapped_column(Text, nullable=False)


__all__ = [
    "SimulationStatus",
    "MemoryNodeType",
    "Simulation",
    "SimulationConfigSnapshot",
    "Persona",
    "SpatialMemoryTree",
    "MemoryNode",
    "MemoryKeywordsToEvent",
    "MemoryKeywordsToChat",
    "MemoryKeywordsToThought",
    "StepEnvironment",
    "StepMovement",
    "LlmCall",
    "LlmProfile",
    "AppSetting",
]
