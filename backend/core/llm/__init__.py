#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM facade for backend.core.

Re-exports BaseLLM and the LLM() factory. Importing this module also
triggers provider registration via `_providers` (OpenAI + Anthropic;
DeepSeek piggybacks on the OpenAI provider).
"""
from typing import Optional

from core.llm.base_llm import BaseLLM

# Trigger @register_provider decorators for whitelisted providers.
from core.llm import _providers  # noqa: F401


def LLM(llm_config: "Optional[LLMConfig]" = None, context: "Context" = None) -> BaseLLM:  # noqa: F821
    """Get the default LLM provider.

    Args:
        llm_config: Optional explicit LLMConfig; if provided, an instance
            tied to a cost manager from the current context is returned.
        context: Optional Context to bind to; a new Context() is created
            if not supplied.
    """
    # Lazy imports to avoid circular dependency with backend.core.context.
    from core.context import Context

    ctx = context or Context()
    if llm_config is not None:
        return ctx.llm_with_cost_manager_from_llm_config(llm_config)
    return ctx.llm()


__all__ = ["BaseLLM", "LLM"]
