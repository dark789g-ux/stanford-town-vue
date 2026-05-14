"""Vendored stub for metagpt.tools.tool_registry.

The upstream version maintains a registry of tools for the Data
Interpreter / agent ecosystem. The StanfordTown simulator does not
exercise that machinery, but `backend.core.schema` decorates a couple of
classes with `@register_tool(...)`. We provide a no-op decorator so the
imports resolve and the decorated classes are returned unchanged.
"""
from __future__ import annotations

from typing import Any, Callable


def register_tool(*args: Any, **kwargs: Any) -> Callable[[type], type]:
    """No-op decorator factory matching the upstream signature.

    Upstream `register_tool` accepts tags, schema paths, includes, etc.
    Here we ignore everything and return the class unchanged.
    """

    def decorator(cls: type) -> type:
        return cls

    # `@register_tool` (no args) - first positional arg is the class.
    if args and callable(args[0]) and not kwargs:
        return args[0]
    return decorator


class ToolRegistry:
    """Stub matching the public surface used elsewhere (not exercised)."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def register_tool(self, *args: Any, **kwargs: Any) -> None:
        return None

    def get_tool(self, name: str) -> Any:
        return self.tools.get(name)

    def has_tool(self, name: str) -> bool:
        return name in self.tools


TOOL_REGISTRY = ToolRegistry()

__all__ = ["register_tool", "ToolRegistry", "TOOL_REGISTRY"]
