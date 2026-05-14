"""PersonaRepo — persona CRUD + scratch / spatial-memory tree helpers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from storage.models import Persona, SpatialMemoryTree


class PersonaRepo:
    """Per-simulation persona storage."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # --------------------------------------------------------- create / upsert
    def create(self, sim_id: int, name: str, **fields) -> Persona:
        """Insert a persona; if ``(sim_id, name)`` already exists, update it.

        Only non-None fields are copied during an upsert so callers can pass
        partial updates without nulling out previously-set values.
        """
        existing = self.session.scalar(
            select(Persona).where(
                Persona.sim_id == sim_id,
                Persona.name == name,
            )
        )
        if existing is not None:
            for k, v in fields.items():
                if v is not None:
                    setattr(existing, k, v)
            self.session.commit()
            self.session.refresh(existing)
            return existing

        persona = Persona(sim_id=sim_id, name=name, **fields)
        self.session.add(persona)
        self.session.commit()
        self.session.refresh(persona)
        return persona

    # ------------------------------------------------------------------ reads
    def get(self, sim_id: int, name: str) -> Persona | None:
        return self.session.scalar(
            select(Persona).where(
                Persona.sim_id == sim_id,
                Persona.name == name,
            )
        )

    def get_by_id(self, persona_id: int) -> Persona | None:
        return self.session.get(Persona, persona_id)

    def list_by_sim(self, sim_id: int) -> list[Persona]:
        stmt = select(Persona).where(Persona.sim_id == sim_id).order_by(Persona.id)
        return list(self.session.scalars(stmt).all())

    # ---------------------------------------------------------------- scratch
    def save_scratch(self, persona_id: int, scratch_json: dict) -> None:
        persona = self.session.get(Persona, persona_id)
        if persona is None:
            raise ValueError(f"Persona id={persona_id} not found")
        persona.scratch_json = scratch_json
        self.session.commit()

    def load_scratch(self, persona_id: int) -> dict | None:
        persona = self.session.get(Persona, persona_id)
        if persona is None:
            return None
        return persona.scratch_json

    # ------------------------------------------------------------ spatial mem
    def save_spatial_memory(self, persona_id: int, tree_json: dict) -> None:
        """Upsert the spatial-memory tree for a persona."""
        existing = self.session.scalar(
            select(SpatialMemoryTree).where(SpatialMemoryTree.persona_id == persona_id)
        )
        if existing is not None:
            existing.tree_json = tree_json
        else:
            self.session.add(
                SpatialMemoryTree(persona_id=persona_id, tree_json=tree_json)
            )
        self.session.commit()

    def load_spatial_memory(self, persona_id: int) -> dict | None:
        tree = self.session.scalar(
            select(SpatialMemoryTree).where(SpatialMemoryTree.persona_id == persona_id)
        )
        return tree.tree_json if tree is not None else None


__all__ = ["PersonaRepo"]
