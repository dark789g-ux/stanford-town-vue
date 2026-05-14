"""Tests for the live StanfordTown runner (runner/st_runner.py + step_sync.py).

LLM policy: **no real API calls**. The full generative-agents step loop under
a mocked LLM is too deep to drive reliably within this milestone's budget
(every Action has its own prompt template + parser). So, per the M3b-2 spec's
documented fallback, this module covers:

  * ``materialize_fork`` — fork copy + meta.json settling (persona subset,
    start time) against a synthesised on-disk fork base placed under a temp
    ``settings.forks_dir``.
  * ``sync_step_to_db`` — environment/movement JSON -> SQLite, with real
    fixture JSON written in the live-runner layout.
  * ``stanford_town_runner`` — the step loop's pause/stop cooperation and
    per-step DB sync + EventBus emission, with ``build_town`` monkeypatched to
    a fake town whose ``env.run`` is a no-op that writes step files. The heavy
    simulator + LLM are never imported on this path.

What is NOT covered here: the simulator's internal per-step behaviour
(perceive/plan/reflect/execute) and real LLM-driven Action parsing — that needs
an end-to-end mocked-LLM harness, deferred to M3b-3.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Make backend/ importable when pytest is run from the repo root.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from config.settings import get_settings  # noqa: E402
from runner.events import EventBus  # noqa: E402
from runner.manager import RunContext  # noqa: E402
from runner.step_sync import sync_step_to_db  # noqa: E402
from runner import st_runner  # noqa: E402
from storage.db import Base  # noqa: E402
from storage.models import SimulationStatus  # noqa: E402
from storage.repos import make_repos  # noqa: E402

# --------------------------------------------------------------------- fixtures


@pytest.fixture()
def fork_base(tmp_path, monkeypatch):
    """Synthesise an on-disk fork base under a temp ``settings.forks_dir``.

    Mirrors the live-runner layout (``reverie/meta.json`` + ``environment/``)
    so ``materialize_fork`` can copy + settle it without any bundled demo data.
    """
    forks_dir = tmp_path / "_forks"
    base = forks_dir / "storage" / "base_the_ville_isabella_maria_klaus"
    (base / "reverie").mkdir(parents=True)
    (base / "reverie" / "meta.json").write_text(
        json.dumps(
            {
                "fork_sim_code": "base_the_ville_isabella_maria_klaus",
                "start_date": "February 13, 2023",
                "curr_time": "February 13, 2023, 00:00:00",
                "sec_per_step": 10,
                "maze_name": "the_ville",
                "persona_names": [
                    "Isabella Rodriguez",
                    "Maria Lopez",
                    "Klaus Mueller",
                ],
                "step": 0,
            }
        ),
        encoding="utf-8",
    )
    (base / "environment").mkdir()
    (base / "environment" / "0.json").write_text(
        json.dumps(
            {
                "Isabella Rodriguez": {"maze": "the_ville", "x": 72, "y": 14},
                "Maria Lopez": {"maze": "the_ville", "x": 60, "y": 30},
                "Klaus Mueller": {"maze": "the_ville", "x": 126, "y": 46},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STT_FORKS_DIR", str(forks_dir))
    get_settings.cache_clear()
    yield base
    get_settings.cache_clear()


@pytest.fixture()
def session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


def _seed_sim(
    session_factory: sessionmaker,
    sim_code: str = "run_test",
    fork_sim_code: str | None = "base_the_ville_isabella_maria_klaus",
    n_round: int = 3,
) -> int:
    with session_factory() as s:
        repos = make_repos(s)
        sim = repos.simulations.create(
            sim_code,
            fork_sim_code=fork_sim_code,
            status=SimulationStatus.RUNNING,
            start_time_iso="2023-02-13T07:00:00",
            curr_time_iso="2023-02-13T07:00:00",
            sec_per_step=10,
            step=0,
            maze_name="the_ville",
            n_round=n_round,
            investment=0.0,
        )
        return sim.id


def _make_ctx(
    session_factory: sessionmaker,
    sim_id: int,
    sim_code: str = "run_test",
    n_round: int = 3,
) -> tuple[RunContext, EventBus, asyncio.Event, asyncio.Event]:
    pause_event = asyncio.Event()
    pause_event.set()
    stop_event = asyncio.Event()
    bus = EventBus()
    ctx = RunContext(
        sim_id=sim_id,
        sim_code=sim_code,
        n_round=n_round,
        session_factory=session_factory,
        event_bus=bus,
        pause_event=pause_event,
        stop_event=stop_event,
    )
    return ctx, bus, pause_event, stop_event


# ----------------------------------------------------------- materialize_fork


def test_materialize_fork_copies_on_disk_base(tmp_path, fork_base):
    work_dir = st_runner.materialize_fork(
        "sim_copy", "base_the_ville_isabella_maria_klaus", tmp_path
    )
    assert work_dir == tmp_path / "sim_copy"
    meta = json.loads((work_dir / "reverie" / "meta.json").read_text(encoding="utf-8"))
    # Default copy keeps the fork's three personas.
    assert set(meta["persona_names"]) == {
        "Isabella Rodriguez",
        "Maria Lopez",
        "Klaus Mueller",
    }
    # The fork ships an environment/0.json bootstrap file.
    assert (work_dir / "environment" / "0.json").is_file()


def test_materialize_fork_applies_persona_subset_and_start_time(tmp_path, fork_base):
    work_dir = st_runner.materialize_fork(
        "sim_subset",
        "base_the_ville_isabella_maria_klaus",
        tmp_path,
        personas=["Isabella Rodriguez", "Klaus Mueller"],
        start_time_iso="2023-02-13T09:30:00",
        sec_per_step=20,
    )
    meta = json.loads((work_dir / "reverie" / "meta.json").read_text(encoding="utf-8"))
    assert meta["persona_names"] == ["Isabella Rodriguez", "Klaus Mueller"]
    assert meta["start_time"] == "February 13, 2023, 09:30:00"
    assert meta["curr_time"] == "February 13, 2023, 09:30:00"
    assert meta["sec_per_step"] == 20


def test_materialize_fork_unknown_persona_raises(tmp_path, fork_base):
    with pytest.raises(st_runner.RunnerError):
        st_runner.materialize_fork(
            "sim_bad",
            "base_the_ville_isabella_maria_klaus",
            tmp_path,
            personas=["Nobody Here"],
        )


def test_materialize_fork_missing_fork_raises(tmp_path):
    with pytest.raises(st_runner.RunnerError):
        st_runner.materialize_fork("sim_x", "does_not_exist_anywhere", tmp_path)


def test_materialize_fork_no_fork_code_raises(tmp_path):
    with pytest.raises(st_runner.RunnerError):
        st_runner.materialize_fork("sim_x", None, tmp_path)


def test_materialize_fork_copies_storage_local_fork(tmp_path):
    """A fork that already lives under storage_root is copied into place."""
    # Build a minimal fork dir under the storage root.
    local = tmp_path / "my_local_fork"
    (local / "reverie").mkdir(parents=True)
    (local / "reverie" / "meta.json").write_text(
        json.dumps(
            {
                "fork_sim_code": "my_local_fork",
                "start_date": "February 13, 2023",
                "curr_time": "February 13, 2023, 00:00:00",
                "sec_per_step": 10,
                "maze_name": "the_ville",
                "persona_names": ["Isabella Rodriguez"],
                "step": 0,
            }
        ),
        encoding="utf-8",
    )
    work_dir = st_runner.materialize_fork("sim_local", "my_local_fork", tmp_path)
    assert work_dir == tmp_path / "sim_local"
    meta = json.loads((work_dir / "reverie" / "meta.json").read_text(encoding="utf-8"))
    assert meta["persona_names"] == ["Isabella Rodriguez"]


# -------------------------------------------------------------- sync_step_to_db


def _write_step_files(storage_dir: Path, step: int) -> None:
    """Write a realistic live-runner environment/movement pair for ``step``."""
    env_dir = storage_dir / "environment"
    move_dir = storage_dir / "movement"
    env_dir.mkdir(parents=True, exist_ok=True)
    move_dir.mkdir(parents=True, exist_ok=True)

    env_payload = {
        "Isabella Rodriguez": {"maze": "the_ville", "x": 72 + step, "y": 14},
        "Klaus Mueller": {"maze": "the_ville", "x": 126, "y": 46 + step},
    }
    (env_dir / f"{step}.json").write_text(json.dumps(env_payload), encoding="utf-8")

    move_payload = {
        "persona": {
            "Isabella Rodriguez": {
                "movement": [72 + step, 14],
                "pronunciatio": "🛏️",
                "description": f"sleeping @ the Ville:Isabella's house:bedroom:bed",
                "chat": None,
            },
            "Klaus Mueller": {
                "movement": [126, 46 + step],
                "pronunciatio": "💬",
                "description": "talking @ the Ville:cafe",
                "chat": [["Klaus Mueller", "hello"]],
            },
        },
        "meta": {"curr_time": "February 13, 2023, 07:00:10"},
    }
    (move_dir / f"{step}.json").write_text(json.dumps(move_payload), encoding="utf-8")


def test_sync_step_to_db_writes_env_and_movements(session_factory, tmp_path):
    sim_id = _seed_sim(session_factory)
    _write_step_files(tmp_path, step=0)

    rows = sync_step_to_db(session_factory, sim_id, 0, tmp_path)

    # Returned rows mirror what was written.
    assert len(rows) == 2
    names = {r["persona_name"] for r in rows}
    assert names == {"Isabella Rodriguez", "Klaus Mueller"}

    with session_factory() as s:
        repos = make_repos(s)
        env = repos.steps.get_environment(sim_id, 0)
        assert env is not None
        assert env["Isabella Rodriguez"]["x"] == 72
        movements = repos.steps.get_movements(sim_id, 0)
        assert {m.persona_name for m in movements} == {
            "Isabella Rodriguez",
            "Klaus Mueller",
        }
        klaus = next(m for m in movements if m.persona_name == "Klaus Mueller")
        assert klaus.chat_json == [["Klaus Mueller", "hello"]]
        # description "@ <path>" is split into location_path by the importer helper.
        assert klaus.location_path == "the Ville:cafe"


def test_sync_step_to_db_missing_files_is_noop(session_factory, tmp_path):
    sim_id = _seed_sim(session_factory)
    rows = sync_step_to_db(session_factory, sim_id, 99, tmp_path)
    assert rows == []
    with session_factory() as s:
        repos = make_repos(s)
        assert repos.steps.get_environment(sim_id, 99) is None
        assert repos.steps.get_movements(sim_id, 99) == []


def test_sync_step_to_db_idempotent(session_factory, tmp_path):
    sim_id = _seed_sim(session_factory)
    _write_step_files(tmp_path, step=2)
    sync_step_to_db(session_factory, sim_id, 2, tmp_path)
    sync_step_to_db(session_factory, sim_id, 2, tmp_path)  # second run: no dupes
    with session_factory() as s:
        repos = make_repos(s)
        movements = repos.steps.get_movements(sim_id, 2)
        assert len(movements) == 2


# ------------------------------------------------------- stanford_town_runner


class _FakeEnv:
    """Stand-in for ``town.env`` — its run() writes the step files a real tick
    would have produced, so the runner's sync path exercises real JSON I/O."""

    def __init__(self, storage_dir: Path):
        self._storage_dir = storage_dir
        self._tick = 0
        self.run_calls = 0

    async def run(self):
        self.run_calls += 1
        _write_step_files(self._storage_dir, self._tick)
        self._tick += 1


