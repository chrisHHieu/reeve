"""A provider proxy that reports every call's usage — for model calls outside a turn.

A turn's own calls are accounted by the engine (`turn/end` tokens, each explained by a
logged event). Work that calls the model on its own, away from a turn (session titles and
review assists today; the removed background memory passes were the first), had no
accounting at all: its calls reached the provider and left no number anywhere. Wrapping the provider it is handed is the one seam
that covers every such caller without each module growing its own bookkeeping.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

UsageSink = Callable[[str, Optional[Any]], None]


class MeteredProvider:
    """Delegates everything to `inner`; after each `complete`, hands `(model, usage)` to
    `sink`. A sink that raises is logged and ignored — accounting never fails a call."""

    def __init__(self, inner: Any, sink: UsageSink) -> None:
        self._inner = inner
        self._sink = sink

    def complete(self, *, model: str, messages: list, tools: Any = None, **settings: Any) -> Any:
        turn = self._inner.complete(model=model, messages=messages, tools=tools, **settings)
        try:
            self._sink(model, getattr(turn, "usage", None))
        except Exception:  # noqa: BLE001 — see the class docstring
            logger.debug("usage sink failed", exc_info=True)
        return turn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
