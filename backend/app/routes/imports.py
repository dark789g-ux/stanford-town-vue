"""Import / export endpoints for forked simulations."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator, Literal

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db
from config.settings import get_settings
from storage.exporter import SimulationNotFound, export_simulation
from storage.importer import ImportError_, SimulationExists, import_simulation
from storage.json_schemas import parse_reverie_meta
from storage.models import SimulationStatus
from storage.repos import make_repos

router = APIRouter(prefix="/api/sims", tags=["imports"])

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ImportRequest(BaseModel):
    source_path: str
    sim_code_override: str | None = None
    on_conflict: Literal["fail", "replace", "skip"] = "fail"


class ImportResponse(BaseModel):
    sim_id: int
    sim_code: str
    counts: dict[str, int]


class ExportRequest(BaseModel):
    target_dir: str
    layout: Literal["compressed", "live"] = "compressed"


class ExportResponse(BaseModel):
    sim_id: int
    sim_code: str
    output_path: str


class ForkInfo(BaseModel):
    sim_code: str
    source: Literal["compressed_storage", "storage", "db"]
    path: str
    persona_names: list[str] = Field(default_factory=list)
    step_count: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_root() -> Path:
    """Return the project root used to resolve relative input paths.

    This file is at ``<project>/backend/app/routes/imports.py``; walking up
    three parents lands on ``backend/`` and one more on the project root.
    User-supplied relative import/export paths anchor here.
    """
    backend_dir = Path(__file__).resolve().parent.parent.parent
    return backend_dir.parent


def _resolve_path(p: str) -> Path:
    """Resolve a user-supplied path; relative paths anchor at the project root."""
    path = Path(p)
    if not path.is_absolute():
        path = (_project_root() / p).resolve()
    else:
        path = path.resolve()
    return path


def _count_imports(repos, sim_id: int) -> dict[str, int]:
    """Aggregate per-section row counts for the imported sim."""
    personas = repos.personas.list_by_sim(sim_id)
    memory_nodes = 0
    for p in personas:
        memory_nodes += len(repos.memory.get_all_nodes(p.id))
    max_step = repos.steps.get_max_step(sim_id)
    # rough proxy: number of (persona,step) movement rows
    if max_step < 0:
        step_movements = 0
    else:
        step_movements = len(
            repos.steps.list_movements_range(sim_id, 0, max_step)
        )
    llm_calls = repos.llm_logs.count(sim_id)
    return {
        "personas": len(personas),
        "memory_nodes": memory_nodes,
        "step_movements": step_movements,
        "llm_calls": llm_calls,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/import", response_model=ImportResponse)
def import_sim(
    payload: ImportRequest = Body(...),
    db: Session = Depends(get_db),
) -> ImportResponse:
    src = _resolve_path(payload.source_path)
    if not src.exists() or not src.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Source directory does not exist: {src}",
        )

    try:
        sim_id = import_simulation(
            src,
            db,
            sim_code_override=payload.sim_code_override,
            on_conflict=payload.on_conflict,
        )
    except SimulationExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("import_simulation failed")
        raise HTTPException(status_code=500, detail=f"Import failed: {exc}") from exc

    repos = make_repos(db)
    sim = repos.simulations.get_by_id(sim_id)
    counts = _count_imports(repos, sim_id)
    return ImportResponse(sim_id=sim_id, sim_code=sim.sim_code, counts=counts)


@router.post("/{sim_id}/export", response_model=ExportResponse)
def export_sim(
    sim_id: int,
    payload: ExportRequest = Body(...),
    db: Session = Depends(get_db),
) -> ExportResponse:
    target = _resolve_path(payload.target_dir)
    target.mkdir(parents=True, exist_ok=True)

    try:
        out_path = export_simulation(
            sim_id, target, db, layout=payload.layout
        )
    except SimulationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("export_simulation failed")
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc

    repos = make_repos(db)
    sim = repos.simulations.get_by_id(sim_id)
    return ExportResponse(
        sim_id=sim_id,
        sim_code=sim.sim_code if sim is not None else out_path.name,
        output_path=str(out_path),
    )


def _scan_fork_dir(
    base: Path,
    source: Literal["compressed_storage", "storage"],
) -> Iterator[ForkInfo]:
    """Yield ForkInfo for each direct child sim directory under ``base``."""
    if not base.is_dir():
        return
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        # Prefer reverie/meta.json then meta.json.
        meta_path = child / "reverie" / "meta.json"
        if not meta_path.is_file():
            meta_path = child / "meta.json"
        persona_names: list[str] = []
        if meta_path.is_file():
            try:
                meta = parse_reverie_meta(meta_path.read_text(encoding="utf-8"))
                persona_names = list(meta.persona_names)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to parse meta.json for fork %s: %s", child, exc
                )
                continue
        else:
            # Not a fork directory.
            continue
        yield ForkInfo(
            sim_code=child.name,
            source=source,
            path=str(child),
            persona_names=persona_names,
            step_count=0,
        )


def fork_persona_names(db: Session, fork_sim_code: str) -> list[str] | None:
    """Resolve a fork's persona names, or ``None`` when the fork is unknown.

    Mirrors :func:`list_forks` resolution order — on-disk fork bases first,
    then completed DB simulations — so callers can validate a creation request
    against exactly what the UI offers. ``None`` means "could not resolve";
    callers should treat that as "skip validation", not "reject", since
    :func:`runner.st_runner.materialize_fork` also accepts plain directories
    under the storage root that this scan does not see.
    """
    forks_dir = get_settings().expanded_forks_dir()
    for source in ("compressed_storage", "storage"):
        for fork in _scan_fork_dir(forks_dir / source, source):  # type: ignore[arg-type]
            if fork.sim_code == fork_sim_code:
                return list(fork.persona_names)

    try:
        repos = make_repos(db)
        sim = repos.simulations.get_by_code(fork_sim_code)
        if sim is not None and sim.status == SimulationStatus.COMPLETED:
            return [p.name for p in repos.personas.list_by_sim(sim.id)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("fork_persona_names: DB lookup failed: %s", exc)
    return None


@router.get("/import/forks", response_model=list[ForkInfo])
def list_forks(db: Session = Depends(get_db)) -> list[ForkInfo]:
    items: list[ForkInfo] = []

    # Original on-disk demos live under ``settings.forks_dir`` (default
    # ``~/.stanford-town-vue/forks``), in ``storage/`` / ``compressed_storage/``
    # subdirs. Drop fork directories there to have them surface here.
    forks_dir = get_settings().expanded_forks_dir()
    items.extend(
        _scan_fork_dir(forks_dir / "compressed_storage", "compressed_storage")
    )
    items.extend(_scan_fork_dir(forks_dir / "storage", "storage"))

    # Also include completed sims from DB.
    try:
        repos = make_repos(db)
        completed = repos.simulations.list(status=SimulationStatus.COMPLETED)
        for sim in completed:
            persona_rows = repos.personas.list_by_sim(sim.id)
            items.append(
                ForkInfo(
                    sim_code=sim.sim_code,
                    source="db",
                    path=f"db://{sim.sim_code}",
                    persona_names=[p.name for p in persona_rows],
                    step_count=sim.step or 0,
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to scan DB forks: %s", exc)

    return items
