"""LLM call log endpoints (scoped under a simulation)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.deps import get_repos
from storage.repos import Repos

# These endpoints live under /api/sims/{sim_id}/llm-logs but are kept in
# their own module to match the spec's split of concerns.
router = APIRouter(prefix="/api/sims", tags=["llm-logs"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LlmCallSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    persona_name: str | None
    step: int | None
    ts: datetime
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    error: str | None


class LlmCallDetail(LlmCallSummary):
    prompt: str
    response: str


class LlmLogsListOut(BaseModel):
    items: list[LlmCallSummary]
    total: int
    offset: int
    limit: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_sim(repos: Repos, sim_id: int):
    sim = repos.simulations.get_by_id(sim_id)
    if sim is None:
        raise HTTPException(
            status_code=404, detail=f"Simulation {sim_id} not found"
        )
    return sim


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{sim_id}/llm-logs", response_model=LlmLogsListOut)
def list_llm_logs(
    sim_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    persona: str | None = Query(default=None),
    model: str | None = Query(default=None),
    repos: Repos = Depends(get_repos),
) -> LlmLogsListOut:
    _require_sim(repos, sim_id)
    rows = repos.llm_logs.list(
        sim_id, offset=offset, limit=limit, persona=persona, model=model
    )
    total = repos.llm_logs.count(sim_id)
    return LlmLogsListOut(
        items=[LlmCallSummary.model_validate(r) for r in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/{sim_id}/llm-logs/{call_id}", response_model=LlmCallDetail)
def get_llm_log(
    sim_id: int, call_id: int, repos: Repos = Depends(get_repos)
) -> LlmCallDetail:
    _require_sim(repos, sim_id)
    call = repos.llm_logs.get_by_id(sim_id, call_id)
    if call is None:
        raise HTTPException(
            status_code=404,
            detail=f"LLM call {call_id} not found in sim {sim_id}",
        )
    return LlmCallDetail.model_validate(call)
