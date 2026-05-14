"""Pydantic v2 schemas that mirror the original Stanford Town JSON storage format.

These models are the *wire format* for the legacy Generative Agents simulator
files (under `storage/{sim_code}/...`). They are **not** the SQLAlchemy ORM
models — those live in :mod:`storage.models`. Two consumers use this module:

* The **importer** (M2 wave 3) parses on-disk JSON into these models, then
  translates them into ORM rows.
* The **exporter** (M2 wave 3) builds these models from ORM rows, then dumps
  JSON back out for the legacy replay viewer.

Every model sets ``extra="allow"`` so upstream forks that add fields don't break
parsing. See ``docs/json_format.md`` for the full field-by-field spec.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator


# ---------------------------------------------------------------------------
# reverie/meta.json
# ---------------------------------------------------------------------------


class ReverieMeta(BaseModel):
    """`reverie/meta.json` — one per simulation."""

    model_config = ConfigDict(extra="allow")

    fork_sim_code: str | None = None
    start_date: str  # "%B %d, %Y"
    curr_time: str  # "%B %d, %Y, %H:%M:%S"
    sec_per_step: int = 10
    maze_name: str = "the_ville"
    persona_names: list[str] = Field(default_factory=list)
    step: int = 0


# ---------------------------------------------------------------------------
# environment/{step}.json
# ---------------------------------------------------------------------------


class PersonaPosition(BaseModel):
    """One persona's tile position in an environment snapshot."""

    model_config = ConfigDict(extra="allow")

    maze: str = "the_ville"
    x: int
    y: int


class EnvironmentSnapshot(RootModel[dict[str, PersonaPosition]]):
    """`environment/{step}.json` — keyed by persona full name."""


# ---------------------------------------------------------------------------
# movement/{step}.json
# ---------------------------------------------------------------------------


class PersonaMovement(BaseModel):
    """One persona's movement delta for a single step.

    Deliberately permissive: the live simulator builds this dict from Action
    outputs, and a degraded run (a flaky LLM response, or the FakeLLM used in
    tests) can yield odd field types — ``description`` may come back as a
    bool, ``movement`` may be a tuple, etc. We coerce rather than reject so a
    single bad frame never breaks step ingestion.
    """

    model_config = ConfigDict(extra="allow")

    # On-disk shape is normally [x, y]. Accept tuples / odd values and coerce.
    movement: list[int] = Field(default_factory=lambda: [0, 0])
    pronunciatio: str | None = None
    description: str | None = None
    # When the persona is in conversation, `chat` is a list of [speaker, utterance]
    # pairs. `null` otherwise — but a degraded run can produce other shapes.
    chat: Any = None

    @field_validator("movement", mode="before")
    @classmethod
    def _coerce_movement(cls, v: Any) -> list[int]:
        if v is None:
            return [0, 0]
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            try:
                return [int(v[0]), int(v[1])]
            except (TypeError, ValueError):
                return [0, 0]
        return [0, 0]

    @field_validator("pronunciatio", "description", mode="before")
    @classmethod
    def _coerce_optional_str(cls, v: Any) -> str | None:
        if v is None or isinstance(v, str):
            return v
        return str(v)


class MovementMeta(BaseModel):
    """Step-level metadata block inside `movement/{step}.json`."""

    model_config = ConfigDict(extra="allow")

    curr_time: str  # "%B %d, %Y, %H:%M:%S"


class MovementSnapshot(BaseModel):
    """`movement/{step}.json` — live-runner per-step file.

    Shape: ``{"persona": {name: PersonaMovement, ...}, "meta": MovementMeta}``.
    """

    model_config = ConfigDict(extra="allow")

    persona: dict[str, PersonaMovement] = Field(default_factory=dict)
    meta: MovementMeta


class MasterMovement(RootModel[dict[str, dict[str, PersonaMovement]]]):
    """`master_movement.json` — compressed-archive concatenation.

    Top-level keys are stringified step numbers ("0", "1", ...). The inner dict
    is ``{persona_name: PersonaMovement}`` (no outer "meta" envelope).
    """


# ---------------------------------------------------------------------------
# personas/<name>/bootstrap_memory/scratch.json
# ---------------------------------------------------------------------------


