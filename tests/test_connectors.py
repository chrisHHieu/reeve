"""Connectors: how a message is addressed and sent, who may reach the agent, the gateway that
carries messages both ways, and the catalog a person connects apps from.

Everything runs offline. Platforms are the `FakeAdapter` or a recorded `connector_calls.request`;
no test opens a socket to Slack, Telegram, Google or Microsoft.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from harness.integrations.connectors import (
    ConnectorSettings,
    FakeAdapter,
    Gateway,
    MessageEvent,
    SessionSource,
    accounts,
    connect_connector,
    connector_list,
    disconnect_connector,
    format_target,
    is_authorized,
    make_send_message_tool,
    parse_target,
)
from harness.integrations.connectors import calls as connector_calls
from harness.integrations.connectors.config import load_settings
from harness.integrations.connectors.descriptors import ValidationResult, get_descriptor, list_descriptors
from harness.integrations.connectors.messaging import SendResult
from harness.safety.secrets import SecretStore


def _store(tmp_path) -> SecretStore:
    return SecretStore(tmp_path / "secrets.json")


def _by_name(secrets: SecretStore) -> dict[str, dict]:
    return {entry["name"]: entry for entry in connector_list(secrets)}


def _record_requests(monkeypatch, answer=lambda method, url: {"ok": True, "data": {}}) -> list[dict]:
    """Every call a connector tool makes, instead of the network; `answer` decides the reply."""
    seen: list[dict] = []

    def request(method, url, *, headers=None, params=None, json=None, auth=None):
        seen.append({"method": method, "url": url, "headers": headers or {}, "params": params, "json": json})
        return answer(method, url)

    monkeypatch.setattr(connector_calls, "request", request)
    return seen


def _tools(secrets: SecretStore) -> dict:
    from harness.integrations.connectors.integration_tools import make_integration_tools

    return {tool.__name__: tool for tool in make_integration_tools(secrets)}


def _sign_in(secrets: SecretStore, name: str, profile: dict) -> dict:
    """Add an account the way a browser sign-in does: keyed by the descriptor's account field."""
    return accounts.add_account(secrets, name, accounts.derive_account_id(get_descriptor(name), profile), profile)


# -- addressing a message ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "chat", "thread", "token"),
    [("telegram", "12345", None, "telegram:12345"), ("slack", "C1", "168.9", "slack:C1:168.9")],
)
def test_a_target_is_platform_chat_and_thread_and_reads_back_the_same(platform, chat, thread, token):
    assert format_target(platform, chat, *([thread] if thread else [])) == token
    assert parse_target(token) == (platform, chat, thread)


@pytest.mark.parametrize("token", ["", "telegram", "telegram:", ":123"])
def test_a_target_without_both_a_platform_and_a_chat_is_refused(token):
    with pytest.raises(ValueError):
        parse_target(token)


def test_where_a_message_came_from_names_its_reply_target_and_its_sender():
    dm = SessionSource(platform="telegram", chat_id="42", user_name="Alice", chat_type="dm")
    assert dm.target == "telegram:42"
    assert "Alice" in dm.label() and "telegram" in dm.label()
    # The text the agent reads carries the handle a reply goes back to.
    channel = SessionSource(platform="slack", chat_id="C9", user_name="Bob", chat_type="channel")
    tagged = MessageEvent(text="ship it", source=channel).tagged_text()
    assert "reply→slack:C9" in tagged and "ship it" in tagged


# -- send_message --------------------------------------------------------------------------------


def _send_tool(secrets: SecretStore, sent: list[dict] | None = None):
    def send(token, chat_id, text, thread_id=None):
        if sent is not None:
            sent.append({"token": token, "chat_id": chat_id, "text": text, "thread_id": thread_id})
        return SendResult(True, message_id="99")

    return make_send_message_tool(secrets, senders={"telegram": send, "slack": send})


def test_send_message_posts_with_the_stored_bot_token_and_always_asks_first(tmp_path):
    secrets = _store(tmp_path)
    secrets.put("telegram:default", {"type": "token", "bot_token": "T0K"})
    sent: list[dict] = []
    tool = _send_tool(secrets, sent)
    assert tool(target="telegram:12345", text="hello") == {"ok": True, "message_id": "99", "target": "telegram:12345"}
    assert sent == [{"token": "T0K", "chat_id": "12345", "text": "hello", "thread_id": None}]
    assert tool.__aisuite_tool_metadata__.requires_approval is True
    assert tool.__tool_schema__["function"]["name"] == "send_message"


