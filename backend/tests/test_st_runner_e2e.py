"""Integration test for StanfordTown construction under a mocked LLM.

This exercises the *real* vendored simulator up to the point of seating
the agents on the maze: ``materialize_fork`` stages the 3-persona
``base_the_ville_isabella_maria_klaus`` fork, ``build_town`` constructs a
``StanfordTown`` + three ``STRole``s, and ``town.hire`` runs the maze
pathfinding that places each persona at its starting tile.

The real simulator needs a *full* fork (persona bootstrap memory + maze
data) — a synthesised stub can't drive ``build_town`` / ``hire``. The fork
is therefore looked up under ``settings.forks_dir`` (default
``~/.stanford-town-vue/forks``); drop the original fork there to run these
tests, otherwise they skip.

**No real LLM calls** — ``LLM_REGISTRY.get_provider`` is monkeypatched to
a ``FakeLLM`` so no provider construction touches the network.

### Why this stops at ``hire`` and not a full ``env.run()`` tick

A full tick was tried and *does* work — it surfaced (and we fixed) a real
runner bug: ``stanford_town_runner`` was missing the ``town.run_project()``
kickoff, so every role stayed idle and ``env.run()`` was a silent no-op.
But one mocked tick over the 3-persona base fork takes ~4 minutes (maze
load + retrieval over each persona's full bootstrap memory + the
plan/reflect/execute cascade), which is far too slow for the normal test
suite. The full-tick smoke therefore lives behind ``-m slow`` below and
is skipped by default; run it manually with ``pytest -m slow``.

What no automated test here covers: that the agents behave *intelligently*
— that needs a real LLM and is a manual demo step.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from config.settings import get_settings  # noqa: E402
from runner import st_runner  # noqa: E402
from runner.step_sync import sync_step_to_db  # noqa: E402
from storage.db import Base  # noqa: E402
from storage.models import SimulationStatus, StepMovement  # noqa: E402
from storage.repos import make_repos  # noqa: E402

# Resolved from the configured forks dir — the same place ``materialize_fork``
# looks. Not bundled in this repo, so these tests skip unless the user drops
# the original fork under ``settings.forks_dir/storage/``.
_FORK_BASE_NAME = "base_the_ville_isabella_maria_klaus"
_FORK_BASE = get_settings().expanded_forks_dir() / "storage" / _FORK_BASE_NAME


class FakeLLM:
    """Minimal duck-typed stand-in for a vendored ``BaseLLM`` provider.

    Returns canned text from ``aask`` / ``acompletion`` with no network I/O.
    Action parsers may reject it — that's fine, ``STRole._react`` absorbs
    per-persona failures and still emits a movement frame.
    """

    def __init__(self, *args, **kwargs) -> None:
        self.cost_manager = None
        self.system_prompt = "You are a helpful assistant."
        self.use_system_prompt = True
        self.model = "fake-model"

    async def aask(self, prompt, system_msgs=None, *args, **kwargs) -> str:  # noqa: ANN001
        return "ok"

    async def acompletion(self, messages, *args, **kwargs):  # noqa: ANN001
        return {"choices": [{"message": {"content": "ok"}}]}

    async def acompletion_text(self, messages, *args, **kwargs) -> str:  # noqa: ANN001
        return "ok"

    def get_costs(self):
        return None


@pytest.fixture()
def session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def patched_llm(monkeypatch):
    """Force every ``create_llm_instance`` call to return a ``FakeLLM``."""
    from core.llm import llm_provider_registry as reg

    monkeypatch.setattr(
        reg.LLM_REGISTRY, "get_provider", lambda _api_type: FakeLLM
    )
    return reg


@pytest.mark.skipif(
    not _FORK_BASE.is_dir(),
    reason=f"bundled fork {_FORK_BASE_NAME} not present in this checkout",
)
def test_materialize_and_build_town(session_factory, patched_llm):
    """materialize_fork -> build_town -> hire seats 3 personas on the maze."""
    from simulator.utils.const import STORAGE_PATH

    sim_code = f"e2e_test_{uuid.uuid4().hex[:8]}"
    storage_root = Path(STORAGE_PATH)
    work_dir = storage_root / sim_code

    try:
        materialized = st_runner.materialize_fork(
            sim_code,
            _FORK_BASE_NAME,
            storage_root,
            session_factory=session_factory,
        )
        assert materialized == work_dir
        assert (work_dir / "reverie" / "meta.json").is_file()

        town, roles = st_runner.build_town(sim_code, work_dir)
        assert len(roles) == 3
        assert {r.name for r in roles} == {
            "Isabella Rodriguez",
            "Maria Lopez",
            "Klaus Mueller",
        }

        # hire() runs init_curr_tile for each role — maze pathfinding, no LLM.
        asyncio.run(town.hire(roles))
        for role in roles:
            # Every role should have been seated at a concrete (x, y) tile.
            assert role.scratch.curr_tile is not None
    finally:
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)


@pytest.mark.slow
@pytest.mark.skipif(
    not _FORK_BASE.is_dir(),
    reason=f"bundled fork {_FORK_BASE_NAME} not present in this checkout",
)
def test_one_tick_end_to_end(session_factory, patched_llm):
    """Full mocked-LLM tick: build -> hire -> run_project -> one env.run().

    Slow (~3 min) — marked ``slow`` so it's excluded from the default suite;
    run with ``pytest -m slow``. Verifies the integration plumbing end to
    end: materialize_fork -> build_town -> hire -> run_project -> one
    resilient env.run() tick -> all 3 personas write step-0
    movement/environment files -> ``sync_step_to_db`` lands 3 rows in SQLite.

    Getting this green required several robustness fixes (M3b-4):
      * the runner was missing ``town.run_project()`` (roles stayed idle);
      * ``Environment.run`` now gathers with ``return_exceptions=True`` so one
        role can't abort the whole tick;
      * ``add_inner_voice`` is isolated (it runs before ``_react``'s own
        try/except) and ``run_event_triple`` / ``GenPronunciatio`` no longer
        crash on a degraded (non-str / None) LLM response;
      * ``PersonaMovement`` coerces odd field types instead of rejecting.

    The agents don't behave *intelligently* under FakeLLM — that still needs
    a real LLM and is a manual demo step — but the pipeline is proven sound.
    """
    from simulator.utils.const import STORAGE_PATH

    sim_code = f"e2e_slow_{uuid.uuid4().hex[:8]}"
    storage_root = Path(STORAGE_PATH)
    work_dir = storage_root / sim_code

    try:
        st_runner.materialize_fork(
            sim_code, _FORK_BASE_NAME, storage_root, session_factory=session_factory
        )
        town, roles = st_runner.build_town(sim_code, work_dir)

        async def _drive() -> None:
            await town.hire(roles)
            town.invest(30.0)
            town.run_project("")
            await town.env.run()

        asyncio.run(_drive())

        assert (work_dir / "movement" / "0.json").is_file()
        assert (work_dir / "environment" / "0.json").is_file()

        with session_factory() as s:
            sim = make_repos(s).simulations.create(
                sim_code,
                status=SimulationStatus.RUNNING,
                n_round=1,
                start_time_iso="2023-02-13T00:00:00",
                curr_time_iso="2023-02-13T00:00:00",
            )
            sim_id = sim.id

        rows = sync_step_to_db(session_factory, sim_id, 0, work_dir)
        assert len(rows) == 3

        with session_factory() as s:
            count = s.execute(
                select(func.count(StepMovement.id)).where(
                    StepMovement.sim_id == sim_id, StepMovement.step == 0
                )
            ).scalar_one()
        assert count == 3
    finally:
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
