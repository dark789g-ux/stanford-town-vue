#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Vendored environment package.

Only StanfordTown is wired up; other upstream environments (android,
software, werewolf, minecraft, mgx, api) are not vendored.
"""

from core.environment.base_env import Environment
from core.environment.stanford_town.stanford_town_env import StanfordTownEnv

__all__ = ["Environment", "StanfordTownEnv"]