class Scratch(BaseModel):
    """`personas/<name>/bootstrap_memory/scratch.json` — large blob.

    Mirrors :class:`simulator.memory.scratch.Scratch` but keeps every field as
    the on-disk JSON type (e.g. ``curr_time`` stays a string, not a
    ``datetime``). The importer / exporter do the conversion.
    """

    model_config = ConfigDict(extra="allow")

    # --- hyperparameters
    vision_r: int = 4
    att_bandwidth: int = 3
    retention: int = 5

    # --- world snapshot
    curr_time: str | None = None
    curr_tile: list[int] | None = None
    daily_plan_req: str | None = None

    # --- core identity
    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    age: int | None = None
    innate: str | None = None
    learned: str | None = None
    currently: str | None = None
    lifestyle: str | None = None
    living_area: str | None = None

    # --- reflection knobs
    concept_forget: int = 100
    daily_reflection_time: int = 180
    daily_reflection_size: int = 5
    overlap_reflect_th: int = 2
    kw_strg_event_reflect_th: int = 4
    kw_strg_thought_reflect_th: int = 4

    recency_w: int = 1
    relevance_w: int = 1
    importance_w: int = 1
    recency_decay: float = 0.99
    importance_trigger_max: int = 150
    importance_trigger_curr: int = 150
    importance_ele_n: int = 0
    thought_count: int = 5

    # --- daily plan
    daily_req: list[str] = Field(default_factory=list)
    # On-disk values are always [str, int] but the simulator declares the
    # broader union; tolerate both orderings.
    f_daily_schedule: list[list[Any]] = Field(default_factory=list)
    f_daily_schedule_hourly_org: list[list[Any]] = Field(default_factory=list)

    # --- current action
    act_address: str | None = None
    act_start_time: str | None = None
    act_duration: int | None = None
    act_description: str | None = None
    act_pronunciatio: str | None = None
    act_event: list[str | None] = Field(default_factory=lambda: [None, None, None])

    act_obj_description: str | None = None
    act_obj_pronunciatio: str | None = None
    act_obj_event: list[str | None] = Field(default_factory=lambda: [None, None, None])

    chatting_with: str | None = None
    # `chat` is declared str|None in the simulator but at runtime can hold a
    # list of [speaker, utterance] pairs; on disk it is always null for
    # bootstrap_memory. Tolerate all three shapes.
    chat: list[list[str]] | str | None = None
    chatting_with_buffer: dict[str, int] = Field(default_factory=dict)
    chatting_end_time: str | None = None

    act_path_set: bool = False
    planned_path: list[list[int]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# personas/<name>/bootstrap_memory/spatial_memory.json
# ---------------------------------------------------------------------------


class SpatialMemoryTree(RootModel[dict[str, Any]]):
    """`spatial_memory.json` — nested dict ``world -> sector -> arena -> list[obj]``.

    Typed loosely as ``dict[str, Any]`` because pydantic v2's recursive dict
    inference doesn't handle the leaf-list cleanly. Importer / exporter treat
    it as an opaque blob.
    """


# ---------------------------------------------------------------------------
# personas/<name>/.../associative_memory/nodes.json
# ---------------------------------------------------------------------------


NodeType = Literal["event", "thought", "chat"]


class AssociativeMemoryNode(BaseModel):
    """One entry inside `nodes.json` (which is keyed by ``node_id``)."""

    model_config = ConfigDict(extra="allow")

    node_count: int
    type_count: int
    type: NodeType
    depth: int = 0
    created: str  # "%Y-%m-%d %H:%M:%S"
    expiration: str | None = None
    subject: str
    predicate: str
    object: str
    description: str
    embedding_key: str
    poignancy: int = 0
    keywords: list[str] = Field(default_factory=list)
    # `filling` is heterogeneous: None for some thoughts, [] for events,
    # list[str] of constituent node_ids for most thoughts, list[list[str]]
    # (speaker/utterance pairs) for chat nodes, and occasionally a bare string
    # like ``"node_1"`` referencing a single source node. Importer normalizes.
    filling: Any = None


class NodesFile(RootModel[dict[str, AssociativeMemoryNode]]):
    """`nodes.json` — keyed by ``"node_N"`` strings."""


# ---------------------------------------------------------------------------
# personas/<name>/.../associative_memory/kw_strength.json
# ---------------------------------------------------------------------------


class KwStrength(BaseModel):
    """`kw_strength.json` — aggregate keyword counts."""

    model_config = ConfigDict(extra="allow")

    kw_strength_event: dict[str, int] = Field(default_factory=dict)
    kw_strength_thought: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# personas/<name>/.../associative_memory/embeddings.json
# ---------------------------------------------------------------------------


class EmbeddingsFile(RootModel[dict[str, list[float]]]):
    """`embeddings.json` — ``{embedding_key: [float, ...]}``.

    NOT persisted in stanford-town-vue; importer skips this file, exporter
    writes an empty ``{}``. Schema is provided for reference / parity only.
    """


# ---------------------------------------------------------------------------
# llm_logs.jsonl
# ---------------------------------------------------------------------------


class LlmUsage(BaseModel):
    """Usage block inside an `llm_logs.jsonl` record."""

    model_config = ConfigDict(extra="allow")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LlmLogLine(BaseModel):
    """One JSONL record from `llm_logs.jsonl`.

    Field set matches :mod:`simulator.utils.llm_logger.log_call`. The SQLite
    ``llm_calls`` table is a slimmer subset — see the mapping table in
    ``docs/json_format.md``.
    """

    model_config = ConfigDict(extra="allow")

    seq: int | None = None
    ts: str  # ISO 8601 with local TZ offset
    step: int | None = None
    persona: str | None = None
    action: str | None = None
    model: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    prompt: str
    response: str | None = None
    usage: LlmUsage | None = None
    cost_usd: float | None = None
    latency_ms: int = 0
    retry_idx: int = 0
    used_fail_default: bool = False
    error: str | None = None


# ---------------------------------------------------------------------------
# Thin typed loader helpers
# ---------------------------------------------------------------------------


def parse_reverie_meta(text: str) -> ReverieMeta:
    """Parse `reverie/meta.json` text into :class:`ReverieMeta`."""
    return ReverieMeta.model_validate_json(text)


def parse_environment_snapshot(text: str) -> dict[str, PersonaPosition]:
    """Parse `environment/{step}.json` text into ``{persona_name: PersonaPosition}``."""
    return EnvironmentSnapshot.model_validate_json(text).root


def parse_movement_snapshot(text: str) -> MovementSnapshot:
    """Parse a live-runner `movement/{step}.json` file."""
    return MovementSnapshot.model_validate_json(text)


def parse_master_movement(text: str) -> dict[str, dict[str, PersonaMovement]]:
    """Parse a compressed-archive `master_movement.json` file."""
    return MasterMovement.model_validate_json(text).root


def parse_scratch(text: str) -> Scratch:
    """Parse `personas/<name>/bootstrap_memory/scratch.json`."""
    return Scratch.model_validate_json(text)


def parse_spatial_memory(text: str) -> dict[str, Any]:
    """Parse `personas/<name>/bootstrap_memory/spatial_memory.json` into a dict tree."""
    return SpatialMemoryTree.model_validate_json(text).root


def parse_nodes(text: str) -> dict[str, AssociativeMemoryNode]:
    """Parse `associative_memory/nodes.json` into ``{node_id: Node}``."""
    return NodesFile.model_validate_json(text).root


def parse_kw_strength(text: str) -> KwStrength:
    """Parse `associative_memory/kw_strength.json`."""
    return KwStrength.model_validate_json(text)


def parse_embeddings(text: str) -> dict[str, list[float]]:
    """Parse `associative_memory/embeddings.json`."""
    return EmbeddingsFile.model_validate_json(text).root


def iter_llm_log_lines(text: str):
    """Yield one :class:`LlmLogLine` per non-empty line of `llm_logs.jsonl`."""
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        yield LlmLogLine.model_validate_json(raw)


__all__ = [
    # models
    "ReverieMeta",
    "PersonaPosition",
    "EnvironmentSnapshot",
    "PersonaMovement",
    "MovementMeta",
    "MovementSnapshot",
    "MasterMovement",
    "Scratch",
    "SpatialMemoryTree",
    "NodeType",
    "AssociativeMemoryNode",
    "NodesFile",
    "KwStrength",
    "EmbeddingsFile",
    "LlmUsage",
    "LlmLogLine",
    # loaders
    "parse_reverie_meta",
    "parse_environment_snapshot",
    "parse_movement_snapshot",
    "parse_master_movement",
    "parse_scratch",
    "parse_spatial_memory",
    "parse_nodes",
    "parse_kw_strength",
    "parse_embeddings",
    "iter_llm_log_lines",
]
