"""Redmine connector: request shapes, the guards, and the Confluence removal.

Redmine is self-hosted, so the site URL is user-supplied and every request is built
from it. The tests below assert on what actually leaves the process — URL, headers,
params, body — because that is the only part the Redmine on the other end sees.
"""

from __future__ import annotations
from harness.integrations.connectors import calls as connector_calls

import pytest

from harness.safety.secrets import SecretStore

_CREDS = {"base_url": "https://redmine.example.com/", "api_key": "K3Y", "enabled": True}


@pytest.fixture
def tools(tmp_path, monkeypatch):
    """Redmine connected, with `_request` recording instead of hitting the network."""
    import harness.integrations.connectors.integration_tools as it

    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("redmine:default", dict(_CREDS))
    calls: list[dict] = []

    def fake_request(method, url, *, headers=None, params=None, json=None, auth=None):
        calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "params": params,
                "json": json,
            }
        )
        return {"ok": True, "data": {}}

    monkeypatch.setattr(connector_calls, "request", fake_request)
    built = {t.__name__: t for t in it.make_integration_tools(secrets)}
    built["_calls"] = calls  # type: ignore[assignment]
    return built


def test_not_connected_says_so(tmp_path):
    from harness.integrations.connectors.integration_tools import make_integration_tools

    built = {
        t.__name__: t
        for t in make_integration_tools(SecretStore(tmp_path / "secrets.json"))
    }
    assert "not connected" in built["redmine_search_issues"](query="x")["error"]
    assert "not connected" in built["redmine_get_issue"](7)["error"]


def test_search_routes_with_the_api_key_header(tools):
    calls = tools["_calls"]
    tools["redmine_search_issues"](query="crash", project_id="eng", max_results=5)
    call = calls[-1]
    # the trailing slash on the stored base_url must not survive into the path
    assert call["url"] == "https://redmine.example.com/issues.json"
    assert call["headers"]["X-Redmine-API-Key"] == "K3Y"
    assert call["params"]["subject"] == "~crash"  # Redmine's "contains" operator
    assert call["params"]["project_id"] == "eng"
    assert call["params"]["limit"] == 5


def test_status_filter_maps_to_redmines_own_vocabulary(tools):
    calls = tools["_calls"]
    for given, expected in (("open", "open"), ("closed", "closed"), ("any", "*")):
        tools["redmine_search_issues"](status=given)
        assert calls[-1]["params"]["status_id"] == expected
    # an unknown word must not silently narrow the search to open issues
    tools["redmine_search_issues"](status="whatever")
    assert calls[-1]["params"]["status_id"] == "*"


def test_assigned_to_me_uses_the_me_literal(tools):
    calls = tools["_calls"]
    tools["redmine_search_issues"](assigned_to_me=True)
    assert calls[-1]["params"]["assigned_to_id"] == "me"
    tools["redmine_search_issues"]()
    assert "assigned_to_id" not in calls[-1]["params"]


def test_get_issue_asks_for_the_comment_thread(tools):
    calls = tools["_calls"]
    tools["redmine_get_issue"](42)
    assert calls[-1]["url"] == "https://redmine.example.com/issues/42.json"
    assert "journals" in calls[-1]["params"]["include"]


def test_create_issue_omits_the_ids_that_were_not_given(tools):
    calls = tools["_calls"]
    tools["redmine_create_issue"]("eng", "Login fails", tracker_id=1)
    body = calls[-1]["json"]["issue"]
    assert calls[-1]["method"] == "POST"
    assert body == {"project_id": "eng", "subject": "Login fails", "tracker_id": 1}


