"""Vendored stub for metagpt.exp_pool.

The upstream experience-pool/cache machinery is not exercised by the
StanfordTown simulator. We expose a no-op `exp_cache` decorator so
`backend.core.action_node` imports cleanly. If real caching is later
needed, this module can be expanded.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable


def exp_cache(*args: Any, **kwargs: Any) -> Callable:
    """No-op cache decorator factory.

    Usage in upstream: `@exp_cache(serializer=...)` — returns a decorator
    that wraps an async function. Our stub just returns the function
    unchanged regardless of args.
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*a: Any, **kw: Any) -> Any:
            return await func(*a, **kw)

        @wraps(func)
        def sync_wrapper(*a: Any, **kw: Any) -> Any:
            return func(*a, **kw)

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    # Bare-decorator usage: `@exp_cache` (no parens)
    if args and callable(args[0]) and not kwargs:
        return decorator(args[0])
    return decorator


__all__ = ["exp_cache"]
