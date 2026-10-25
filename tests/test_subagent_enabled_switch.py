"""Per-subagent enable switches — the `disabled_subagents.json` store and its route.

Settings ▸ Subagents' second per-agent control (after the model pin): a built-in
subagent — `general`, `explore`, and the two CLI agents `codex` / `claude-code` — can be
turned OFF without a markdown override. Off must mean "not delegable", not "hidden": the
row stays so the switch can flip back, while the model's listing, the `task` tool and
`spawn_subagent` all refuse it. These tests prove the contract:

- the store round-trips (`set` → `load`, clearing is an absence) and a torn file
  degrades to "nothing disabled";
- `resolve_agents` FLAGS the name (row keeps its description/model) and `describe_task`
  hides it from the listing the model reads;
- the `task` tool and a manager spawn refuse it with a sentence naming the setting;
- `PUT /v1/subagents/{name}/enabled` round-trips, and unknown / user-defined /
  config-defined names are 400 — those own their lifecycle through their file.
"""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from harness.config.config import load_config
from harness.llm.providers import ModelCapabilities, ProviderClient
from harness.interface.server import SessionManager, create_app
from harness.session.sessions import SessionRecord
from harness.orchestration.subagent_flags import disabled_subagents_path, load_disabled_subagents, set_subagent_disabled
from harness.tools.task import task_tools
from harness.tools.task_agents import AgentInfo, describe_task, resolve_agents


class _Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("the enable switch never calls a provider")

    def capabilities(self, model):
        return ModelCapabilities()


BODY = {
    "scope": "user",
    "description": "Reviews a diff and reports blocking issues as path:line.",
    "color": "#2f9e5f",
    "model": "opencode-go/deepseek-v4.1-flash",
    "tools": {"preset": "custom", "allow": ["read", "grep", "list", "shell-read"]},
    "prompt": "You are a code reviewer.",
    "inject_agents": True,
}


def _task(tools):
    return next(t for t in tools if getattr(t, "__name__", "") == "task")


# -- the store ------------------------------------------------------------------------


def test_the_store_round_trips_and_clearing_is_an_absence():
    assert load_disabled_subagents() == set()
    assert set_subagent_disabled("codex", True) == {"codex"}
    assert load_disabled_subagents() == {"codex"}
    assert json.loads(disabled_subagents_path().read_text(encoding="utf-8")) == ["codex"]
    assert set_subagent_disabled("codex", False) == set()
    assert load_disabled_subagents() == set()
    assert json.loads(disabled_subagents_path().read_text(encoding="utf-8")) == []


def test_a_torn_store_degrades_to_nothing_disabled():
    path = disabled_subagents_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_disabled_subagents() == set()


# -- the resolver and the listing ------------------------------------------------------


def test_a_disabled_name_keeps_its_row_but_leaves_the_listing():
    from harness.tools.task_agents import _builtin_agents

    before = _builtin_agents()["explore"]
    set_subagent_disabled("explore", True)
    agents = resolve_agents(None)

    assert agents["explore"].disabled is True
    # The row the GUI renders is still a real definition, not a husk.
    assert agents["explore"].description == before.description
    caller = AgentInfo(name="reeve")
    listing = describe_task(agents, caller)
    assert "- explore:" not in listing
    assert "- general:" in listing


def test_the_task_tool_refuses_a_disabled_agent(tmp_path):
    set_subagent_disabled("explore", True)
    tools = task_tools(workspace=tmp_path, provider=_Provider(), model="gpt-5.6")
    out = _task(tools)(description="d", prompt="p", subagent_type="explore")
    assert "turned off in Settings" in out["error"]


# -- the route --------------------------------------------------------------------------


def test_put_enabled_route_round_trips(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    mgr = SessionManager(workspace=ws, provider=_Provider())
    client = TestClient(create_app(mgr))

    off = client.put("/v1/subagents/general/enabled", json={"enabled": False})
    assert off.status_code == 200 and off.json()["ok"] is True
    assert off.json()["agent"]["name"] == "general"
    assert off.json()["agent"]["disabled"] is True
    assert load_disabled_subagents() == {"general"}
    # The row is still listed — the switch must be visible to flip back.
    rows = {r["name"]: r for r in client.get("/v1/subagents").json()["agents"]}
    assert rows["general"]["disabled"] is True

    on = client.put("/v1/subagents/general/enabled", json={"enabled": True})
    assert on.status_code == 200
    assert on.json()["agent"]["disabled"] is False
    assert load_disabled_subagents() == set()


def test_put_enabled_route_rejects_unknown_user_and_config_names(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    mgr = SessionManager(workspace=ws, provider=_Provider())
    client = TestClient(create_app(mgr))

    unknown = client.put("/v1/subagents/never-saved/enabled", json={"enabled": False})
    assert unknown.status_code == 400 and "never-saved" in unknown.json()["error"]

    mgr.save_subagent("code-reviewer", BODY)
    user_defined = client.put(
        "/v1/subagents/code-reviewer/enabled", json={"enabled": False}
    )
    assert user_defined.status_code == 400
    assert "built-in" in user_defined.json()["error"]

    mgr.save_subagent("explore", BODY)
    config_defined = client.put("/v1/subagents/explore/enabled", json={"enabled": False})
    assert config_defined.status_code == 400
    assert "markdown" in config_defined.json()["error"]
    assert load_disabled_subagents() == set()


# -- a manager spawn refuses it ---------------------------------------------------------


def test_a_spawn_refuses_a_disabled_agent(tmp_path):
    set_subagent_disabled("general", True)
    mgr = SessionManager(workspace=tmp_path, provider=_Provider())
    record = SessionRecord(
        session_id="parent-1",
        workspace=str(mgr.default_workspace or tmp_path),
        model="gpt-5.6",
        mode="auto",
        messages=[{"role": "user", "content": "hi"}],
        title="p",
        agent="reeve",
    )
    mgr.session_store.save(record)

    # The switch flips the resolved map the manager reads; `general` needs a live
    # definition, so make sure the built-in is the one being disabled.
    assert resolve_agents(load_config(mgr.default_workspace))["general"].disabled is True
    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id="parent-1",
            description="d",
            prompt="p",
            agent_name="general",
        )
    )
    assert "turned off in Settings" in out["error"]
    assert mgr.children_of("parent-1") == []
