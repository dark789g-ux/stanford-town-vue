#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Provider registrations (whitelisted: OpenAI, Anthropic, DeepSeek).

Imports here trigger the @register_provider decorators in each provider
module. DeepSeek is served by OpenAILLM (registered for LLMType.DEEPSEEK
within openai_api.py).
"""

from core.llm.openai_api import OpenAILLM
from core.llm.anthropic_api import AnthropicLLM
from core.llm.human_provider import HumanProvider

__all__ = [
    "OpenAILLM",
    "AnthropicLLM",
    "HumanProvider",
]