@pytest.mark.parametrize("target", ["telegram:1", "nonsense"])
def test_send_message_without_a_token_or_a_readable_target_says_so(tmp_path, target):
    assert "error" in _send_tool(_store(tmp_path))(target=target, text="x")


def test_a_platform_nobody_can_send_to_is_named_with_the_ones_that_work(tmp_path):
    # A bare "unknown platform: discord" leaves a non-developer nothing to do.
    said = _send_tool(_store(tmp_path))(target="discord:1", text="x")["error"]
    assert "discord" in said and "slack" in said and "telegram" in said


# -- who may reach the agent ----------------------------------------------------------------------


def test_only_listed_senders_reach_the_agent_unless_the_connector_is_open_to_all():
    listed = ConnectorSettings(platform="telegram", allowed_users={"u1"})
    assert is_authorized(listed, SessionSource("telegram", "c", user_id="u1"))
    assert not is_authorized(listed, SessionSource("telegram", "c", user_id="u2"))
    assert not is_authorized(ConnectorSettings("telegram"), SessionSource("telegram", "c", user_id="u1"))
    assert is_authorized(ConnectorSettings("telegram", allow_all=True), SessionSource("telegram", "c", user_id="x"))


def test_settings_come_from_the_stored_profile_and_a_platform_without_a_token_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    secrets = _store(tmp_path)
    secrets.put("telegram:default", {"type": "token", "bot_token": "T", "allowed_users": ["u1"]})
    settings = load_settings(secrets)
    assert settings["telegram"].enabled is True and settings["telegram"].allowed_users == {"u1"}
    assert settings["slack"].enabled is False


def test_the_environment_can_name_the_allowed_senders(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "a, b ,c")
    secrets = _store(tmp_path)
    secrets.put("telegram:default", {"bot_token": "T"})
    assert load_settings(secrets)["telegram"].allowed_users == {"a", "b", "c"}


# -- the gateway ----------------------------------------------------------------------------------


def _gateway(**settings) -> tuple[Gateway, FakeAdapter]:
    gateway = Gateway(settings={"fake": ConnectorSettings("fake", enabled=True, **settings)})
    adapter = FakeAdapter()
    gateway.register(adapter)
    return gateway, adapter


async def test_the_gateway_hands_on_what_an_allowed_sender_says_and_drops_the_rest():
    heard: list[MessageEvent] = []

    async def hear(event: MessageEvent) -> None:
        heard.append(event)

    gateway, adapter = _gateway(allowed_users={"u1"})
    gateway.set_handler(hear)
    assert await gateway.start() == ["fake"] and adapter.connected
    await adapter.inject("hi", user_id="u1")
    await adapter.inject("nope", user_id="intruder")
    assert [event.text for event in heard] == ["hi"]
    await gateway.stop()
    assert not adapter.connected


async def test_a_reply_is_delivered_through_the_platforms_adapter():
    gateway, adapter = _gateway(allow_all=True)
    assert (await gateway.deliver("fake:c9", "pong")).ok

    async def echo(event: MessageEvent) -> None:
        await gateway.deliver(event.source.target, f"echo: {event.text}")

    gateway.set_handler(echo)
    await adapter.inject("ping", chat_id="c1", user_id="u1")
    assert adapter.outbox == [
        {"chat_id": "c9", "text": "pong", "thread_id": None},
        {"chat_id": "c1", "text": "echo: ping", "thread_id": None},
    ]


async def test_recent_senders_include_the_refused_newest_first_and_once_each():
    """The allow-list is filled from who tried to write, so a refused sender is still recorded."""
    gateway, adapter = _gateway(allowed_users={"u1"})
    await adapter.inject("hi", user_id="u2", user_name="Bob")
    await adapter.inject("yo", user_id="u1", user_name="Al")
    recent = gateway.recent_senders()
    assert [row["user_id"] for row in recent] == ["u1", "u2"] and recent[1]["user_name"] == "Bob"
    await adapter.inject("again", user_id="u2")
    assert [row["user_id"] for row in gateway.recent_senders("fake")] == ["u2", "u1"]


def test_the_manager_allows_and_disallows_a_sender_on_a_connected_platform_only(tmp_path, monkeypatch):
    from harness.interface.server.manager import SessionManager

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    connect_connector(manager.secrets, "telegram", {"bot_token": "T"}, validate=False)
    assert manager.allow_user("telegram", "12345")["allowed_users"] == ["12345"]
    assert manager.secrets.get("telegram:default")["allowed_users"] == ["12345"]
    assert manager.disallow_user("telegram", "12345")["allowed_users"] == []
    assert manager.allow_user("slack", "x")["ok"] is False


