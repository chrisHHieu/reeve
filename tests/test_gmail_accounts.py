"""Gmail with several accounts, and "Never show agents" filters (M3.6 step 3).

Each account is kept at `gmail:account:<email>`, whether it came from a managed sign-in or a pasted
token; `gmail:default` holds only which account is the default and the filters. The tools apply the
filters without a word: a hidden message is left out (no placeholder), fetched directly it reads as
a plain 404, and the count of hidden messages rides the `_display` sidecar, which no provider is sent.
"""

from __future__ import annotations

import json

import pytest

from harness.integrations.connectors import calls as connector_calls
from harness.integrations.connectors import gmail_accounts
from harness.integrations.connectors.integration_tools import make_integration_tools
from harness.integrations.connectors.lifecycle import connector_list, disconnect_connector
from harness.safety.secrets import SecretStore


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return SecretStore()


def _signed_in(secrets, *emails):
    for email in emails:
        gmail_accounts.managed_connect_account(secrets, {"type": "oauth", "enabled": True, "managed": True, "access_token": f"tok-{email}", "account": email})


def _tool(secrets, name: str):
    return next(tool for tool in make_integration_tools(secrets) if tool.__name__ == name)


def _gmail_answers(monkeypatch, routes: dict[str, dict]) -> list[tuple[str, str]]:
    """Gmail's API by URL suffix (the first that matches answers); each call's URL and bearer is kept."""
    seen: list[tuple[str, str]] = []

    def request(method, url, *, headers=None, params=None, json=None, auth=None):
        seen.append((url, (headers or {}).get("Authorization", "")))
        return next((answer for suffix, answer in routes.items() if suffix in url), {"error": "HTTP 404", "details": "no fake route"})

    monkeypatch.setattr(connector_calls, "request", request)
    return seen


def _message(message_id: str, sender: str, labels=None) -> dict:
    return {"id": message_id, "labelIds": labels or [], "payload": {"headers": [{"name": "From", "value": f"Some One <{sender}>"}]}}


def _gmail_row(secrets):
    return {c["name"]: c for c in connector_list(secrets)}["gmail"]


# -- accounts ------------------------------------------------------------------------------------


def test_an_old_single_profile_becomes_one_account_and_the_default_pointer(secrets):
    secrets.put("gmail:default", {"type": "oauth", "enabled": True, "access_token": "ya29", "account": "Old@X.com"})
    accounts = gmail_accounts.list_accounts(secrets)
    assert [email for email, _ in accounts] == ["old@x.com"] and accounts[0][1]["access_token"] == "ya29"
    pointer = secrets.get("gmail:default")
    assert pointer["default_account"] == "old@x.com" and "access_token" not in pointer  # moved, not copied
    assert gmail_accounts.default_account(secrets) == "old@x.com"


def test_moving_the_old_profile_keeps_its_filters_and_happens_once(secrets):
    secrets.put("gmail:default", {"access_token": "t", "account": "a@x.com", "filters": {"senders": ["ceo@x.com"]}})
    gmail_accounts.migrate_legacy_default(secrets)
    gmail_accounts.migrate_legacy_default(secrets)
    assert gmail_accounts.get_filters(secrets)["senders"] == ["ceo@x.com"] and len(gmail_accounts.list_accounts(secrets)) == 1


def test_the_first_account_stays_the_default_when_a_second_is_added(secrets):
    _signed_in(secrets, "one@x.com", "two@y.com")
    assert gmail_accounts.default_account(secrets) == "one@x.com"
    row = _gmail_row(secrets)
    assert [a["email"] for a in row["accounts"]] == ["one@x.com", "two@y.com"] and row["connected"]


def test_disconnecting_the_default_moves_it_to_the_account_left(secrets):
    _signed_in(secrets, "one@x.com", "two@y.com")
    assert gmail_accounts.set_default(secrets, "two@y.com")["ok"] and gmail_accounts.default_account(secrets) == "two@y.com"
    assert gmail_accounts.disconnect_account(secrets, "two@y.com")["ok"]
    assert gmail_accounts.default_account(secrets) == "one@x.com"


def test_the_last_account_leaving_keeps_the_filters(secrets):
    _signed_in(secrets, "one@x.com")
    gmail_accounts.set_filters(secrets, senders=["@spam.com"])
    gmail_accounts.disconnect_account(secrets, "one@x.com")
    assert not _gmail_row(secrets)["connected"]
    assert gmail_accounts.get_filters(secrets)["senders"] == ["@spam.com"]  # the policy stays


def test_disconnecting_gmail_removes_every_account(secrets):
    _signed_in(secrets, "one@x.com", "two@y.com")
    assert disconnect_connector(secrets, "gmail")["ok"]
    assert gmail_accounts.list_accounts(secrets) == [] and secrets.get("gmail:default") is None


# -- which account a tool uses -------------------------------------------------------------------


