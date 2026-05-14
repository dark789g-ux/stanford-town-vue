"""Unit tests for :func:`runner.step_sync.sync_personas_step`.

Each test gets its own in-memory SQLite engine + ``sessionmaker`` so the
function under test (which opens its own short-lived session) can run against
a real schema. Live ``STRole`` objects are stubbed with a lightweight
:class:`_FakeRole` exposing only the four attributes the function touches:
``.name`` / ``.a_mem.storage`` / ``.scratch`` / ``.s_mem.tree``.

The implementation under test is written concurrently; these tests encode the
interface contract from
``docs/superpowers/specs/2026-05-14-per-tick-persona-sync-design.md``.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Make ``import storage.*`` / ``import runner.*`` work when pytest runs from
# the repo root.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from runner.step_sync import sync_personas_step  # noqa: E402
from simulator.memory.agent_memory import BasicMemory  # noqa: E402
from storage import Base  # noqa: E402
from storage.json_schemas import AssociativeMemoryNode  # noqa: E402
from storage.models import (  # noqa: E402
    MemoryKeywordsToChat,
    MemoryKeywordsToEvent,
    MemoryKeywordsToThought,
    SimulationStatus,
)
from storage.repos import make_repos  # noqa: E402

# Common conversion params shared across tests. start_dt matches the game-time
# strings on the BasicMemory nodes below so ``_node_to_row`` -> created step
# computes to deterministic, small integers.
START_DT = datetime(2023, 2, 13, 0, 0, 0)
SEC_PER_STEP = 10


# ---------------------------------------------------------------------------
# Engine / session helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def session_factory():
    """A clean in-memory SQLite DB with the full schema, per-test.

    Yields the ``sessionmaker`` itself (not a session) — ``sync_personas_step``
    opens and closes its own short-lived session, exactly like
    ``sync_step_to_db``.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    try:
        yield SessionLocal
    finally:
        engine.dispose()


def _make_sim(session_factory, code: str = "persona_sync_sim") -> int:
    """Create a ``simulations`` row and return its id."""
    with session_factory() as session:
        repos = make_repos(session)
        sim = repos.simulations.create(
            code,
            status=SimulationStatus.RUNNING,
            start_time_iso="2023-02-13T00:00:00",
            curr_time_iso="2023-02-13T00:00:00",
            n_round=100,
        )
        return sim.id


def _make_persona(session_factory, sim_id: int, name: str) -> int:
    """Create a persona row via the real repo so ``personas.get`` finds it."""
    with session_factory() as session:
        repos = make_repos(session)
        persona = repos.personas.create(sim_id, name=name)
        return persona.id


# ---------------------------------------------------------------------------
# Fake live STRole
# ---------------------------------------------------------------------------


def _make_scratch(**values):
    """A minimal scratch stub whose ``.model_dump()`` returns a plain dict.

    The real ``role.scratch`` is a pydantic-ish object; the function under
    test only calls ``.model_dump()`` on it, so a ``SimpleNamespace`` carrying
    a ``model_dump`` callable is a faithful enough stand-in.
    """
    payload = dict(values)
    return SimpleNamespace(model_dump=lambda: dict(payload))


def _make_role(name: str, nodes: list[BasicMemory], scratch: dict, tree: dict):
    """Build a fake live ``STRole``.

    Shape exposed (everything ``sync_personas_step`` is documented to touch):
      * ``.name``            -> str
      * ``.a_mem.storage``   -> list[BasicMemory]
      * ``.scratch``         -> object with ``.model_dump() -> dict``
      * ``.s_mem.tree``      -> dict
    """
    return SimpleNamespace(
        name=name,
        a_mem=SimpleNamespace(storage=list(nodes)),
        scratch=_make_scratch(**scratch),
        s_mem=SimpleNamespace(tree=dict(tree)),
    )


def _basic_memory(
    *,
    count: int,
    memory_type: str,
    created: datetime,
    description: str,
    keywords: list[str],
    subject: str = "Isabella Rodriguez",
    predicate: str = "is",
    object_: str = "idle",
    poignancy: int = 3,
    depth: int = 0,
    expiration: datetime | None = None,
    filling: list | None = None,
) -> BasicMemory:
    """Construct a real ``BasicMemory`` node valid for ``save_to_dict()``.

    ``Message`` (BasicMemory's base) requires ``content``; BasicMemory's
    ``check_values`` validator mirrors ``content`` into ``description``.
    """
    return BasicMemory(
        content=description,
        memory_id=f"node_{count}",
        memory_count=count,
        type_count=count,
        memory_type=memory_type,
        depth=depth,
        created=created,
        expiration=expiration,
        subject=subject,
        predicate=predicate,
        object=object_,
        description=description,
        embedding_key=description,
        poignancy=poignancy,
        keywords=keywords,
        filling=filling or [],
    )


