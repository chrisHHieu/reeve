"""Reading provider failures: a plain sentence for access and quota problems, whether a failure is
worth retrying, how often, and what the real cause was."""

from __future__ import annotations

from .access import friendly_model_error
from .chain import deepest_error, error_chain
from .failures import EmptyCompletion, StreamStalled
from .transient import classify_stream_error, is_transient, media_rejected, retry_budget, unexplained_refusal
from .waiting import RETRY_AFTER_MAX_WAIT_SECONDS, retry_after_seconds

__all__ = [
    "RETRY_AFTER_MAX_WAIT_SECONDS",
    "EmptyCompletion",
    "StreamStalled",
    "classify_stream_error",
    "deepest_error",
    "error_chain",
    "friendly_model_error",
    "is_transient",
    "media_rejected",
    "retry_after_seconds",
    "retry_budget",
    "unexplained_refusal",
]
