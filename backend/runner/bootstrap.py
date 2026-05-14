"""Runner bootstrap — wires the real runner + session factory into the manager.

Called once from the FastAPI lifespan handler. Kept tiny and side-effect-free
on import so test code can import :mod:`runner.manager` without dragging in the
heavy simulator dependency graph.
"""

from __future__ import annotations

from loguru import logger


def bootstrap_runner() -> None:
    """Inject the live StanfordTown runner + production session factory."""
    from runner.manager import manager_singleton
    from runner.sessions import build_session_factory
    from runner.st_runner import stanford_town_runner

    manager_singleton.set_session_factory(build_session_factory())
    manager_singleton.set_runner(stanford_town_runner)
    logger.info("runner bootstrap: stanford_town_runner wired into manager")


__all__ = ["bootstrap_runner"]