def test_zero_done_ratio_is_sent_and_an_empty_update_never_leaves(tools):
    calls = tools["_calls"]
    # 0% is a real value ("not started"), which is why the unset marker is -1 and
    # not 0 — a falsy check here would make "reset progress to zero" impossible.
    tools["redmine_update_issue"](7, done_ratio=0)
    assert calls[-1]["json"]["issue"] == {"done_ratio": 0}
    assert calls[-1]["method"] == "PUT"

    tools["redmine_update_issue"](7, status_id=3)
    assert "done_ratio" not in calls[-1]["json"]["issue"]

    before = len(calls)
    assert tools["redmine_update_issue"](7)["error"] == "nothing to update"
    assert len(calls) == before, "an empty update must not reach the server"


def test_comment_is_a_note_on_the_issue(tools):
    calls = tools["_calls"]
    tools["redmine_comment_issue"](7, "fixed in 2.1")
    assert calls[-1]["json"] == {"issue": {"notes": "fixed in 2.1"}}


def test_log_time_demands_exactly_one_target(tools):
    calls = tools["_calls"]
    before = len(calls)
    for kwargs in ({}, {"issue_id": 7, "project_id": "eng"}):
        result = tools["redmine_log_time"](1.5, **kwargs)
        assert "either issue_id or project_id" in result["error"]
    assert len(calls) == before, "a rejected call must not reach the server"

    tools["redmine_log_time"](1.5, issue_id=7, comments="debugging")
    assert calls[-1]["json"]["time_entry"] == {
        "hours": 1.5,
        "issue_id": 7,
        "comments": "debugging",
    }
    tools["redmine_log_time"](2.0, project_id="eng")
    assert calls[-1]["json"]["time_entry"] == {"hours": 2.0, "project_id": "eng"}


def test_enumerations_survive_one_endpoint_being_missing(tmp_path, monkeypatch):
    """Those four endpoints are `alpha` in Redmine's docs and an older site may not
    serve every one. A single 404 must not cost the caller the other three."""
    import harness.integrations.connectors.integration_tools as it

    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("redmine:default", dict(_CREDS))

    def fake_request(method, url, **kw):
        if url.endswith("/trackers.json"):
            return {"error": "HTTP 404"}
        return {"ok": True, "data": {"x": url.rsplit("/", 1)[-1]}}

    monkeypatch.setattr(connector_calls, "request", fake_request)
    built = {t.__name__: t for t in it.make_integration_tools(secrets)}

    out = built["redmine_list_enumerations"]()
    assert out["ok"] is True
    assert out["data"]["trackers"] == {"error": "HTTP 404"}
    assert out["data"]["issue_statuses"] == {"x": "issue_statuses.json"}
    assert set(out["data"]) == {
        "trackers",
        "issue_statuses",
        "issue_priorities",
        "time_entry_activities",
    }


# -- sign-in --------------------------------------------------------------------
class _Resp:
    def __init__(self, status, payload=None, boom=False):
        self.status_code = status
        self._payload = payload
        self._boom = boom

    def json(self):
        if self._boom:
            raise ValueError("not json")
        return self._payload


def _answer(monkeypatch, resp):
    import httpx

    seen: dict = {}

    def fake_get(url, **kw):
        seen["url"] = url
        seen["headers"] = kw.get("headers") or {}
        return resp

    monkeypatch.setattr(httpx, "get", fake_get)
    return seen


def test_validate_names_the_setting_the_admin_has_to_turn_on(monkeypatch):
    """Redmine ships with the REST API OFF, and answers 401 for that exactly as it
    does for a bad key. A bare "HTTP 401" sends the user hunting for the wrong thing."""
    import harness.integrations.connectors.descriptors as d
    import harness.integrations.connectors.descriptor.validators as validators

    _answer(monkeypatch, _Resp(401, boom=True))
    res = validators.validate_redmine({"base_url": "https://r.example.com", "api_key": "bad"})
    assert not res.ok
    assert "API key is wrong" in res.error and "Settings -> API" in res.error


