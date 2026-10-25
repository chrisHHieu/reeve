"""The inbound allow-list: closed by default, one flat list per connector (`<name>:default`).

A sender who is not on it is parked, not lost; allowing them (with or without delivering the
parked message) writes the list and takes effect on the live gateway.
"""

from __future__ import annotations

import pytest

from harness.integrations.connectors import ConnectorSettings, MessageEvent, SessionSource
from harness.integrations.connectors.config import is_authorized
from harness.llm.providers import ModelCapabilities, ProviderClient
from harness.interface.server.manager import SessionManager


class ScriptedProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(tmp_path) -> SessionManager:
    m = SessionManager(data_dir=tmp_path / "data", provider=ScriptedProvider())
    m.secrets.put("slack:default", {"bot_token": "xoxb-x", "app_token": "xapp-x", "enabled": True})
    return m


def test_the_list_is_closed_by_default_and_allow_all_opens_it():
    s = ConnectorSettings(platform="slack", allowed_users={"U_OK"})
    assert is_authorized(s, SessionSource("slack", "C1", user_id="U_OK"))
    assert not is_authorized(s, SessionSource("slack", "C1", user_id="U_STRANGER"))
    assert is_authorized(ConnectorSettings(platform="slack", allow_all=True), SessionSource("slack", "C1", user_id="anyone"))


def test_allow_and_disallow_write_the_flat_list(tmp_path):
    m = _manager(tmp_path)
    assert m.allow_user("slack", "U_FLAT")["allowed_users"] == ["U_FLAT"]
    assert m.secrets.get("slack:default")["allowed_users"] == ["U_FLAT"]
    assert m.disallow_user("slack", "U_FLAT")["allowed_users"] == []
    assert m.allow_user("nope", "U_X")["ok"] is False  # a connector that is not connected


@pytest.mark.asyncio
async def test_a_parked_sender_is_allowed_into_the_flat_list(tmp_path):
    m = _manager(tmp_path)

    async def _noop(event) -> None:
        pass

    m._dispatch_inbound = _noop
    await m._park_unauthorized(
        MessageEvent(text="hi", source=SessionSource("slack", "D1", user_id="U_M", chat_type="dm"))
    )
    item = m.parked.list("slack")[0]
    assert "team_id" not in item
    assert (await m.resolve_unauthorized("slack", item["id"], "allow"))["ok"] is True
    assert m.secrets.get("slack:default")["allowed_users"] == ["U_M"]


def test_a_row_parked_before_the_relay_was_removed_still_loads(tmp_path):
    from harness.integrations.connectors.messaging import ParkedStore

    path = tmp_path / "parked.json"
    path.write_text(
        '{"items": [{"platform": "slack", "chat_id": "C1", "user_id": "U1", "text": "hi", "team_id": "T1"}]}',
        encoding="utf-8",
    )
    assert ParkedStore(path).list("slack")[0]["user_id"] == "U1"