def test_a_tool_uses_the_account_it_names_and_otherwise_the_default(secrets, monkeypatch):
    _signed_in(secrets, "one@x.com", "two@y.com")
    seen = _gmail_answers(monkeypatch, {"/messages": {"ok": True, "data": {"messages": []}}})
    search = _tool(secrets, "gmail_search_messages")
    assert search("from:bob")["account"] == "one@x.com"
    assert search("from:bob", account="two@y.com")["account"] == "two@y.com"
    assert [bearer for _, bearer in seen] == ["Bearer tok-one@x.com", "Bearer tok-two@y.com"]
    assert "no gmail account" in search("x", account="nobody@z.com")["error"]


def test_a_store_from_before_accounts_still_works_through_the_tools(secrets, monkeypatch):
    secrets.put("gmail:default", {"access_token": "legacy-tok", "account": "me@x.com"})  # the pasted-token era
    seen = _gmail_answers(monkeypatch, {"/messages": {"ok": True, "data": {"messages": []}}})
    found = _tool(secrets, "gmail_search_messages")("q")
    assert found["ok"] and found["account"] == "me@x.com" and seen[0][1] == "Bearer legacy-tok"


# -- filters -------------------------------------------------------------------------------------


def test_a_search_leaves_out_filtered_senders_and_only_the_sidecar_counts_them(secrets, monkeypatch):
    _signed_in(secrets, "me@x.com")
    gmail_accounts.set_filters(secrets, senders=["ceo@corp.com", "@secret.org"])
    _gmail_answers(monkeypatch, {
        "/messages/m1": {"ok": True, "data": _message("m1", "ceo@corp.com")},
        "/messages/m2": {"ok": True, "data": _message("m2", "pal@ok.com")},
        "/messages/m3": {"ok": True, "data": _message("m3", "x@secret.org")},
        "/messages": {"ok": True, "data": {"messages": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}], "resultSizeEstimate": 3}},
    })
    found = _tool(secrets, "gmail_search_messages")("q")
    assert [m["id"] for m in found["data"]["messages"]] == ["m2"] and found["data"]["resultSizeEstimate"] == 1
    assert found["_display"] == {"hidden_by_filters": 2, "connector": "gmail"}
    assert "hidden" not in json.dumps(found["data"]).lower()  # nothing in what the agent reads says so


def test_a_filtered_message_fetched_directly_reads_as_a_plain_404(secrets, monkeypatch):
    _signed_in(secrets, "me@x.com")
    gmail_accounts.set_filters(secrets, senders=["ceo@corp.com"])
    _gmail_answers(monkeypatch, {"/messages/m1": {"ok": True, "data": _message("m1", "ceo@corp.com")}})
    fetched = _tool(secrets, "gmail_get_message")("m1")
    assert fetched["error"] == "HTTP 404" and fetched["_display"] == {"hidden_by_filters": 1, "connector": "gmail"}
    assert "filter" not in json.dumps({k: v for k, v in fetched.items() if k != "_display"}).lower()


def test_a_label_filter_is_written_with_the_labels_name(secrets, monkeypatch):
    _signed_in(secrets, "me@x.com")
    gmail_accounts.set_filters(secrets, labels=["Personal"])
    _gmail_answers(monkeypatch, {
        "/labels": {"ok": True, "data": {"labels": [{"id": "Label_7", "name": "Personal"}]}},
        "/messages/m1": {"ok": True, "data": _message("m1", "a@b.com", ["Label_7"])},
        "/messages/m2": {"ok": True, "data": _message("m2", "a@b.com", ["INBOX"])},
        "/messages": {"ok": True, "data": {"messages": [{"id": "m1"}, {"id": "m2"}]}},
    })
    found = _tool(secrets, "gmail_search_messages")("q")
    assert [m["id"] for m in found["data"]["messages"]] == ["m2"] and found["_display"]["hidden_by_filters"] == 1


def test_without_filters_a_search_is_one_call(secrets, monkeypatch):
    _signed_in(secrets, "me@x.com")
    seen = _gmail_answers(monkeypatch, {"/messages": {"ok": True, "data": {"messages": [{"id": "m1"}]}}})
    assert _tool(secrets, "gmail_search_messages")("q")["ok"] and len(seen) == 1


@pytest.mark.parametrize(
    ("sender", "rules", "hidden"),
    [
        ("ceo@corp.com", ["ceo@corp.com"], True),
        ("CEO@Corp.com", ["ceo@corp.com"], True),
        ("a@secret.org", ["@secret.org"], True),
        ("a@notsecret.org", ["@secret.org"], False),
        ("other@corp.com", ["ceo@corp.com"], False),
        ("", ["@x.com"], False),
    ],
)
def test_a_sender_rule_is_an_address_or_a_whole_domain_in_any_case(sender, rules, hidden):
    assert gmail_accounts.sender_matches(sender, rules) is hidden
