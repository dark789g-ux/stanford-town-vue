"""MemoryRepo — associative memory nodes + keyword indices."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from storage.models import (
    MemoryKeywordsToChat,
    MemoryKeywordsToEvent,
    MemoryKeywordsToThought,
    MemoryNode,
    MemoryNodeType,
)


def _coerce_type(node_type: MemoryNodeType | str) -> str:
    if isinstance(node_type, MemoryNodeType):
        return node_type.value
    return str(node_type)


_KEYWORD_TABLES = {
    MemoryNodeType.EVENT.value: MemoryKeywordsToEvent,
    MemoryNodeType.CHAT.value: MemoryKeywordsToChat,
    MemoryNodeType.THOUGHT.value: MemoryKeywordsToThought,
}


class MemoryRepo:
    """Nodes + keyword routing for the per-persona associative memory."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ nodes
    def add_node(
        self,
        persona_id: int,
        node_id: str,
        node_type: MemoryNodeType | str,
        **fields,
    ) -> MemoryNode:
        """Insert one memory node, auto-assigning ``node_count`` if absent."""
        node_type_val = _coerce_type(node_type)
        if "node_count" not in fields or fields["node_count"] is None:
            fields["node_count"] = self.get_max_node_count(persona_id) + 1
        # Sensible defaults the simulator usually omits at import time.
        fields.setdefault("type_count", fields["node_count"])
        fields.setdefault("depth", 0)
        fields.setdefault("created", 0)
        fields.setdefault("subject", "")
        fields.setdefault("predicate", "")
        fields.setdefault("object", "")
        fields.setdefault("description", "")
        fields.setdefault("poignancy", 0)
        fields.setdefault("keywords_json", [])

        node = MemoryNode(
            persona_id=persona_id,
            node_id=node_id,
            node_type=node_type_val,
            **fields,
        )
        self.session.add(node)
        self.session.commit()
        self.session.refresh(node)
        return node

    def add_nodes_bulk(self, persona_id: int, nodes: list[dict]) -> int:
        """Insert many nodes; existing ``(persona_id, node_id)`` rows skipped.

        Returns the number of rows actually inserted.
        """
        if not nodes:
            return 0
        rows = []
        for n in nodes:
            row = dict(n)
            row["persona_id"] = persona_id
            if isinstance(row.get("node_type"), MemoryNodeType):
                row["node_type"] = row["node_type"].value
            rows.append(row)

        stmt = sqlite_insert(MemoryNode).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["persona_id", "node_id"]
        )
        result = self.session.execute(stmt)
        self.session.commit()
        # ``rowcount`` is the inserts that actually happened on SQLite.
        return int(result.rowcount or 0)

    def get_node(self, persona_id: int, node_id: str) -> MemoryNode | None:
        return self.session.scalar(
            select(MemoryNode).where(
                MemoryNode.persona_id == persona_id,
                MemoryNode.node_id == node_id,
            )
        )

    def list_nodes(
        self,
        persona_id: int,
        node_type: MemoryNodeType | str | None = None,
        before_step: int | None = None,
        limit: int | None = None,
    ) -> list[MemoryNode]:
        stmt = select(MemoryNode).where(MemoryNode.persona_id == persona_id)
        if node_type is not None:
            stmt = stmt.where(MemoryNode.node_type == _coerce_type(node_type))
        if before_step is not None:
            stmt = stmt.where(MemoryNode.created < before_step)
        stmt = stmt.order_by(MemoryNode.node_count.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.scalars(stmt).all())

    def get_all_nodes(self, persona_id: int) -> list[MemoryNode]:
        stmt = (
            select(MemoryNode)
            .where(MemoryNode.persona_id == persona_id)
            .order_by(MemoryNode.node_count.asc())
        )
        return list(self.session.scalars(stmt).all())

    def delete_nodes_for_persona(self, persona_id: int) -> int:
        rows = self.session.scalars(
            select(MemoryNode).where(MemoryNode.persona_id == persona_id)
        ).all()
        for row in rows:
            self.session.delete(row)
        # Also drop keyword indices to keep the persona slate clean.
        for tbl in _KEYWORD_TABLES.values():
            kw_rows = self.session.scalars(
                select(tbl).where(tbl.persona_id == persona_id)
            ).all()
            for r in kw_rows:
                self.session.delete(r)
        self.session.commit()
        return len(rows)

    def get_max_node_count(self, persona_id: int) -> int:
        val = self.session.scalar(
            select(func.coalesce(func.max(MemoryNode.node_count), 0)).where(
                MemoryNode.persona_id == persona_id
            )
        )
        return int(val or 0)

    # ---------------------------------------------------------------- keywords
    def _table_for(self, node_type: MemoryNodeType | str):
        key = _coerce_type(node_type)
        try:
            return _KEYWORD_TABLES[key]
        except KeyError as exc:
            raise ValueError(f"Unknown memory node_type: {key!r}") from exc

    def add_keyword(
        self,
        persona_id: int,
        node_type: MemoryNodeType | str,
        keyword: str,
        node_id: str,
    ) -> None:
        tbl = self._table_for(node_type)
        stmt = sqlite_insert(tbl).values(
            persona_id=persona_id, keyword=keyword, node_id=node_id
        )
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["persona_id", "keyword", "node_id"]
        )
        self.session.execute(stmt)
        self.session.commit()

    def add_keywords_bulk(
        self,
        persona_id: int,
        node_type: MemoryNodeType | str,
        items: list[tuple[str, str]],
    ) -> int:
        if not items:
            return 0
        tbl = self._table_for(node_type)
        rows = [
            {"persona_id": persona_id, "keyword": kw, "node_id": nid}
            for kw, nid in items
        ]
        stmt = sqlite_insert(tbl).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["persona_id", "keyword", "node_id"]
        )
        result = self.session.execute(stmt)
        self.session.commit()
        return int(result.rowcount or 0)

    def list_keywords(
        self,
        persona_id: int,
        node_type: MemoryNodeType | str,
        keyword: str | None = None,
    ) -> list[tuple[str, str]]:
        tbl = self._table_for(node_type)
        stmt = select(tbl.keyword, tbl.node_id).where(tbl.persona_id == persona_id)
        if keyword is not None:
            stmt = stmt.where(tbl.keyword == keyword)
        rows = self.session.execute(stmt).all()
        return [(r[0], r[1]) for r in rows]


__all__ = ["MemoryRepo"]
