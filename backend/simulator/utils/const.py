#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Desc   : Constants for the vendored stanford_town simulator.

from pathlib import Path

# backend/simulator/utils/const.py -> backend/simulator/
ST_ROOT_PATH = Path(__file__).parent.parent
# backend/simulator/utils/const.py -> backend/
_BACKEND_ROOT = ST_ROOT_PATH.parent

# Maze assets live under backend/assets/maze/the_ville/
MAZE_ASSET_PATH = _BACKEND_ROOT / "assets" / "maze" / "the_ville"

# Prompts live under backend/simulator/prompts/
PROMPTS_DIR = ST_ROOT_PATH / "prompts"

# TODO M2: replace JSON storage usage with Repo
STORAGE_PATH = _BACKEND_ROOT / "storage" / "stanford_town" / "storage"
# TODO M2: replace JSON storage usage with Repo
TEMP_STORAGE_PATH = _BACKEND_ROOT / "storage" / "stanford_town" / "temp_storage"

collision_block_id = "32125"
