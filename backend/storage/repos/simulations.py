"""SimulationRepo — CRUD + status transitions for the simulations table."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from storage.models import Simulation, SimulationStatus


def _coerce_status(status: SimulationStatus | str) -> str:
    if isinstance(status, SimulationStatus):
        return status.value
    return str(status)


class SimulationRepo:
    """Lifecycle + bookkeeping for ``simulations`` rows."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ create
    def create(self, sim_code: str, **fields) -> Simulation:
        """Insert a new simulation row.

        ``fields`` are forwarded to the ORM constructor; defaults defined on
        the model apply for anything omitted. ``status`` is coerced.
        """
        if "status" in fields and fields["status"] is not None:
            fields["status"] = _coerce_status(fields["status"])
        sim = Simulation(sim_code=sim_code, **fields)
        self.session.add(sim)
        self.session.commit()
        self.session.refresh(sim)
        return sim

    # ------------------------------------------------------------------ reads
    def get_by_id(self, sim_id: int) -> Simulation | None:
        return self.session.get(Simulation, sim_id)

    def get_by_code(self, sim_code: str) -> Simulation | None:
        return self.session.scalar(
            select(Simulation).where(Simulation.sim_code == sim_code)
        )

    def list(
        self,
        status: SimulationStatus | str | None = None,
        include_deleted: bool = False,
    ) -> list[Simulation]:
        stmt = select(Simulation)
        if not include_deleted:
            stmt = stmt.where(Simulation.deleted.is_(False))
        if status is not None:
            stmt = stmt.where(Simulation.status == _coerce_status(status))
        stmt = stmt.order_by(Simulation.id.desc())
        return list(self.session.scalars(stmt).all())

    # ---------------------------------------------------------------- updates
    def update(self, sim_id: int, **fields) -> Simulation:
        sim = self.session.get(Simulation, sim_id)
        if sim is None:
            raise ValueError(f"Simulation id={sim_id} not found")
        if "status" in fields and fields["status"] is not None:
            fields["status"] = _coerce_status(fields["status"])
        for k, v in fields.items():
            setattr(sim, k, v)
        self.session.commit()
        self.session.refresh(sim)
        return sim

    def soft_delete(self, sim_id: int) -> None:
        sim = self.session.get(Simulation, sim_id)
        if sim is None:
            raise ValueError(f"Simulation id={sim_id} not found")
        sim.deleted = True
        self.session.commit()

    def set_status(
        self,
        sim_id: int,
        status: SimulationStatus | str,
        error_message: str | None = None,
    ) -> None:
        sim = self.session.get(Simulation, sim_id)
        if sim is None:
            raise ValueError(f"Simulation id={sim_id} not found")
        sim.status = _coerce_status(status)
        if error_message is not None:
            sim.error_message = error_message
        self.session.commit()

    def advance_step(self, sim_id: int, step: int, curr_time_iso: str) -> None:
        """Update step counter + current ISO time — called every tick."""
        sim = self.session.get(Simulation, sim_id)
        if sim is None:
            raise ValueError(f"Simulation id={sim_id} not found")
        sim.step = step
        sim.curr_time_iso = curr_time_iso
        self.session.commit()


__all__ = ["SimulationRepo"]