# -- what a platform's event becomes --------------------------------------------------------------


def test_a_telegram_text_becomes_an_event_and_anything_else_is_ignored():
    from harness.integrations.connectors import telegram_message_to_event

    event = telegram_message_to_event(
        SimpleNamespace(
            text="hello", message_id=7, chat=SimpleNamespace(id=12345, type="private"),
            from_user=SimpleNamespace(id=99, full_name="Alice"), message_thread_id=None,
        )
    )
    assert (event.text, event.source.target, event.source.user_id, event.source.chat_type) == (
        "hello", "telegram:12345", "99", "dm",
    )
    sticker = SimpleNamespace(text=None, chat=SimpleNamespace(id=1, type="private"))
    assert telegram_message_to_event(sticker) is None


def test_a_slack_message_becomes_an_event_but_the_bots_own_and_edits_do_not():
    """The bot's own posts must not come back in, or every reply would answer itself."""
    from harness.integrations.connectors import slack_event_to_event

    event = slack_event_to_event({"text": "ship it", "channel": "C9", "user": "U1", "channel_type": "channel", "ts": "1.2"}, "BOT")
    assert (event.text, event.source.target, event.source.chat_type) == ("ship it", "slack:C9", "channel")
    assert slack_event_to_event({"text": "x", "user": "BOT"}, "BOT") is None
    assert slack_event_to_event({"text": "x", "bot_id": "B1"}, None) is None
    assert slack_event_to_event({"subtype": "message_changed", "text": "x"}, None) is None


def test_an_adapter_is_built_only_from_a_complete_profile():
    from harness.integrations.connectors import SlackAdapter, TelegramAdapter, make_adapter

    assert isinstance(make_adapter("telegram", {"bot_token": "T"}), TelegramAdapter)
    assert isinstance(make_adapter("slack", {"bot_token": "x", "app_token": "y"}), SlackAdapter)
    assert make_adapter("slack", {"bot_token": "x"}) is None
    assert make_adapter("telegram", {}) is None


@pytest.mark.parametrize(
    ("method", "api", "reply", "name"),
    [
        ("resolve_user_name", "users_info", {"user": {"name": "ann", "profile": {"display_name": "Ann"}}}, "Ann"),
        ("resolve_channel_name", "conversations_info", {"channel": {"id": "C1", "name": "reeve-test"}}, "reeve-test"),
    ],
)
async def test_slack_names_are_looked_up_once_and_a_failed_lookup_falls_back_to_none(method, api, reply, name):
    from harness.integrations.connectors import SlackAdapter

    asked: list[str] = []

    async def answer(**named):
        asked.extend(named.values())
        return reply

    async def broken(**named):
        raise RuntimeError("nope")

    adapter = SlackAdapter("b", "x")
    adapter._app = SimpleNamespace(client=SimpleNamespace(**{api: answer}))
    resolve = getattr(adapter, method)
    assert await resolve("X1") == name
    assert await resolve("X1") == name
    assert asked == ["X1"]
    adapter._app.client = SimpleNamespace(**{api: broken})
    assert await resolve("X2") is None  # the caller shows the id instead
    assert await resolve("") is None


# -- tools an agent gets from a connector ---------------------------------------------------------


class _NoProvider:
    """Building an engine never calls the model."""

    def complete(self, **kwargs):  # pragma: no cover
        raise AssertionError("not called")

    def capabilities(self, model):  # pragma: no cover
        from harness.llm.providers.base import ModelCapabilities

        return ModelCapabilities()


def test_connector_tools_reach_the_agents_that_work_with_apps_once_one_is_connected(tmp_path):
    from harness.compose import build_engine
    from harness.modes.agents import chat_agent, reeve_agent, myhelper_agent

    secrets = _store(tmp_path)

    def names(agent, **kwargs):
        return build_engine(agent=agent(), provider=_NoProvider(), secrets=secrets, **kwargs).registry.names()

    assert "send_message" not in names(chat_agent)
    secrets.put("telegram:default", {"bot_token": "T"})
    assert "send_message" not in names(chat_agent)
    assert "send_message" in names(myhelper_agent, workspace=tmp_path)
    agent = names(reeve_agent, workspace=tmp_path)
    assert "send_message" in agent and "jira_search_issues" not in agent

    secrets.put("jira:default", {"base_url": "https://example.atlassian.net", "email": "me@example.com", "api_token": "tok", "enabled": True})
    registry = build_engine(agent=reeve_agent(), workspace=tmp_path, provider=_NoProvider(), secrets=secrets).registry
    # A read of the tracker runs freely; creating an issue still asks.
    assert registry.get("jira_search_issues").metadata.requires_approval is False
    assert registry.get("jira_create_issue").metadata.requires_approval is True


