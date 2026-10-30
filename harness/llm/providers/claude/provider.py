"""Anthropic's Messages API as a provider.

The loop keeps its history in OpenAI-shaped messages. This provider converts a request into
the Messages form (`convert.py`, `request.py`), sends it, and reads the reply back into an
`AssistantTurn` (`turns.py` for a whole reply, `stream.py` for a streamed one).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from ..base import AssistantTurn, ModelCapabilities, ProviderClient
from ..capabilities import capabilities_for
from ..timeouts import stream_timeout
from ..turns import CACHE_AFFINITY, session_headers
from . import binding, context_editing, diagnostics, refusal
from .context_editing import normalize_context_editing
from ..endpoints import normalize_base_url
from .credentials import resolve_api_key, resolve_base_url
from .request import build_request
from .stream import read_stream
from .turns import turn_from_response

logger = logging.getLogger("harness.llm.providers.anthropic")

#: How many stale thinking blocks a provider remembers having reported (`_reported_stale`).
_REPORTED = 4096


def _conversation(settings: dict[str, Any]) -> Optional[str]:
    """The session's routing key, which names one conversation for its cache diagnostics."""
    key = settings.get(CACHE_AFFINITY)
    return str(key) if key else None


class AnthropicProvider(ProviderClient):
    def __init__(
        self,
        client: Any = None,
        *,
        default_model: str = "claude-sonnet-4-6",
        api_key: Optional[str] = None,
        secrets: Any = None,
        thinking_budget: Optional[int] = None,
        cache_ttl: Optional[str] = None,
        context_editing: Optional[dict[str, Any]] = None,
        base_url: Optional[str] = None,
        default_headers: Optional[dict[str, str]] = None,
        session_header: Optional[str] = None,
    ):
        """The SDK client is made on first use, so a loop can be assembled before any key
        exists; the key is looked up then (argument, environment, secret store). Tests, and
        providers that speak this wire through their own SDK client, pass `client`.

        `thinking_budget` comes from the provider profile and turns on extended thinking.
        `cache_ttl` ("1h") moves both cache breakpoints off the 5-minute default for sessions
        whose turns are further apart. `context_editing` is the `[context_editing]` table; it
        is for the native endpoint only, so an injected client (Vertex, Bedrock, a gateway)
        stays out of it, since those wires do not all carry the beta. `base_url` points at a
        proxy that speaks the Messages API and is ignored when a client is injected.
        `default_headers` are static headers a gateway demands; `session_header` names the one whose
        value is the session id, so a gateway routes one conversation consistently (as on the chat wire).
        """
        self._client = client
        self._api_key = api_key
        self._secrets = secrets
        self._base_url = base_url
        self._default_headers = dict(default_headers or {})
        self._session_header = session_header
        self._session_id = uuid.uuid4().hex
        self.default_model = default_model
        self.thinking_budget = thinking_budget or 0
        self.cache_ttl = cache_ttl
        self.context_editing = normalize_context_editing(context_editing)
        # One-way latches. The first 400 naming context editing switches it off for the
        # process, so a gateway without the beta pays to find out once. A 400 naming only the
        # thinking half drops that half: a model without thinking keeps the tool-result
        # clearing it does support.
        self._context_editing_disabled = False
        self._clear_thinking_disabled = False
        # The same latch for the thinking-binding controls: a cloud or gateway that
        # refuses them gets the request without them from then on.
        self._binding_disabled = False
        # Thinking blocks a 400 refused once. They are left out of every later request, where they
        # would be refused again and cost a second round trip each time.
        self._refused_blocks: set[str] = set()
        # Stale blocks already reported. A block that went stale stays in the history and is left
        # out of every request after, so the step that first went without it is the loss; the
        # 2026-10-26 store counted one session's 3-13 blocks once a step, 314 times. Kept by key
        # (insertion-ordered, the oldest let go past `_REPORTED`); a restart forgets it, and the
        # first request after one reports its session's stale blocks once more.
        self._reported_stale: dict[str, None] = {}
        # The betas a route sends on every call through its client's default header (the
        # subscription's OAuth one). The SDK sends `betas` as the request's own header, which
        # replaces the default, so a beta call has to name them again.
        standing = next((v for k, v in self._default_headers.items() if k.lower() == "anthropic-beta"), "")
        self._standing_betas = [beta.strip() for beta in standing.split(",") if beta.strip()]
        # Cache diagnostics, on the Claude API only: a client someone else built is a
        # cloud or a gateway (Bedrock and Google's Claude do not take the field), and so is a base
        # URL that is not Anthropic's. A 400 about it latches it off like the betas above.
        self._diagnostics = client is None and (not base_url or "api.anthropic.com" in base_url)
        # The last response id of each conversation, keyed by the session's routing key.
        self._last_response: dict[str, str] = {}

    # -- client ---------------------------------------------------------------------------

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        from anthropic import Anthropic
        import anthropic as sdk

        key = self._api_key or resolve_api_key(self._secrets)
        if not key:
            raise RuntimeError(
                "No Anthropic API key configured. Set ANTHROPIC_API_KEY in the environment, "
                "or add your key in Manage → Configure Models."
            )
        options: dict[str, Any] = {}
        base_url = normalize_base_url(self._base_url) or resolve_base_url(self._secrets)
        if base_url:  # never pass None: it would hide the SDK's own ANTHROPIC_BASE_URL lookup
            options["base_url"] = base_url
        if self._default_headers:
            options["default_headers"] = self._default_headers
        # One attempt per call: the loop's retry policy is the only one. The idle read timeout
        # turns a stalled stream into a failure the loop can retry.
        options["max_retries"] = 0
        options["timeout"] = stream_timeout(sdk)
        self._client = Anthropic(api_key=key, **options)
        return self._client

    # -- sending --------------------------------------------------------------------------

    def _request_kwargs(self, *, model, messages, tools, settings) -> dict[str, Any]:
        routing = session_headers(self._session_header, settings, self._session_id)
        kwargs = build_request(
            model=model,
            messages=messages,
            tools=tools,
            settings=settings,
            thinking_budget=self.thinking_budget,
            cache_ttl=self.cache_ttl,
        )
        if routing:
            kwargs["extra_headers"] = routing
        return kwargs

    def _post(self, client: Any, kwargs: dict[str, Any], betas: list[str], extra: dict[str, Any]) -> Any:
        """The plain endpoint, or the beta one when something on the request needs a beta."""
        if betas:
            sent = list(dict.fromkeys([*self._standing_betas, *betas]))
            return client.beta.messages.create(**kwargs, betas=sent, **extra)
        return client.messages.create(**kwargs)

    def _features(
        self, kwargs: dict[str, Any], model: str, conversation: Optional[str] = None
    ) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
        """The call with what this provider adds to it: the refusal fallback, context editing, the
        thinking-binding controls (each with its beta) and the cache diagnostics, minus what a latch
        has turned off. `conversation` is the session's routing key; a call with none (a pass no
        session owns) asks for no diagnostics, since it has no previous response to compare with."""
        edits: list[dict[str, Any]] = []
        if self.context_editing and not self._context_editing_disabled:
            edits = context_editing.context_editing_edits(self.context_editing)
            if self._clear_thinking_disabled:
                edits = [edit for edit in edits if edit["type"] != context_editing.THINKING_EDIT]

        betas: list[str] = []
        extra: dict[str, Any] = {}
        if refusal.needs_fallback(model):
            betas.append(refusal.BETA)
            extra["fallbacks"] = [{"model": refusal.FALLBACK_MODEL}]
        call = dict(kwargs)
        if edits:
            betas.append(context_editing.BETA)
            call["context_management"] = {"edits": edits}
        if not self._binding_disabled and binding.wants_controls(call):
            # A block the digest could not see go stale (a gateway's own rewrite, say) is dropped by
            # the API and reported, and the turn goes on.
            betas.append(binding.BETA)
            call["thinking"] = binding.with_drop(call["thinking"])
        if self._diagnostics and conversation:
            asked = diagnostics.request(self._last_response.get(conversation))
            call["extra_body"] = {**(call.get("extra_body") or {}), diagnostics.FIELD: asked}
        return call, betas, extra

    def _latch(self, exc: Exception, call: dict[str, Any], model: str) -> bool:
        """Turn off, for the process, the feature a 400 says the model or gateway does not serve;
        whether one was. An optimisation must not cost a turn."""
        if diagnostics.FIELD in (call.get("extra_body") or {}) and diagnostics.is_rejection(exc):
            self._diagnostics = False
            logger.warning("anthropic: %s rejected cache diagnostics (%s) — sending without them", model, exc)
            return True
        if "block_binding" in (call.get("thinking") or {}) and binding.is_controls_rejection(exc):
            self._binding_disabled = True
            logger.warning("anthropic: %s rejected the thinking-binding controls (%s) — sending without them", model, exc)
            return True
        edits = (call.get("context_management") or {}).get("edits") or []
        if not edits or not context_editing.is_rejection(exc):
            return False
        keeps_tool_edit = any(edit["type"] == context_editing.TOOL_EDIT for edit in edits)
        thinking_edit = any(edit["type"] == context_editing.THINKING_EDIT for edit in edits)
        if "clear_thinking" in str(exc).lower() and keeps_tool_edit and thinking_edit:
            self._clear_thinking_disabled = True
            logger.warning(
                "anthropic: %s does not support thinking-block clearing (%s) — keeping tool-result clearing only",
                model,
                exc,
            )
            return True
        self._context_editing_disabled = True
        logger.warning(
            "anthropic: %s rejected context editing (%s) — disabling it for this "
            "process; set [context_editing] enabled = false to silence this",
            model,
            exc,
        )
        return True

    def _create(
        self, client: Any, kwargs: dict[str, Any], *, model: str, conversation: Optional[str] = None
    ) -> tuple[Any, dict[str, Any], dict[str, int]]:
        """One Messages call: the reply, the request as it was sent, and the thinking it went
        without.

        A 400 that names a feature this provider added latches that feature off and sends again
        (`_latch`). A 400 that refuses a replayed thinking signature is answered once by sending
        the conversation with no thinking at all: the reply loses that reasoning, the turn goes on,
        and those blocks are never sent again."""
        dropped: dict[str, int] = {}
        retried = False
        while True:
            call, betas, extra = self._features(kwargs, model, conversation)
            try:
                return self._post(client, call, betas, extra), call, dropped
            except Exception as exc:  # noqa: BLE001 — anything not answered below is re-raised
                if self._latch(exc, call, model):
                    continue
                if not retried and binding.is_signature_refusal(exc):
                    removed = binding.strip_thinking(kwargs)
                    if removed:
                        retried = True
                        self._refused_blocks.update(removed)
                        dropped["retried"] = len(removed)
                        logger.warning(
                            "anthropic: %s refused %d replayed thinking block(s) (%s) — sending again without thinking",
                            model,
                            len(removed),
                            exc,
                        )
                        continue
                raise

    def _prepared(self, *, model, messages, tools, settings) -> tuple[dict[str, Any], list[str]]:
        """The request, with the thinking blocks it can still carry (`binding.keep_bound`), and the
        keys of those it left out."""
        kwargs = self._request_kwargs(model=model, messages=messages, tools=tools, settings=settings)
        return kwargs, binding.keep_bound(kwargs, frozenset(self._refused_blocks))

    def _finished(
        self,
        turn: AssistantTurn,
        sent: dict[str, Any],
        stale: list[str],
        dropped: dict[str, int],
        conversation: Optional[str] = None,
    ) -> AssistantTurn:
        """The turn with the prefix its thinking was written after, kept for the next request, the
        thinking this request went without (`stale`, and `stale_new` for the ones no earlier request
        went without), and what the cache diagnostics said, for the event log. The response's id
        becomes the conversation's previous one."""
        sidecar = turn.extras.get("_anthropic") if turn.extras else None
        if isinstance(sidecar, dict):
            sidecar["prefix"] = binding.prefixes(sent)[-1]
        counts = {**(turn.thinking_dropped or {}), **dropped}
        if stale:
            new = [key for key in stale if key not in self._reported_stale]
            for key in new:
                self._reported_stale[key] = None
            while len(self._reported_stale) > _REPORTED:
                self._reported_stale.pop(next(iter(self._reported_stale)))
            counts.update(stale=len(stale), stale_new=len(new))
        turn.thinking_dropped = counts or None
        asked = (sent.get("extra_body") or {}).get(diagnostics.FIELD)
        if asked is None:
            turn.cache_miss = None
            return turn
        turn.cache_miss = diagnostics.outcome(turn.cache_miss, compared=bool(asked.get("previous_message_id")))
        if conversation and turn.response_id:
            self._last_response.pop(conversation, None)
            self._last_response[conversation] = turn.response_id
            while len(self._last_response) > diagnostics.REMEMBERED:
                self._last_response.pop(next(iter(self._last_response)))
        return turn

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **settings: Any,
    ) -> AssistantTurn:
        kwargs, stale = self._prepared(model=model, messages=messages, tools=tools, settings=settings)
        conversation = _conversation(settings)
        response, sent, dropped = self._create(self._ensure_client(), kwargs, model=model, conversation=conversation)
        turn = turn_from_response(response)
        turn.context_cleared = context_editing.cleared(response)
        return self._finished(turn, sent, stale, dropped, conversation)

    def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **settings: Any,
    ):
        kwargs, stale = self._prepared(model=model, messages=messages, tools=tools, settings=settings)
        kwargs["stream"] = True
        conversation = _conversation(settings)
        events, sent, dropped = self._create(self._ensure_client(), kwargs, model=model, conversation=conversation)
        for chunk in read_stream(events):
            if chunk.turn is not None:
                self._finished(chunk.turn, sent, stale, dropped, conversation)
            yield chunk

    # -- what the loop asks about the provider ---------------------------------------------

    def capabilities(self, model: str) -> ModelCapabilities:
        return capabilities_for(model)

    def cache_ttl_seconds(self, model: str) -> Optional[float]:
        """How long the prefix cache written here lives: five minutes, or an hour on the 1h
        tier. The loop's opportunistic pruning reads it to tell a warm cache from a cold one;
        breakpoints go on every request, so there is always a cache to weigh."""
        return 3600.0 if (self.cache_ttl or "").strip().lower() == "1h" else 300.0

    def image_limits(self, model: str) -> tuple[int, Optional[int]]:
        """(bytes, pixels on a side) the API accepts. Below its ~5 MB so the base64 envelope
        and request overhead still fit."""
        return (4_500_000, 8_000)
