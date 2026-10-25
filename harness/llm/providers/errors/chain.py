"""Finding the real failure behind a wrapper exception."""

from __future__ import annotations


def deepest_error(exc: BaseException, *, depth: int = 12) -> BaseException:
    """The innermost `__cause__` or `__context__`, bounded. A wrapper's message hides the real one:
    the OpenAI SDK reports `APIConnectionError("Connection error.")` around the `ConnectError` that
    says which host refused and why."""
    seen: set[int] = set()
    current = exc
    for _ in range(depth):
        inner = current.__cause__ or current.__context__
        if inner is None or id(inner) in seen:
            break
        seen.add(id(inner))
        current = inner
    return current


def error_chain(exc: BaseException, *, limit: int = 500) -> str:
    """`str(exc)`, plus the innermost cause when a wrapper hides it: the string a log row should
    carry, so the next reader sees the real failure. Bounded like every log string."""
    deepest = deepest_error(exc)
    text = str(exc)
    if deepest is not exc and str(deepest):
        text = f"{text} (cause: {type(deepest).__name__}: {deepest})"
    return text[:limit]
