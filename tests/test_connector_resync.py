"""A session already open follows the Connectors page (compose/features/connectors.py): what is
connected, disconnected or switched off between two turns is in the next turn, in the same engine,
with no new session and no restart. Invented credentials; no service is called."""

from __future__ import annotations

import asyncio

import pytest

from harness.integrations.connectors.lifecycle.connect import connect_connector
from harness.integrations.connectors.lifecycle.disconnect import disconnect_connector
from harness.interface.server.manager import SessionManager

TRELLO = {"api_key": "k" * 32, "token": "t" * 64}


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "ws").mkdir()
    return SessionManager(data_dir=tmp_path / "data")


def _open(manager, tmp_path):
    return manager.get_engine("s1", agent="reeve", workspace=str(tmp_path / "ws"))


def _trello(engine) -> int:
    return sum(1 for name in engine.registry.names() if name.startswith("trello_"))


def _next_turn(manager):
    """What every turn path does before it runs."""
    asyncio.run(manager.arm_mcp("s1", agent="reeve"))


def test_a_connector_connected_mid_conversation_is_there_at_the_next_message(manager, tmp_path):
    engine = _open(manager, tmp_path)
    assert _trello(engine) == 0
    connect_connector(manager.secrets, "trello", TRELLO, validate=False)
    _next_turn(manager)
    assert manager.get_engine("s1") is engine  # the same engine: nothing else was rebuilt
    assert _trello(engine) == 18
    assert engine.registry.get("trello_get_card").defer  # still behind tool_search, like at build


def test_a_disconnected_connector_leaves_the_open_session(manager, tmp_path):
    connect_connector(manager.secrets, "trello", TRELLO, validate=False)
    engine = _open(manager, tmp_path)
    assert _trello(engine) == 18
    disconnect_connector(manager.secrets, "trello")
    _next_turn(manager)
    assert _trello(engine) == 0


def test_a_tool_switched_off_and_a_connector_muted_for_this_session_go_too(manager, tmp_path):
    from harness.integrations.connectors.catalog import patch_tool_settings

    connect_connector(manager.secrets, "trello", TRELLO, validate=False)
    engine = _open(manager, tmp_path)
    patch_tool_settings(manager.secrets, "trello", {"trello_delete": False})
    _next_turn(manager)
    assert "trello_delete" not in engine.registry.names() and "trello_get_card" in engine.registry.names()
    manager.session_connections.set("s1", "trello", False)
    _next_turn(manager)
    assert _trello(engine) == 0


def test_nothing_is_rebuilt_on_a_turn_where_nothing_changed(manager, tmp_path, monkeypatch):
    import harness.compose.features.connectors as feature

    engine = _open(manager, tmp_path)
    builds = []
    real = feature.make_integration_tools
    monkeypatch.setattr(feature, "make_integration_tools", lambda *a, **k: builds.append(1) or real(*a, **k))
    _next_turn(manager)
    _next_turn(manager)
    assert builds == []
    connect_connector(manager.secrets, "trello", TRELLO, validate=False)
    _next_turn(manager)
    _next_turn(manager)
    assert builds == [1] and _trello(engine) == 18