def _event(count: int, *, keywords: list[str], description: str | None = None) -> BasicMemory:
    return _basic_memory(
        count=count,
        memory_type="event",
        created=datetime(2023, 2, 13, 6, 0, 0),
        description=description or f"event node {count}",
        keywords=keywords,
    )


def _thought(count: int, *, keywords: list[str], description: str | None = None) -> BasicMemory:
    return _basic_memory(
        count=count,
        memory_type="thought",
        created=datetime(2023, 2, 13, 6, 0, 0),
        description=description or f"thought node {count}",
        keywords=keywords,
        depth=1,
    )


def _chat(count: int, *, keywords: list[str], description: str | None = None) -> BasicMemory:
    return _basic_memory(
        count=count,
        memory_type="chat",
        created=datetime(2023, 2, 13, 6, 0, 0),
        description=description or f"chat node {count}",
        keywords=keywords,
        depth=1,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_first_call_inserts_nodes_scratch_and_spatial(session_factory) -> None:
    """First call: N memory nodes + scratch + spatial all land in the DB."""
    sim_id = _make_sim(session_factory)
    persona_id = _make_persona(session_factory, sim_id, "Isabella Rodriguez")

    nodes = [
        _event(1, keywords=["wake"]),
        _event(2, keywords=["coffee"]),
        _thought(3, keywords=["plan"]),
    ]
    scratch = {"curr_tile": [10, 20], "daily_plan_req": "Open the cafe"}
    tree = {"the Ville": {"Hobbs Cafe": {"cafe": ["counter"]}}}
    role = _make_role("Isabella Rodriguez", nodes, scratch, tree)

    sync_personas_step(session_factory, sim_id, [role], START_DT, SEC_PER_STEP)

    with session_factory() as session:
        repos = make_repos(session)
        db_nodes = repos.memory.get_all_nodes(persona_id)
        assert {n.node_id for n in db_nodes} == {"node_1", "node_2", "node_3"}
        assert repos.memory.get_max_node_count(persona_id) == 3
        assert repos.personas.load_scratch(persona_id) == scratch
        assert repos.personas.load_spatial_memory(persona_id) == tree


def test_second_call_only_inserts_new_nodes_and_overwrites_state(session_factory) -> None:
    """Appending 2 nodes -> only 2 new rows (watermark); scratch/spatial overwritten."""
    sim_id = _make_sim(session_factory)
    persona_id = _make_persona(session_factory, sim_id, "Isabella Rodriguez")

    nodes = [_event(1, keywords=["wake"]), _event(2, keywords=["coffee"])]
    role = _make_role(
        "Isabella Rodriguez",
        nodes,
        {"curr_tile": [1, 1]},
        {"the Ville": {"Park": {}}},
    )
    sync_personas_step(session_factory, sim_id, [role], START_DT, SEC_PER_STEP)

    with session_factory() as session:
        repos = make_repos(session)
        assert len(repos.memory.get_all_nodes(persona_id)) == 2

    # Tick 2: append two more nodes + change scratch/spatial in-place.
    role.a_mem.storage.append(_thought(3, keywords=["reflect"]))
    role.a_mem.storage.append(_thought(4, keywords=["reflect"]))
    role.scratch = _make_scratch(curr_tile=[9, 9], daily_plan_req="Close the cafe")
    role.s_mem.tree = {"the Ville": {"Cafe": {"cafe": ["table"]}}}

    sync_personas_step(session_factory, sim_id, [role], START_DT, SEC_PER_STEP)

    with session_factory() as session:
        repos = make_repos(session)
        db_nodes = repos.memory.get_all_nodes(persona_id)
        # Only 2 rows added by the watermark increment -> 4 total, no dupes.
        assert {n.node_id for n in db_nodes} == {
            "node_1",
            "node_2",
            "node_3",
            "node_4",
        }
        assert repos.memory.get_max_node_count(persona_id) == 4
        # scratch + spatial overwritten with the new values.
        assert repos.personas.load_scratch(persona_id) == {
            "curr_tile": [9, 9],
            "daily_plan_req": "Close the cafe",
        }
        assert repos.personas.load_spatial_memory(persona_id) == {
            "the Ville": {"Cafe": {"cafe": ["table"]}}
        }


def test_idempotent_when_role_state_unchanged(session_factory) -> None:
    """Calling twice with identical role state -> no duplicate node rows."""
    sim_id = _make_sim(session_factory)
    persona_id = _make_persona(session_factory, sim_id, "Isabella Rodriguez")

    nodes = [
        _event(1, keywords=["wake"]),
        _thought(2, keywords=["plan"]),
        _chat(3, keywords=["greeting"]),
    ]
    role = _make_role(
        "Isabella Rodriguez",
        nodes,
        {"curr_tile": [5, 5]},
        {"the Ville": {}},
    )

    sync_personas_step(session_factory, sim_id, [role], START_DT, SEC_PER_STEP)
    sync_personas_step(session_factory, sim_id, [role], START_DT, SEC_PER_STEP)

    with session_factory() as session:
        repos = make_repos(session)
        db_nodes = repos.memory.get_all_nodes(persona_id)
        assert len(db_nodes) == 3
        assert {n.node_id for n in db_nodes} == {"node_1", "node_2", "node_3"}


def test_missing_persona_row_is_skipped_other_roles_still_synced(session_factory) -> None:
    """A role with no matching persona row -> no exception; siblings still sync."""
    sim_id = _make_sim(session_factory)
    # Only Isabella has a persona row; "Ghost Persona" deliberately does not.
    isabella_id = _make_persona(session_factory, sim_id, "Isabella Rodriguez")

    ghost = _make_role(
        "Ghost Persona",
        [_event(1, keywords=["void"])],
        {"curr_tile": [0, 0]},
        {"nowhere": {}},
    )
    isabella = _make_role(
        "Isabella Rodriguez",
        [_event(1, keywords=["wake"]), _event(2, keywords=["coffee"])],
        {"curr_tile": [3, 4]},
        {"the Ville": {"Cafe": {}}},
    )

    # Must not raise even though the first role has no persona row.
    sync_personas_step(
        session_factory, sim_id, [ghost, isabella], START_DT, SEC_PER_STEP
    )

    with session_factory() as session:
        repos = make_repos(session)
        # Ghost persona never created -> still absent.
        assert repos.personas.get(sim_id, "Ghost Persona") is None
        # Isabella, in the same call, was synced normally.
        isabella_nodes = repos.memory.get_all_nodes(isabella_id)
        assert {n.node_id for n in isabella_nodes} == {"node_1", "node_2"}
        assert repos.personas.load_scratch(isabella_id) == {"curr_tile": [3, 4]}
        assert repos.personas.load_spatial_memory(isabella_id) == {
            "the Ville": {"Cafe": {}}
        }


def test_keywords_routed_to_correct_tables(session_factory) -> None:
    """New nodes' keywords land in the event / chat / thought keyword tables."""
    sim_id = _make_sim(session_factory)
    persona_id = _make_persona(session_factory, sim_id, "Isabella Rodriguez")

    nodes = [
        _event(1, keywords=["coffee", "morning"]),
        _thought(2, keywords=["plan"]),
        _chat(3, keywords=["greeting"]),
    ]
    role = _make_role(
        "Isabella Rodriguez",
        nodes,
        {"curr_tile": [2, 2]},
        {"the Ville": {}},
    )

    sync_personas_step(session_factory, sim_id, [role], START_DT, SEC_PER_STEP)

    with session_factory() as session:
        repos = make_repos(session)

        event_kw = repos.memory.list_keywords(persona_id, "event")
        assert set(event_kw) == {("coffee", "node_1"), ("morning", "node_1")}

        thought_kw = repos.memory.list_keywords(persona_id, "thought")
        assert set(thought_kw) == {("plan", "node_2")}

        chat_kw = repos.memory.list_keywords(persona_id, "chat")
        assert set(chat_kw) == {("greeting", "node_3")}

        # Cross-table isolation: nothing leaked into the wrong table.
        assert session.query(MemoryKeywordsToEvent).count() == 2
        assert session.query(MemoryKeywordsToThought).count() == 1
        assert session.query(MemoryKeywordsToChat).count() == 1


def test_basic_memory_save_to_dict_constructs_associative_memory_node() -> None:
    """Lock the contract: ``BasicMemory.save_to_dict()`` -> ``AssociativeMemoryNode``.

    ``_node_to_row`` (reused by ``sync_personas_step``) feeds the parsed
    ``AssociativeMemoryNode`` downstream, so the keyed dict that
    ``save_to_dict()`` emits must be directly constructible into one.
    """
    node = _basic_memory(
        count=7,
        memory_type="event",
        created=datetime(2023, 2, 13, 8, 30, 0),
        description="Isabella Rodriguez wakes up",
        keywords=["wake", "morning"],
        subject="Isabella Rodriguez",
        predicate="wake",
        object_="up",
        poignancy=4,
    )

    dumped = node.save_to_dict()
    # save_to_dict() is keyed by node_id; the value is the GA-shaped node dict.
    assert list(dumped.keys()) == ["node_7"]
    node_dict = dumped["node_7"]

    # The keyed dict must construct an AssociativeMemoryNode without adaptation.
    amn = AssociativeMemoryNode(**node_dict)
    assert amn.node_count == 7
    assert amn.type_count == 7
    assert amn.type == "event"
    assert amn.created == "2023-02-13 08:30:00"  # serialized format
    assert amn.subject == "Isabella Rodriguez"
    assert amn.predicate == "wake"
    assert amn.object == "up"
    assert amn.description == "Isabella Rodriguez wakes up"
    assert amn.embedding_key == "Isabella Rodriguez wakes up"
    assert amn.poignancy == 4
    assert set(amn.keywords) == {"wake", "morning"}
    # extra="allow" tolerates GA-incompatible keys like cause_by.
    assert hasattr(amn, "cause_by")
