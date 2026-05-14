"""Pydantic v2 schemas for WebSocket hub message envelopes.

These models describe the JSON wire shapes documented in M3b. They are
validation/serialization helpers — the hub itself sends raw dicts via
``websocket.send_json`` to avoid the per-message Pydantic overhead, but
both producers and tests can use these for structural assertions.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Client -> server
# ---------------------------------------------------------------------------


class SubscribeIn(BaseModel):
    """First message a client must send after the WS handshake."""

    action: Literal["subscribe"]
    since_step: int = Field(0, ge=0)


class PingIn(BaseModel):
    action: Literal["ping"]


class UnsubscribeIn(BaseModel):
    action: Literal["unsubscribe"]


WsClientMessage = Annotated[
    Union[SubscribeIn, PingIn, UnsubscribeIn],
    Field(discriminator="action"),
]


# ---------------------------------------------------------------------------
# Server -> client
# ---------------------------------------------------------------------------


class SnapshotOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    event: Literal["snapshot"] = "snapshot"
    sim: dict[str, Any]
    current_step: int


class StepOut(BaseModel):
    event: Literal["step"] = "step"
    step: int
    curr_time: str
    movements: list[dict[str, Any]]


class StatusOut(BaseModel):
    event: Literal["status"] = "status"
    status: str
    error_message: str | None = None


class LlmCallOut(BaseModel):
    event: Literal["llm_call"] = "llm_call"
    persona: str
    step: int
    model: str
    latency_ms: int


class ErrorOut(BaseModel):
    event: Literal["error"] = "error"
    detail: str


class PongOut(BaseModel):
    event: Literal["pong"] = "pong"
    ts: str


__all__ = [
    "SubscribeIn",
    "PingIn",
    "UnsubscribeIn",
    "WsClientMessage",
    "SnapshotOut",
    "StepOut",
    "StatusOut",
    "LlmCallOut",
    "ErrorOut",
    "PongOut",
]
