"""Thinking blocks the API will accept back.

On the Claude 5.5 family and Fable 5.1 a thinking block's signature binds the conversation it was
written in: the top-level `system`, the set of `tools`, and every message before it. A request that
replays a block after any of those changed is a 400 ("bound to a different conversation") on an
enforced account, and accounts created from 2026-08-31 are enforced by default. The harness changes
them by design: a compaction swaps the history for a block and keeps the tail, a prune shortens old
tool results, plan mode hides the write tools.

So the block carries the prefix it was written after, as a digest on the `_anthropic` sidecar, and a
request keeps only the blocks whose prefix is still the one it is about to send. Thinking is left
out of the digest, as the API leaves it out of the check (each block chains to the one before
instead), and so is `cache_control`, which the check ignores. A block from before this existed has
no digest and is sent as it always was.

Two backstops for what the digest cannot see (a gateway that rewrites the request, a rule the API
adds): the binding-controls beta asks the API to drop a mismatched block instead of failing, and a
400 that still names a thinking signature is answered once with every thinking block stripped.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

#: Lets a request set `thinking.block_binding` and makes each reply list what was dropped.
BETA = "thinking-binding-controls-2026-08-01"

#: The key a converted thinking block carries until `keep_bound` reads it. Never sent.
PREFIX_KEY = "_prefix"

_THINKING = ("thinking", "redacted_thinking")

#: What an answer that was only thinking says once its thinking is gone: the API refuses an empty
#: message, and the same words every time keep the prefix the same from one request to the next.
_THOUGHT_ONLY = {"type": "text", "text": "(no answer text)"}


def _filled(blocks: list[Any]) -> list[Any]:
    return blocks or [dict(_THOUGHT_ONLY)]


def _clean(value: Any) -> Any:
    """`value` without `cache_control` markers or the private prefix key, at any depth."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k not in ("cache_control", PREFIX_KEY)}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _bytes(value: Any) -> bytes:
    return json.dumps(_clean(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _system_texts(system: Any) -> list[str]:
    """The system prompt as its texts, whether it was sent as a string or as blocks."""
    if isinstance(system, str):
        return [system] if system else []
    return [str(block.get("text") or "") for block in system or [] if isinstance(block, dict)]


def _message_bytes(message: dict[str, Any]) -> bytes:
    """A message as it counts toward the prefix: its role and its blocks other than thinking."""
    content = message.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    blocks = [b for b in content or [] if not (isinstance(b, dict) and b.get("type") in _THINKING)]
    return _bytes({"role": message.get("role"), "content": blocks})


def _head(kwargs: dict[str, Any]) -> "hashlib._Hash":
    head = hashlib.sha256()
    head.update(_bytes(_system_texts(kwargs.get("system"))))
    # The tools are bound as a set, so their order is not part of it.
    tools = sorted(kwargs.get("tools") or [], key=lambda tool: str(tool.get("name") or ""))
    head.update(_bytes(tools))
    return head


def prefixes(kwargs: dict[str, Any]) -> list[str]:
    """For each message of the request, the digest of everything before it, and one more entry
    last: the digest of the whole request, which is the prefix of the reply it gets."""
    running = _head(kwargs)
    out = []
    for message in kwargs.get("messages") or []:
        out.append(running.copy().hexdigest())
        running.update(_message_bytes(message))
    out.append(running.hexdigest())
    return out


def block_key(block: dict[str, Any]) -> str:
    """A short name for one thinking block, from what makes it unique (its signature)."""
    secret = block.get("signature") if block.get("type") == "thinking" else block.get("data")
    return hashlib.sha256(str(secret or "").encode("utf-8")).hexdigest()[:32]


def keep_bound(kwargs: dict[str, Any], refused: frozenset[str] = frozenset()) -> list[str]:
    """Drop, in place, the thinking blocks the API would refuse; the keys of those dropped.

    A block is kept when its recorded prefix is the request's prefix before its message, or when it
    has none recorded (older history). `refused` names blocks a 400 already turned down.

    Each block also chains to the thinking block before it, and the API refuses a block whose
    predecessor was removed from under it. That cannot happen here: a block goes stale when
    something before it changed, which makes every later block stale too, and a block written after
    a drop was written without the dropped one, since this runs on every request."""
    messages = kwargs.get("messages") or []
    before = prefixes(kwargs)
    dropped: list[str] = []
    for index, message in enumerate(messages):
        content = message.get("content")
        if not isinstance(content, list) or not any(
            isinstance(b, dict) and b.get("type") in _THINKING for b in content
        ):
            continue
        out = []
        for block in content:
            if not (isinstance(block, dict) and block.get("type") in _THINKING):
                out.append(block)
                continue
            recorded = block.get(PREFIX_KEY)
            key = block_key(block)
            if key in refused or (recorded is not None and recorded != before[index]):
                dropped.append(key)
                continue
            out.append({k: v for k, v in block.items() if k != PREFIX_KEY})
        message["content"] = _filled(out)
    return dropped


def strip_thinking(kwargs: dict[str, Any]) -> list[str]:
    """Remove every thinking block from the request, in place; the keys of those removed."""
    removed = []
    for message in kwargs.get("messages") or []:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        kept = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in _THINKING:
                removed.append(block_key(block))
            else:
                kept.append(block)
        if len(kept) != len(content):
            message["content"] = _filled(kept)
    return removed


def wants_controls(kwargs: dict[str, Any]) -> bool:
    """Whether the request may carry `block_binding`: only beside adaptive or enabled thinking."""
    return (kwargs.get("thinking") or {}).get("type") in ("adaptive", "enabled")


def with_drop(thinking: dict[str, Any]) -> dict[str, Any]:
    return {**thinking, "block_binding": {"prefix_mismatch_behavior": "drop_block"}}


def _text(exc: Exception) -> str:
    return str(exc).lower()


def is_controls_rejection(exc: Exception) -> bool:
    """A 400 about the controls themselves (a model, cloud or gateway that does not take them), not
    about the conversation."""
    return type(exc).__name__ == "BadRequestError" and (
        "block_binding" in _text(exc) or BETA in _text(exc)
    ) and "different conversation" not in _text(exc)


def is_signature_refusal(exc: Exception) -> bool:
    """A 400 refusing a replayed thinking block: bound to another conversation, or a signature the
    API cannot read (another account or route)."""
    return type(exc).__name__ == "BadRequestError" and "signature" in _text(exc) and "thinking" in _text(exc)


def dropped_by_api(source: Any) -> dict[str, int]:
    """What the reply says the API dropped (`input_transformations`), counted by reason: `api` for a
    changed prefix, `other_model` for a block another model wrote (a switch, not a defect)."""
    entries = getattr(source, "input_transformations", None)
    if entries is None and isinstance(source, dict):
        entries = source.get("input_transformations")
    counts = {"api": 0, "other_model": 0}
    for entry in entries or []:
        get = entry.get if isinstance(entry, dict) else (lambda key, e=entry: getattr(e, key, None))
        if get("type") != "thinking_dropped":
            continue
        if get("reason") == "prefix_binding_mismatch":
            counts["api"] += 1
        elif get("reason") == "model_binding_mismatch":
            counts["other_model"] += 1
    return {key: value for key, value in counts.items() if value}