def test_drive_reads_a_google_doc_as_text_and_refuses_a_type_with_no_text(tmp_path, monkeypatch):
    secrets = _store(tmp_path)
    secrets.put("google_drive:default", {"access_token": "ya29.x", "enabled": True})
    kinds = {"f1": "application/vnd.google-apps.document", "f2": "application/vnd.google-apps.drawing"}

    def answer(method, url):
        if url.endswith("/export"):
            return {"ok": True, "data": "Doc body text"}
        return {"ok": True, "data": {"id": "f1", "name": "Plan", "mimeType": kinds[url.rsplit("/", 1)[-1]]}}

    _record_requests(monkeypatch, answer)
    read = _tools(secrets)["drive_read_file"]
    assert read("f1") == {**read("f1"), "ok": True, "content": "Doc body text"}
    assert "cannot read" in read("f2")["error"]  # rather than dumping a binary


def test_a_notion_page_reads_as_the_plain_text_of_its_blocks(tmp_path, monkeypatch):
    secrets = _store(tmp_path)
    accounts.add_account(secrets, "notion", "ws1", {"access_token": "t"})
    blocks = [
        {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Title"}]}},
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Body text"}]}},
        {"type": "divider", "divider": {}},
    ]

    def answer(method, url):
        if "/blocks/" in url:
            return {"ok": True, "data": {"results": blocks}}
        return {"ok": True, "data": {"properties": {"p": 1}, "url": "https://n/x"}}

    _record_requests(monkeypatch, answer)
    page = _tools(secrets)["notion_read_page"]("pg1")
    assert (page["text"], page["account"], page["url"]) == ("Title\nBody text", "ws1", "https://n/x")


# -- accounts -------------------------------------------------------------------------------------


def test_each_notion_workspace_is_its_own_account_and_the_first_stays_default(tmp_path):
    secrets = _store(tmp_path)
    assert _sign_in(secrets, "notion", {"access_token": "t1", "account": "Main Workspace", "account_id": "ws-1"})["ok"]
    _sign_in(secrets, "notion", {"access_token": "t2", "account": "Ops Space", "account_id": "ws-2"})
    assert [key for key, _ in accounts.list_accounts(secrets, "notion")] == ["ws-1", "ws-2"]
    first = accounts.account_rows(secrets, "notion")[0]
    assert first["name"] == "Main Workspace" and first["default"]


def test_google_drive_accounts_are_keyed_by_email_not_the_opaque_google_id(tmp_path):
    secrets = _store(tmp_path)
    _sign_in(secrets, "google_drive", {"access_token": "t1", "account": "me@example.com", "account_id": "100000000000000000001"})
    _sign_in(secrets, "google_drive", {"access_token": "t2", "account": "work@example.org", "account_id": "100000000000000000002"})
    assert [key for key, _ in accounts.list_accounts(secrets, "google_drive")] == ["me@example.com", "work@example.org"]
    assert accounts.resolve(secrets, "google_drive", "work@example.org")[2]["access_token"] == "t2"


def test_an_outlook_tool_uses_the_mailbox_it_names_and_otherwise_the_first(tmp_path, monkeypatch):
    secrets = _store(tmp_path)
    for email, token in (("me@example.com", "g1"), ("ops@example.org", "g2")):
        _sign_in(secrets, "outlook", {"access_token": token, "account": email})
    assert [key for key, _ in accounts.list_accounts(secrets, "outlook")] == ["me@example.com", "ops@example.org"]
    seen = _record_requests(monkeypatch)
    tools = _tools(secrets)
    assert tools["outlook_search_messages"]("q", account="ops@example.org")["account"] == "ops@example.org"
    assert seen[-1]["headers"]["Authorization"] == "Bearer g2"
    assert tools["outlook_list_events"]()["account"] == "me@example.com"
    # A plain listing is the next week's calendar view, recurring events expanded.
    assert seen[-1]["url"] == "https://graph.microsoft.com/v1.0/me/calendarView"


def test_outlook_calendar_tools_map_onto_microsoft_graph(tmp_path, monkeypatch):
    secrets = _store(tmp_path)
    _sign_in(secrets, "outlook", {"access_token": "tok", "account": "me@example.com"})
    seen = _record_requests(monkeypatch)
    tools = _tools(secrets)

    tools["outlook_create_event"](
        "Sync", "2026-07-20T10:00:00", "2026-07-20T10:30:00",
        attendees="a@x.com, b@y.com", location="Room 4", teams_meeting=True,
    )
    created = seen[-1]["json"]
    assert [a["emailAddress"]["address"] for a in created["attendees"]] == ["a@x.com", "b@y.com"]
    assert created["location"] == {"displayName": "Room 4"} and created["isOnlineMeeting"] is True

    tools["outlook_update_event"]("ev1", subject="Moved", start="2026-07-21T10:00:00")
    assert (seen[-1]["method"], seen[-1]["url"].rsplit("/me/", 1)[1], set(seen[-1]["json"])) == (
        "PATCH", "events/ev1", {"subject", "start"},  # only what changed is sent
    )
    tools["outlook_delete_event"]("ev1")
    assert seen[-1]["method"] == "DELETE" and seen[-1]["url"].endswith("/me/events/ev1")

    tools["outlook_respond_event"]("ev1", "tentative", comment="might be late")
    assert seen[-1]["url"].endswith("/me/events/ev1/tentativelyAccept")
    assert seen[-1]["json"] == {"comment": "might be late", "sendResponse": True}
    assert "error" in tools["outlook_respond_event"]("ev1", "maybe")

    tools["outlook_list_events"](start="2026-07-20T00:00:00Z", end="2026-07-22T00:00:00Z")
    window = seen[-1]["params"]
    assert (window["startDateTime"], window["endDateTime"], window["$orderby"]) == (
        "2026-07-20T00:00:00Z", "2026-07-22T00:00:00Z", "start/dateTime",
    )


# -- the catalog and connecting -------------------------------------------------------------------


def test_the_catalog_says_what_each_connector_does_before_anything_is_connected(tmp_path):
    listed = _by_name(_store(tmp_path))
    telegram, slack, github, gmail = (listed[name] for name in ("telegram", "slack", "github", "gmail"))
    assert telegram["two_way"] is True and telegram["connected"] is False
    # Receiving messages is narrower than two-way: a session subscribes to a chat's channels.
    assert telegram["channels"] is True and slack["channels"] is True
    # GitHub only acts: its inbound needed a public webhook URL, which a desktop does not have.
    assert github["two_way"] is False and github["channels"] is False
    assert github["available"] is True and github["connected"] is False
    assert gmail["available"] is True and gmail["connected"] is False
    assert "bot_token" in {field["key"] for field in telegram["fields"]} and telegram["instructions"]


def test_every_connector_says_what_it_can_access_and_the_available_ones_in_their_own_words(tmp_path):
    """An empty Access list reads as "this app tells you nothing about what it can do"."""
    from harness.integrations.connectors.catalog_copy import ACCESS

    for entry in connector_list(_store(tmp_path)):
        assert isinstance(entry["about"], str)
        assert entry["access"] and all(isinstance(line, str) and line for line in entry["access"]), entry["name"]
    generic = [d.name for d in list_descriptors() if d.available and not d.experimental and d.name not in ACCESS]
    assert generic == []


def test_connecting_stores_the_profile_lists_it_and_disconnecting_removes_it(tmp_path):
    secrets = _store(tmp_path)
    assert connect_connector(secrets, "telegram", {"bot_token": "T0K", "allowed_users": "u1, u2"}, validate=False)["ok"]
    profile = secrets.get("telegram:default")
    assert (profile["bot_token"], profile["allowed_users"], profile["enabled"]) == ("T0K", ["u1", "u2"], True)
    listed = _by_name(secrets)["telegram"]
    assert (listed["connected"], listed["enabled"], listed["allowed_users"]) == (True, True, ["u1", "u2"])
    assert disconnect_connector(secrets, "telegram")["ok"] is True
    assert secrets.get("telegram:default") is None


def test_a_connector_with_several_fields_is_connected_and_its_tools_switch_one_by_one(tmp_path):
    from harness.integrations.connectors import update_connector_tools

    secrets = _store(tmp_path)
    assert connect_connector(secrets, "github", {"token": "ghp_test"}, validate=False)["ok"] is True
    jira = {"base_url": "https://example.atlassian.net", "email": "me@example.com", "api_token": "tok"}
    assert connect_connector(secrets, "jira", jira, validate=False)["ok"] is True
    listed = _by_name(secrets)
    for name in ("github", "jira"):
        assert listed[name]["connected"] is True and listed[name]["enabled"] is True
    assert update_connector_tools(secrets, "github", {"github_clone": False})["ok"] is True
    tools = {tool["name"]: tool["enabled"] for tool in _by_name(secrets)["github"]["tools"]}
    assert tools["github_clone"] is False and tools["github_pull"] is True


def test_reconnecting_with_the_masked_token_or_a_blank_list_keeps_what_was_stored(tmp_path):
    """The form sends the field's mask back for a secret it never showed; that must not become the
    stored token, and an empty allow-list field must not empty the list."""
    secrets = _store(tmp_path)
    mask = next(field.placeholder for field in get_descriptor("telegram").fields if field.key == "bot_token")
    connect_connector(secrets, "telegram", {"bot_token": "REAL-TOKEN-123", "allowed_users": "u1, u2"}, validate=False)
    connect_connector(secrets, "telegram", {"bot_token": mask, "allowed_users": ""}, validate=False)
    assert secrets.get("telegram:default")["bot_token"] == "REAL-TOKEN-123"
    assert secrets.get("telegram:default")["allowed_users"] == ["u1", "u2"]
    connect_connector(secrets, "telegram", {"bot_token": "NEW-TOKEN-999"}, validate=False)
    assert secrets.get("telegram:default")["bot_token"] == "NEW-TOKEN-999"
    assert secrets.get("telegram:default")["allowed_users"] == ["u1", "u2"]


def test_a_missing_required_field_is_refused_by_name(tmp_path):
    result = connect_connector(_store(tmp_path), "slack", {"bot_token": "xoxb"}, validate=False)
    assert result["ok"] is False and "missing" in result["error"]


def test_reconnecting_slack_by_hand_keeps_who_may_message_the_bot(tmp_path):
    secrets = _store(tmp_path)
    secrets.put("slack:default", {
        "bot_token": "xoxb-old", "app_token": "xapp-old",
        "allowed_users": ["U_OWNER"], "approval_owner_ids": ["U_OWNER"],
    })
    assert connect_connector(secrets, "slack", {"bot_token": "xoxb-new", "app_token": "xapp-new"}, validate=False)["ok"]
    profile = secrets.get("slack:default")
    assert profile["allowed_users"] == ["U_OWNER"]
    # Answering approvals from Slack is gone, so its owner list is not carried over.
    assert "approval_owner_ids" not in profile


def test_a_validated_connect_records_the_account_the_platform_named(tmp_path, monkeypatch):
    secrets = _store(tmp_path)
    monkeypatch.setattr(get_descriptor("telegram"), "validate", lambda creds: ValidationResult(True, identity="@mybot"))
    assert connect_connector(secrets, "telegram", {"bot_token": "T"}) == {"ok": True, "account": "@mybot"}
    assert secrets.get("telegram:default")["account"] == "@mybot"


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server.app import create_app
    from harness.interface.server.manager import SessionManager

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))


