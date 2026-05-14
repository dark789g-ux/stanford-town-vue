"""Flatten the the_ville Tiled map into background PNGs for the Pixi viewer.

The original Stanford Town frontend (Phaser) loaded the Tiled JSON + ~18
tilesets and composited them at runtime. The Vue/Pixi viewer instead uses a
pre-flattened image, so the only hard part — multi-tileset compositing — is
done once here, offline.

Two outputs (the simulation's agents are drawn *between* them so they appear
in front of furniture but behind tree-tops etc.):

  the_ville_ground.png      — everything below the agents
  the_ville_foreground.png  — the "Foreground L*" layers, drawn above agents

The 7 block-registry layers (Collisions, *Blocks, Special Blocks Registry)
are logic-only and never drawn.

Usage (from repo root):
    python scripts/flatten_map.py
    python scripts/flatten_map.py --force        # overwrite existing outputs
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required: pip install Pillow")

# Tiled GID flip flags live in the top 3 bits.
_FLIP_H = 0x80000000
_FLIP_V = 0x40000000
_FLIP_D = 0x20000000
_GID_MASK = ~(_FLIP_H | _FLIP_V | _FLIP_D) & 0xFFFFFFFF

# Visible art layers, in draw order, split around the agent layer.
_GROUND_LAYERS = [
    "Bottom Ground",
    "Exterior Ground",
    "Exterior Decoration L1",
    "Exterior Decoration L2",
    "Interior Ground",
    "Wall",
    "Interior Furniture L1",
    "Interior Furniture L2 ",  # note: trailing space in the source map
]
_FOREGROUND_LAYERS = ["Foreground L1", "Foreground L2"]


def _load_tilesets(tiled: dict, visuals_dir: Path) -> list[dict]:
    """Return tilesets sorted by firstgid, each with its loaded image + geometry."""
    out = []
    for ts in tiled["tilesets"]:
        img_path = visuals_dir / ts["image"]
        if not img_path.is_file():
            sys.exit(f"tileset image missing: {img_path}")
        img = Image.open(img_path).convert("RGBA")
        tw, th = ts["tilewidth"], ts["tileheight"]
        margin = ts.get("margin", 0)
        spacing = ts.get("spacing", 0)
        columns = ts.get("columns") or ((img.width - 2 * margin + spacing) // (tw + spacing))
        out.append(
            {
                "firstgid": ts["firstgid"],
                "image": img,
                "tw": tw,
                "th": th,
                "margin": margin,
                "spacing": spacing,
                "columns": columns,
                "tilecount": ts.get("tilecount", columns * ((img.height - 2 * margin + spacing) // (th + spacing))),
            }
        )
    out.sort(key=lambda t: t["firstgid"])
    return out


def _tileset_for(gid: int, tilesets: list[dict]) -> dict | None:
    chosen = None
    for ts in tilesets:
        if gid >= ts["firstgid"]:
            chosen = ts
        else:
            break
    return chosen


def _tile_image(gid: int, tilesets: list[dict]) -> Image.Image | None:
    """Crop and (if flagged) flip the tile image for a raw Tiled gid."""
    flip_h = bool(gid & _FLIP_H)
    flip_v = bool(gid & _FLIP_V)
    flip_d = bool(gid & _FLIP_D)
    real_gid = gid & _GID_MASK
    if real_gid == 0:
        return None
    ts = _tileset_for(real_gid, tilesets)
    if ts is None:
        return None
    local = real_gid - ts["firstgid"]
    if local < 0 or local >= ts["tilecount"]:
        return None
    col = local % ts["columns"]
    row = local // ts["columns"]
    x = ts["margin"] + col * (ts["tw"] + ts["spacing"])
    y = ts["margin"] + row * (ts["th"] + ts["spacing"])
    tile = ts["image"].crop((x, y, x + ts["tw"], y + ts["th"]))
    if flip_d:
        tile = tile.transpose(Image.Transpose.TRANSPOSE)
    if flip_h:
        tile = tile.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if flip_v:
        tile = tile.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return tile


def _composite(tiled: dict, layer_names: list[str], tilesets: list[dict]) -> Image.Image:
    w = tiled["width"] * tiled["tilewidth"]
    h = tiled["height"] * tiled["tileheight"]
    tw, th = tiled["tilewidth"], tiled["tileheight"]
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layers_by_name = {layer["name"]: layer for layer in tiled["layers"] if layer["type"] == "tilelayer"}
    for name in layer_names:
        layer = layers_by_name.get(name)
        if layer is None:
            print(f"  ! layer not found, skipping: {name!r}")
            continue
        data = layer["data"]
        cols = layer["width"]
        placed = 0
        for idx, gid in enumerate(data):
            if gid == 0:
                continue
            tile = _tile_image(gid, tilesets)
            if tile is None:
                continue
            px = (idx % cols) * tw
            py = (idx // cols) * th
            canvas.alpha_composite(tile, (px, py))
            placed += 1
        print(f"  + {name}: {placed} tiles")
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser(description="Flatten the the_ville Tiled map.")
    parser.add_argument("--force", action="store_true", help="overwrite existing outputs")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    visuals = repo_root / "backend" / "assets" / "maze" / "the_ville" / "visuals"
    tiled_path = visuals / "the_ville_jan7.json"
    if not tiled_path.is_file():
        sys.exit(f"Tiled map not found: {tiled_path}")

    ground_out = visuals / "the_ville_ground.png"
    fg_out = visuals / "the_ville_foreground.png"
    if not args.force and ground_out.is_file() and fg_out.is_file():
        print(f"outputs already exist (use --force to regenerate):\n  {ground_out}\n  {fg_out}")
        return 0

    tiled = json.loads(tiled_path.read_text(encoding="utf-8"))
    print(f"map: {tiled['width']}x{tiled['height']} tiles @ {tiled['tilewidth']}px")
    tilesets = _load_tilesets(tiled, visuals)
    print(f"loaded {len(tilesets)} tilesets")

    print("compositing ground layers:")
    ground = _composite(tiled, _GROUND_LAYERS, tilesets)
    ground.save(ground_out)
    print(f"  -> {ground_out} ({ground.size[0]}x{ground.size[1]})")

    print("compositing foreground layers:")
    foreground = _composite(tiled, _FOREGROUND_LAYERS, tilesets)
    foreground.save(fg_out)
    print(f"  -> {fg_out} ({foreground.size[0]}x{foreground.size[1]})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
