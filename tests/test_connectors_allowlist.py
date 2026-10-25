"""Who may message the agent, on the Connectors page: `GET /v1/connectors` gives the allow-list as a
list together with the senders the gateway saw lately, each marked as allowed or not, and the allow
and disallow routes change it.
"""

from fastapi.testclient import TestClient

from harness.interface.server import create_app
from harness.interface.server.manager import SessionManager
from harness.llm.providers import ModelCapabilities, ProviderClient


class _NoTurns(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


class _Gateway:
    """Just the part of the gateway the manager reads: who wrote lately."""

    def __init__(self, recent):
        self._recent, self.settings = recent, {}

    def recent_senders(self, name):
        return [dict(sender) for sender in self._recent] if name == "slack" else []


def _served(tmp_path, *, allowed=(), recent=None):
    manager = SessionManager(workspace=tmp_path, provider=_NoTurns())
    manager.secrets.put("slack:default", {"type": "token", "bot_token": "xoxb-1", "app_token": "xapp-1", "allowed_users": list(allowed)})
    manager.gateway = _Gateway(recent) if recent is not None else None
    client = TestClient(create_app(manager))
    return lambda: next(c for c in client.get("/v1/connectors").json()["connectors"] if c["name"] == "slack"), client


def _sender(uid, name, chat):
    return {"user_id": uid, "user_name": name, "chat_id": chat, "chat_type": "dm", "target": f"slack:{chat}"}


def test_the_row_lists_who_is_allowed_and_marks_each_recent_sender(tmp_path):
    slack, _ = _served(tmp_path, allowed=["U_OK"], recent=[_sender("U_OK", "Ann", "D1"), _sender("U_NEW", "Bob", "D2")])
    row = slack()
    assert row["connected"] is True and row["allowed_users"] == ["U_OK"]  # the ids, not a count
    marked = {sender["user_id"]: sender["authorized"] for sender in row["recent"]}
    assert marked == {"U_OK": True, "U_NEW": False}


def test_allowing_and_disallowing_change_the_list(tmp_path):
    slack, client = _served(tmp_path, recent=[])
    client.post("/v1/connectors/slack/allow", json={"user_id": "U_NEW"})
    assert slack()["allowed_users"] == ["U_NEW"]
    client.post("/v1/connectors/slack/disallow", json={"user_id": "U_NEW"})
    assert slack()["allowed_users"] == []


def test_with_no_gateway_running_there_are_no_recent_senders(tmp_path):
    # a server started without messaging: an empty list, and nothing breaks
    slack, _ = _served(tmp_path)
    assert slack()["recent"] == []
