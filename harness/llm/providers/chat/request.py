"""Building a chat-completions request, and repairing it when a server names a parameter it
does not take."""

from __future__ import annotations

from typing import Any, Optional

from ..base import CACHE_AFFINITY, sanitize_messages
from .media import carry_media

#: A ceiling, not a target. A report written in one tool call carries the whole file in its
#: arguments, and compatible servers left to their own limits cap a completion absurdly low
#: (one host defaulted a model to about 2k tokens, so every 5 KB write was cut off).
DEFAULT_MAX_TOKENS = 32000

# GPT-5.6 reasons at "medium" unless told otherwise, and /v1/chat/completions refuses function
# tools together with any effort but "none". Native OpenAI goes through the Responses
# provider, but the model still arrives here through a custom endpoint (Azure OpenAI speaks
# the same wire), so effort is pinned to none whenever tools ride along. If a server still
# raises that exact complaint (a later generation, an alias not listed), one retry at "none"
# gives a working turn instead of a 400.
_EFFORT_COMPLAINT = "function tools with reasoning_effort are not supported"
_MAX_TOKENS_COMPLAINT = "'max_tokens' is not supported"


def pin_reasoning_effort(kwargs: dict[str, Any]) -> None:
    if kwargs.get("tools") and str(kwargs.get("model", "")).startswith("gpt-5.6"):
        kwargs.setdefault("reasoning_effort", "none")


def without_foreign_sidecars(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages without the other providers' private keys (anything starting with an
    underscore, like Gemini's `_gemini` thought signatures). This wire, and the compatible
    servers behind it, reject message fields they do not know."""
    return [
        {key: value for key, value in message.items() if not key.startswith("_")}
        if any(key.startswith("_") for key in message)
        else message
        for message in messages
    ]


def add_cache_affinity(kwargs: dict[str, Any], affinity: Any, *, base_url: Optional[str]) -> None:
    """The loop's private affinity key as OpenAI's `prompt_cache_key`.

    It routes one session's requests to the same backend so the append-only prefix cache is
    actually hit. Only stock OpenAI knows the field; a compatible endpoint (Ollama, a gateway)
    may refuse unknown parameters, so with a `base_url` the key is dropped. The caller has
    already taken it out of `settings`, so it cannot leak under its private name.
    """
    if affinity and not base_url:
        kwargs["prompt_cache_key"] = str(affinity)[:64]


def build_request(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]],
    settings: dict[str, Any],
    base_url: Optional[str],
    streaming: bool,
) -> tuple[dict[str, Any], Any]:
    """(request kwargs, the cache-affinity value taken out of `settings`)."""
    settings = dict(settings)
    affinity = settings.pop(CACHE_AFFINITY, None)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": sanitize_messages(carry_media(without_foreign_sidecars(messages))),
    }
    if streaming:
        kwargs["stream"] = True
        # Usage arrives on the last chunk, the one with no choices. A server that does not know
        # the option gets one retry without it (`repair`).
        kwargs["stream_options"] = {"include_usage": True}
    kwargs.update(settings)
    add_cache_affinity(kwargs, affinity, base_url=base_url)
    if tools:
        kwargs["tools"] = tools
    kwargs.setdefault("max_tokens", DEFAULT_MAX_TOKENS)
    pin_reasoning_effort(kwargs)
    return kwargs, affinity


def repair(kwargs: dict[str, Any], exc: Exception) -> dict[str, Any]:
    """The request to send again after a server refused a parameter, or `exc` re-raised when
    this is not a complaint we can answer. Each fix changes exactly what the server named.

    `max_tokens` is only swapped for `max_completion_tokens` after a refusal and never up
    front: reasoning models want the new name, but compatible servers (Ollama's /v1) know only
    the old one.
    """
    message = str(exc).lower()
    if _EFFORT_COMPLAINT in message and kwargs.get("reasoning_effort") != "none":
        return {**kwargs, "reasoning_effort": "none"}
    if _MAX_TOKENS_COMPLAINT in message and "max_tokens" in kwargs:
        fixed = dict(kwargs)
        fixed["max_completion_tokens"] = fixed.pop("max_tokens")
        return fixed
    if "stream_options" in message and "stream_options" in kwargs:
        # An older compatible server: without the option only the metering is lost.
        return {key: value for key, value in kwargs.items() if key != "stream_options"}
    if ("max_tokens" in message or "max_new_tokens" in message) and "max_tokens" in kwargs:
        # The 32k default is over this model's limit. Servers word that 400 in different
        # ways, so no number is parsed: drop the parameter and take the server's own default.
        return {key: value for key, value in kwargs.items() if key != "max_tokens"}
    raise exc


def send(create: Any, kwargs: dict[str, Any]) -> Any:
    """`create(**kwargs)`, repaired up to three times: the effort, the `max_tokens` rename and
    the over-limit drop can all be needed on one call."""
    for _ in range(3):
        try:
            return create(**kwargs)
        except Exception as exc:
            kwargs = repair(kwargs, exc)
    return create(**kwargs)
