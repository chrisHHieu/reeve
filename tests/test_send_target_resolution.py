"""Two rules. A connector tool that only reads never asks: the kind the tool registry gives
it decides. And a Slack channel can be named, in send_message and send_file alike: "post Hi to
#general" has to work once Slack is connected (the owner's repro, 2026-07-14).
"""

import pytest

from harness.integrations.connectors import slack_directory
from harness.integrations.connectors.catalog import TOOL_DEFS, approval_for_tool
from harness.integrations.connectors.integration_tools import make_integration_tools
from harness.integrations.connectors.messaging import SendResult
from harness.integrations.connectors.outbound import make_send_file_tool, make_send_message_tool
from harness.safety.secrets import SecretStore

GENERAL = {"id": "C9", "name": "all-reeve", "is_private": False, "is_member": True}
CHANNEL_ID = "C0BFPTFE7RV"


# -- a read never asks ---------------------------------------------------------------------------


def test_every_registered_tool_is_a_read_or_a_write_and_only_a_write_asks():
    for tool in TOOL_DEFS:
        assert tool.kind in ("read", "write"), f"{tool.name} has kind {tool.kind!r}"
        assert approval_for_tool(tool.name) is (tool.kind != "read")
    for default in (True, False):  # a tool the registry does not know (MCP, experiments) keeps the caller's
        assert approval_for_tool("mcp_mystery_tool", default=default) is default


@pytest.mark.parametrize(
    ("name", "asks"),
    [
        ("jira_search_issues", False),
        ("jira_get_issue", False),
        ("jira_create_issue", True),
        # GitHub tools that write to disk: a clone makes a folder, a pull moves a checkout on
        ("github_clone", True),
        ("github_pull", True),
    ],
)
def test_the_built_tools_ask_by_their_kind(tmp_path, name, asks):
    tools = {tool.__name__: tool for tool in make_integration_tools(SecretStore(tmp_path / "secrets.json"))}
    assert tools[name].__aisuite_tool_metadata__.requires_approval is asks


# -- a channel by its name -----------------------------------------------------------------------


@pytest.fixture
def secrets(tmp_path) -> SecretStore:
    store = SecretStore(tmp_path / "secrets.json")
    store.put("slack:default", {"bot_token": "xoxb-t1", "app_token": "xapp-t1", "enabled": True})
    return store


class _Posts(list):
    """Slack's post, kept instead of sent."""

    def senders(self):
        def post(token, chat_id, text, thread_id):
            self.append({"token": token, "chat_id": chat_id, "text": text})
            return SendResult(True, message_id="123.456")

        return {"slack": post}


def _channels(monkeypatch, *channels) -> list[str]:
    """Slack's channel list for the lookup; each query it was asked is kept."""
    queried: list[str] = []

    def list_channels(secrets, query="", limit=25, *, refresh=False):
        queried.append(query)
        return {"ok": True, "channels": list(channels)}

    monkeypatch.setattr(slack_directory, "list_channels", list_channels)
    return queried


def _send(secrets, posts):
    return make_send_message_tool(secrets, senders=posts.senders())


@pytest.mark.parametrize("target", ["slack:#all-reeve", "all-reeve", "#all-reeve"])
def test_a_channel_name_with_or_without_the_slack_prefix_is_sent_to_its_id(secrets, monkeypatch, target):
    # The owner's transcript: the model wrote 'all-reeve' and '#all-reeve' with no 'slack:' and got
    # "invalid target". A bare name is shaped like Slack's, so it is looked up there.
    _channels(monkeypatch, GENERAL)
    posts = _Posts()
    assert _send(secrets, posts)(target, "Hi")["ok"] is True
    assert (posts[0]["chat_id"], posts[0]["token"]) == ("C9", "xoxb-t1")


def test_something_that_is_neither_an_address_nor_a_name_is_an_invalid_target(secrets, monkeypatch):
    _channels(monkeypatch, GENERAL)
    assert "invalid target" in _send(secrets, _Posts())("Not A Channel!", "Hi")["error"]


@pytest.mark.parametrize(
    "target",
    [
        f"slack:{CHANNEL_ID}",
        # saved by automations and standing rules while the hosted relay existed, as T…/C…; there is
        # one workspace now, so the prefix is dropped rather than refused
        f"slack:T1/{CHANNEL_ID}",
    ],
)
def test_an_id_is_sent_as_it_is_without_a_lookup(secrets, monkeypatch, target):
    queried = _channels(monkeypatch)
    posts = _Posts()
    assert _send(secrets, posts)(target, "Hi")["ok"] is True
    assert queried == [] and (posts[0]["chat_id"], posts[0]["token"]) == (CHANNEL_ID, "xoxb-t1")


def test_a_name_slack_does_not_know_or_a_channel_the_bot_is_not_in_says_what_to_do(secrets, monkeypatch):
    send = _send(secrets, _Posts())
    _channels(monkeypatch, {"id": "C1", "name": "general", "is_private": False, "is_member": True})
    assert "no Slack channel named #nope" in send("slack:#nope", "Hi")["error"]
    _channels(monkeypatch, {"id": "C3", "name": "private-ops", "is_private": True, "is_member": False})
    assert "invite @Reeve" in send("slack:#private-ops", "Hi")["error"]


def test_a_file_can_be_sent_to_a_channel_by_name_too(secrets, monkeypatch, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "r.pdf").write_bytes(b"%PDF")
    _channels(monkeypatch, {"id": "C9", "name": "general", "is_private": False, "is_member": True})
    uploads: list[dict] = []

    def upload(token, chat_id, thread_id, filename, data, title, comment):
        uploads.append({"chat_id": chat_id, "filename": filename})
        return SendResult(True, message_id="F1")

    sent = make_send_file_tool(secrets, workspace=workspace, file_senders={"slack": upload})("slack:#general", "r.pdf")
    assert sent["ok"] is True and uploads[0]["chat_id"] == "C9"
