"""OffTurnMixin: metering the model calls that happen outside any turn."""

from __future__ import annotations

from typing import Any

from harness.llm.providers.metered import MeteredProvider


class OffTurnMixin:
    def _off_turn_provider(self, provider: Any, purpose: str) -> Any:
        """`provider`, with its usage recorded in the rollup under the sentinel `__<purpose>__`.

        Titles and review assists call the model away from any turn, so no `turn/end` counts
        them, and until they were metered the spend left no number anywhere. The sentinel keeps
        it out of every real session's fold, which rewrites rows by session id.
        """

        def record(model: str, usage: Any) -> None:
            if usage is None:
                return
            self.metrics_store.record_model_usage(
                f"__{purpose}__",
                model=model,
                tokens_in=int(getattr(usage, "input", 0) or 0),
                tokens_out=int(getattr(usage, "output", 0) or 0),
                cache_read=int(getattr(usage, "cache_read", 0) or 0),
                cache_write=int(getattr(usage, "cache_write", 0) or 0),
            )

        return MeteredProvider(provider, record)
