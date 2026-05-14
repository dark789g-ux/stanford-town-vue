"""Vendored stub for metagpt.utils.git_repository.

Upstream's `GitRepository` wraps `gitpython` to manage per-project
working trees. The StanfordTown simulator does not commit anything to
git, but `backend.core.environment.base_env.Environment.archive()`
references the class. We provide a thin no-op stub matching the
constructor signature and the most common methods.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Union


class GitRepository:
    """No-op stub; logs nothing and persists nothing."""

    def __init__(
        self,
        local_path: Optional[Union[str, Path]] = None,
        auto_init: bool = True,
        **kwargs: Any,
    ) -> None:
        self.workdir: Optional[Path] = Path(local_path) if local_path else None
        self.auto_init = auto_init

    def add_change(self, files: Any) -> None:
        return None

    def commit(self, comments: str) -> None:
        return None

    def archive(self, *args: Any, **kwargs: Any) -> None:
        return None

    def delete_repository(self) -> None:
        return None

    @property
    def is_valid(self) -> bool:
        return False

    @property
    def changed_files(self) -> dict:
        return {}

    def get_files(self, *args: Any, **kwargs: Any) -> List[str]:
        return []

    @classmethod
    async def clone_from(
        cls, url: Union[str, Path], output_dir: Optional[Union[str, Path]] = None
    ) -> "GitRepository":
        return cls(local_path=output_dir, auto_init=False)


__all__ = ["GitRepository"]