class _FakeTown:
    def __init__(self, storage_dir: Path):
        self.env = _FakeEnv(storage_dir)
        self.hired = False
        self.invested = None
        self.project_idea = None

    async def hire(self, roles):
        self.hired = True

    def invest(self, amount):
        self.invested = amount

    def run_project(self, idea, send_to: str = ""):
        self.project_idea = idea


def _patch_town(monkeypatch, work_dir: Path):
    """Patch materialize_fork + build_town so the runner never touches the
    heavy simulator or any LLM. materialize_fork just makes the work dir;
    build_town returns a FakeTown writing into it."""

    def fake_materialize(sim_code, fork_sim_code, storage_root, **kwargs):
        d = Path(storage_root) / sim_code
        (d / "environment").mkdir(parents=True, exist_ok=True)
        (d / "movement").mkdir(parents=True, exist_ok=True)
        (d / "reverie").mkdir(parents=True, exist_ok=True)
        (d / "reverie" / "meta.json").write_text(
            json.dumps(
                {
                    "fork_sim_code": fork_sim_code,
                    "start_date": "February 13, 2023",
                    "start_time": "February 13, 2023, 07:00:00",
                    "curr_time": "February 13, 2023, 07:00:00",
                    "sec_per_step": 10,
                    "maze_name": "the_ville",
                    "persona_names": ["Isabella Rodriguez", "Klaus Mueller"],
                    "step": 0,
                }
            ),
            encoding="utf-8",
        )
        return d

    town = _FakeTown(work_dir)

    def fake_build_town(sim_code, wd, **kwargs):
        # Re-point the fake env at the actual work dir the runner passes.
        town.env._storage_dir = Path(wd)
        return town, []

    def fake_persist(ctx, sim_code, wd, roles):
        return None  # final-state persist needs real roles; skip in this seam.

    monkeypatch.setattr(st_runner, "materialize_fork", fake_materialize)
    monkeypatch.setattr(st_runner, "build_town", fake_build_town)
    monkeypatch.setattr(st_runner, "_persist_final_state", fake_persist)
    return town


