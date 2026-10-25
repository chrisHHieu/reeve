"""Risk overrides the person keeps on their own machine (`config/overrides`): a rule by tool name or
pattern that relaxes or tightens a tool's class, the most specific rule winning. What an override
may not do to a built-in tool is in test_egress_and_overrides.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from harness.config.overrides import RiskOverrideStore
from harness.safety.permissions import PermissionEngine
from harness.safety.risk import RiskClass, classify

MCP = SimpleNamespace(requires_approval=True, category="mcp")


@pytest.fixture
def store(tmp_path):
    return RiskOverrideStore(tmp_path / "ro.json")


def test_the_most_specific_rule_wins_and_no_rule_leaves_the_tool_as_it_is(store):
    store.set_rule("mcp__notion__*", "read")  # the server, relaxed
    store.set_rule("mcp__notion__create_*", "external")  # its writes, not
    assert store.resolve("mcp__notion__get_page") == RiskClass.READ
    assert store.resolve("mcp__notion__create_page") == RiskClass.EXTERNAL
    assert store.resolve("mcp__github__push") is None


def test_a_relaxed_mcp_tool_is_classed_a_read_and_runs_unasked(store, tmp_path):
    store.set_rule("mcp__notion__get_*", "read")
    assert classify("mcp__notion__get_page", MCP) == RiskClass.EXTERNAL  # without the rule
    assert classify("mcp__notion__get_page", MCP, store.resolver()) == RiskClass.READ
    engine = PermissionEngine(workspace_root=tmp_path, risk_overrides=store.resolver())
    relaxed = engine.evaluate("mcp__notion__get_page", {}, MCP)
    assert relaxed.allowed and not relaxed.needs_user
    other = engine.evaluate("mcp__notion__delete_page", {}, MCP)  # no rule covers it: it still asks
    assert not other.allowed and other.needs_user


def test_a_rule_survives_a_reload(store, tmp_path):
    store.set_rule("mcp__x__*", "read")
    assert RiskOverrideStore(tmp_path / "ro.json").resolve("mcp__x__y") == RiskClass.READ


def test_tightening_is_always_allowed(store):
    store.set_rule("read_file", "external")
    assert store.resolve("read_file") == RiskClass.EXTERNAL
