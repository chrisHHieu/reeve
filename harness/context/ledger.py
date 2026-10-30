"""The context ledger: what the model's own context is made of, shown to it.

"LLM Agents Are Latent Context Managers" (arXiv 2606.30005, VISTA) found that models judge the
size of their own context badly (a median relative error of 0.43-0.84 without help) and manage
it far better when shown a ledger of its blocks: on LOCA-Bench, taking the ledger away cost 13
points and nearly doubled the tokens. Reeve shows a total once per ten-percent band and asks for a
fold at work boundaries. On the store (2026-10-14..26), 122 of 135 folds followed such a hint, and
one route answered 6 of 115.

This module is pure: messages in, the ledger's text and its numbers out. What is heavy is
measured the way the request is priced (`estimate_tokens` over the message as sent), and how
old it is is counted in the model's own steps since the result arrived. The engine decides
when to show it (`loop/context_ledger.py`).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .compaction.tokens import estimate_tokens

#: How many of the heaviest results are named.
TOP = 4
#: How much of a call's argument names it in the ledger.
LABEL_CHARS = 48
_ARGUMENT_KEYS = ("path", "file_path", "command", "pattern", "query", "url", "name", "code")


def _label(name: str, arguments: Any) -> str:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {}
    value = next((arguments.get(key) for key in _ARGUMENT_KEYS if isinstance(arguments, dict) and arguments.get(key)), "")
    text = " ".join(str(value).split())
    if len(text) > LABEL_CHARS:
        text = text[: LABEL_CHARS - 1] + "…"
    return f"{name} {text}".strip()


def heaviest(messages: list[dict[str, Any]], start: int, *, top: int = TOP) -> list[dict[str, Any]]:
    """The heaviest tool results from `messages[start:]`, each `{label, tokens, age}`.

    `age` is how many of the model's steps came after the result; the steps since are what the
    model has already acted on it."""
    calls: dict[str, tuple[str, Any]] = {}
    found: list[tuple[int, int, str]] = []  # (tokens, assistant index, label)
    steps = 0
    for message in messages[start:]:
        role = message.get("role")
        if role == "assistant":
            steps += 1
            for call in message.get("tool_calls") or []:
                fn = call.get("function") or {}
                calls[str(call.get("id") or "")] = (str(fn.get("name") or "tool"), fn.get("arguments"))
        elif role == "tool":
            name, arguments = calls.get(str(message.get("tool_call_id") or ""), ("tool", {}))
            found.append((estimate_tokens([message]), steps, _label(name, arguments)))
    found.sort(key=lambda item: -item[0])
    return [{"label": label, "tokens": tokens, "age": steps - at} for tokens, at, label in found[:top]]


def _k(value: int) -> str:
    return f"{value / 1000:.1f}k" if value < 10_000 else f"{value / 1000:.0f}k"


def render(*, used: int, trigger: int, foldable: int, items: list[dict[str, Any]]) -> str:
    """The ledger as the model reads it: facts, no instruction to fold."""
    lines = [
        f"context: ~{_k(used)} of ~{_k(trigger)} tokens before compaction; ~{_k(foldable)} of it is "
        "older than your recent steps and is what fold_context would replace.",
    ]
    if items:
        lines.append("heaviest in view: " + " · ".join(
            f"{item['label']} ~{_k(int(item['tokens']))}, "
            + ("this step" if not item["age"] else f"{item['age']} step{'' if item['age'] == 1 else 's'} ago")
            for item in items
        ))
    return "<context-ledger>\n" + "\n".join(lines) + "\n</context-ledger>"


def build(
    messages: list[dict[str, Any]], start: int, *, used: int, trigger: int, foldable: int
) -> Optional[tuple[str, dict[str, Any]]]:
    """`(text, row)`: the ledger and the numbers its log row keeps, which rebuild the text."""
    if trigger <= 0:
        return None
    items = heaviest(messages, start)
    row = {"used": used, "trigger": trigger, "foldable": foldable, "items": items}
    return render(used=used, trigger=trigger, foldable=foldable, items=items), row


__all__ = ["LABEL_CHARS", "TOP", "build", "heaviest", "render"]
