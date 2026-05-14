"""Integration tests for /api/sims/import* and /api/sims/{id}/export.

The original Stanford Town demo data is not bundled in this repository, so the
fork used here is synthesised in a temp dir (compressed on-disk layout). This
keeps the suite self-contained — no dependency on a parent MetaGPT checkout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

# Make backend/ importable when pytest runs from the repo root.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.deps import get_db  # noqa: E402
from app.main import app  # noqa: E402
from config.settings import get_settings  # noqa: E402
from storage.db import Base  # noqa: E402

_FORK_CODE = "the_ville_iso_maria_klaus"
_FORK_PERSONAS = ["Isabella Rodriguez", "Maria Lopez", "Klaus Mueller"]


# --------------------------------------------------------------- fork fixture


def _write_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_fork(parent: Path, sim_code: str = _FORK_CODE) -> Path:
    """Synthesise an importable compressed-format fork under ``parent/<sim_code>/``."""
    sim_dir = parent / sim_code

    _write_json(
        sim_dir / "meta.json",
        {
            "fork_sim_code": None,
            "start_date": "February 13, 2023",
            "curr_time": "February 13, 2023, 00:00:30",
            "sec_per_step": 10,
            "maze_name": "the_ville",
            "persona_names": _FORK_PERSONAS,
            "step": 0,
        },
    )

    master: dict[str, dict] = {}
    for step in range(2):
        master[str(step)] = {
            name: {
                "movement": [10 + step, 20 + i],
                "pronunciatio": "😴",
                "description": f"sleeping @ the Ville:{name}'s apartment:main room:bed",
                "chat": None,
            }
            for i, name in enumerate(_FORK_PERSONAS)
        }
    _write_json(sim_dir / "master_movement.json", master)

    for name in _FORK_PERSONAS:
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

    return sim_dir


@pytest.fixture()
def bundled_fork(tmp_path: Path) -> Path:
    """A synthesised importable fork directory."""
    return _build_fork(tmp_path / "forks")


@pytest.fixture()
def forks_dir(tmp_path: Path, monkeypatch) -> Iterator[Path]:
    """Point ``settings.forks_dir`` at a temp dir with empty storage subdirs."""
    d = tmp_path / "forks_root"
    (d / "storage").mkdir(parents=True)
    (d / "compressed_storage").mkdir(parents=True)
    monkeypatch.setenv("STT_FORKS_DIR", str(d))
    get_settings.cache_clear()
    yield d
    get_settings.cache_clear()


@pytest.fixture()
def session_factory():
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    yield SessionLocal
    engine.dispose()


@pytest.fixture()
def client(session_factory) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


# ----------------------------------------------------------------- import


def test_import_bundled_fork_succeeds(client, bundled_fork: Path):
    resp = client.post(
        "/api/sims/import",
        json={"source_path": str(bundled_fork), "on_conflict": "fail"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sim_id"] >= 1
    assert body["sim_code"] == _FORK_CODE
    assert body["counts"]["personas"] == len(_FORK_PERSONAS)


def test_import_duplicate_fails(client, bundled_fork: Path):
    payload = {"source_path": str(bundled_fork), "on_conflict": "fail"}
    r1 = client.post("/api/sims/import", json=payload)
    assert r1.status_code == 200, r1.text
    r2 = client.post("/api/sims/import", json=payload)
    assert r2.status_code == 409


def test_import_replace_succeeds(client, bundled_fork: Path):
    payload = {"source_path": str(bundled_fork), "on_conflict": "fail"}
    r1 = client.post("/api/sims/import", json=payload)
    assert r1.status_code == 200, r1.text
    payload2 = {"source_path": str(bundled_fork), "on_conflict": "replace"}
    r2 = client.post("/api/sims/import", json=payload2)
    assert r2.status_code == 200, r2.text


def test_import_missing_path_returns_400(client):
    resp = client.post(
        "/api/sims/import",
        json={
            "source_path": "/nonexistent/place/that/does/not/exist",
            "on_conflict": "fail",
        },
    )
    assert resp.status_code == 400


# ----------------------------------------------------------------- export


def test_export_round_trip(client, bundled_fork: Path, tmp_path):
    imp = client.post(
        "/api/sims/import",
        json={"source_path": str(bundled_fork), "on_conflict": "fail"},
    )
    assert imp.status_code == 200, imp.text
    sim_id = imp.json()["sim_id"]

    target = tmp_path / "exports"
    target.mkdir()
    resp = client.post(
        f"/api/sims/{sim_id}/export",
        json={"target_dir": str(target), "layout": "compressed"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    out_path = Path(body["output_path"])
    assert out_path.is_dir()
    assert (out_path / "meta.json").is_file()


def test_export_unknown_sim_returns_404(client, tmp_path):
    resp = client.post(
        "/api/sims/9999/export",
        json={"target_dir": str(tmp_path), "layout": "compressed"},
    )
    assert resp.status_code == 404


# ------------------------------------------------------------------ forks


def test_list_forks_finds_storage_fork(client, forks_dir: Path):
    """A fork dropped under ``settings.forks_dir/storage`` surfaces in the list."""
    _build_fork(forks_dir / "storage")

    resp = client.get("/api/sims/import/forks")
    assert resp.status_code == 200, resp.text
    items = resp.json()

    sources = {item["source"] for item in items}
    assert "storage" in sources
    bundled = [
        i
        for i in items
        if i["sim_code"] == _FORK_CODE and i["source"] == "storage"
    ]
    assert bundled, f"expected {_FORK_CODE} in forks list"
    assert len(bundled[0]["persona_names"]) == len(_FORK_PERSONAS)


def test_list_forks_empty_is_clean(client, forks_dir: Path):
    """With no forks on disk the endpoint still responds with a list."""
    resp = client.get("/api/sims/import/forks")
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)
