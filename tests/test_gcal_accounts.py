"""Google Calendar with several accounts: Gmail's account model without its filters.

Each account is kept at `google_calendar:account:<email>`, from a managed sign-in or a pasted token
alike, and `google_calendar:default` holds only which one is the default. A tool takes an `account`,
falls back to the default, and names the account in every answer, so an approval or a transcript
says whose calendar was changed.
"""

from __future__ import annotations

import pytest

from harness.integrations.connectors import calls as connector_calls
from harness.integrations.connectors import gcal_accounts
from harness.integrations.connectors.integration_tools import make_integration_tools
from harness.integrations.connectors.lifecycle import connector_list, disconnect_connector
from harness.safety.secrets import SecretStore


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return SecretStore()


def _signed_in(secrets, *emails):
    for email in emails:
        gcal_accounts.managed_connect_account(secrets, {"type": "oauth", "enabled": True, "managed": True, "access_token": f"tok-{email}", "account": email})


def _tool(secrets, name: str):
    return next(tool for tool in make_integration_tools(secrets) if tool.__name__ == name)


def _calendar_answers(monkeypatch, routes: dict[str, dict]) -> list[tuple]:
    """The Calendar API by URL suffix; each call's method, URL, bearer and body is kept."""
    seen: list[tuple] = []

    def request(method, url, *, headers=None, params=None, json=None, auth=None):
        seen.append((method, url, (headers or {}).get("Authorization", ""), json))
        return next((answer for suffix, answer in routes.items() if suffix in url), {"error": "HTTP 404", "details": "no fake route"})

    monkeypatch.setattr(connector_calls, "request", request)
    return seen


def _calendar_row(secrets):
    return {c["name"]: c for c in connector_list(secrets)}["google_calendar"]


# -- accounts ------------------------------------------------------------------------------------


def test_an_old_single_profile_becomes_one_account_once(secrets):
    secrets.put("google_calendar:default", {"type": "oauth", "enabled": True, "access_token": "ya29", "account": "Old@X.com"})
    accounts = gcal_accounts.list_accounts(secrets)
    assert [email for email, _ in accounts] == ["old@x.com"] and accounts[0][1]["access_token"] == "ya29"
    pointer = secrets.get("google_calendar:default")
    assert pointer["default_account"] == "old@x.com" and "access_token" not in pointer  # moved, not copied
    assert gcal_accounts.default_account(secrets) == "old@x.com"
    gcal_accounts.migrate_legacy_default(secrets)
    assert len(gcal_accounts.list_accounts(secrets)) == 1


def test_the_first_account_stays_the_default_when_a_second_is_added(secrets):
    _signed_in(secrets, "one@x.com", "two@y.com")
    assert gcal_accounts.default_account(secrets) == "one@x.com"
    row = _calendar_row(secrets)
    assert [a["email"] for a in row["accounts"]] == ["one@x.com", "two@y.com"]
    assert row["connected"] and row["account"] == "one@x.com"


def test_disconnecting_the_default_moves_it_to_the_account_left(secrets):
    _signed_in(secrets, "one@x.com", "two@y.com")
    assert gcal_accounts.set_default(secrets, "two@y.com")["ok"] and gcal_accounts.default_account(secrets) == "two@y.com"
    assert gcal_accounts.disconnect_account(secrets, "two@y.com")["ok"]
    assert gcal_accounts.default_account(secrets) == "one@x.com"


def test_the_last_account_leaving_takes_the_pointer_with_it(secrets):
    _signed_in(secrets, "one@x.com")
    assert gcal_accounts.disconnect_account(secrets, "one@x.com")["ok"]
    assert not _calendar_row(secrets)["connected"] and secrets.get("google_calendar:default") is None


def test_disconnecting_the_calendar_removes_every_account(secrets):
    _signed_in(secrets, "one@x.com", "two@y.com")
    assert disconnect_connector(secrets, "google_calendar")["ok"]
    assert gcal_accounts.list_accounts(secrets) == [] and secrets.get("google_calendar:default") is None


