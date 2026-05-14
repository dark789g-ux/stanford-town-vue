"""Vendored stub for metagpt.strategy.planner.

The upstream `Planner` orchestrates task decomposition for Data
Interpreter / RoleZero. The StanfordTown simulator does NOT trigger the
react/plan path of `Role`, but `Role.planner` is declared as a Field
with `default_factory=Planner`, which is evaluated when any Role is
constructed. We therefore provide a minimal pydantic-compatible class
whose constructor accepts the kwargs `Role.plan()` passes
(`goal`, `working_memory`, `auto_run`) and whose attributes
(`plan.goal`, `current_task`) are safe defaults.

If the planning path is ever exercised, replace this with a real port.
"""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Plan(BaseModel):
    goal: str = ""
    tasks: List[Any] = Field(default_factory=list)
    current_task_id: Optional[str] = None


class Planner(BaseModel):
    """No-op Planner stub (see module docstring)."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    goal: str = ""
    plan: _Plan = Field(default_factory=_Plan)
    working_memory: Any = None
    auto_run: bool = True
    current_task: Optional[Any] = None

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if self.goal:
            self.plan.goal = self.goal

    async def update_plan(self, goal: str = "", **kwargs: Any) -> None:
        self.goal = goal
        self.plan.goal = goal

    async def process_task_result(self, task_result: Any) -> None:
        return None

    def get_useful_memories(self) -> List[Any]:
        return []


__all__ = ["Planner"]
