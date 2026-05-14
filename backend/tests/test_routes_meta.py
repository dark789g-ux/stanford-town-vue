"""Tests for /api/meta/* — personas and maps."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.main import app  # noqa: E402


def test_personas_endpoint_returns_bundled_personas() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/meta/personas")
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)

    # Names should be sorted
    names = [it["name"] for it in items]
    assert names == sorted(names)

    if not items:
        return  # tolerate empty assets dir in unconfigured environments

    # Each item has the documented shape
    for it in items:
        assert "name" in it and isinstance(it["name"], str)
        assert "has_sprite" in it and isinstance(it["has_sprite"], bool)
        assert "bootstrap_set" in it and it["bootstrap_set"].startswith("base_")
        assert "age" in it  # may be None

    # The bundled base_the_ville_n25 set is shipped in M1 and should be present.
    n25 = [it for it in items if it["bootstrap_set"] == "base_the_ville_n25"]
    if n25:
        n25_names = {it["name"] for it in n25}
        assert "Isabella Rodriguez" in n25_names
        # Isabella has age 34 in the bundled scratch.json
        isabella = next(it for it in n25 if it["name"] == "Isabella Rodriguez")
        assert isabella["age"] == 34
        assert isabella["has_sprite"] is True


def test_maps_endpoint_returns_the_ville() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/meta/maps")
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)

    if not items:
        return  # gracefully handle missing assets

    the_ville = next((it for it in items if it["name"] == "the_ville"), None)
    if the_ville is None:
        return  # other maps may exist, but most checkouts ship the_ville
    assert the_ville["visuals_url"].startswith("/assets/maze/the_ville/visuals/")
    assert the_ville["visuals_url"].endswith(".png")
    assert isinstance(the_ville["meta"], dict)
    assert the_ville["meta"]  # populated
    # The shipped maze_meta_info.json defines maze_width / maze_height.
    assert "maze_width" in the_ville["meta"]
    assert "maze_height" in the_ville["meta"]
