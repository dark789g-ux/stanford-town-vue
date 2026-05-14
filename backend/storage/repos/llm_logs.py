"""LlmLogRepo — append-only log of LLM calls scoped per simulation."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from storage.models import LlmCall


class LlmLogRepo:
    """CRUD-light access to the ``llm_calls`` table."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ----------------------------------------------------------------- writes
    def add(self, sim_id: int, **fields) -> LlmCall:
        call = LlmCall(sim_id=sim_id, **fields)
        self.session.add(call)
        self.session.commit()
        self.session.refresh(call)
        return call

    def add_bulk(self, sim_id: int, rows: list[dict]) -> int:
        if not rows:
            return 0
        prepared = [dict(r, sim_id=sim_id) for r in rows]
        self.session.bulk_insert_mappings(LlmCall, prepared)
        self.session.commit()
        return len(prepared)

    # ------------------------------------------------------------------ reads
    def list(
        self,
        sim_id: int,
        offset: int = 0,
        limit: int = 100,
        persona: str | None = None,
        model: str | None = None,
    ) -> list[LlmCall]:
        stmt = select(LlmCall).where(LlmCall.sim_id == sim_id)
        if persona is not None:
            stmt = stmt.where(LlmCall.persona_name == persona)
        if model is not None:
            stmt = stmt.where(LlmCall.model == model)
        stmt = stmt.order_by(LlmCall.id.desc()).offset(offset).limit(limit)
        return list(self.session.scalars(stmt).all())

    def get_by_id(self, sim_id: int, call_id: int) -> LlmCall | None:
        return self.session.scalar(
            select(LlmCall).where(LlmCall.id == call_id, LlmCall.sim_id == sim_id)
        )

    def count(self, sim_id: int) -> int:
        return int(
            self.session.scalar(
                select(func.count(LlmCall.id)).where(LlmCall.sim_id == sim_id)
            )
            or 0
        )


__all__ = ["LlmLogRepo"]
