"""Built-in model overrides — the `subagent_models.json` store and its route.

Settings ▸ Subagents edits a built-in's MODEL only, and only through the store: no
markdown file is written, the shipped definition (description, prompt, ruleset) stays
exactly as resolved. These tests prove the contract:

- the store round-trips (`set` → `load`, and `null` clears to an absence);
- `resolve_agents` merges the store over the built-ins and SKIPS names the config
  defines — a markdown/TOML definition owns its `model`;
- `PUT /v1/subagents/{name}/model` answers with the effective agent, `GET` reflects it,
  and unknown names / non-built-ins / config-defined names are 400;
- `load_config` never clobbers the store file.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from harness.config.config import load_config
from harness.llm.providers import ModelCapabilities, ProviderClient
from harness.interface.server import SessionManager, create_app
from harness.orchestration.subagent_models import load_model_overrides, model_overrides_path, set_model_override
from harness.tools.task_agents import resolve_agents


class _Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("the model store never calls a provider")

    def capabilities(self, model):
        return ModelCapabilities()


BODY = {
    "scope": "user",
    "description": "Reviews a diff and reports blocking issues as path:line.",
    "color": "#2f9e5f",
    "model": "opencode-go/deepseek-v4.1-flash",
    "tools": {"preset": "custom", "allow": ["read", "grep", "list", "shell-read"]},
    "prompt": "You are a code reviewer. Report findings as path:line.",
    "inject_agents": False,
}


# -- the store --------------------------------------------------------------------------


def test_store_round_trips_and_inherit_clears():
    path = model_overrides_path()
    assert not path.exists()
    assert load_model_overrides() == {}

    assert set_model_override("general", "deepseek:chat") == {"general": "deepseek:chat"}
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == {"general": "deepseek:chat"}
    assert load_model_overrides() == {"general": "deepseek:chat"}

    set_model_override("explore", "anthropic:claude-sonnet")
    assert load_model_overrides() == {
        "general": "deepseek:chat",
        "explore": "anthropic:claude-sonnet",
    }

    # `null` (and "") clear — the file keeps only real pins.
    assert set_model_override("general", None) == {"explore": "anthropic:claude-sonnet"}
    assert set_model_override("explore", "") == {}
    assert json.loads(path.read_text(encoding="utf-8")) == {}


def test_store_survives_a_config_load(tmp_path):
    state = model_overrides_path().parent
    state.mkdir(parents=True, exist_ok=True)
    (state / "config.toml").write_text(
        'model = "gpt-5.6-sol"\n[agents.helper]\nmodel = "deepseek:chat"\n',
        encoding="utf-8",
    )
    set_model_override("general", "deepseek:chat")
    ws = tmp_path / "ws"
    ws.mkdir()
    load_config(ws)
    assert load_model_overrides() == {"general": "deepseek:chat"}


# -- the merge --------------------------------------------------------------------------


def test_resolve_agents_merges_the_store_over_builtins_only(tmp_path):
    state = model_overrides_path().parent
    (state / "agents").mkdir(parents=True, exist_ok=True)
    # A markdown definition owns `explore` outright; the store must not touch it.
    (state / "agents" / "explore.md").write_text(
        '---\ndescription: My own explore\nmodel: "zai:glm-5"\n---\nbody\n',
        encoding="utf-8",
    )
    # A store entry for a name that is not a built-in is ignored.
    set_model_override("general", "deepseek:chat")
    set_model_override("helper", "another:model")

    ws = tmp_path / "ws"
    ws.mkdir()
    agents = resolve_agents(load_config(ws))
    assert agents["general"].model == "deepseek:chat"
    assert agents["explore"].model == "zai:glm-5"
    assert "helper" not in agents


# -- the route --------------------------------------------------------------------------


def test_put_model_route_sets_clears_and_get_reflects(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    mgr = SessionManager(workspace=ws, provider=_Provider())
    client = TestClient(create_app(mgr))

    # Unknown name and a non-built-in definition are 400.
    unknown = client.put("/v1/subagents/never-saved/model", json={"model": "deepseek:chat"})
    assert unknown.status_code == 400 and "never-saved" in unknown.json()["error"]
    mgr.save_subagent("code-reviewer", BODY)
    user_defined = client.put(
        "/v1/subagents/code-reviewer/model", json={"model": "deepseek:chat"}
    )
    assert user_defined.status_code == 400 and "built-in" in user_defined.json()["error"]
    # The definition's own model is untouched by the rejected call.
    assert resolve_agents(load_config(ws))["code-reviewer"].model == BODY["model"]

    # A built-in: the store is written, no markdown file appears.
    ok = client.put("/v1/subagents/general/model", json={"model": "deepseek:chat"})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert ok.json()["agent"]["name"] == "general"
    assert ok.json()["agent"]["model"] == "deepseek:chat"
    assert load_model_overrides() == {"general": "deepseek:chat"}
    assert not (model_overrides_path().parent / "agents" / "general.md").exists()

    # GET /v1/subagents carries the effective model; the row stays a built-in.
    rows = {r["name"]: r for r in client.get("/v1/subagents").json()["agents"]}
    assert rows["general"]["model"] == "deepseek:chat"
    assert rows["general"]["source"] == "builtin"
    assert rows["general"]["overrides"] is False

    # A bad model id is a 400 and changes nothing.
    bad = client.put("/v1/subagents/general/model", json={"model": "not a model"})
    assert bad.status_code == 400 and "provider/model" in bad.json()["error"]
    assert load_model_overrides() == {"general": "deepseek:chat"}

    # `null` clears the pin; the built-in inherits again.
    clear = client.put("/v1/subagents/general/model", json={"model": None})
    assert clear.status_code == 200
    assert clear.json()["agent"]["model"] == ""
    assert load_model_overrides() == {}


def test_put_model_route_rejects_a_config_defined_builtin(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    mgr = SessionManager(workspace=ws, provider=_Provider())
    client = TestClient(create_app(mgr))
    mgr.save_subagent("explore", BODY)

    resp = client.put("/v1/subagents/explore/model", json={"model": "deepseek:chat"})
    assert resp.status_code == 400
    assert "markdown" in resp.json()["error"]
    assert load_model_overrides() == {}
