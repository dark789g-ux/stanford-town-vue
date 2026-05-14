"""Catalog / metadata endpoints.

Read-only descriptions of bundled personas and maze maps.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.deps import get_settings
from config.settings import Settings
from storage.json_schemas import parse_scratch

router = APIRouter(prefix="/api/meta", tags=["meta"])


# --------------------------------------------------------------------- schemas
class PersonaMetaItem(BaseModel):
    name: str
    age: int | None = None
    has_sprite: bool
    bootstrap_set: str


class MapMetaItem(BaseModel):
    name: str
    visuals_url: str
    meta: dict[str, Any]
    sprite_sheet_url: str | None = None


# ----------------------------------------------------------------- file helpers
def _assets_dir(settings: Settings) -> Path:
    return settings.expanded_assets_dir()


def _persona_age(scratch_path: Path) -> int | None:
    try:
        text = scratch_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        scratch = parse_scratch(text)
        return scratch.age
    except Exception:  # noqa: BLE001 - tolerate any schema drift
        try:
            data = json.loads(text)
            age = data.get("age")
            return int(age) if age is not None else None
        except Exception:  # noqa: BLE001
            return None


# ----------------------------------------------------------------- endpoints
@router.get("/personas", response_model=list[PersonaMetaItem])
def list_personas(settings: Settings = Depends(get_settings)) -> list[PersonaMetaItem]:
    """Walk the bundled persona directories and summarise each one."""
    base = _assets_dir(settings)
    personas_root = base / "personas"
    characters_root = base / "characters"

    items: list[PersonaMetaItem] = []
    if not personas_root.is_dir():
        return items

    for bootstrap_dir in sorted(personas_root.iterdir()):
        if not bootstrap_dir.is_dir() or not bootstrap_dir.name.startswith("base_"):
            continue
        personas_subdir = bootstrap_dir / "personas"
        if not personas_subdir.is_dir():
            continue
        for persona_dir in sorted(personas_subdir.iterdir()):
            if not persona_dir.is_dir():
                continue
            scratch_path = persona_dir / "bootstrap_memory" / "scratch.json"
            age = _persona_age(scratch_path) if scratch_path.exists() else None
            sprite_name = persona_dir.name.replace(" ", "_") + ".png"
            has_sprite = (characters_root / sprite_name).exists()
            items.append(
                PersonaMetaItem(
                    name=persona_dir.name,
                    age=age,
                    has_sprite=has_sprite,
                    bootstrap_set=bootstrap_dir.name,
                )
            )

    items.sort(key=lambda i: i.name)
    return items


@router.get("/maps", response_model=list[MapMetaItem])
def list_maps(settings: Settings = Depends(get_settings)) -> list[MapMetaItem]:
    """Scan ``<assets>/maze/*`` and surface each map's meta + visuals URL."""
    base = _assets_dir(settings)
    maze_root = base / "maze"

    items: list[MapMetaItem] = []
    if not maze_root.is_dir():
        return items

    for map_dir in sorted(maze_root.iterdir()):
        if not map_dir.is_dir():
            continue
        meta_path = map_dir / "matrix" / "maze_meta_info.json"
        if not meta_path.exists():
            continue
        try:
            meta_data: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - skip unreadable meta
            continue

        visuals_dir = map_dir / "visuals"
        visuals_url = f"/assets/maze/{map_dir.name}/visuals/map.png"
        if visuals_dir.is_dir():
            preferred = visuals_dir / "map.png"
            if preferred.exists():
                visuals_url = f"/assets/maze/{map_dir.name}/visuals/map.png"
            else:
                pngs = sorted(p for p in visuals_dir.glob("*.png"))
                if pngs:
                    visuals_url = f"/assets/maze/{map_dir.name}/visuals/{pngs[0].name}"

        items.append(
            MapMetaItem(
                name=map_dir.name,
                visuals_url=visuals_url,
                meta=meta_data,
                sprite_sheet_url=None,
            )
        )

    return items
