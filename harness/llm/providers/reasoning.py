"""Which route wants the model's own thinking echoed back on the wire.

Measured on `zai-coding-plan:glm-5.3-flash` (2026-09-23): a tool-call turn in thinking
mode 400s with "The `reasoning_content` in the thinking mode must be passed back to the
API." — Reeve stripped reasoning from every outbound message, so the requirement could
never be met. The same rule is kept per vendor (the models.dev
`interleaved.field` data and per-vendor compat flags); the
differences that matter here:

- The thinking-mode families (DeepSeek, Kimi/Moonshot, Z.AI/GLM, Xiaomi MiMo) require
  `reasoning_content` back on assistant turns — the official DeepSeek rule names
  tool-call turns, the others accept it everywhere.
- Aggregators are the opposite: OpenRouter carries `reasoning_details` and rejects a
  hand-rolled `reasoning_content`, so they are excluded by host (the official
  OpenRouter SDK from the manual echo for the same reason).
- A tool-call assistant with no reasoning gets a NON-EMPTY placeholder: an empty string
  is itself a 400 on the non-official DeepSeek-compatible relays (a pad of
  with a single space).

`reasoning` is Reeve's canonical field (every provider parses its thinking into it);
`reasoning_content` is the OpenAI-compatible wire spelling. Anthropic thinking is
separate and already replayed verbatim with signatures by its own provider.
"""

from __future__ import annotations

#: Model-id markers of the families whose thinking-mode endpoints require passback.
ECHO_FAMILIES = (("deepseek",), ("kimi", "moonshot"), ("glm", "zhipu"), ("mimo",))

#: Hosts that must NOT receive a hand-rolled `reasoning_content` (they carry their own
#: reasoning-details channel and reject the field).
EXCLUDED_HOSTS = ("openrouter.ai",)

#: What a tool-call turn with no retained reasoning carries instead of an empty string.
REASONING_PLACEHOLDER = "[reasoning not retained for this turn]"

#: The OpenAI-compatible wire spelling.
ECHO_FIELD = "reasoning_content"


def reasoning_echo_field(model: str, base_url: str | None = None) -> str | None:
    """`"reasoning_content"` when this route requires thinking passback, else None.

    Keyed on the BARE model id (the router strips the `provider:` prefix before the
    client sees it) plus the endpoint host for the aggregator exclusion.
    """
    haystack = (model or "").lower()
    if not any(marker in haystack for family in ECHO_FAMILIES for marker in family):
        return None
    host = (base_url or "").lower()
    if any(bad in host for bad in EXCLUDED_HOSTS):
        return None
    return ECHO_FIELD