def test_the_connectors_api_connects_lists_and_disconnects_without_echoing_a_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(get_descriptor("telegram"), "validate", lambda creds: ValidationResult(True, identity="@testbot"))
    client = _client(tmp_path, monkeypatch)

    def telegram():
        return {c["name"]: c for c in client.get("/v1/connectors").json()["connectors"]}["telegram"]

    assert telegram()["connected"] is False
    answer = client.post("/v1/connectors/telegram/connect", json={"fields": {"bot_token": "T0K", "allowed_users": "u1"}})
    assert answer.json() == {"ok": True, "account": "@testbot"}
    assert telegram()["connected"] is True and telegram()["account"] == "@testbot"
    assert "T0K" not in client.get("/v1/connectors").text
    assert client.post("/v1/connectors/telegram/disconnect").json()["ok"] is True
    assert telegram()["connected"] is False


def test_no_connector_and_no_tool_name_is_listed_twice():
    """A full descriptor once sat beside a stale placeholder of the same name: the page drew the
    connector twice and two tools collided."""
    from harness.integrations.connectors.catalog import TOOL_DEFS
    from harness.integrations.connectors.descriptors import DESCRIPTORS

    for names in ([d.name for d in DESCRIPTORS], [tool.name for tool in TOOL_DEFS]):
        assert len(names) == len(set(names)), sorted({n for n in names if names.count(n) > 1})


