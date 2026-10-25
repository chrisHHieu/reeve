"""Building a Responses API request, and repairing it when a server names a parameter it does
not take."""

from __future__ import annotations

import re
from typing import Any, Optional

from ..base import CACHE_AFFINITY
from ..chat.request import add_cache_affinity
from .convert import convert_messages, convert_tools

#: Settings passed through. Others (a frequency penalty, say) are dropped. `reasoning_effort`
#: is read separately because it joins the summary flag in one `reasoning` object.
_ACCEPTED_SETTINGS = {"temperature", "top_p", "max_output_tokens", "tool_choice", "parallel_tool_calls"}

# "Unsupported parameter: 'temperature' is not supported with this model." Reasoning models
# refuse sampling parameters and the others refuse `reasoning` and `include`. The server names
# one offender per error, so each retry drops exactly that one.
_UNSUPPORTED = re.compile(r"unsupported (?:parameter|value)s?:?\s*'([^']+)'")


def build_request(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]],
    settings: dict[str, Any],
    base_url: Optional[str],
    reasoning_summary: bool,
) -> dict[str, Any]:
    instructions, items = convert_messages(messages)
    settings = dict(settings)
    affinity = settings.pop(CACHE_AFFINITY, None)
    # The thinking level the person chose rides as `reasoning.effort`.
    effort = settings.pop("reasoning_effort", None)
    if "max_tokens" in settings and "max_output_tokens" not in settings:
        settings = {**settings, "max_output_tokens": settings["max_tokens"]}

    kwargs: dict[str, Any] = {
        "model": model,
        "input": items,
        # Stateless: nothing is kept on the server. The encrypted reasoning travels in the
        # `_openai` sidecar, and the summaries feed the app's thinking display.
        "store": False,
        "include": ["reasoning.encrypted_content"],
        **{key: value for key, value in settings.items() if key in _ACCEPTED_SETTINGS},
    }
    add_cache_affinity(kwargs, affinity, base_url=base_url)
    reasoning: dict[str, Any] = {}
    if reasoning_summary:
        reasoning["summary"] = "auto"
    if isinstance(effort, str) and effort:
        reasoning["effort"] = effort
    if reasoning:
        kwargs["reasoning"] = reasoning
    if instructions:
        kwargs["instructions"] = instructions
    declarations = convert_tools(tools) if tools else []
    if declarations:
        kwargs["tools"] = declarations
    return kwargs


def repair(kwargs: dict[str, Any], exc: Exception) -> dict[str, Any]:
    """The request to send again after the server refused a parameter, or `exc` re-raised when
    this is not that complaint. A dotted name (`reasoning.summary`) drops its top-level
    parameter."""
    found = _UNSUPPORTED.search(str(exc).lower())
    if found:
        name = found.group(1).split(".", 1)[0].split("[", 1)[0]
        if name in kwargs and name not in ("model", "input"):
            return {key: value for key, value in kwargs.items() if key != name}
    raise exc


def send(create: Any, kwargs: dict[str, Any]) -> Any:
    """`create(**kwargs)`, repaired up to three times: sampling parameters, `reasoning` and
    `include` can each need dropping, depending on whether the model reasons."""
    for _ in range(3):
        try:
            return create(**kwargs)
        except Exception as exc:
            kwargs = repair(kwargs, exc)
    return create(**kwargs)
