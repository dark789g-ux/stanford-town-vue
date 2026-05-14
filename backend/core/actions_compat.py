"""Compatibility shim for `from metagpt.actions import ...`.

Re-exports the small set of action symbols vendored into this project so
that code originally written as `from metagpt.actions import Action,
UserRequirement, ActionOutput` can be rewritten to import from
`backend.core.actions_compat` instead.
"""
from core.action import Action
from core.action_output import ActionOutput
from core._add_requirement import UserRequirement

__all__ = ["Action", "ActionOutput", "UserRequirement"]
