"""LLM profile management endpoints."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.deps import get_db, get_settings
from config.settings import Settings
from storage.repos.llm_profiles import LlmProfileRepo

router = APIRouter(prefix="/api/llm-profiles", tags=["llm-profiles"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LlmProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    provider: str
    model: str
    base_url: str | None
    max_tokens: int
    temperature: float
    created_at: datetime


class LlmProfileCreate(BaseModel):
    name: str
    provider: Literal["openai", "deepseek", "anthropic"]
    model: str
    api_key: str
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.5
    extra: dict | None = None


class LlmProfileUpdate(BaseModel):
    name: str | None = None
    provider: Literal["openai", "deepseek", "anthropic"] | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    extra: dict | None = None


class LlmProfileTestResult(BaseModel):
    ok: bool
    elapsed_ms: int
    model: str
    sample_response: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


def get_llm_profile_repo(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> LlmProfileRepo:
    """Build a Fernet-keyed LlmProfileRepo for this request."""
    key_path = settings.expanded_secret_key_path()
    if not key_path.is_file():
        raise HTTPException(
            status_code=500, detail="secret key not bootstrapped"
        )
    try:
        key = key_path.read_bytes()
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to read secret key: {exc}"
        ) from exc
    return LlmProfileRepo(db, key)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[LlmProfileOut])
@router.get("/", response_model=list[LlmProfileOut], include_in_schema=False)
def list_profiles(
    repo: LlmProfileRepo = Depends(get_llm_profile_repo),
) -> list[LlmProfileOut]:
    return [LlmProfileOut.model_validate(p) for p in repo.list()]


@router.post("", response_model=LlmProfileOut, status_code=201)
@router.post("/", response_model=LlmProfileOut, status_code=201, include_in_schema=False)
def create_profile(
    payload: LlmProfileCreate = Body(...),
    repo: LlmProfileRepo = Depends(get_llm_profile_repo),
) -> LlmProfileOut:
    fields = {
        "base_url": payload.base_url,
        "max_tokens": payload.max_tokens,
        "temperature": payload.temperature,
    }
    if payload.extra is not None:
        fields["extra_json"] = payload.extra
    try:
        profile = repo.create(
            name=payload.name,
            provider=payload.provider,
            model=payload.model,
            api_key=payload.api_key,
            **fields,
        )
    except IntegrityError as exc:
        repo.session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"LLM profile with name={payload.name!r} already exists",
        ) from exc
    return LlmProfileOut.model_validate(profile)


@router.put("/{profile_id}", response_model=LlmProfileOut)
def update_profile(
    profile_id: int,
    payload: LlmProfileUpdate = Body(...),
    repo: LlmProfileRepo = Depends(get_llm_profile_repo),
) -> LlmProfileOut:
    if repo.get(profile_id) is None:
        raise HTTPException(
            status_code=404, detail=f"LLM profile id={profile_id} not found"
        )
    fields = payload.model_dump(exclude_unset=True)
    if "extra" in fields:
        fields["extra_json"] = fields.pop("extra")
    # Drop None for non-nullable / sensitive columns: if user passes
    # ``api_key=null`` explicitly we treat it as "leave as-is".
    if fields.get("api_key", "sentinel") is None:
        fields.pop("api_key", None)
    try:
        updated = repo.update(profile_id, **fields)
    except IntegrityError as exc:
        repo.session.rollback()
        raise HTTPException(
            status_code=409, detail="LLM profile update violates a uniqueness constraint"
        ) from exc
    return LlmProfileOut.model_validate(updated)


@router.delete("/{profile_id}", status_code=204)
def delete_profile(
    profile_id: int,
    repo: LlmProfileRepo = Depends(get_llm_profile_repo),
) -> Response:
    if repo.get(profile_id) is None:
        raise HTTPException(
            status_code=404, detail=f"LLM profile id={profile_id} not found"
        )
    repo.delete(profile_id)
    return Response(status_code=204)


@router.post("/{profile_id}/test", response_model=LlmProfileTestResult)
def test_profile(
    profile_id: int,
    repo: LlmProfileRepo = Depends(get_llm_profile_repo),
) -> LlmProfileTestResult:
    profile = repo.get(profile_id)
    if profile is None:
        raise HTTPException(
            status_code=404, detail=f"LLM profile id={profile_id} not found"
        )

    try:
        api_key = repo.get_decrypted_key(profile_id)
    except Exception as exc:  # noqa: BLE001
        return LlmProfileTestResult(
            ok=False, elapsed_ms=0, model=profile.model, error=f"decrypt failed: {exc}"
        )

    start = time.monotonic()
    try:
        sample: str | None = None
        if profile.provider in ("openai", "deepseek"):
            import openai  # local import to keep startup cheap

            client_kwargs: dict = {"api_key": api_key}
            if profile.base_url:
                client_kwargs["base_url"] = profile.base_url
            client = openai.OpenAI(**client_kwargs)
            resp = client.chat.completions.create(
                model=profile.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=8,
            )
            try:
                sample = resp.choices[0].message.content
            except (AttributeError, IndexError, TypeError):
                sample = str(resp)
        elif profile.provider == "anthropic":
            import anthropic

            client_kwargs = {"api_key": api_key}
            if profile.base_url:
                client_kwargs["base_url"] = profile.base_url
            client = anthropic.Anthropic(**client_kwargs)
            resp = client.messages.create(
                model=profile.model,
                max_tokens=8,
                messages=[{"role": "user", "content": "ping"}],
            )
            try:
                sample = resp.content[0].text
            except (AttributeError, IndexError, TypeError):
                sample = str(resp)
        else:  # pragma: no cover — pydantic Literal blocks this
            return LlmProfileTestResult(
                ok=False,
                elapsed_ms=int((time.monotonic() - start) * 1000),
                model=profile.model,
                error=f"unsupported provider: {profile.provider}",
            )
    except Exception as exc:  # noqa: BLE001
        return LlmProfileTestResult(
            ok=False,
            elapsed_ms=int((time.monotonic() - start) * 1000),
            model=profile.model,
            error=str(exc),
        )

    elapsed = int((time.monotonic() - start) * 1000)
    if sample is not None and len(sample) > 200:
        sample = sample[:200]
    return LlmProfileTestResult(
        ok=True,
        elapsed_ms=elapsed,
        model=profile.model,
        sample_response=sample,
    )
