"""Progressive discovery on by default, and the model is told what it can search for.

The 2026-09-24 audit measured the real store: a median request carried 100 tools and
~15k tokens of MCP schemas (52% of the tool payload), with `tool_discovery` off by
default. Every MCP schema can be deferred while the tool NAMES stay in
context, so the model knows a server exists before it searches. Reeve's bridge had no
such list: a model asked about "my wealthlog balance" saw only a generic `tool_search`.
Pinned here:

- `tool_search`'s wire description carries the deferred catalog, grouped by server,
  names only — and it follows the registry live (an MCP server armed mid-session shows);
- the Settings default is on, and "on" means "defer when it pays": a deferrable payload
  under `DISCOVERY_MIN_CHARS` stays eager (a search round-trip would cost more).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from harness.interface.server.app import create_app
from harness.interface.server.manager import SessionManager
from harness.tools import ToolRegistry
from harness.tools.contract import READ_ONLY_CAPS, ToolContract, attach_contract
from harness.tools.discovery import DISCOVERY_MIN_CHARS, TOOL_SEARCH, discovery_tools


def _mcp(name: str, *, description: str = "does a thing"):
    def fn(**kwargs):
        return {"ok": True}

    fn.__name__ = name
    fn.__tool_schema__ = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }
    fn.__tool_defer__ = True
    fn.__tool_group__ = "mcp"
    attach_contract(fn, ToolContract(capabilities=READ_ONLY_CAPS))
    return fn


def _search_description(registry: ToolRegistry) -> str:
    for schema in registry.schemas():
        if schema["function"]["name"] == TOOL_SEARCH:
            return schema["function"]["description"]
    raise AssertionError("tool_search is not on the wire")


def _registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_all([_mcp(n) for n in names])
    registry.set_progressive_discovery(True)
    registry.register_all(discovery_tools(registry))
    return registry


def test_the_search_tool_names_every_deferred_tool_by_server():
    registry = _registry(
        "mcp__wealthlog__get_accounts",
        "mcp__wealthlog__query_database",
        "mcp__browser__browser_click",
    )
    text = _search_description(registry)
    assert "wealthlog: get_accounts, query_database" in text
    assert "browser: browser_click" in text
    # Names only: the schemas themselves stay off the wire.
    assert "does a thing" not in text


def test_the_catalog_follows_the_registry_live():
    registry = _registry("mcp__wealthlog__get_accounts")
    assert "tavily" not in _search_description(registry)
    registry.sync_group(
        "mcp", [_mcp("mcp__wealthlog__get_accounts"), _mcp("mcp__tavily__tavily_search")]
    )
    assert "tavily: tavily_search" in _search_description(registry)


def test_a_huge_catalog_is_bounded_to_server_counts():
    names = [f"mcp__srv{i}__tool_with_a_long_descriptive_name_{j}" for i in range(20) for j in range(20)]
    text = _search_description(_registry(*names))
    assert len(text) < 6_000
    assert "srv7 (20 tools)" in text


def test_a_connector_keeps_its_name_when_the_index_falls_back_to_counts():
    """Under the count fallback a connector's tools folded into `built-in (N tools)` would hide
    that the mailbox is there at all; they are listed under the connector they declare."""
    mail = []
    for i in range(30):
        fn = _mcp(f"gmail_tool_with_a_long_descriptive_name_{i}")
        fn.__tool_catalog__ = "gmail"
        mail.append(fn)
    names = [f"mcp__srv{i}__tool_with_a_long_descriptive_name_{j}" for i in range(20) for j in range(20)]
    registry = _registry(*names)
    registry.register_all(mail)
    text = _search_description(registry)
    assert "gmail (30 tools)" in text and "- built-in" not in text


def _build(tmp_path, tools):
    from harness.compose import build_engine
    from harness.modes.agents.reeve import reeve_agent

    return build_engine(
        agent=reeve_agent(),
        workspace=tmp_path,
        provider=object(),
        tool_discovery=True,
        extra_tools=tools,
    )


def test_a_small_server_joins_the_deferred_catalog_of_a_coding_build(tmp_path, monkeypatch):
    """A coding build's situational built-ins already pay for the bridge (amended
    2026-10-10), so one small server is deferred with them and named in the index; a payload too
    small to pay is kept eager (`test_tool_discovery`)."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    engine = _build(tmp_path, [_mcp("mcp__tiny__ping")])
    assert engine.registry.progressive_discovery is True
    wire = {s["function"]["name"]: s for s in engine.registry.schemas()}
    assert "mcp__tiny__ping" not in wire
    assert "ping" in wire[TOOL_SEARCH]["function"]["description"]


def test_a_large_deferrable_payload_is_deferred(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    per_tool = DISCOVERY_MIN_CHARS // 10
    tools = [_mcp(f"mcp__big__t{i}", description="x" * per_tool) for i in range(12)]
    engine = _build(tmp_path, tools)
    assert engine.registry.progressive_discovery is True
    wire = {s["function"]["name"] for s in engine.registry.schemas()}
    assert TOOL_SEARCH in wire and "mcp__big__t0" not in wire


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))


def test_settings_default_on(client):
    assert client.get("/v1/settings").json()["tool_discovery"] is True


def test_the_index_spells_out_the_full_name_shape():
    text = _search_description(_registry("mcp__wealthlog__get_accounts"))
    assert "mcp__<server>__<tool>" in text


def test_a_unique_short_name_reaches_its_tool_through_the_bridge():
    """The live A/B's first bridge call was `tool_call(name="get_accounts")` — the name as
    the index lists it. A short name that matches exactly one deferred tool resolves; an
    ambiguous one is refused with the candidates named."""
    from harness.llm.providers.base import ToolCall
    from harness.tools.discovery import rewrite_bridge_call

    registry = _registry(
        "mcp__wealthlog__get_accounts", "mcp__bank__list", "mcp__crm__list"
    )
    call = ToolCall(id="c", name="tool_call", arguments={"name": "get_accounts", "arguments": {}})
    assert rewrite_bridge_call(registry, call) is None
    assert call.name == "mcp__wealthlog__get_accounts"

    also = ToolCall(id="d", name="tool_call", arguments={"name": "wealthlog__get_accounts"})
    assert rewrite_bridge_call(registry, also) is None
    assert also.name == "mcp__wealthlog__get_accounts"

    ambiguous = ToolCall(id="e", name="tool_call", arguments={"name": "list"})
    error = rewrite_bridge_call(registry, ambiguous)
    assert error is not None and "mcp__bank__list" in error and "mcp__crm__list" in error
