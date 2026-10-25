"""Google Vertex AI: which of its three clients serves a model id, the open-weight endpoint and the
bearer token it refreshes, the sign-in methods, and how the settings form and its check see it.

Nothing reaches Google: credentials, `genai.Client` and the check's HTTP call are replaced.
"""

from __future__ import annotations

import pytest

from harness.llm.providers import GeminiProvider, capabilities_for
from harness.llm.providers.base import AssistantTurn, ProviderClient, StreamChunk
from harness.llm.providers.registry import build_provider_client, descriptor_configured, get_descriptor, verify_provider_key
from harness.llm.providers.vertex import VertexProvider
from harness.llm.providers.vertex.credentials import load_credentials

ASK = [{"role": "user", "content": "x"}]


class _Seen(ProviderClient):
    def __init__(self):
        self.models: list[str] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.models.append(model)
        return AssistantTurn(text="ok")

    def stream(self, *, model, messages, tools=None, **settings):
        self.models.append(model)
        yield StreamChunk(turn=AssistantTurn(text="ok"))

    def capabilities(self, model):
        return capabilities_for(model)


class _Credentials:
    """google-auth's shape: `valid` turns true once refreshed, and each refresh is a new token."""

    def __init__(self):
        self.token, self.valid, self.refreshes = "tok-1", False, 0

    def refresh(self, request):
        self.refreshes += 1
        self.token, self.valid = f"tok-{self.refreshes + 1}", True


def _routed(*models):
    gemini, claude, openweight = _Seen(), _Seen(), _Seen()
    provider = VertexProvider(project="proj", location="us-east5", gemini_client=gemini, claude_client=claude, openweight_client=openweight)
    for model in models:
        provider.complete(model=model, messages=ASK)
    return gemini.models, claude.models, openweight.models


# -- which client serves a model -----------------------------------------------------------------


def test_a_family_prefixed_id_reaches_its_family_and_only_the_first_slash_splits():
    assert _routed("gemini/gemini-3.6-flash", "claude/claude-sonnet-4-6", "openweight/meta/llama-4-maverick-17b-128e-instruct-maas") == (
        ["gemini-3.6-flash"], ["claude-sonnet-4-6"], ["meta/llama-4-maverick-17b-128e-instruct-maas"],
    )


def test_an_id_without_a_family_is_routed_by_its_name():
    assert _routed("gemini-2.5-pro", "claude-haiku-4-5", "deepseek-ai/deepseek-v4-maas") == (
        ["gemini-2.5-pro"], ["claude-haiku-4-5"], ["deepseek-ai/deepseek-v4-maas"],
    )


def test_a_vertex_id_keeps_everything_after_the_provider_prefix():
    from harness.llm.providers.router import ProviderRouter

    model = "vertex:openweight/meta/llama-4-maverick-17b-128e-instruct-maas"
    assert ProviderRouter.__new__(ProviderRouter)._provider_name(model) == "vertex"
    assert ProviderRouter._bare(model) == "openweight/meta/llama-4-maverick-17b-128e-instruct-maas"


def test_capabilities_come_from_the_matrix_and_an_unknown_id_gets_the_cautious_default():
    assert capabilities_for("vertex:gemini/gemini-3.6-flash").vision
    assert capabilities_for("vertex:claude/claude-sonnet-4-6").pdf
    assert capabilities_for("vertex:openweight/meta/llama-4-maverick-17b-128e-instruct-maas").tools
    assert not capabilities_for("vertex:gemini/gemini-4.0-preview").vision
    unknown = capabilities_for("vertex:openweight/some-org/new-model-maas")
    assert unknown.tools and not unknown.parallel_tool_calls


# -- the open-weight endpoint --------------------------------------------------------------------


def test_open_weight_models_go_to_the_regions_maas_endpoint_with_a_refreshed_bearer():
    credentials = _Credentials()
    provider = VertexProvider(project="proj", location="us-east5", credentials=credentials)
    client = provider._openweight_client()
    assert credentials.refreshes == 1 and client._api_key == "tok-2"
    assert client._base_url == "https://us-east5-aiplatform.googleapis.com/v1/projects/proj/locations/us-east5/endpoints/openapi"
    # While the token holds, the same client is kept.
    assert provider._openweight_client() is client and credentials.refreshes == 1
    # Once it lapses, it is refreshed and the client rebuilt on the new one.
    credentials.valid = False
    rebuilt = provider._openweight_client()
    assert rebuilt is not client and credentials.refreshes == 2 and rebuilt._api_key == "tok-3"


def test_the_global_location_has_no_region_in_its_host():
    client = VertexProvider(project="proj", location="global", credentials=_Credentials())._openweight_client()
    assert client._base_url == "https://aiplatform.googleapis.com/v1/projects/proj/locations/global/endpoints/openapi"


# -- signing in ----------------------------------------------------------------------------------


def test_no_service_account_json_means_application_default_credentials_and_bad_json_is_refused():
    assert load_credentials(None) is None and load_credentials("   ") is None
    with pytest.raises(Exception):
        load_credentials('{"type": "service_account"')


