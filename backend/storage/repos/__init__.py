"""Repository layer for the storage package.

The repos wrap SQLAlchemy 2.0 sessions and expose a stable, dict-friendly API
for the simulator, importer, exporter, and FastAPI handlers. Each repo
commits on success; methods explicitly named ``*_no_commit`` skip commit.

The :class:`Repos` aggregate plus :func:`make_repos` factory let upstream
code construct all repositories from a single ``Session`` (and an optional
Fernet key for LLM profile encryption).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from storage.repos.llm_logs import LlmLogRepo
from storage.repos.llm_profiles import LlmProfileRepo
from storage.repos.memory import MemoryRepo
from storage.repos.personas import PersonaRepo
from storage.repos.simulations import SimulationRepo
from storage.repos.steps import StepRepo


@dataclass
class Repos:
    """Aggregate of all repository handles bound to a single session."""

    simulations: SimulationRepo
    personas: PersonaRepo
    memory: MemoryRepo
    steps: StepRepo
    llm_logs: LlmLogRepo
    llm_profiles: LlmProfileRepo


def make_repos(session: Session, fernet_key: bytes | None = None) -> Repos:
    """Construct a :class:`Repos` bundle for the given session.

    When ``fernet_key`` is ``None`` the LLM profile repo is built with a
    zero-length key marker; encryption/decryption methods will raise a
    ``RuntimeError`` until a real key is supplied.
    """
    key = fernet_key if fernet_key is not None else b""
    return Repos(
        simulations=SimulationRepo(session),
        personas=PersonaRepo(session),
        memory=MemoryRepo(session),
        steps=StepRepo(session),
        llm_logs=LlmLogRepo(session),
        llm_profiles=LlmProfileRepo(session, key),
    )


__all__ = [
    "Repos",
    "make_repos",
    "SimulationRepo",
    "PersonaRepo",
    "MemoryRepo",
    "StepRepo",
    "LlmLogRepo",
    "LlmProfileRepo",
]
