"""Vendored stub for metagpt.exp_pool.serializers."""
from __future__ import annotations

from typing import Any


class ActionNodeSerializer:
    """No-op serializer matching upstream's surface."""

    def serialize_req(self, *args: Any, **kwargs: Any) -> str:
        return ""

    def serialize_resp(self, *args: Any, **kwargs: Any) -> str:
        return ""

    def deserialize_resp(self, data: Any) -> Any:
        return data


__all__ = ["ActionNodeSerializer"]
