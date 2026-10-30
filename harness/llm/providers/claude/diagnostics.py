"""Cache diagnostics: the API says where a request stopped matching the one before it.

A request names the previous response of its conversation (`diagnostics.previous_message_id`),
and the response says where the two diverged: the model, the system prompt, the tools, or the
messages. If they did not diverge, the field is null, which means an entry the request should have
hit was gone on the provider's side. The comparison is on the request's structure, so it tells a
prefix the harness changed from a cache the provider dropped, which the token counts alone cannot.
On the store (2026-10-17..26), 2.3M of Sonnet 5.5's input priced in full came on requests under a
minute after the previous one, with nothing the harness logged in between.

The Claude API only: Bedrock and Google's Claude do not take the field. The API stores hashes and
token estimates, never the text, and charges nothing for it.
"""

from __future__ import annotations

from typing import Any, Optional

#: The request field, sent through the SDK's `extra_body` so an SDK that does not name it yet
#: still sends it.
FIELD = "diagnostics"
#: The reasons that say the request itself changed, earliest point first.
CHANGED = frozenset({"model_changed", "system_changed", "tools_changed", "messages_changed"})
#: The previous response did not diverge from this request: a miss after it was the provider's.
UNCHANGED = "unchanged"
#: How many conversations a provider remembers the last response of.
REMEMBERED = 512


def request(previous: Optional[str]) -> dict[str, Any]:
    """The field for a request; `previous` is None on a conversation's first one, which opts in."""
    return {"previous_message_id": previous}


def _get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def read(message: Any) -> Optional[dict[str, Any]]:
    """A response's `diagnostics`, as `{type, tokens?}`; None when it carries none.

    `pending` is a comparison still running when the response was written: inconclusive."""
    diagnostics = _get(message, FIELD)
    if diagnostics is None:
        return None
    reason = _get(diagnostics, "cache_miss_reason")
    if reason is None:
        return {"type": "pending"}
    out: dict[str, Any] = {"type": str(_get(reason, "type") or "unknown")}
    tokens = _get(reason, "cache_missed_input_tokens")
    if isinstance(tokens, (int, float)) and not isinstance(tokens, bool):
        out["tokens"] = int(tokens)
    return out


def outcome(found: Optional[dict[str, Any]], *, compared: bool) -> Optional[dict[str, Any]]:
    """What the log keeps: nothing when no previous response was named (no comparison was asked
    for), and `unchanged` when one was and the response says nothing diverged."""
    if not compared:
        return None
    return found or {"type": UNCHANGED}


def is_rejection(exc: Exception) -> bool:
    """A 400 about the field: a proxy or an endpoint that does not take it."""
    text = str(exc).lower()
    return FIELD in text and any(word in text for word in ("400", "invalid", "unknown", "extra", "not permitted"))


__all__ = ["CHANGED", "FIELD", "REMEMBERED", "UNCHANGED", "is_rejection", "outcome", "read", "request"]
