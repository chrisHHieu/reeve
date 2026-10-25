"""Per-call LLM routes for delegated children — the delegation options.

`provider` and `model` on the call let ONE call pick the child's LLM target:
either field alone, a bare model id keeping the provider, a full `provider:model` used as
written. Ours is the same fact on three surfaces — the `task` tool's `provider`/`model`
parameters, a workflow's `agent(...)` opts, and the discovery tool
`list_subagent_models` that names the registered/configured ids — resolved by
`subagent_models.resolve_route`, validated by `route_problem`, and persisted on the child
row so the run happens on the model the caller asked for.
"""

from __future__ import annotations

import asyncio


from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.providers.registry import get_descriptor
from harness.interface.server.manager import SessionManager
from harness.session.sessions import SessionRecord
from harness.orchestration.subagent_models import resolve_route, route_problem, set_model_override
from harness.tools.task import task_tools


class _Scripted(ProviderClient):
    """Records every model it is asked for, then answers with one text turn."""

    def __init__(self, text: str = "ok") -> None:
        self.models: list[str] = []
        self._text = text

    def complete(self, *, model, messages, tools=None, **settings):
        self.models.append(model)
        return AssistantTurn(text=self._text, finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


class _Secrets:
    """The one method the route checks read."""

    def __init__(self, profiles=None) -> None:
        self._profiles = profiles or {}

    def get(self, profile):
        return self._profiles.get(profile)


def _no_env_keys(monkeypatch, *names: str) -> None:
    """Provider config must come from the profile, not the developer's shell."""
    for name in names:
        descriptor = get_descriptor(name)
        if descriptor is not None and descriptor.env_key:
            monkeypatch.delenv(descriptor.env_key, raising=False)


def _tools(tmp_path, provider, secrets=None, **kwargs):
    return task_tools(
        workspace=tmp_path,
        provider=provider,
        model="gpt-5.6",
        secrets=secrets,
        **kwargs,
    )


def _by_name(tools, name):
    return next(t for t in tools if getattr(t, "__name__", "") == name)


# -- resolve_route: the four shapes --------------------------------------------------


def test_no_override_returns_the_base_untouched():
    assert resolve_route("openai:gpt-5.6", "", "") == ("openai:gpt-5.6", "")


def test_provider_alone_keeps_the_model_id_on_another_provider():
    assert resolve_route("openai:gpt-5.6", "anthropic", "") == (
        "anthropic:gpt-5.6",
        "",
    )


def test_a_bare_model_keeps_the_provider():
    assert resolve_route("openai:gpt-5.6", "", "gpt-5.6-mini") == (
        "openai:gpt-5.6-mini",
        "",
    )


def test_a_full_route_is_used_as_written():
    assert resolve_route("openai:gpt-5.6", "", "anthropic:claude-x") == (
        "anthropic:claude-x",
        "",
    )
    assert resolve_route("openai:gpt-5.6", "anthropic", "anthropic:claude-x") == (
        "anthropic:claude-x",
        "",
    )


def test_a_conflicting_pair_is_a_problem_not_a_preference():
    route, problem = resolve_route("openai:gpt-5.6", "anthropic", "zai:glm-5")
    assert route == ""
    assert "already names provider" in problem


# -- route_problem: the two refusals rendered --------------------------------------


def test_an_unregistered_provider_names_the_registered_ones():
    problem = route_problem("definitely-not-a-provider", _Secrets())
    assert "not registered" in problem
    assert "openai" in problem


def test_an_unconfigured_provider_names_the_configured_ones(monkeypatch):
    _no_env_keys(monkeypatch, "anthropic", "openai")
    problem = route_problem("anthropic", _Secrets({"provider:openai": {"api_key": "k"}}))
    assert "not configured" in problem
    assert "openai" in problem and "anthropic" not in problem.split("providers:")[-1]


def test_a_configured_provider_passes(monkeypatch):
    _no_env_keys(monkeypatch, "openai")
    assert route_problem("openai", _Secrets({"provider:openai": {"api_key": "k"}})) == ""


# -- the task tool --------------------------------------------------------------------


def test_the_override_reaches_the_spawner(tmp_path):
    captured: dict = {}

    def spawner(**kwargs):
        captured.update(kwargs)
        return {
            "task_id": "t-1",
            "status": "completed",
            "report": "done",
            "tokens": 0,
        }

    tools = _tools(
        tmp_path,
        _Scripted(),
        _Secrets({"provider:openai": {"api_key": "k"}}),
        spawner=spawner,
    )
    out = _by_name(tools, "task")(
        description="d",
        prompt="p",
        subagent_type="general",
        provider="openai",
        model="gpt-5.6-mini",
    )

    assert captured["provider"] == "openai"
    assert captured["model"] == "gpt-5.6-mini"
    assert out["status"] == "completed"


def test_an_unregistered_provider_is_refused_before_any_spawn(tmp_path):
    spawned: list[dict] = []
    tools = _tools(
        tmp_path,
        _Scripted(),
        _Secrets(),
        spawner=lambda **kwargs: spawned.append(kwargs) or {},
    )
    out = _by_name(tools, "task")(
        description="d",
        prompt="p",
        subagent_type="general",
        provider="nope",
        model="x",
    )
    assert "not registered" in out["error"]
    assert spawned == []


def test_an_external_cli_child_takes_a_bare_model_only(tmp_path):
    """A codex/claude-code child IS its CLI process: a bare model id reaches `--model`;
    a provider prefix is meaningless there."""
    from harness.tools.task_agents import resolve_agents

    agents = resolve_agents(None)
    agents["general"].provider = "codex"
    tools = _tools(
        tmp_path,
        _Scripted(),
        _Secrets({"provider:openai": {"api_key": "k"}}),
        agents=agents,
    )
    out = _by_name(tools, "task")(
        description="d",
        prompt="p",
        subagent_type="general",
        provider="openai",
        model="gpt-5.6-mini",
    )
    assert "runs on the codex CLI's own provider" in out["error"]

    out = _by_name(tools, "task")(
        description="d",
        prompt="p",
        subagent_type="general",
        model="openai:gpt-5.6-mini",
    )
    assert "bare model id" in out["error"]


def test_a_resume_refuses_the_override(tmp_path):
    tools = _tools(tmp_path, _Scripted(), _Secrets())
    out = _by_name(tools, "task")(
        description="d",
        prompt="p",
        subagent_type="general",
        task_id="t-1",
        provider="openai",
        model="gpt-5.6-mini",
    )
    assert "only apply to a new task" in out["error"]


def test_a_thread_child_runs_on_the_requested_route(tmp_path):
    """The no-manager path (CLI, unit tests) rebuilds the child engine's model too."""
    provider = _Scripted("child says hi")
    tools = _tools(
        tmp_path,
        provider,
        _Secrets({"provider:openai": {"api_key": "k"}}),
    )
    out = _by_name(tools, "task")(
        description="d",
        prompt="p",
        subagent_type="general",
        provider="openai",
        model="gpt-5.6-mini",
    )
    assert out["status"] == "completed"
    assert provider.models == ["openai:gpt-5.6-mini"]


# -- the manager spawn path -----------------------------------------------------------


def _manager(tmp_path, provider) -> SessionManager:
    return SessionManager(workspace=tmp_path, provider=provider)


def _parent(manager: SessionManager) -> SessionRecord:
    record = SessionRecord(
        session_id="parent-1",
        workspace=str(manager.default_workspace or "."),
        model="gpt-5.6",
        mode="auto",
        messages=[{"role": "user", "content": "delegate"}],
        title="parent",
        agent="reeve",
    )
    manager.session_store.save(record)
    return record


def _spawn(manager: SessionManager, **kwargs):
    return asyncio.run(
        manager.spawn_subagent(
            parent_id="parent-1",
            description=kwargs.pop("description", "route check"),
            prompt=kwargs.pop("prompt", "p"),
            agent_name=kwargs.pop("agent_name", "general"),
            **kwargs,
        )
    )


def test_a_spawn_runs_the_child_on_the_requested_route(tmp_path):
    provider = _Scripted()
    manager = _manager(tmp_path, provider)
    manager.secrets.put("provider:openai", {"api_key": "k"})
    _parent(manager)

    out = _spawn(manager, provider="openai", model="gpt-5.6-mini")

    assert out["status"] == "completed"
    child = manager.session_store.load(out["task_id"])
    assert child is not None
    assert child.model == "openai:gpt-5.6-mini"
    assert provider.models == ["openai:gpt-5.6-mini"]


def test_a_pinned_agent_model_does_not_beat_the_override(tmp_path):
    """Settings ▸ Subagents may pin `general`; a route override is the caller's
    decision for THIS child and must win (route_pinned)."""
    set_model_override("general", "anthropic:claude-x")
    provider = _Scripted()
    manager = _manager(tmp_path, provider)
    manager.secrets.put("provider:openai", {"api_key": "k"})
    _parent(manager)

    out = _spawn(manager, provider="openai", model="gpt-5.6-mini")

    assert out["status"] == "completed"
    assert provider.models == ["openai:gpt-5.6-mini"]
    assert manager.session_store.load(out["task_id"]).model == "openai:gpt-5.6-mini"


def test_an_unconfigured_provider_leaves_no_child_row(tmp_path, monkeypatch):
    _no_env_keys(monkeypatch, "anthropic")
    manager = _manager(tmp_path, _Scripted())
    _parent(manager)

    out = _spawn(manager, provider="anthropic", model="claude-x")

    assert "not configured" in out["error"]
    assert manager.children_of("parent-1") == []


def test_a_spawn_with_a_cli_name_as_provider_gets_the_targeted_answer(tmp_path):
    manager = _manager(tmp_path, _Scripted())
    _parent(manager)

    out = _spawn(manager, provider="codex", model="gpt-5.6-luna")

    assert "is a CLI subagent (an agent type), not an LLM provider" in out["error"]
    assert manager.children_of("parent-1") == []


# -- list_subagent_models -------------------------------------------------------------


def test_the_discovery_tool_lists_the_configured_providers(tmp_path, monkeypatch):
    _no_env_keys(monkeypatch, "openai", "anthropic")
    tools = _tools(
        tmp_path, _Scripted(), _Secrets({"provider:openai": {"api_key": "k"}})
    )
    listed = _by_name(tools, "list_subagent_models")()
    assert "openai — " in listed
    assert "anthropic" not in listed


def test_the_discovery_tool_requires_provider_with_model(tmp_path):
    tools = _tools(tmp_path, _Scripted(), _Secrets())
    assert "requires `provider`" in _by_name(tools, "list_subagent_models")(
        model="gpt-5.6"
    )


def test_the_discovery_tool_lists_a_providers_models(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "harness.tools.task.models_for_provider",
        lambda name, secrets=None: {"ok": True, "models": ["glm-5", "glm-4.6"]},
    )
    tools = _tools(
        tmp_path, _Scripted(), _Secrets({"provider:openai": {"api_key": "k"}})
    )
    listed = _by_name(tools, "list_subagent_models")(provider="openai")
    assert listed.splitlines() == ["openai:glm-5", "openai:glm-4.6"]


def test_the_discovery_tool_inspects_one_model(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "harness.tools.task.levels_for",
        lambda model, facts=None: ["low", "high"],
    )
    tools = _tools(
        tmp_path, _Scripted(), _Secrets({"provider:openai": {"api_key": "k"}})
    )
    listed = _by_name(tools, "list_subagent_models")(
        provider="openai", model="gpt-5.6"
    )
    assert listed.splitlines()[0] == "openai:gpt-5.6"
    assert "low, high" in listed


# -- the Settings switch -----------------------------


def test_model_selection_off_hides_and_refuses_the_route(tmp_path):
    tools = _tools(tmp_path, _Scripted(), _Secrets(), model_selection=False)
    properties = _by_name(tools, "task").__tool_schema__["function"]["parameters"][
        "properties"
    ]
    assert "provider" not in properties and "model" not in properties
    # The discovery companion is not registered either.
    assert not any(
        getattr(t, "__name__", "") == "list_subagent_models" for t in tools
    )

    out = _by_name(tools, "task")(
        description="d",
        prompt="p",
        subagent_type="general",
        provider="openai",
        model="gpt-5.6-mini",
    )
    assert "turned off in Settings" in out["error"]


def test_model_selection_is_prefs_backed_and_in_the_settings_payload(tmp_path):
    manager = _manager(tmp_path, _Scripted())
    assert manager.subagent_model_selection() is True
    assert manager.set_subagent_model_selection(False) == {
        "ok": True,
        "subagent_model_selection": False,
    }
    assert manager.subagent_model_selection() is False
    assert manager.get_settings()["subagent_model_selection"] is False
    assert manager.set_subagent_model_selection(True)["subagent_model_selection"] is True


def test_a_spawn_refuses_the_override_when_the_switch_is_off(tmp_path):
    provider = _Scripted()
    manager = _manager(tmp_path, provider)
    manager.secrets.put("provider:openai", {"api_key": "k"})
    manager.set_subagent_model_selection(False)
    _parent(manager)

    out = _spawn(manager, provider="openai", model="gpt-5.6-mini")

    assert "turned off in Settings" in out["error"]
    assert provider.models == []
    assert manager.children_of("parent-1") == []


# -- CLI subagents in the discovery tool ----------------------------------------------


def test_the_discovery_tool_lists_the_cli_subagents_as_agent_types(tmp_path):
    """The 2026-09-21 session passed `provider="claude-code"` because the discovery
    output listed the CLI agents beside the real providers — the label now says where
    they belong (`subagent_type`)."""
    tools = _tools(tmp_path, _Scripted(), _Secrets())
    listed = _by_name(tools, "list_subagent_models")()
    assert 'codex — CLI subagent: call task(subagent_type="codex"), not a provider' in listed
    assert (
        'claude-code — CLI subagent: call task(subagent_type="claude-code"), not a provider'
        in listed
    )


def test_a_cli_name_in_provider_gets_the_targeted_answer(tmp_path):
    from harness.tools.task_agents import resolve_agents

    agents = resolve_agents(None)
    agents["general"].provider = "codex"
    tools = _tools(tmp_path, _Scripted(), _Secrets(), agents=agents)
    out = _by_name(tools, "task")(
        description="d",
        prompt="p",
        subagent_type="general",
        provider="codex",
        model="gpt-5.6-luna",
    )
    assert "is a CLI subagent (an agent type), not an LLM provider" in out["error"]
    assert 'task(subagent_type="codex", model="gpt-5.6-luna")' in out["error"]


def test_the_discovery_tool_reads_a_cli_account_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "harness.orchestration.subagent_external.list_models",
        lambda provider: {
            "ok": True,
            "models": [{"id": "claude-opus-5", "name": "Opus 5"}],
        },
    )
    tools = _tools(tmp_path, _Scripted(), _Secrets())
    listed = _by_name(tools, "list_subagent_models")(provider="claude-code")
    assert listed.splitlines()[0] == "claude-opus-5 — Opus 5"
    assert 'task(subagent_type="claude-code")' in listed
    assert "not a provider route" in listed
    # With a model named, the answer spells out the exact call to make.
    one = _by_name(tools, "list_subagent_models")(
        provider="claude-code", model="claude-opus-5"
    )
    assert 'task(subagent_type="claude-code", model="claude-opus-5")' in one


# -- workflow meta --------------------------------------------------------------------


def test_meta_phases_may_declare_a_route():
    from harness.tools.workflow import validate_meta

    assert (
        validate_meta(
            {
                "name": "n",
                "description": "d",
                "phases": [
                    {"title": "cheap sweep", "provider": "openai", "model": "gpt-5.6-mini"}
                ],
            }
        )
        is None
    )
    problem = validate_meta(
        {"name": "n", "description": "d", "phases": [{"title": "t", "model": 7}]}
    )
    assert "must be a string" in problem