# -- experimental connectors ----------------------------------------------------------------------


@pytest.fixture
def risky_connector():
    """A connector registered through the hook the experimental package uses, removed after."""
    import harness.integrations.connectors.descriptors as d

    descriptor = d.ConnectorDescriptor(
        name="dangerzone", title="Danger Zone", icon="!", blurb="Test-only experimental connector.",
        auth="token", two_way=False, fields=[d.Field("token", "Token", secret=True)],
        instructions=["test only"], experimental=True, risk_notice="This may eat your laundry.",
    )
    d.register_descriptor(descriptor)
    yield descriptor
    d.DESCRIPTORS.remove(descriptor)
    d._BY_NAME.pop(descriptor.name, None)


def test_an_experimental_connector_is_listed_only_while_the_setting_is_on(tmp_path, risky_connector):
    from harness.integrations.connectors import set_experimental_enabled

    secrets = _store(tmp_path)
    assert "dangerzone" not in _by_name(secrets)
    assert set_experimental_enabled(secrets, True)["enabled"] is True
    listed = _by_name(secrets)
    assert listed["dangerzone"]["experimental"] is True and "laundry" in listed["dangerzone"]["risk_notice"]
    assert listed["github"]["experimental"] is False and listed["whatsapp"]["experimental"] is False
    set_experimental_enabled(secrets, False)
    assert "dangerzone" not in _by_name(secrets)


