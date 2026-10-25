"""AWS Bedrock: one entry in Settings, two wires chosen by model family.

A routed id reads `bedrock:<family>/<bedrock model id>`. The router drops `bedrock:` and this
provider takes the family:

- `claude/…`: the native `AnthropicProvider` over the SDK's `AnthropicBedrock` client, so
  Claude on Bedrock gets what direct Anthropic gets (thinking, refusal handling).
- `other/…`: the Converse API (`converse.py`).

An id with no family goes to Converse as it is. Converse serves every Bedrock model, Claude
included minus the native extras, so a raw model id pasted without the add-model dropdown
still works. Credentials are described in `credentials.py`.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from ..base import AssistantTurn, ModelCapabilities, ProviderClient
from ..capabilities import capabilities_for
from ..claude import AnthropicProvider
from .converse import ConverseClient
from .credentials import narrow_to_method


class BedrockProvider(ProviderClient):
    """Splits `<family>/<model id>` and hands the request to the client for that family."""

    def __init__(
        self,
        *,
        region: Optional[str] = None,
        auth_method: Optional[str] = None,
        bedrock_api_key: Optional[str] = None,
        profile_name: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        session_token: Optional[str] = None,
        claude_client: Optional[ProviderClient] = None,
        converse_client: Optional[ProviderClient] = None,
    ):
        (self._bedrock_api_key, self._profile_name, self._access_key_id,
         self._secret_access_key, self._session_token) = narrow_to_method(
            auth_method, bedrock_api_key, profile_name, access_key_id, secret_access_key, session_token
        )
        self._region = region
        # Ready-made clients (tests) skip building an SDK client below.
        self._clients: dict[str, ProviderClient] = {}
        if claude_client is not None:
            self._clients["claude"] = claude_client
        if converse_client is not None:
            self._clients["other"] = converse_client

    @staticmethod
    def _split(model: str) -> tuple[str, str]:
        if "/" in model:
            family, rest = model.split("/", 1)
            if family in ("claude", "other"):
                return family, rest
        return "other", model

    def _claude(self) -> ProviderClient:
        from anthropic import AnthropicBedrock

        # A Bedrock API key, from the profile or the environment, takes the bearer path and
        # leaves out the signing arguments: AnthropicBedrock raises on a mix of the two.
        bearer = self._bedrock_api_key or os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        if bearer:
            sdk = AnthropicBedrock(api_key=bearer, aws_region=self._region)
        else:
            sdk = AnthropicBedrock(
                aws_region=self._region,
                aws_profile=self._profile_name,
                aws_access_key=self._access_key_id,
                aws_secret_key=self._secret_access_key,
                aws_session_token=self._session_token,
            )
        return AnthropicProvider(client=sdk)

    def _converse(self) -> ProviderClient:
        return ConverseClient(
            region=self._region,
            bedrock_api_key=self._bedrock_api_key,
            profile_name=self._profile_name,
            access_key_id=self._access_key_id,
            secret_access_key=self._secret_access_key,
            session_token=self._session_token,
        )

    def _family_client(self, family: str) -> ProviderClient:
        if family not in self._clients:
            self._clients[family] = self._claude() if family == "claude" else self._converse()
        return self._clients[family]

    def complete(self, *, model: str, messages: list[dict[str, Any]],
                 tools: Optional[list[dict[str, Any]]] = None, **settings: Any) -> AssistantTurn:
        family, rest = self._split(model)
        return self._family_client(family).complete(model=rest, messages=messages, tools=tools, **settings)

    def stream(self, *, model: str, messages: list[dict[str, Any]],
               tools: Optional[list[dict[str, Any]]] = None, **settings: Any):
        family, rest = self._split(model)
        return self._family_client(family).stream(model=rest, messages=messages, tools=tools, **settings)

    def capabilities(self, model: str) -> ModelCapabilities:
        return capabilities_for(model if model.startswith("bedrock:") else f"bedrock:{model}")

    def image_limits(self, model: str) -> tuple[int, Optional[int]]:
        """Bedrock puts vendors with different image limits under one name: Claude has
        Anthropic's (~5 MB, 8000 px), everything else (Nova, Llama, Mistral) the wider
        default."""
        from ...images import DEFAULT_LIMITS, IMAGE_LIMITS, is_claude_route

        return IMAGE_LIMITS["anthropic"] if is_claude_route(model) else DEFAULT_LIMITS
