#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Desc   : MG StanfordTown Env

from core.environment.base_env import Environment
from core.environment.stanford_town.stanford_town_ext_env import StanfordTownExtEnv


class StanfordTownEnv(StanfordTownExtEnv, Environment):
    pass
