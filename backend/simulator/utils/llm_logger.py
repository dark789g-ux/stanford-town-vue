"""Append-only JSONL logger for every LLM call inside a Stanford Town sim.

The logger sits inside STAction's LLM chokepoint and writes one line per call
to <STORAGE_PATH>/<sim_code>/llm_logs.jsonl. Step / persona / action are kept
in contextvars so the call sites do not have to thread them through.

Behavior:
- If set_sim_code has not been called, log_call is a no-op (safe for unit tests
  and for code paths that exercise STAction outside a sim).
- File is opened, written, flushed, and closed per call; stanford_town runs at
  ~one LLM call per few seconds so the sync overhead is negligible.
"""
from __future__ import annotations

import itertools
import json
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from simulator.utils.const import STORAGE_PATH
from core.logs import logger

_sim_code: ContextVar[Optional[str]] = ContextVar("st_llm_sim_code", default=None)
_step: ContextVar[Optional[int]] = ContextVar("st_llm_step", default=None)
_persona: ContextVar[Optional[str]] = ContextVar("st_llm_persona", default=None)
_action: ContextVar[Optional[str]] = ContextVar("st_llm_action", default=None)

_seq = itertools.count()


def _reset_for_tests() -> None:
    """Reset module-level state. Tests only."""
    global _seq
    _seq = itertools.count()
    _sim_code.set(None)
    _step.set(None)
    _persona.set(None)
    _action.set(None)


def set_sim_code(sim_code: str) -> None:
    _sim_code.set(sim_code)


def set_step(step: int) -> None:
    _step.set(step)


def set_persona(persona: Optional[str]) -> None:
    _persona.set(persona)


def set_action(action_cls_name: Optional[str]) -> None:
    _action.set(action_cls_name)


def log_call(
    *,
    prompt: str,
    response: Optional[str],
    model: Optional[str],
    params: dict,
    usage: Optional[dict],
    cost_usd: Optional[float],
    latency_ms: int,
    retry_idx: int,
    used_fail_default: bool,
    error: Optional[str],
) -> None:
    sim_code = _sim_code.get()
    if not sim_code:
        return

    record: dict[str, Any] = {
        "seq": next(_seq),
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
        "step": _step.get(),
        "persona": _persona.get(),
        "action": _action.get(),
        "model": model,
        "params": params,
        "prompt": prompt,
        "response": response,
        "usage": usage,
        "cost_usd": cost_usd,
        "latency_ms": latency_ms,
        "retry_idx": retry_idx,
        "used_fail_default": used_fail_default,
        "error": error,
    }

    out_dir = Path(STORAGE_PATH) / sim_code
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / "llm_logs.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")
            f.flush()
    except Exception as exc:  # never let logging take down a sim
        logger.warning(f"llm_logger.log_call failed: {exc}")