# -- which account a tool uses -------------------------------------------------------------------


def test_a_tool_uses_the_account_it_names_and_otherwise_the_default(secrets, monkeypatch):
    _signed_in(secrets, "one@x.com", "two@y.com")
    seen = _calendar_answers(monkeypatch, {"/events": {"ok": True, "data": {"items": []}}})
    events = _tool(secrets, "gcal_list_events")
    for named, used in ((None, "one@x.com"), ("two@y.com", "two@y.com")):
        listed = events(account=named) if named else events()
        assert listed["ok"] and listed["account"] == used
    assert [bearer for _, _, bearer, _ in seen] == ["Bearer tok-one@x.com", "Bearer tok-two@y.com"]
    assert "no google calendar account" in events(account="nobody@z.com")["error"]


def test_a_store_from_before_accounts_still_works_through_the_tools(secrets, monkeypatch):
    secrets.put("google_calendar:default", {"access_token": "legacy-tok", "account": "me@x.com"})  # the pasted-token era
    seen = _calendar_answers(monkeypatch, {"/events": {"ok": True, "data": {"items": []}}})
    listed = _tool(secrets, "gcal_list_events")()
    assert listed["ok"] and listed["account"] == "me@x.com" and seen[0][2] == "Bearer legacy-tok"


# -- changing a calendar -------------------------------------------------------------------------


def test_an_update_sends_only_the_fields_it_was_given(secrets, monkeypatch):
    _signed_in(secrets, "me@x.com")
    seen = _calendar_answers(monkeypatch, {"/events/ev1": {"ok": True, "data": {"id": "ev1"}}})
    moved = _tool(secrets, "gcal_update_event")("ev1", summary="Moved", start="2026-07-10T10:00:00Z")
    assert moved["ok"] and moved["account"] == "me@x.com"
    [(method, url, _, body)] = seen
    assert method == "PATCH" and url.endswith("/calendars/primary/events/ev1")
    assert body == {"summary": "Moved", "start": {"dateTime": "2026-07-10T10:00:00Z", "timeZone": "UTC"}}


def test_an_update_with_nothing_to_change_is_refused(secrets, monkeypatch):
    _signed_in(secrets, "me@x.com")
    _calendar_answers(monkeypatch, {})
    assert "nothing to update" in _tool(secrets, "gcal_update_event")("ev1")["error"]


def test_a_delete_goes_to_the_calendar_it_names(secrets, monkeypatch):
    _signed_in(secrets, "me@x.com")
    seen = _calendar_answers(monkeypatch, {"/events/ev9": {"ok": True, "data": ""}})
    assert _tool(secrets, "gcal_delete_event")("ev9", calendar_id="team@group.calendar.google.com")["ok"]
    [(method, url, _, _)] = seen
    assert method == "DELETE" and url.endswith("/calendars/team@group.calendar.google.com/events/ev9")


def test_free_busy_asks_about_each_calendar_in_the_list(secrets, monkeypatch):
    _signed_in(secrets, "me@x.com")
    seen = _calendar_answers(monkeypatch, {"/freeBusy": {"ok": True, "data": {"calendars": {}}}})
    busy = _tool(secrets, "gcal_free_busy")("2026-07-10T00:00:00Z", "2026-07-11T00:00:00Z", calendars="primary, team@x.com")
    assert busy["ok"] and busy["account"] == "me@x.com"
    body = seen[0][3]
    assert body["items"] == [{"id": "primary"}, {"id": "team@x.com"}] and body["timeMin"] == "2026-07-10T00:00:00Z"


@pytest.mark.parametrize("name", ["gcal_create_event", "gcal_update_event", "gcal_delete_event"])
def test_a_tool_that_changes_a_calendar_always_asks(secrets, name):
    # Connector tools ask by default, reads too; the writes are the ones that must never lose it.
    tools = {tool.__name__: tool for tool in make_integration_tools(secrets)}
    assert tools[name].__aisuite_tool_metadata__.requires_approval
