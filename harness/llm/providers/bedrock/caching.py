"""Prompt caching on the Converse API.

A Converse cache point is a block of its own, and everything before it in the same list is the
cached prefix. (Anthropic's `cache_control` is the reverse: an attribute on the block it
caches.)
"""

from __future__ import annotations

from typing import Any

CACHE_POINT = {"cachePoint": {"type": "default"}}


def add_cache_points(kwargs: dict[str, Any]) -> None:
    """Ask for caching with the same two breakpoints the native Anthropic path uses: after the
    last system block, and after the last content block of the final message, so each request
    reads earlier turns from cache and writes only the new tail.

    The response counters for cache reads and writes were being read for a long time while no
    cache was ever requested, so they were always zero and looked like a cache that did not
    work.
    """
    system = kwargs.get("system")
    if isinstance(system, list) and system:
        kwargs["system"] = [*system, CACHE_POINT]
    messages = kwargs.get("messages") or []
    if messages:
        content = messages[-1].get("content")
        if isinstance(content, list) and content:
            # A new message, not an edit: a cache point left in a structure the caller still
            # owns would be sent again on the next request.
            messages[-1] = {**messages[-1], "content": [*content, CACHE_POINT]}


def strip_cache_points(kwargs: dict[str, Any]) -> bool:
    """Take every cache point out. True when there was one."""
    found = False
    system = kwargs.get("system")
    if isinstance(system, list) and any("cachePoint" in block for block in system):
        kwargs["system"] = [block for block in system if "cachePoint" not in block]
        found = True
    for index, message in enumerate(kwargs.get("messages") or []):
        content = message.get("content")
        if isinstance(content, list) and any("cachePoint" in block for block in content):
            kwargs["messages"][index] = {
                **message,
                "content": [block for block in content if "cachePoint" not in block],
            }
            found = True
    return found


def rejects_cache_points(exc: BaseException) -> bool:
    """Whether a failure is the service saying the model does not do prompt caching: a
    validation error that names the field. Anything wider would swallow real request errors
    and send them again for nothing."""
    if exc.__class__.__name__ not in ("ValidationException", "ClientError"):
        return False
    return "cachepoint" in str(exc).lower()