def test_validate_returns_the_login_as_identity(monkeypatch):
    import harness.integrations.connectors.descriptors as d
    import harness.integrations.connectors.descriptor.validators as validators

    seen = _answer(monkeypatch, _Resp(200, {"user": {"id": 3, "login": "alice"}}))
    res = validators.validate_redmine(
        {"base_url": "https://r.example.com/", "api_key": "K3Y"}
    )
    assert res.ok and res.identity == "alice"
    assert seen["url"] == "https://r.example.com/users/current.json"
    assert seen["headers"]["X-Redmine-API-Key"] == "K3Y"


def test_validate_rejects_a_200_that_is_not_redmine(monkeypatch):
    """A proxy or SSO wall can answer 200 with an HTML page. Accepting that would
    store a connection that fails on its first real call."""
    import harness.integrations.connectors.descriptors as d
    import harness.integrations.connectors.descriptor.validators as validators

    _answer(monkeypatch, _Resp(200, boom=True))
    res = validators.validate_redmine({"base_url": "https://sso.example.com", "api_key": "k"})
    assert not res.ok and "not as a Redmine API" in res.error


# -- catalog wiring --------------------------------------------------------------
def test_redmine_is_in_the_catalog_with_its_tools(tmp_path):
    from harness.integrations.connectors import connector_list

    card = {c["name"]: c for c in connector_list(SecretStore(tmp_path / "secrets.json"))}
    assert "redmine" in card, "the connector must be listed even before it is connected"
    entry = card["redmine"]
    assert entry["available"] is True and entry["connected"] is False
    assert entry["instructions"], "the REST-API-disabled trap has to be stated up front"

    kinds = {t["name"]: t["kind"] for t in entry["tools"]}
    assert kinds["redmine_search_issues"] == "read"
    assert kinds["redmine_list_enumerations"] == "read"
    # the write set is pinned: nothing here deletes, and nothing touches settings
    writes = {n for n, k in kinds.items() if k != "read"}
    assert writes == {
        "redmine_create_issue",
        "redmine_update_issue",
        "redmine_comment_issue",
        "redmine_log_time",
    }, writes


def test_every_catalog_tool_is_actually_built(tmp_path):
    """A catalogue entry with no implementation would show in the UI and 404 on use."""
    from harness.integrations.connectors.integration_tools import make_integration_tools
    from harness.integrations.connectors.catalog import TOOL_DEFS

    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("redmine:default", dict(_CREDS))
    built = {t.__name__ for t in make_integration_tools(secrets)}
    listed = {d.name for d in TOOL_DEFS if d.connector == "redmine"}
    assert listed and listed <= built, listed - built


# -- Confluence is gone ----------------------------------------------------------
def test_confluence_connector_is_removed_everywhere(tmp_path):
    """Superseded by the Atlassian card, which covers Confluence through Rovo MCP.

    Every surface is checked, not just the descriptor: a leftover catalogue row would
    still render a card's tool list, and a leftover tool would still be callable.
    """
    from harness.integrations.connectors import connector_list
    from harness.integrations.connectors.catalog_copy import ACCESS
    from harness.integrations.connectors.integration_tools import make_integration_tools
    from harness.integrations.connectors.catalog import TOOL_DEFS

    secrets = SecretStore(tmp_path / "secrets.json")
    assert "confluence" not in {c["name"] for c in connector_list(secrets)}
    assert not [d for d in TOOL_DEFS if d.connector == "confluence"]
    assert not [
        t for t in make_integration_tools(secrets) if "confluence" in t.__name__
    ]
    assert "confluence" not in ACCESS


def test_atlassian_still_advertises_confluence(tmp_path):
    """The card is gone, the capability is not. Someone searching the catalog for
    "confluence" must still land on Atlassian, or the removal reads as a loss."""
    from harness.integrations.connectors import connector_list

    cards = {c["name"]: c for c in connector_list(SecretStore(tmp_path / "secrets.json"))}
    haystack = " ".join(
        str(cards["jira"].get(k, "")) for k in ("blurb", "title", "aliases")
    ).lower()
    assert "confluence" in haystack
