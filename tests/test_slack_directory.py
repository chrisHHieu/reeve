"""Slack's people and channels for the pickers (`connectors/slack_directory`): fetched with
users.list and conversations.list, kept in memory, filtered and ranked here. It needs no scope an
install does not already have (users:read, channels:read, groups:read), and no roster is written to
disk.
"""

from __future__ import annotations

import pytest

from harness.integrations.connectors import slack_directory
from harness.safety.secrets import SecretStore


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    store = SecretStore()
    store.put("slack:default", {"bot_token": "xoxb-manual"})
    return store


@pytest.fixture(autouse=True)
def _nothing_cached():
    slack_directory.clear_cache()
    yield
    slack_directory.clear_cache()


def _person(uid, display, handle, **flags):
    return {"id": uid, "name": handle, "profile": {"display_name": display}, **flags}


PEOPLE = [
    _person("U1", "Maya Chen", "maya"),
    _person("U2", "Sam Okafor", "sam"),
    _person("U3", "", "zed"),  # no display name: the handle stands in
    _person("U4", "Contractor Cal", "cal", is_restricted=True),
    _person("UB", "Beep", "beep-bot", is_bot=True),
    _person("UG", "Gone", "gone", deleted=True),
    {"id": "USLACKBOT", "name": "slackbot", "profile": {"display_name": "Slackbot"}},
]


def _slack_answers(monkeypatch, by_method: dict[str, list[dict]]) -> list[tuple[str, str]]:
    """Slack's paged lists, answered from `by_method`; each call's method and token is kept."""
    asked: list[tuple[str, str]] = []

    def pages(token, method, params, key, page_limit=200):
        asked.append((method, token))
        return by_method[method]

    monkeypatch.setattr(slack_directory, "_get_pages", pages)
    return asked


def _ids(listed):
    return [person["id"] for person in listed["members"]]


def test_people_are_listed_by_name_without_bots_or_the_departed_and_guests_say_so(secrets, monkeypatch):
    _slack_answers(monkeypatch, {"users.list": PEOPLE})
    listed = slack_directory.list_members(secrets)
    assert listed["ok"] and _ids(listed) == ["U4", "U1", "U2", "U3"]
    by_id = {person["id"]: person for person in listed["members"]}
    assert by_id["U4"]["guest"] and not by_id["U2"]["guest"]
    assert by_id["U3"]["name"] == "zed"
    assert _ids(slack_directory.list_members(secrets, query="sa")) == ["U2"]  # the display name matches
    assert _ids(slack_directory.list_members(secrets, query="maya")) == ["U1"]  # the handle matches too


def test_the_people_are_fetched_once_and_filtered_from_memory(secrets, monkeypatch):
    asked = _slack_answers(monkeypatch, {"users.list": PEOPLE})
    slack_directory.list_members(secrets)
    slack_directory.list_members(secrets, query="sa")
    assert asked == [("users.list", "xoxb-manual")]


def test_a_channel_says_whether_it_is_private_and_whether_the_bot_is_in_it(secrets, monkeypatch):
    _slack_answers(monkeypatch, {"conversations.list": [
        {"id": "C1", "name": "general", "is_private": False, "is_member": True},
        {"id": "C2", "name": "launch-team", "is_private": False, "is_member": False},
        {"id": "C3", "name": "leads", "is_private": True, "is_member": True},
    ]})
    listed = slack_directory.list_channels(secrets, query="l")
    assert listed["ok"] and [c["name"] for c in listed["channels"]] == ["launch-team", "leads", "general"]
    by_name = {c["name"]: c for c in listed["channels"]}
    assert by_name["leads"]["is_private"] and by_name["leads"]["is_member"]
    assert not by_name["launch-team"]["is_member"]  # where the app says to invite the bot


def test_no_slack_and_a_slack_error_are_both_answers_not_raises(secrets, monkeypatch):
    assert not slack_directory.list_members(SecretStore())["ok"]

    def rate_limited(token, method, params, key):
        raise RuntimeError("ratelimited")

    monkeypatch.setattr(slack_directory, "_get_pages", rate_limited)
    assert slack_directory.list_members(secrets) == {"ok": False, "error": "ratelimited"}


def test_a_person_allowed_from_the_picker_is_named_at_once(tmp_path, monkeypatch):
    """Picked from the list, the person is on the allow-list and their chip shows the name
    straight away, before any message from them."""
    from harness.interface.server.manager import SessionManager
    from harness.llm.providers import ModelCapabilities, ProviderClient

    class _NoTurns(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            raise AssertionError("no turns expected")

        def capabilities(self, model):
            return ModelCapabilities()

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path, provider=_NoTurns())
    manager.secrets.put("slack:default", {"bot_token": "xoxb", "allowed_users": []})
    assert manager.allow_user("slack", "U2", display_name="Sam Okafor")["ok"]
    assert manager.secrets.get("slack:default")["allowed_users"] == ["U2"]
    assert manager._people.get("slack:U2") == "Sam Okafor"
