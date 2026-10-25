"""Google Vertex AI: one entry in Settings, three wires chosen by model family.

A routed id reads `vertex:<family>/<model id>`. The router drops `vertex:` and this provider
takes the family and reuses a provider class for it:

- `gemini/…`: `GeminiProvider` over `genai.Client(vertexai=True)`.
- `claude/…`: `AnthropicProvider` over the SDK's `AnthropicVertex` client.
- `openweight/…`: `OpenAIProvider` against Vertex's OpenAI-compatible endpoint (Llama, Qwen,
  DeepSeek and others; ids keep their publisher segment, as in `openweight/meta/…`).

An id with no known family is routed by its name as a best effort (gemini* to Gemini, claude*
to Claude, anything else to the open-weight endpoint), so a raw id pasted without the
add-model dropdown still works. Credentials are described in `credentials.py`.

The open-weight endpoint authenticates with a google-auth bearer token that expires about
hourly. This class refreshes it and rebuilds that sub-client; the two native SDK clients take
the credentials object and refresh on their own.
"""

from __future__ import annotations

from typing import Any, Optional

from ..base import AssistantTurn, ModelCapabilities, ProviderClient
from ..capabilities import capabilities_for
from ..chat import OpenAIProvider
from ..claude import AnthropicProvider
from ..gemini import GeminiProvider
from ..timeouts import stream_timeout
from .credentials import SCOPES, load_credentials, narrow_to_method, regional_host

_FAMILIES = ("gemini", "claude", "openweight")


class VertexProvider(ProviderClient):
    """Splits `<family>/<model id>` and hands the request to the client for that family."""

    def __init__(
        self,
        *,
        project: Optional[str] = None,
        location: Optional[str] = None,
        auth_method: Optional[str] = None,
        service_account_json: Optional[str] = None,
        api_key: Optional[str] = None,
        credentials: Any = None,
        gemini_client: Optional[ProviderClient] = None,
        claude_client: Optional[ProviderClient] = None,
        openweight_client: Optional[ProviderClient] = None,
    ):
        self._service_account_json, self._api_key = narrow_to_method(
            auth_method, service_account_json, api_key
        )
        self._project = project
        self._location = location
        self._credentials = credentials  # normally loaded on first use; tests pass one
        # Ready-made clients (tests) skip building an SDK client below.
        self._clients: dict[str, ProviderClient] = {}
        if gemini_client is not None:
            self._clients["gemini"] = gemini_client
        if claude_client is not None:
            self._clients["claude"] = claude_client
        if openweight_client is not None:
            self._clients["openweight"] = openweight_client
        self._openweight_injected = openweight_client is not None

    @staticmethod
    def _split(model: str) -> tuple[str, str]:
        if "/" in model:
            family, rest = model.split("/", 1)
            if family in _FAMILIES:
                return family, rest
        if model.startswith("gemini"):
            return "gemini", model
        if model.startswith("claude"):
            return "claude", model
        return "openweight", model

    # -- credentials ------------------------------------------------------------------

    def _explicit_credentials(self) -> Any:
        """The service-account credentials, or None to let each SDK use ADC."""
        if self._credentials is None:
            self._credentials = load_credentials(self._service_account_json)
        return self._credentials

    def _bearer_credentials(self) -> Any:
        """Credentials for the open-weight bearer token: the service account, else ADC."""
        credentials = self._explicit_credentials()
        if credentials is None:
            import google.auth

            try:
                credentials, _ = google.auth.default(scopes=SCOPES)
            except Exception as exc:
                raise RuntimeError(
                    "No Google Cloud credentials found — paste a service-account JSON "
                    "in Settings ▸ Models, or run `gcloud auth application-default login`."
                ) from exc
            self._credentials = credentials
        return credentials

    # -- one client per family ----------------------------------------------------------

    def _gemini(self) -> ProviderClient:
        from google import genai

        if self._api_key:
            # Express mode. The key identifies the project, and the SDK forbids passing the
            # project and location with it.
            sdk = genai.Client(vertexai=True, api_key=self._api_key)
        else:
            sdk = genai.Client(
                vertexai=True,
                project=self._project,
                location=self._location,
                credentials=self._explicit_credentials(),
            )
        return GeminiProvider(client=sdk)

    def _claude(self) -> ProviderClient:
        import anthropic as sdk
        from anthropic import AnthropicVertex

        # The transport rules of the direct Anthropic client: the idle-read timeout inside the
        # SDK's own Timeout class, and one attempt per stream because the loop owns retries.
        # Vertex used to run on the SDK's ten-minute default, so a stalled stream hung the turn
        # for that long.
        return AnthropicProvider(client=AnthropicVertex(
            project_id=self._project,
            region=self._location,
            credentials=self._explicit_credentials(),
            timeout=stream_timeout(sdk),
            max_retries=0,
        ))

    def _openweight_client(self) -> ProviderClient:
        """`OpenAIProvider` over the open-weight endpoint, rebuilt whenever the bearer token had
        to be refreshed."""
        if self._openweight_injected:
            return self._clients["openweight"]
        credentials = self._bearer_credentials()
        if not getattr(credentials, "valid", False):
            from google.auth.transport.requests import Request

            credentials.refresh(Request())
            self._clients.pop("openweight", None)  # the token changed: build a new client
        if "openweight" not in self._clients:
            base_url = (
                f"https://{regional_host(self._location)}/v1/projects/"
                f"{self._project}/locations/{self._location}/endpoints/openapi"
            )
            self._clients["openweight"] = OpenAIProvider(api_key=credentials.token, base_url=base_url)
        return self._clients["openweight"]

    def _family_client(self, family: str) -> ProviderClient:
        if self._api_key and family != "gemini":
            raise RuntimeError(
                "Vertex API keys cover Gemini models only — switch the Vertex provider "
                "to Google Cloud login or a service account for Claude and open-weight "
                "models (Settings ▸ Models)."
            )
        if family == "openweight":
            return self._openweight_client()
        if family not in self._clients:
            self._clients[family] = self._gemini() if family == "gemini" else self._claude()
        return self._clients[family]

    # -- ProviderClient -------------------------------------------------------------------

    def complete(self, *, model: str, messages: list[dict[str, Any]],
                 tools: Optional[list[dict[str, Any]]] = None, **settings: Any) -> AssistantTurn:
        family, rest = self._split(model)
        return self._family_client(family).complete(model=rest, messages=messages, tools=tools, **settings)

    def stream(self, *, model: str, messages: list[dict[str, Any]],
               tools: Optional[list[dict[str, Any]]] = None, **settings: Any):
        family, rest = self._split(model)
        return self._family_client(family).stream(model=rest, messages=messages, tools=tools, **settings)

    def capabilities(self, model: str) -> ModelCapabilities:
        return capabilities_for(model if model.startswith("vertex:") else f"vertex:{model}")

    def media_carry(self, model: str, mime: str) -> bool:
        # The Gemini family goes out through the Gemini converter, which takes inline data;
        # Claude and the open-weight models do not. No client is built to answer, because this
        # is a fact about the wire and building one can need credentials the question does not.
        family, _ = self._split(model.removeprefix("vertex:"))
        return family == "gemini" and mime.startswith(("audio/", "video/"))

    def image_limits(self, model: str) -> tuple[int, Optional[int]]:
        """Vertex serves Gemini and Claude under one name, and the Gemini routes take much
        larger images than Anthropic's ~5 MB / 8000 px."""
        from ...images import DEFAULT_LIMITS, IMAGE_LIMITS, is_claude_route

        return IMAGE_LIMITS["anthropic"] if is_claude_route(model) else DEFAULT_LIMITS