def test_connecting_an_experimental_connector_needs_the_setting_and_the_risk_acknowledged(tmp_path, risky_connector):
    from harness.integrations.connectors import set_experimental_enabled

    secrets = _store(tmp_path)
    refused = connect_connector(secrets, "dangerzone", {"token": "t"}, validate=False)
    assert refused["ok"] is False and "disabled" in refused["error"]
    set_experimental_enabled(secrets, True)
    unacknowledged = connect_connector(secrets, "dangerzone", {"token": "t"}, validate=False)
    assert unacknowledged["ok"] is False and "acknowledgment" in unacknowledged["error"]
    assert "laundry" in unacknowledged["risk_notice"]  # for the card to show
    assert connect_connector(secrets, "dangerzone", {"token": "t"}, validate=False, acknowledged=True)["ok"] is True
    # Turned off again, it is hidden even though it is connected.
    set_experimental_enabled(secrets, False)
    assert "dangerzone" not in _by_name(secrets)
    # An ordinary connector never asks for the acknowledgment.
    assert connect_connector(secrets, "github", {"token": "ghp_x"}, validate=False)["ok"] is True


def test_the_settings_api_turns_experimental_connectors_on_and_the_connect_asks_for_the_risk(
    tmp_path, monkeypatch, risky_connector
):
    client = _client(tmp_path, monkeypatch)

    def names():
        return {c["name"] for c in client.get("/v1/connectors").json()["connectors"]}

    assert client.get("/v1/settings").json()["experimental_connectors"] is False and "dangerzone" not in names()
    assert client.post("/v1/settings/experimental-connectors", json={"value": True}).json()["enabled"] is True
    assert client.get("/v1/settings").json()["experimental_connectors"] is True and "dangerzone" in names()
    asked = client.post("/v1/connectors/dangerzone/connect", json={"fields": {"token": "T"}}).json()
    assert asked["ok"] is False and "acknowledgment" in asked["error"]
    body = {"fields": {"token": "T"}, "acknowledge_risk": True}
    assert client.post("/v1/connectors/dangerzone/connect", json=body).json()["ok"] is True


def test_only_the_experimental_package_marks_connectors_experimental():
    """The loader decides what is experimental, not the descriptor; none is offered unless the
    person opted in (`test_whatsapp_connector.py`)."""
    from harness.integrations.connectors.descriptors import DESCRIPTORS
    from harness.integrations.connectors.experimental import EXPERIMENTAL_DESCRIPTORS

    # Empty since WhatsApp (personal) left experimental on 2026-10-21; the package stays
    # for the next use-at-your-own-risk connector.
    assert [d.name for d in EXPERIMENTAL_DESCRIPTORS] == []
    names = {d.name for d in EXPERIMENTAL_DESCRIPTORS}
    assert all(d.experimental is (d.name in names) for d in DESCRIPTORS if d.name != "dangerzone")
    assert all(d.risk_notice for d in EXPERIMENTAL_DESCRIPTORS)


def test_every_connector_with_a_working_sign_in_advertises_one_click(tmp_path, monkeypatch):
    """The GUI shows its Connect button from `one_click`, and nothing else does.

    This was keyed on `mcp_url` alone. When the six Google connectors moved off MCP —
    keeping a browser sign-in that works — the flag went false and their cards lost the
    button entirely: connecting was still possible from the API, and impossible from the
    UI. Nothing failed, nothing logged; the card simply sat there.

    So assert the property that matters: any connector whose build CAN start a browser
    sign-in must say so.
    """
    from harness.integrations.connectors.lifecycle.listing import _mcp_one_click_ready

    # conftest redirects REEVE_STATE_DIR, so no client id is visible by default and
    # every pkce connector would be skipped — leaving a test that cannot fail. Supply
    # them, which is also the state a real install is in.
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "x.apps.googleusercontent.com")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "y.apps.googleusercontent.com")

    by_name = _by_name(_store(tmp_path))
    missing = []
    for d in list_descriptors():
        # An MCP server authenticated by a pasted API key has no browser flow to
        # advertise — the button would open nothing (firecrawl, 2026-09-07).
        can_sign_in = (
            (bool(d.mcp_url) or getattr(d, "pkce_auth", False))
            and not getattr(d, "mcp_api_key_auth", False)
            and _mcp_one_click_ready(d)
        )
        if not can_sign_in:
            continue
        entry = by_name.get(d.name)
        if entry is None or not entry.get("one_click"):
            missing.append(d.name)
    assert missing == [], f"these have a working browser sign-in but no button in the GUI: {missing}"


