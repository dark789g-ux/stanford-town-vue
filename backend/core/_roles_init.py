"""Compatibility shim for `from metagpt.roles import ...`.

Only the base `Role` class is vendored — re-exported here so legacy
imports that say `from metagpt.roles import Role` can be rewritten to
`from backend.core._roles_init import Role`.
"""
from core.role import Role

__all__ = ["Role"]
