"""Translate a stored ``LlmProfile`` row into a vendored-core LLM context.

The runner reads ``llm_profile_id`` from a simulation's config snapshot,
looks up the encrypted profile, decrypts the API key with the app's Fernet
key, and builds a :class:`core.context.Context` whose ``config.llm`` points
at the chosen provider/model. That Context is handed to ``StanfordTown`` so
every Action's ``self.llm`` resolves to the user-selected provider instead
of whatever ambient ``~/.metagpt/config2.yaml`` happens to define.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

# Profile.provider (our enum) -> vendored LLMType value string.
_PROVIDER_TO_LLM_TYPE = {
    "openai": "openai",
    "deepseek": "deepseek",
    "anthropic": "anthropic",
}


def build_llm_config(profile: Any, decrypted_key: str) -> Any:
    """Build a vendored ``LLMConfig`` from an ``LlmProfile`` row + plaintext key."""
    from core.config.llm_config import LLMConfig, LLMType

    api_type = LLMType(_PROVIDER_TO_LLM_TYPE.get(profile.provider, "openai"))
    fields: dict[str, Any] = {
        "api_key": decrypted_key,
        "api_type": api_type,
        "model": profile.model,
    }
    if profile.base_url:
        fields["base_url"] = profile.base_url
    if profile.max_tokens:
        fields["max_token"] = profile.max_tokens
    if profile.temperature is not None:
        fields["temperature"] = profile.temperature
    return LLMConfig(**fields)


def build_context(profile: Any, decrypted_key: str) -> Any:
    """Build a ``Context`` whose ``config.llm`` is the chosen profile.

    Returns ``None`` is never used — callers pass the Context straight to
    ``StanfordTown(context=...)``. Raises on a genuinely broken profile so the
    run fails fast with a clear ``status=FAILED`` message rather than silently
    falling back to ambient config.
    """
    from core.config.config2 import Config
    from core.context import Context

    llm_config = build_llm_config(profile, decrypted_key)
    config = Config.from_llm_config(llm_config.model_dump())
    logger.info(
        "runner.llm_config: using provider={} model={} for the simulation context",
        profile.provider,
        profile.model,
    )
    return Context(config=config)


def load_profile_context(session_factory, profile_id: int, fernet_key: bytes):
    """Look up ``profile_id``, decrypt its key, return a configured ``Context``.

    Returns ``None`` when ``profile_id`` is falsy or the profile is missing —
    the runner then falls back to ambient ``core`` config (useful in tests and
    for users who pre-configured ``~/.metagpt/config2.yaml``).
    """
    if not profile_id:
        return None

    from storage.repos.llm_profiles import LlmProfileRepo

    with session_factory() as session:
        repo = LlmProfileRepo(session, fernet_key)
        profile = repo.get(profile_id)
        if profile is None:
            logger.warning(
                "runner.llm_config: llm_profile_id={} not found — "
                "falling back to ambient config",
                profile_id,
            )
            return None
        decrypted_key = repo.get_decrypted_key(profile_id)
        return build_context(profile, decrypted_key)


__all__ = ["build_llm_config", "build_context", "load_profile_context"]
