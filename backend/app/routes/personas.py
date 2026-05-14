"""Persona endpoints (currently empty — persona reads live under sims.py).

Kept as a stable module so future cross-sim persona operations have a home.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/personas", tags=["personas"])
