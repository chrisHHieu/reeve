"""Extended thinking for the Messages API, and the output ceiling it has to fit under.

The request shape depends on the model family:

- Older models (Haiku 4.5, Sonnet 4.5, Opus 4.5 and before) take an explicit token budget:
  `{"type": "enabled", "budget_tokens": N}`.
- 4.6 and later, and the Claude 5 family, reject a budget (a hard 400 from 4.7 on) and take
  `{"type": "adaptive"}`. Those models also return empty thinking text unless
  `display: "summarized"` is asked for, so the trace shown in the app needs it.

Thinking is on by default. There is no user-facing switch; the provider profile's
`thinking_budget` is a hidden override where a number replaces the default and 0 turns it off.
"""

from __future__ import annotations

from typing import Any

#: Required by the API and meant as a ceiling, not a target. Sized for a whole file written
#: inside one tool call's arguments: at 16k such a call was cut off mid-argument and the write
#: failed.
DEFAULT_MAX_TOKENS = 32000

DEFAULT_THINKING_BUDGET = 8192

_BUDGETED_FAMILIES = (
    "claude-haiku-4-5",
    "claude-sonnet-4-5",
    "claude-opus-4-5",
    "claude-opus-4-1",
    "claude-opus-4-0",
    "claude-sonnet-4-0",
    "claude-3",
    "claude-2",
)

#: Sampling controls the API refuses next to thinking (temperature has to stay at 1).
_SAMPLING_KEYS = ("temperature", "top_p", "top_k")


def uses_budget_thinking(model: str) -> bool:
    """Whether `model` is asked for thinking with a token budget rather than adaptively."""
    return model.startswith(_BUDGETED_FAMILIES)


def apply_thinking(settings: dict[str, Any], model: str, budget: int) -> None:
    """Fit thinking into the request settings, in place.

    Adds the thinking parameter when `budget` is positive and the caller set none, makes room
    for a budget under `max_tokens`, and drops the sampling controls thinking forbids.
    """
    if budget > 0 and "thinking" not in settings:
        if uses_budget_thinking(model):
            settings["thinking"] = {"type": "enabled", "budget_tokens": budget}
        else:
            settings["thinking"] = {"type": "adaptive", "display": "summarized"}

    mode = (settings.get("thinking") or {}).get("type")
    if mode == "enabled":
        spend = int(settings["thinking"].get("budget_tokens") or 0)
        if int(settings.get("max_tokens") or 0) <= spend:
            settings["max_tokens"] = max(DEFAULT_MAX_TOKENS, spend + 4096)
    if mode in ("enabled", "adaptive"):
        for key in _SAMPLING_KEYS:
            settings.pop(key, None)