async def test_runner_drives_loop_and_syncs_each_step(
    session_factory, tmp_path, monkeypatch
):
    sim_id = _seed_sim(session_factory, n_round=3)
    work_dir = tmp_path / "work"
    town = _patch_town(monkeypatch, work_dir)
    # Point STORAGE_PATH at tmp so the runner's work dir lands under tmp_path.
    monkeypatch.setattr(
        "simulator.utils.const.STORAGE_PATH", tmp_path, raising=False
    )

    ctx, bus, _, _ = _make_ctx(session_factory, sim_id, n_round=3)
    sub = bus.subscribe(sim_id)

    await st_runner.stanford_town_runner(ctx)

    # env.run called once per round.
    assert town.env.run_calls == 3
    assert town.hired is True

    # Each step synced into the DB.
    with session_factory() as s:
        repos = make_repos(s)
        assert repos.steps.get_max_step(sim_id) == 2
        for step in range(3):
            assert repos.steps.get_environment(sim_id, step) is not None
            assert len(repos.steps.get_movements(sim_id, step)) == 2
        sim = repos.simulations.get_by_id(sim_id)
        # advance_step ran for the final step.
        assert sim.step == 2
        assert sim.curr_time_iso == "2023-02-13T07:00:30"

    # EventBus saw a status + one step event per round.
    received = []
    while not sub.queue.empty():
        received.append(await sub.__anext__())
    await sub.close()
    step_events = [e for e in received if e.event_type == "step"]
    assert len(step_events) == 3
    assert {e.payload["step"] for e in step_events} == {0, 1, 2}
    assert any(e.event_type == "status" for e in received)


