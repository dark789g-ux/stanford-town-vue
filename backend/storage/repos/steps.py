"""StepRepo — per-step environment payloads and persona movement rows."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from storage.models import StepEnvironment, StepMovement


class StepRepo:
    """Per-tick environment + movement persistence."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ----------------------------------------------------------- environments
    def upsert_environment(
        self, sim_id: int, step: int, payload_json: dict
    ) -> None:
        existing = self.session.scalar(
            select(StepEnvironment).where(
                StepEnvironment.sim_id == sim_id,
                StepEnvironment.step == step,
            )
        )
        if existing is not None:
            existing.payload_json = payload_json
        else:
            self.session.add(
                StepEnvironment(sim_id=sim_id, step=step, payload_json=payload_json)
            )
        self.session.commit()

    def get_environment(self, sim_id: int, step: int) -> dict | None:
        env = self.session.scalar(
            select(StepEnvironment).where(
                StepEnvironment.sim_id == sim_id,
                StepEnvironment.step == step,
            )
        )
        return env.payload_json if env is not None else None

    # -------------------------------------------------------------- movements
    def upsert_movements_for_step(
        self, sim_id: int, step: int, movements: list[dict]
    ) -> None:
        """Replace-style upsert: delete then bulk-insert for this (sim, step)."""
        # Clear out any prior movement rows for this step so re-imports stay clean.
        old = self.session.scalars(
            select(StepMovement).where(
                StepMovement.sim_id == sim_id,
                StepMovement.step == step,
            )
        ).all()
        for row in old:
            self.session.delete(row)
        if old:
            self.session.flush()

        if not movements:
            self.session.commit()
            return

        rows = []
        for mv in movements:
            rows.append(
                {
                    "sim_id": sim_id,
                    "step": step,
                    "persona_name": mv["persona_name"],
                    "x": mv["x"],
                    "y": mv["y"],
                    "description": mv.get("description"),
                    "pronunciatio": mv.get("pronunciatio"),
                    "chat_json": mv.get("chat"),
                    "location_path": mv.get("location_path"),
                }
            )
        stmt = sqlite_insert(StepMovement).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["sim_id", "step", "persona_name"]
        )
        self.session.execute(stmt)
        self.session.commit()

    def get_movements(self, sim_id: int, step: int) -> list[StepMovement]:
        stmt = (
            select(StepMovement)
            .where(StepMovement.sim_id == sim_id, StepMovement.step == step)
            .order_by(StepMovement.persona_name)
        )
        return list(self.session.scalars(stmt).all())

    def list_movements_range(
        self, sim_id: int, from_step: int, to_step: int
    ) -> list[StepMovement]:
        stmt = (
            select(StepMovement)
            .where(
                StepMovement.sim_id == sim_id,
                StepMovement.step >= from_step,
                StepMovement.step <= to_step,
            )
            .order_by(StepMovement.step, StepMovement.persona_name)
        )
        return list(self.session.scalars(stmt).all())

    def get_max_step(self, sim_id: int) -> int:
        """Return the highest known step (env OR movement). -1 if none."""
        env_max = self.session.scalar(
            select(func.max(StepEnvironment.step)).where(
                StepEnvironment.sim_id == sim_id
            )
        )
        mv_max = self.session.scalar(
            select(func.max(StepMovement.step)).where(StepMovement.sim_id == sim_id)
        )
        candidates = [v for v in (env_max, mv_max) if v is not None]
        if not candidates:
            return -1
        return int(max(candidates))

    def delete_steps_for_sim(self, sim_id: int) -> int:
        env_rows = self.session.scalars(
            select(StepEnvironment).where(StepEnvironment.sim_id == sim_id)
        ).all()
        mv_rows = self.session.scalars(
            select(StepMovement).where(StepMovement.sim_id == sim_id)
        ).all()
        for r in env_rows:
            self.session.delete(r)
        for r in mv_rows:
            self.session.delete(r)
        self.session.commit()
        return len(env_rows) + len(mv_rows)


__all__ = ["StepRepo"]