# -- the shared request helper --------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status, content=b"", ctype="application/json"):
        self.status_code = status
        self.content = content
        self.headers = {"content-type": ctype}

    @property
    def text(self):
        return self.content.decode()

    def json(self):
        import json as _json

        return _json.loads(self.content)


def _answer_with(monkeypatch, resp):
    import httpx

    class _Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, *a, **kw):
            return resp

    monkeypatch.setattr(httpx, "Client", _Client)


def test_request_keeps_the_status_when_a_json_body_is_empty(monkeypatch):
    """Owner-hit 2026-09-07, live against redmine.org: Redmine answers 403 with
    `content-type: application/json` and a ZERO-length body. `resp.json()` raised,
    the raise escaped to the catch-all, and the status code was lost — the caller
    saw "Expecting value: line 1 column 1 (char 0)" instead of "HTTP 403".

    204 matters just as much: Redmine returns one from every successful issue
    update, so the same crash turned a success into an error.
    """
    _answer_with(monkeypatch, _FakeResp(403, b""))
    assert connector_calls.request("GET", "https://x.test/a") == {"error": "HTTP 403", "details": ""}

    _answer_with(monkeypatch, _FakeResp(204, b""))
    assert connector_calls.request("PUT", "https://x.test/a") == {"ok": True, "data": ""}

    # whitespace-only counts as empty; a body of "   " is not JSON either
    _answer_with(monkeypatch, _FakeResp(200, b"  \n "))
    assert connector_calls.request("GET", "https://x.test/a") == {"ok": True, "data": ""}


def test_request_falls_back_to_text_when_json_does_not_parse(monkeypatch):
    """A proxy or login wall can answer with an HTML page under a JSON content-type.
    The body is still the most useful thing to hand back — losing it to a decode
    error hides what actually answered."""
    _answer_with(monkeypatch, _FakeResp(401, b"<html>please log in</html>"))
    out = connector_calls.request("GET", "https://x.test/a")
    assert out == {"error": "HTTP 401", "details": "<html>please log in</html>"}


def test_request_still_parses_a_real_json_body(monkeypatch):
    _answer_with(monkeypatch, _FakeResp(200, b'{"issues": [{"id": 7}]}'))
    assert connector_calls.request("GET", "https://x.test/a") == {"ok": True, "data": {"issues": [{"id": 7}]}}


# -- brand marks ----------------------------------------------------------------------------------


def _registry_logo_ids() -> set[str]:
    """The logo ids the GUI registry can actually resolve.

    Parsed from the .tsx rather than duplicated here: a copy would drift, and the point
    of the test is to catch exactly that drift.
    """
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "surfaces/gui/src/connectors/registry.tsx").read_text(encoding="utf-8")
    body = src.split("export const CONNECTORS", 1)[1].split("\n};", 1)[0]
    return set(re.findall(r"^  ([A-Za-z0-9_]+):", body, re.M))


def test_every_logo_id_resolves_in_the_gui_registry():
    """A descriptor naming a logo the registry does not have renders the fallback plug —
    silently, because `resolveConnector` is designed not to throw."""
    known = _registry_logo_ids()
    missing = sorted(d.name for d in list_descriptors() if d.logo and d.logo not in known)
    assert missing == [], f"logo id set but not in registry.tsx: {missing}"


def test_the_connectors_without_a_brand_mark_are_a_pinned_list():
    """An empty `logo` is a deliberate choice for a brand with no published monochrome
    mark, and it renders a neutral plug. Docs, Sheets and Slides sat here for a while
    with three plugs in the catalog even though simple-icons ships all three marks — so
    the set is pinned, and adding to it has to be a decision someone writes down.
    """
    no_mark = {d.name for d in list_descriptors() if not d.logo}
    # Empty on purpose since 2026-09-07: every connector in the catalog now carries a
    # real mark. A name appearing here means someone shipped a plug icon.
    assert no_mark == set(), no_mark