async def test_runner_honours_stop_between_steps(
    session_factory, tmp_path, monkeypatch
):
    sim_id = _seed_sim(session_factory, n_round=10)
    work_dir = tmp_path / "work"
    town = _patch_town(monkeypatch, work_dir)
    monkeypatch.setattr(
        "simulator.utils.const.STORAGE_PATH", tmp_path, raising=False
    )

    ctx, bus, _, stop_event = _make_ctx(session_factory, sim_id, n_round=10)
    # Stop immediately — the loop should break before any env.run call.
    stop_event.set()

    await st_runner.stanford_town_runner(ctx)
    assert town.env.run_calls == 0

    with session_factory() as s:
        repos = make_repos(s)
        assert repos.steps.get_max_step(sim_id) == -1


async def test_runner_honours_pause_then_resume(
    session_factory, tmp_path, monkeypatch
):
    sim_id = _seed_sim(session_factory, n_round=4)
    work_dir = tmp_path / "work"
    town = _patch_town(monkeypatch, work_dir)
    monkeypatch.setattr(
        "simulator.utils.const.STORAGE_PATH", tmp_path, raising=False
    )

    ctx, bus, pause_event, _ = _make_ctx(session_factory, sim_id, n_round=4)
    # Start paused: the loop should block on pause_event.wait().
    pause_event.clear()

    task = asyncio.create_task(st_runner.stanford_town_runner(ctx))
    await asyncio.sleep(0.05)
    # While paused, no ticks have run.
    assert town.env.run_calls == 0
    assert not task.done()

    # Resume — the loop should complete all 4 rounds.
    pause_event.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert town.env.run_calls == 4


async def test_runner_missing_sim_raises(session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "simulator.utils.const.STORAGE_PATH", tmp_path, raising=False
    )
    ctx, _, _, _ = _make_ctx(session_factory, sim_id=99999, n_round=1)
    with pytest.raises(st_runner.RunnerError):
        await st_runner.stanford_town_runner(ctx)
