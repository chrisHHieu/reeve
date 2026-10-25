"""Every built-in tool has a risk class someone chose.

A tool missing from the tables used to be a read by default, which skips the read-only modes and the
approval card together. The default is now external; this test keeps that from ever being what
decides a built-in, by building the session the server builds and naming any tool left undeclared.
"""

from __future__ import annotations

import pytest

from harness.interface.server.manager import SessionManager
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.safety.risk import BUILTIN_RISK, RiskClass, declared_class


class _Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    ws = tmp_path / "ws"
    ws.mkdir()
    manager = SessionManager(workspace=ws, provider=_Provider())
    engine = manager.get_engine("risk-probe")
    yield engine
    closer = getattr(getattr(engine, "executor", None), "close", None)
    if callable(closer):
        closer()


def test_every_built_in_the_server_registers_is_declared(engine):
    builtins = [name for name in engine.registry.names() if engine.registry.get(name).group is None]
    assert len(builtins) > 30  # the session really was built with its features
    undeclared = sorted(name for name in builtins if declared_class(name) is None)
    assert not undeclared, f"declare these in harness/safety/risk.py: {undeclared}"


def test_every_connector_tool_takes_its_class_from_the_catalogue(engine):
    from harness.integrations.connectors.catalog import KIND_BY_NAME

    for name, kind in KIND_BY_NAME.items():
        expected = RiskClass.READ if kind == "read" else RiskClass.EXTERNAL
        assert declared_class(name) is expected, name


def test_a_tool_that_writes_or_runs_is_never_declared_a_read():
    reads = {name for name, risk in BUILTIN_RISK.items() if risk is RiskClass.READ}
    assert not reads & {"write_file", "replace_in_file", "apply_patch", "run_shell", "run_code", "terminal_send"}
