"""`openai-codex`: OpenAI's models paid for by a ChatGPT plan instead of an API key.

The backend takes the same Responses requests as `/v1/responses`, without state (the whole history
each turn, `store: false`, encrypted reasoning carried in the `_openai` sidecar), so building
requests and reading answers come from `OpenAIResponsesProvider`. What changes is the credential,
an OAuth bearer that lives minutes (`tokens.py`), and the account, originator and session headers
the backend insists on. Three more differences:

- It only streams, so `complete()` reads a whole stream.
- A 401 means the bearer expired mid-flight: one refresh, one retry. A refresh token the server
  refuses comes out as a sign-in-required error instead of a loop.
- A 429 is the plan's usage window running out, said in words a person can act on.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from ..base import AssistantTurn
from ..responses.provider import OpenAIResponsesProvider
from .constants import CODEX_BASE_URL, PLAN_LIMIT_ERROR
from .tokens import CodexTokenStore, backend_headers

_EXPIRED, _PLAN_LIMIT = 401, 429


def _status_code(exc: Exception) -> Optional[int]:
    for holder in (exc, getattr(exc, "response", None)):
        status = getattr(holder, "status_code", None)
        if isinstance(status, int):
            return status
    return None


class CodexProvider(OpenAIResponsesProvider):
    def __init__(
        self,
        client: Any = None,
        *,
        secrets: Any = None,
        default_model: str = "gpt-5.2-codex",
        reasoning_summary: bool = True,
    ):
        super().__init__(client=client, default_model=default_model, base_url=CODEX_BASE_URL, reasoning_summary=reasoning_summary)
        self._store = CodexTokenStore(secrets)
        # The backend wants a session header per conversation. The router keeps one client per
        # provider, so in practice an instance is one conversation, and one id per instance
        # does without threading conversation ids through `ProviderClient`.
        self._session_id = str(uuid.uuid4())
        self._client_token: Optional[str] = None
        self._injected = client is not None  # a test's client is used as given

    def _ensure_client(self) -> Any:
        if self._injected:
            return self._client
        # Asked for on every call (the store refreshes it when it is close to expiring); the SDK
        # client is made again only when the token it holds is not the current one.
        token, account = self._store.access_token()
        if self._client is None or token != self._client_token:
            from openai import OpenAI

            self._client = OpenAI(api_key=token, base_url=CODEX_BASE_URL, default_headers=backend_headers(account, self._session_id))
            self._client_token = token
        return self._client

    def _request_kwargs(
        self, *, model: str, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]], settings: dict[str, Any]
    ) -> dict[str, Any]:
        request = super()._request_kwargs(model=model, messages=messages, tools=tools, settings=settings)
        effort = settings.get("reasoning_effort")
        if isinstance(effort, str) and effort:  # this backend reads it, unlike plain /v1/responses
            request["reasoning"] = {**request.get("reasoning", {}), "effort": effort}
        # A request without instructions is refused. A conversation always has a system prompt,
        # so only a bare one-off call ever needs this.
        request.setdefault("instructions", "You are a helpful assistant.")
        return request

    def _create(self, client: Any, kwargs: dict[str, Any]) -> Any:
        try:
            return super()._create(client, kwargs)
        except Exception as exc:
            status = _status_code(exc)
            if status == _EXPIRED and not self._injected:
                # One forced refresh and the same request again; a refused refresh token raises
                # `CodexSignInRequired` from the store.
                self._store.refresh()
                self._client, self._client_token = None, None
                return super()._create(self._ensure_client(), kwargs)
            if status == _PLAN_LIMIT:
                raise RuntimeError(PLAN_LIMIT_ERROR) from exc
            raise

    def complete(
        self, *, model: str, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None, **settings: Any
    ) -> AssistantTurn:
        last: Optional[AssistantTurn] = None
        for chunk in self.stream(model=model, messages=messages, tools=tools, **settings):
            last = chunk.turn if chunk.turn is not None else last
        return last if last is not None else AssistantTurn()
