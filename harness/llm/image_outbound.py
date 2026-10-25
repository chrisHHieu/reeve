"""Images already in the history, fitted again to the route they are about to go to.

`images.py` fits an image where it is born; this fits it where it leaves, outbound only
(`harness/loop/outbound.py`, `_adapt_images`).
"""

from __future__ import annotations

from typing import Any, Optional

from .images import fit_attach_parts, limits_for


#: Entries kept by `fit_outbound`'s cache before the oldest is dropped. A session holds a
#: handful of images; the bound only stops a pathological one from growing without end.
_OUTBOUND_CACHE_MAX = 64


def fit_outbound(
    messages: list[dict[str, Any]],
    *,
    model: str,
    provider: Any = None,
    cache: Optional[dict[Any, list[Any]]] = None,
) -> list[dict[str, Any]]:
    """Every image part already in the history, fitted to the route it is about to go to.

    The two birth points fit an image for the model of THAT moment, and an image never
    fitted (born before a route had a ceiling, or under a model that took it whole) stays in
    the saved history and is resent on every request. One such image refused every later
    request of session `50a81a82-280` (2026-10-16) — the session could not be recovered
    without editing its file. Fitting again here, outbound only like the other stages,
    heals that session and makes a switch onto a tighter model safe; the saved history is
    not touched.

    The result is deterministic for one (image, limits), so a provider's prefix cache sees
    the same bytes every request; `cache` (the engine's) keeps the decode to once per image.
    """
    limits = limits_for(model, provider=provider)
    out: list[dict[str, Any]] = []
    changed = False
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list) or not any(
            isinstance(p, dict) and p.get("type") == "image_url" for p in content
        ):
            out.append(message)
            continue
        parts: list[Any] = []
        touched = False
        for part in content:
            holder = part.get("image_url") if isinstance(part, dict) and part.get("type") == "image_url" else None
            url = holder.get("url") if isinstance(holder, dict) else None
            if not (isinstance(url, str) and url.startswith("data:")):
                parts.append(part)
                continue
            key = (url, limits)
            fitted = cache.get(key) if cache is not None else None
            if fitted is None:
                fitted, _ = fit_attach_parts([part], model=model, provider=_Fixed(limits))
                if cache is not None:
                    if len(cache) >= _OUTBOUND_CACHE_MAX:
                        cache.pop(next(iter(cache)))
                    cache[key] = fitted
            if len(fitted) != 1 or fitted[0] is not part:
                touched = True
            parts.extend(fitted)
        if touched:
            changed = True
            out.append({**message, "content": parts})
        else:
            out.append(message)
    return out if changed else messages


class _Fixed:
    """Limits already decided, handed to `fit_attach_parts` so they are not asked twice."""

    def __init__(self, limits: tuple[int, Optional[int]]):
        self._limits = limits

    def image_limits(self, model: str) -> tuple[int, Optional[int]]:
        return self._limits


__all__ = ["fit_outbound"]