def test_the_chosen_sign_in_method_drops_what_another_method_left_stored():
    adc = VertexProvider(project="proj", location="us-east5", auth_method="adc", service_account_json="{stale}", api_key="AQ.stale")
    assert adc._service_account_json is None and adc._api_key is None
    keyed = VertexProvider(project="proj", location="us-east5", auth_method="api_key", api_key="AQ.live", service_account_json="{stale}")
    assert keyed._api_key == "AQ.live" and keyed._service_account_json is None


def test_an_api_key_reaches_gemini_models_only():
    provider = VertexProvider(project="proj", location="us-east5", auth_method="api_key", api_key="AQ.k")
    for model in ("claude/claude-sonnet-4-6", "openweight/meta/llama-4-maverick-maas"):
        with pytest.raises(RuntimeError, match="Gemini models only"):
            provider.complete(model=model, messages=ASK)


def test_an_api_key_builds_an_express_client_without_a_project_or_location(monkeypatch):
    """The SDK takes an API key or a project and location, never both."""
    from google import genai

    built: dict = {}
    monkeypatch.setattr(genai, "Client", lambda **kwargs: built.update(kwargs))
    gemini = VertexProvider(project="proj", location="us-east5", auth_method="api_key", api_key="AQ.k")._family_client("gemini")
    assert isinstance(gemini, GeminiProvider) and built == {"vertexai": True, "api_key": "AQ.k"}


# -- the settings form and its check -------------------------------------------------------------


def test_the_form_asks_for_a_project_a_location_and_one_sign_in_method():
    descriptor = get_descriptor("vertex")
    assert descriptor is not None and descriptor.needs_key
    fields = {field.key: field for field in descriptor.fields}
    assert list(fields) == ["project", "location", "auth_method", "service_account_json", "vertex_api_key"]
    assert [key for key, field in fields.items() if field.required] == ["project", "location"]
    # Application default credentials come first (Google's own advice), with the command to copy.
    method = fields["auth_method"]
    assert method.default == "adc" and [c["value"] for c in method.choices] == ["adc", "service_account", "api_key"]
    assert next(c for c in method.choices if c["value"] == "adc")["command"].startswith("gcloud auth application-default")
    assert fields["service_account_json"].show_when == {"auth_method": "service_account"} and fields["service_account_json"].secret
    assert fields["vertex_api_key"].show_when == {"auth_method": "api_key"} and fields["vertex_api_key"].secret

    built = build_provider_client("vertex", {"project": "proj", "location": "europe-west1"}, None)
    assert isinstance(built, VertexProvider) and (built._project, built._location) == ("proj", "europe-west1")


def test_vertex_is_set_up_once_it_has_a_project_and_a_location():
    descriptor = get_descriptor("vertex")
    assert not descriptor_configured(descriptor, {}) and not descriptor_configured(descriptor, {"project": "proj"})
    assert descriptor_configured(descriptor, {"project": "proj", "location": "us-east5"})


def _google_answers(monkeypatch, status: int, credentials=None) -> dict:
    import httpx

    import harness.llm.providers.vertex.credentials as vertex_credentials

    if credentials is not None:
        monkeypatch.setattr(vertex_credentials, "load_credentials", lambda raw: credentials)
    asked: dict = {}

    def post(url, headers=None, json=None, timeout=None, **kwargs):
        asked.update(url=url, headers=headers)
        return type("_Reply", (), {"status_code": status})()

    monkeypatch.setattr(httpx, "post", post)
    return asked


def test_each_sign_in_method_names_what_its_check_is_missing():
    no_key = verify_provider_key("vertex", fields={"project": "p", "location": "l", "auth_method": "api_key"})
    assert not no_key["ok"] and "API key" in no_key["error"]
    no_json = verify_provider_key("vertex", fields={"project": "p", "location": "l", "auth_method": "service_account"})
    assert not no_json["ok"] and "service-account JSON" in no_json["error"]


def test_an_api_key_is_checked_on_the_global_express_endpoint(monkeypatch):
    asked = _google_answers(monkeypatch, 200)
    fields = {"project": "p", "location": "l", "auth_method": "api_key", "vertex_api_key": "AQ.k"}
    assert verify_provider_key("vertex", fields=fields) == {"ok": True}
    assert asked["headers"]["x-goog-api-key"] == "AQ.k"
    assert asked["url"] == "https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-2.5-flash:countTokens"


def test_a_service_account_is_checked_in_its_project_and_region_with_a_fresh_bearer(monkeypatch):
    credentials = _Credentials()
    asked = _google_answers(monkeypatch, 200, credentials)
    fields = {"project": "proj", "location": "us-east5", "service_account_json": "x"}
    assert verify_provider_key("vertex", fields=fields) == {"ok": True}
    assert credentials.refreshes == 1
    assert "proj/locations/us-east5/publishers/google/models" in asked["url"]
    assert asked["headers"]["Authorization"] == "Bearer tok-2"


@pytest.mark.parametrize(("status", "said"), [(403, "Vertex AI access"), (404, "not found")])
def test_what_google_refused_is_said_in_the_words_of_its_fix(monkeypatch, status, said):
    _google_answers(monkeypatch, status, _Credentials())
    out = verify_provider_key("vertex", fields={"project": "proj", "location": "us-east5", "service_account_json": "x"})
    assert not out["ok"] and said in out["error"]
