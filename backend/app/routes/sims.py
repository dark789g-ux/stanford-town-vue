"""Simulation lifecycle + state endpoints.

M3a: real handlers backed by the storage Repos layer.
M3b-2: pause/resume/stop are wired to :class:`SimulationManager`. When the sim
is actively running in this process the manager drives the cooperative
pause/stop flags (and writes the DB status itself); otherwise the endpoints
fall back to a direct status flip so idle/imported rows still respond. ``POST``
optionally spawns a run when ``start=True``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from app.deps import get_repos
from app.routes.imports import fork_persona_names
from runner.manager import manager_singleton
from storage.models import (
    MemoryNodeType,
    SimulationConfigSnapshot,
    SimulationStatus,
)
from storage.repos import Repos

router = APIRouter(prefix="/api/sims", tags=["simulations"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SimulationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sim_code: str
    fork_sim_code: str | None
    status: str
    start_time_iso: str
    curr_time_iso: str
    sec_per_step: int
    step: int
    maze_name: str
    n_round: int
    investment: float
    error_message: str | None
    deleted: bool
    created_at: datetime


class SimulationCreateIn(BaseModel):
    sim_code: str
    fork_sim_code: str | None = None
    personas: list[str] = Field(default_factory=list)
    inner_voice: str | None = None
    idea: str | None = None
    n_round: int = 200
    start_hms: str = "07:00:00"
    sec_per_step: int = 10
    maze_name: str = "the_ville"
    llm_profile_id: int | None = None
    # When True, spawn the run immediately via SimulationManager. Defaults to
    # False so the M3a "create does not spawn a task" contract holds; the
    # frontend's launch form passes start=True explicitly.
    start: bool = False


class PersonaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    age: int | None
    plan_text: str | None


class MemoryNodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    node_id: str
    node_type: str
    created: int
    expiration_step: int | None
    subject: str
    predicate: str
    object: str
    description: str
    poignancy: int
    keywords: list[str] = Field(
        default_factory=list, validation_alias="keywords_json"
    )


class PersonaStateOut(BaseModel):
    persona: PersonaOut
    scratch: dict | None
    spatial_memory: dict | None
    recent_memory: list[MemoryNodeOut]


class StepMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step: int
    persona_name: str
    x: int
    y: int
    description: str | None
    pronunciatio: str | None
    chat: list | None = Field(default=None, validation_alias="chat_json")
    location_path: str | None


class StepSnapshotOut(BaseModel):
    step: int
    environment: dict | None
    movements: list[StepMovementOut]


class StepListOut(BaseModel):
    items: list[StepMovementOut]
    total: int
    from_step: int
    to_step: int


class MemoryListOut(BaseModel):
    items: list[MemoryNodeOut]
    total: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_VALID_STATUSES = {s.value for s in SimulationStatus}


def _require_sim(repos: Repos, sim_id: int):
    sim = repos.simulations.get_by_id(sim_id)
    if sim is None:
        raise HTTPException(status_code=404, detail=f"Simulation {sim_id} not found")
    return sim


def _parse_hms(hms: str) -> str:
    """Validate HH:MM:SS string; return canonical form."""
    try:
        h, m, s = hms.split(":")
        hh, mm, ss = int(h), int(m), int(s)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid start_hms: {hms!r} (expected HH:MM:SS)"
        ) from exc
    if not (0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60):
        raise HTTPException(
            status_code=400, detail=f"Invalid start_hms: {hms!r} (out of range)"
        )
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def _initial_iso(start_hms: str) -> str:
    """Build an ISO timestamp for today + the given HH:MM:SS."""
    h, m, s = (int(p) for p in start_hms.split(":"))
    today = datetime.utcnow().date()
    return datetime(today.year, today.month, today.day, h, m, s).isoformat()


def _validate_fork_config(repos: Repos, payload: SimulationCreateIn) -> None:
    """Reject a bad persona/inner_voice combo at create time with a 400.

    The runner validates the same constraints (see
    :func:`runner.st_runner.build_town` / ``_settle_meta``) but only once the
    run has spawned — so the row would otherwise land as ``status=failed``.
    Catching it here turns a late, opaque failure into an immediate 400.

    When the fork's personas cannot be resolved (``fork_persona_names``
    returns ``None``) we skip validation rather than reject — the runner
    accepts some forks this scan does not see.
    """
    if not payload.fork_sim_code:
        return
    fork_personas = fork_persona_names(
        repos.simulations.session, payload.fork_sim_code
    )
    if fork_personas is None:
        return

    unknown = sorted(set(payload.personas) - set(fork_personas))
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown personas {unknown}; fork "
                f"{payload.fork_sim_code!r} has {sorted(fork_personas)}"
            ),
        )

    # inner_voice must name one of the *included* personas — the subset when
    # one is given, otherwise all of the fork's personas.
    included = payload.personas or fork_personas
    if payload.inner_voice and payload.inner_voice not in included:
        raise HTTPException(
            status_code=400,
            detail=(
                f"inner_voice {payload.inner_voice!r} is not one of the "
                f"included personas {sorted(included)}"
            ),
        )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@router.post("", status_code=201, response_model=SimulationOut)
@router.post("/", status_code=201, response_model=SimulationOut, include_in_schema=False)
async def create_and_start(
    payload: SimulationCreateIn = Body(...),
    repos: Repos = Depends(get_repos),
) -> SimulationOut:
    """Create a simulation row (IDLE).

    When ``payload.start`` is True, also spawn the run via
    :class:`SimulationManager`. ``start`` defaults to False, preserving the
    M3a contract that plain creation does not spawn a task.
    """
    start_hms = _parse_hms(payload.start_hms)
    iso = _initial_iso(start_hms)

    if repos.simulations.get_by_code(payload.sim_code) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Simulation with sim_code={payload.sim_code!r} already exists",
        )

    _validate_fork_config(repos, payload)

    try:
        sim = repos.simulations.create(
            payload.sim_code,
            fork_sim_code=payload.fork_sim_code,
            status=SimulationStatus.IDLE,
            start_time_iso=iso,
            curr_time_iso=iso,
            sec_per_step=payload.sec_per_step,
            maze_name=payload.maze_name,
            n_round=payload.n_round,
            idea=payload.idea,
            inner_voice=payload.inner_voice,
        )
    except IntegrityError as exc:
        repos.simulations.session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Simulation with sim_code={payload.sim_code!r} already exists",
        ) from exc

    # Always record a config snapshot when personas or an LLM profile are
    # given — the runner reads persona_filter_json from it.
    if payload.llm_profile_id is not None or payload.personas:
        snapshot = SimulationConfigSnapshot(
            sim_id=sim.id,
            llm_profile_json={"llm_profile_id": payload.llm_profile_id},
            persona_filter_json={"personas": list(payload.personas)},
        )
        repos.simulations.session.add(snapshot)
        repos.simulations.session.commit()

    sim_id = sim.id
    if payload.start:
        try:
            await manager_singleton.start(sim_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("create_and_start: manager.start failed: {}", exc)
            raise HTTPException(
                status_code=500, detail=f"Failed to start simulation: {exc}"
            ) from exc
        repos.simulations.session.expire_all()
        sim = repos.simulations.get_by_id(sim_id)

    return SimulationOut.model_validate(sim)


@router.get("", response_model=list[SimulationOut])
@router.get("/", response_model=list[SimulationOut], include_in_schema=False)
def list_sims(
    status: str | None = Query(default=None),
    include_deleted: bool = Query(default=False),
    repos: Repos = Depends(get_repos),
) -> list[SimulationOut]:
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status {status!r}; valid: {sorted(_VALID_STATUSES)}",
        )
    rows = repos.simulations.list(status=status, include_deleted=include_deleted)
    return [SimulationOut.model_validate(r) for r in rows]


@router.get("/{sim_id}", response_model=SimulationOut)
def get_sim(sim_id: int, repos: Repos = Depends(get_repos)) -> SimulationOut:
    sim = _require_sim(repos, sim_id)
    return SimulationOut.model_validate(sim)


@router.post("/{sim_id}/pause", status_code=204)
async def pause_sim(sim_id: int, repos: Repos = Depends(get_repos)) -> Response:
    """Pause a sim. When it is running in this manager the cooperative pause
    flag is used (the manager writes the DB status); otherwise fall back to a
    direct status flip so imported/idle rows still respond."""
    _require_sim(repos, sim_id)
    if manager_singleton.is_running(sim_id):
        await manager_singleton.pause(sim_id)
    else:
        repos.simulations.set_status(sim_id, SimulationStatus.PAUSED)
    return Response(status_code=204)


@router.post("/{sim_id}/resume", status_code=204)
async def resume_sim(sim_id: int, repos: Repos = Depends(get_repos)) -> Response:
    _require_sim(repos, sim_id)
    if manager_singleton.is_running(sim_id):
        await manager_singleton.resume(sim_id)
    else:
        repos.simulations.set_status(sim_id, SimulationStatus.RUNNING)
    return Response(status_code=204)


@router.post("/{sim_id}/stop", status_code=204)
async def stop_sim(sim_id: int, repos: Repos = Depends(get_repos)) -> Response:
    _require_sim(repos, sim_id)
    if manager_singleton.is_running(sim_id):
        await manager_singleton.stop(sim_id)
    else:
        repos.simulations.set_status(sim_id, SimulationStatus.STOPPED)
    return Response(status_code=204)


@router.delete("/{sim_id}", status_code=204)
def delete_sim(sim_id: int, repos: Repos = Depends(get_repos)) -> Response:
    _require_sim(repos, sim_id)
    repos.simulations.soft_delete(sim_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


_MAX_STEP_RANGE = 1000


@router.get("/{sim_id}/steps", response_model=StepListOut)
def list_steps(
    sim_id: int,
    from_: int | None = Query(default=None, alias="from", ge=0),
    to: int | None = Query(default=None, ge=0),
    repos: Repos = Depends(get_repos),
) -> StepListOut:
    _require_sim(repos, sim_id)
    if from_ is None:
        from_ = 0
    if to is None:
        max_step = repos.steps.get_max_step(sim_id)
        to = max(max_step, 0)
    if to < from_:
        raise HTTPException(
            status_code=400, detail=f"`to` ({to}) must be >= `from` ({from_})"
        )
    if (to - from_) > _MAX_STEP_RANGE:
        raise HTTPException(
            status_code=400,
            detail=f"Step range too large; max {_MAX_STEP_RANGE} steps per query",
        )
    rows = repos.steps.list_movements_range(sim_id, from_, to)
    items = [StepMovementOut.model_validate(r) for r in rows]
    return StepListOut(items=items, total=len(items), from_step=from_, to_step=to)


@router.get("/{sim_id}/steps/{step}", response_model=StepSnapshotOut)
def get_step(
    sim_id: int,
    step: int,
    repos: Repos = Depends(get_repos),
) -> StepSnapshotOut:
    _require_sim(repos, sim_id)
    env = repos.steps.get_environment(sim_id, step)
    movements = repos.steps.get_movements(sim_id, step)
    return StepSnapshotOut(
        step=step,
        environment=env,
        movements=[StepMovementOut.model_validate(m) for m in movements],
    )


# ---------------------------------------------------------------------------
# Personas (scoped under a simulation)
# ---------------------------------------------------------------------------


@router.get("/{sim_id}/personas", response_model=list[PersonaOut])
def list_personas(
    sim_id: int, repos: Repos = Depends(get_repos)
) -> list[PersonaOut]:
    _require_sim(repos, sim_id)
    rows = repos.personas.list_by_sim(sim_id)
    return [PersonaOut.model_validate(r) for r in rows]


@router.get("/{sim_id}/personas/{name}/state", response_model=PersonaStateOut)
def persona_state(
    sim_id: int,
    name: str,
    step: int | None = Query(default=None, ge=0),
    k: int = Query(default=20, ge=1, le=500),
    repos: Repos = Depends(get_repos),
) -> PersonaStateOut:
    _require_sim(repos, sim_id)
    persona = repos.personas.get(sim_id, name)
    if persona is None:
        raise HTTPException(
            status_code=404,
            detail=f"Persona {name!r} not found in sim {sim_id}",
        )
    scratch = repos.personas.load_scratch(persona.id)
    spatial = repos.personas.load_spatial_memory(persona.id)
    recent_nodes = repos.memory.list_nodes(
        persona.id, before_step=step, limit=k
    )
    return PersonaStateOut(
        persona=PersonaOut.model_validate(persona),
        scratch=scratch,
        spatial_memory=spatial,
        recent_memory=[MemoryNodeOut.model_validate(n) for n in recent_nodes],
    )


@router.get("/{sim_id}/personas/{name}/memory", response_model=MemoryListOut)
def persona_memory(
    sim_id: int,
    name: str,
    type: str | None = Query(default=None),
    before_step: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    repos: Repos = Depends(get_repos),
) -> MemoryListOut:
    _require_sim(repos, sim_id)
    persona = repos.personas.get(sim_id, name)
    if persona is None:
        raise HTTPException(
            status_code=404,
            detail=f"Persona {name!r} not found in sim {sim_id}",
        )
    if type is not None and type not in {t.value for t in MemoryNodeType}:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type {type!r}; valid: {[t.value for t in MemoryNodeType]}",
        )

    # Repo doesn't have offset; pull (offset+limit) and slice.
    all_filtered = repos.memory.list_nodes(
        persona.id, node_type=type, before_step=before_step, limit=None
    )
    total = len(all_filtered)
    sliced = all_filtered[offset : offset + limit]
    return MemoryListOut(
        items=[MemoryNodeOut.model_validate(n) for n in sliced],
        total=total,
    )
