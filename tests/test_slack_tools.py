"""The Slack tools: a catalog of Web API methods, a client that says what to do
about a failure, and tools that turn what a person says into ids.

Hermetic: `slack_api._send` is the one HTTP seam and is replaced, so no test reaches Slack.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from harness.integrations.connectors import slack_api, slack_shape
from harness.integrations.connectors.slack_specs import READ
from harness.integrations.connectors.slack_specs_write import ADMIN, WRITE
from harness.integrations.connectors.tool_builders import slack as tools
from harness.safety.secrets import SecretStore

SPECS = READ + WRITE + ADMIN
BY_NAME = {s.name: s for s in SPECS}


class Wire:
    """A fake Slack: records each exchange, answers from a script keyed by method."""

    def __init__(self, answers=None):
        self.calls: list[dict] = []
        self.answers = answers or {}

    def __call__(self, method, url, *, headers, params, data, json):
        name = url.split("/api/")[-1]
        self.calls.append({"http": method, "method": name, "headers": headers, "params": params, "data": data, "json": json})
        answer = self.answers.get(name, {"ok": True})
        if callable(answer):
            answer = answer(self.calls[-1])
        status = 200
        if isinstance(answer, tuple):
            status, answer = answer
        return SimpleNamespace(status_code=status, headers=answer.get("_headers", {}) if isinstance(answer, dict) else {},
                               json=lambda: answer, text=json_dumps(answer))


def json_dumps(value):
    return json.dumps(value)


@pytest.fixture
def secrets(tmp_path):
    s = SecretStore(tmp_path / "secrets.json")
    s.put("slack:default", {"bot_token": "xoxb-test", "user_token": "xoxp-test"})
    return s


@pytest.fixture
def wire(monkeypatch):
    w = Wire()
    monkeypatch.setattr(slack_api, "_send", w)
    return w


def call(secrets, name, **args):
    return tools.run(BY_NAME[name], secrets, args)


# -- the catalog ------------------------------------------------------------------


def test_every_tool_is_one_row_with_a_method_or_a_handler():
    names = [s.name for s in SPECS]
    assert len(names) == len(set(names))
    for s in SPECS:
        assert bool(s.method) != bool(s.handler), s.name
        assert s.handler == "" or s.handler in tools.HANDLERS, s.name
        assert s.name.startswith("slack_") and s.kind in ("read", "write") and s.group in ("read", "write", "admin")


def test_reads_are_reads_and_everything_that_changes_something_is_a_write():
    assert all(s.kind == "read" for s in READ)
    assert all(s.kind == "write" for s in WRITE + ADMIN)
    assert {s.group for s in READ} == {"read"} and {s.group for s in WRITE} == {"write"} and {s.group for s in ADMIN} == {"admin"}


def test_the_methods_a_bot_may_not_call_declare_a_user_token():
    user_only = {s.name for s in SPECS if s.token == "user"}
    assert {"slack_search_messages", "slack_search_files", "slack_list_reminders", "slack_add_reminder",
            "slack_delete_reminder", "slack_set_status"} == user_only


def test_a_tool_that_pins_a_target_names_an_argument_it_has():
    for s in SPECS:
        if s.target_arg:
            assert s.target_arg in {a.name for a in s.args}, s.name


def test_no_two_arguments_of_a_tool_go_on_the_wire_under_one_name():
    for s in SPECS:
        sent = [a.sent_as for a in s.args]
        assert len(sent) == len(set(sent)), s.name


def test_every_schema_lists_its_required_arguments():
    for s in SPECS:
        schema = tools.schema_for(s)["function"]
        assert schema["name"] == s.name
        assert set(schema["parameters"]["required"]) == {a.name for a in s.args if a.required}


# -- the client -------------------------------------------------------------------


def test_a_user_only_method_without_a_user_token_says_what_to_add(tmp_path, monkeypatch):
    s = SecretStore(tmp_path / "secrets.json")
    s.put("slack:default", {"bot_token": "xoxb-a"})
    monkeypatch.setattr(slack_api, "_send", Wire())
    out = call(s, "slack_search_messages", query="launch")
    assert "user token" in out["error"]


def test_the_bot_token_rides_the_authorization_header_only(secrets, wire):
    call(secrets, "slack_channel_info", channel="C0123456789")
    sent = wire.calls[0]
    assert sent["headers"]["Authorization"] == "Bearer xoxb-test"
    assert "xoxb" not in json.dumps({k: v for k, v in sent.items() if k != "headers"})


@pytest.mark.parametrize(
    "answer,needle",
    [
        ({"ok": False, "error": "not_in_channel"}, "slack_join_channel"),
        ({"ok": False, "error": "missing_scope", "needed": "channels:history"}, "channels:history"),
        ({"ok": False, "error": "token_revoked"}, "reconnect"),
        ({"ok": False, "error": "something_new"}, "something_new"),
    ],
)
def test_a_failure_says_what_to_do_about_it(secrets, monkeypatch, answer, needle):
    monkeypatch.setattr(slack_api, "_send", Wire({"conversations.info": answer}))
    assert needle in call(secrets, "slack_channel_info", channel="C0123456789")["error"]


def test_a_rate_limit_is_waited_out_once(secrets, monkeypatch):
    slept = []
    monkeypatch.setattr(slack_api.time, "sleep", slept.append)
    hits = {"n": 0}

    def answer(_call):
        hits["n"] += 1
        return (429, {"_headers": {"Retry-After": "2"}}) if hits["n"] == 1 else {"ok": True, "channel": {"id": "C0123456789", "name": "general"}}

    monkeypatch.setattr(slack_api, "_send", Wire({"conversations.info": answer}))
    out = call(secrets, "slack_channel_info", channel="C0123456789")
    assert out["name"] == "general" and slept == [2.0]


def test_a_rate_limit_that_asks_for_a_long_wait_is_reported_not_slept(secrets, monkeypatch):
    monkeypatch.setattr(slack_api.time, "sleep", lambda s: pytest.fail("must not sleep this long"))
    monkeypatch.setattr(
        slack_api, "_send", Wire({"conversations.info": (429, {"_headers": {"Retry-After": "300"}})})
    )
    assert "300" in call(secrets, "slack_channel_info", channel="C0123456789")["error"]


def test_a_transport_failure_is_an_error_not_a_crash(secrets, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("dns")

    monkeypatch.setattr(slack_api, "_send", boom)
    assert "could not reach Slack" in call(secrets, "slack_channel_info", channel="C0123456789")["error"]


# -- the tools --------------------------------------------------------------------


def test_reading_a_thread_shapes_messages_and_names_their_authors(secrets, monkeypatch):
    monkeypatch.setattr(
        slack_api,
        "_send",
        Wire(
            {
                "conversations.replies": {
                    "ok": True,
                    "has_more": False,
                    "messages": [
                        {"ts": "1.1", "user": "U0AAAAAAA", "text": "ship it?", "reply_count": 1, "blocks": [{"x": 1}], "team": "T1"},
                        {"ts": "1.2", "thread_ts": "1.1", "user": "U0BBBBBBB", "text": "yes", "reactions": [{"name": "eyes", "count": 2, "users": ["U1"]}]},
                    ],
                },
                "users.info": lambda c: {"ok": True, "user": {"id": c["params"]["user"], "name": "h", "profile": {"display_name": "Ana" if c["params"]["user"].endswith("A") else "Bo"}}},
            }
        ),
    )
    out = call(secrets, "slack_read_thread", channel="C0123456789", ts="1.1")
    assert [m["text"] for m in out["messages"]] == ["ship it?", "yes"]
    assert out["messages"][0]["user_name"] == "Ana" and out["messages"][1]["user_name"] == "Bo"
    assert "blocks" not in out["messages"][0] and out["messages"][1]["thread_ts"] == "1.1"
    assert out["messages"][1]["reactions"] == [{"name": "eyes", "count": 2}]


def test_a_channel_named_by_a_person_is_resolved_and_an_unknown_one_is_refused(secrets, wire, monkeypatch):
    from harness.integrations.connectors import slack_directory

    monkeypatch.setattr(
        slack_directory, "list_channels",
        lambda s, query, limit=25, **k: {"ok": True, "channels": [{"id": "C0GENERAL1", "name": "general", "is_member": True}]},
    )
    call(secrets, "slack_read_channel", channel="#general", limit=5)
    assert wire.calls[0]["params"] == {"channel": "C0GENERAL1", "limit": 5}
    assert "no channel named #nope" in call(secrets, "slack_read_channel", channel="#nope")["error"]


def test_posting_sends_json_and_answers_with_where_it_landed(secrets, wire):
    wire.answers["chat.postMessage"] = {"ok": True, "channel": "C0123456789", "ts": "9.9", "message": {"text": "hi"}}
    out = call(secrets, "slack_post_message", channel="C0123456789", text="hi", thread_ts="1.1",
               blocks='[{"type":"divider"}]')
    sent = wire.calls[0]
    assert sent["http"] == "POST" and sent["json"] == {"channel": "C0123456789", "text": "hi", "thread_ts": "1.1", "blocks": [{"type": "divider"}]}
    assert out == {"ok": True, "channel": "C0123456789", "ts": "9.9"}


def test_a_required_argument_or_bad_json_is_named_before_any_call(secrets, wire):
    assert call(secrets, "slack_post_message", channel="C0123456789")["error"] == "`text` is required"
    assert "valid JSON" in call(secrets, "slack_post_message", channel="C0123456789", text="x", blocks="{nope")["error"]
    assert wire.calls == []


def test_a_person_can_be_named_by_email(secrets, wire):
    wire.answers["users.lookupByEmail"] = {"ok": True, "user": {"id": "U0EMAILAAA"}}
    wire.answers["chat.postEphemeral"] = {"ok": True, "message_ts": "5.5"}
    call(secrets, "slack_post_ephemeral", channel="C0123456789", user="ana@example.com", text="psst")
    assert wire.calls[0]["method"] == "users.lookupByEmail" and wire.calls[0]["params"] == {"email": "ana@example.com"}
    assert wire.calls[1]["json"]["user"] == "U0EMAILAAA"


def test_a_canvas_is_created_from_markdown(secrets, wire):
    wire.answers["canvases.create"] = {"ok": True, "canvas_id": "F0CANVAS1"}
    out = call(secrets, "slack_create_canvas", title="Plan", markdown="# Steps")
    assert wire.calls[0]["json"] == {"title": "Plan", "document_content": {"type": "markdown", "markdown": "# Steps"}}
    assert out["canvas_id"] == "F0CANVAS1"


def test_status_is_a_profile_object_and_needs_the_user_token(secrets, wire):
    call(secrets, "slack_set_status", status_text="In focus", status_emoji=":headphones:")
    assert wire.calls[0]["json"] == {"profile": {"status_text": "In focus", "status_emoji": ":headphones:"}}
    assert wire.calls[0]["headers"]["Authorization"] == "Bearer xoxp-test"


def test_lists_of_people_go_as_csv_on_a_form_method_and_a_list_on_a_json_one(secrets, wire):
    call(secrets, "slack_invite_to_channel", channel="C0123456789", users=["U0AAAAAAA", "U0BBBBBBB"])
    assert wire.calls[0]["data"] == {"channel": "C0123456789", "users": "U0AAAAAAA,U0BBBBBBB"}
    assert wire.calls[0]["json"] is None


def test_search_matches_are_cut_down_to_what_a_reader_needs(secrets, wire):
    wire.answers["search.messages"] = {
        "ok": True,
        "messages": {"total": 1, "paging": {"page": 1}, "matches": [
            {"ts": "2.2", "user": "U0AAAAAAA", "text": "launch plan", "permalink": "https://x/p1",
             "channel": {"id": "C0123456789", "name": "growth"}, "blocks": [1]}]},
    }
    out = call(secrets, "slack_search_messages", query="launch")
    assert out["matches"][0]["channel_name"] == "growth" and out["matches"][0]["permalink"] == "https://x/p1"
    assert "blocks" not in out["matches"][0] and out["total"] == 1


def test_a_long_message_is_clipped_and_says_so():
    long = "x" * (slack_shape.TEXT_CLIP + 50)
    row = slack_shape.message({"ts": "1", "text": long})
    assert row["text"].endswith("[50 more characters]") and len(row["text"]) < len(long)


def test_a_get_message_returns_the_message_and_its_permalink(secrets, wire):
    wire.answers["conversations.history"] = {"ok": True, "messages": [{"ts": "3.3", "user": "U0AAAAAAA", "text": "hey"}]}
    wire.answers["chat.getPermalink"] = {"ok": True, "permalink": "https://x/p3"}
    wire.answers["users.info"] = {"ok": True, "user": {"name": "ana", "profile": {"display_name": "Ana"}}}
    out = call(secrets, "slack_get_message", channel="C0123456789", ts="3.3")
    assert out["text"] == "hey" and out["permalink"] == "https://x/p3" and out["user_name"] == "Ana"
    wire.answers["conversations.history"] = {"ok": True, "messages": []}
    assert "no message" in call(secrets, "slack_get_message", channel="C0123456789", ts="4.4")["error"]


def test_presence_reads_two_methods(secrets, wire):
    wire.answers["users.getPresence"] = {"ok": True, "presence": "away"}
    wire.answers["dnd.info"] = {"ok": True, "dnd_enabled": False, "snooze_enabled": True}
    out = call(secrets, "slack_user_status", user="U0AAAAAAA")
    assert out == {"user": "U0AAAAAAA", "presence": "away", "do_not_disturb_now": True}


def test_every_callable_carries_its_schema(secrets):
    built = tools.slack_tools(secrets)
    assert [f.__name__ for f in built] == [s.name for s in SPECS]
    assert all(f.__tool_schema__["function"]["name"] == f.__name__ for f in built)


# -- how the catalog reaches the product --------------------------------------------


def test_reads_never_ask_and_writes_always_do():
    from harness.integrations.connectors.catalog import approval_for_tool

    for s in SPECS:
        assert approval_for_tool(s.name, default=True) is (s.kind != "read"), s.name


def test_admin_tools_start_switched_off_and_the_rest_on(secrets):
    from harness.integrations.connectors.catalog import tool_dicts

    rows = {t["name"]: t for t in tool_dicts(secrets, "slack")}
    assert set(rows) == set(BY_NAME)
    assert {n for n, t in rows.items() if not t["enabled"]} == {s.name for s in ADMIN}
    assert {t["group"] for t in rows.values()} == {"read", "write", "admin"}


def test_a_person_can_switch_an_admin_tool_on(secrets):
    from harness.integrations.connectors.lifecycle import update_connector_tools
    from harness.integrations.connectors.catalog import tool_dicts

    assert update_connector_tools(secrets, "slack", {"slack_create_channel": True})["ok"]
    assert {t["name"]: t["enabled"] for t in tool_dicts(secrets, "slack")}["slack_create_channel"] is True


def test_a_standing_rule_can_pin_a_channel_but_never_an_admin_target_less_tool():
    from harness.integrations.connectors.catalog import target_arg_for

    assert target_arg_for("slack_post_message") == "channel"
    assert target_arg_for("slack_search_messages") is None
    assert target_arg_for("slack_create_channel") is None  # creating has no existing target to pin


def test_the_engine_gets_the_tools_only_once_slack_is_connected(tmp_path):
    from harness.modes.agents import reeve_agent
    from harness.compose import build_engine

    class Stub:
        def complete(self, **k):  # never called
            raise AssertionError

    s = SecretStore(tmp_path / "secrets.json")
    bare = build_engine(agent=reeve_agent(), workspace=tmp_path, provider=Stub(), secrets=s)
    assert not any(n.startswith("slack_") for n in bare.registry.names())
    s.put("slack:default", {"bot_token": "xoxb-x", "app_token": "xapp-x", "enabled": True})
    live = build_engine(agent=reeve_agent(), workspace=tmp_path, provider=Stub(), secrets=s)
    names = {n for n in live.registry.names() if n.startswith("slack_")}
    on = {sp.name for sp in READ + WRITE}
    assert names == on  # admin tools are off until switched on


# -- the optional user token --------------------------------------------------------


def test_a_user_token_is_checked_and_kept_beside_the_bot_token_and_can_be_removed(tmp_path, monkeypatch):
    from harness.integrations.connectors.slack_user_token import has_user_token, set_user_token

    s = SecretStore(tmp_path / "secrets.json")
    s.put("slack:default", {"bot_token": "xoxb-1"})
    monkeypatch.setattr(slack_api, "_send", Wire({"auth.test": {"ok": True, "team": "Acme", "user": "ana"}}))
    assert set_user_token(s, "xoxp-good") == {"ok": True, "user": "ana", "team": "Acme"}
    assert has_user_token(s) and s.get("slack:default")["bot_token"] == "xoxb-1"
    assert set_user_token(s, "") == {"ok": True, "removed": True}
    assert not has_user_token(s)


def test_a_token_of_the_wrong_kind_or_one_slack_rejects_is_refused(tmp_path, monkeypatch):
    from harness.integrations.connectors.slack_user_token import has_user_token, set_user_token

    s = SecretStore(tmp_path / "secrets.json")
    s.put("slack:default", {"bot_token": "xoxb-1"})
    assert "xoxp-" in set_user_token(s, "xoxb-a-bot-token")["error"]
    monkeypatch.setattr(slack_api, "_send", Wire({"auth.test": {"ok": False, "error": "invalid_auth"}}))
    assert "reconnect" in set_user_token(s, "xoxp-bad")["error"]
    assert not has_user_token(s)
    assert "not connected" in set_user_token(SecretStore(tmp_path / "none.json"), "xoxp-x")["error"]


def test_the_route_and_the_connector_list_report_the_token_without_showing_it(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server import SessionManager, create_app

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path)
    manager.secrets.put("slack:default", {"bot_token": "xoxb-1", "app_token": "xapp-1"})
    monkeypatch.setattr(slack_api, "_send", Wire({"auth.test": {"ok": True, "team": "Acme", "user": "ana"}}))
    with TestClient(create_app(manager)) as client:
        assert client.post("/v1/connectors/slack/user-token", json={"token": "xoxp-good"}).json()["ok"]
        row = next(c for c in client.get("/v1/connectors").json()["connectors"] if c["name"] == "slack")
        assert row["has_user_token"] is True
        assert "xoxp-good" not in json.dumps(row)


def test_connector_tools_leave_the_wire_under_progressive_discovery(tmp_path):
    """Every connector tool is situational (amendment 5): Gmail's 36 schemas rode every
    request of a session that never touched the mailbox. They are deferred where the connector
    tools are assembled, and the index lists them under their connector."""
    from harness.integrations.connectors import make_integration_tools
    from harness.tools import ToolRegistry
    from harness.tools.discovery import TOOL_SEARCH, discovery_tools

    s = SecretStore(tmp_path / "secrets.json")
    built = make_integration_tools(s, enabled_connectors={"slack", "gmail", "google_calendar"})
    registry = ToolRegistry()
    registry.set_progressive_discovery(True)
    registry.register_all(built)
    # The bridge is registered whenever the mode is on (`build_engine`); without it nothing is deferred.
    registry.register_all(discovery_tools(registry))
    names = {f.__name__ for f in built}
    assert set(BY_NAME) <= names and any(n.startswith("gmail_") for n in names)
    assert set(registry.deferred_names()) == names
    wire = {sc["function"]["name"]: sc for sc in registry.schemas()}
    assert not names & set(wire)
    index = wire[TOOL_SEARCH]["function"]["description"]
    assert "- slack: slack_" in index and "- gmail: gmail_" in index and "- google_calendar: " in index
