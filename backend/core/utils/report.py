"""Vendored stub for metagpt.utils.report.

Upstream uses this to push streaming task/log events to a UI sidecar
process. The simulator does not need that channel — we expose no-op
classes so `from backend.core.utils.report import TaskReporter` keeps
working.
"""
from __future__ import annotations

from typing import Any


class _NoopReporter:
    """Generic no-op reporter."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def async_report(self, *args: Any, **kwargs: Any) -> None:
        return None

    def report(self, *args: Any, **kwargs: Any) -> None:
        return None


class TaskReporter(_NoopReporter):
    pass


class ThoughtReporter(_NoopReporter):
    pass


class BrowserReporter(_NoopReporter):
    pass


class EditorReporter(_NoopReporter):
    pass


class TerminalReporter(_NoopReporter):
    pass


class ServerReporter(_NoopReporter):
    pass


class DocsReporter(_NoopReporter):
    pass


class GalleryReporter(_NoopReporter):
    pass


__all__ = [
    "TaskReporter",
    "ThoughtReporter",
    "BrowserReporter",
    "EditorReporter",
    "TerminalReporter",
    "ServerReporter",
    "DocsReporter",
    "GalleryReporter",
]
