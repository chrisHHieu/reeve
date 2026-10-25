"""The two Settings ▸ Subagents switches as a MATRIX, plus the rebuild boundary.

`subagent_model_selection` (prefs, `modelSelectionSettings`) decides whether a delegation
call may pick its LLM route; `disabled_subagents.json` decides whether an agent TYPE can
be delegated to at all. They are independent, and the manager re-reads both at spawn
time — so a DISABLE or a "selection off" takes effect immediately, while an ENABLE (or
turning selection back on) cannot reach an already-built engine whose tool schema was
composed without it. That asymmetry is the "next session build" semantics the Code Mode
toggle shares, and these tests pin it.
"""

from __future__ import annotations

import asyncio

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.interface.server.manager import SessionManager
from harness.session.sessions import SessionRecord
from harness.orchestration.subagent_flags import set_subagent_disabled
from harness.orchestration.subagent_external import PROVIDER_BINARIES
from harness.tools import task_agents
from harness.tools.task import task_tools


class _Scripted(ProviderClient):
    """Records every model it is asked for, then answers one text turn."""

    def __init__(self, text: str = "ok") -> None:
        self.models: list[str] = []
        self._text = text

    def complete(self, *, model, messages, tools=None, **settings):
        self.models.append(model)
        return AssistantTurn(text=self._text, finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


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
            description=kwargs.pop("description", "switch matrix"),
            prompt=kwargs.pop("prompt", "p"),
            agent_name=kwargs.pop("agent_name", "general"),
            **kwargs,
        )
    )


def _task_of(engine):
    return next(t for t in [engine.registry.get("task").func])


# -- selection × enabled, in-process ---------------------------------------------------


def test_selection_off_still_spawns_on_the_agents_own_model(tmp_path):
    provider = _Scripted()
    manager = _manager(tmp_path, provider)
    manager.set_subagent_model_selection(False)
    _parent(manager)

    out = _spawn(manager)

    assert out["status"] == "completed"
    # No override: the child runs on the model it would have used anyway.
    assert provider.models == ["gpt-5.6"]


def test_selection_off_refuses_an_override_but_not_a_plain_spawn(tmp_path):
    provider = _Scripted()
    manager = _manager(tmp_path, provider)
    manager.secrets.put("provider:deepseek", {"api_key": "k"})
    manager.set_subagent_model_selection(False)
    _parent(manager)

    refused = _spawn(manager, provider="deepseek", model="deepseek-flash")
    assert "turned off in Settings" in refused["error"]
    assert provider.models == []

    plain = _spawn(manager, description="plain")
    assert plain["status"] == "completed"
    assert provider.models == ["gpt-5.6"]


def test_enabled_off_refuses_the_spawn_with_selection_on(tmp_path):
    provider = _Scripted()
    manager = _manager(tmp_path, provider)
    manager.secrets.put("provider:deepseek", {"api_key": "k"})
    _parent(manager)
    set_subagent_disabled("general", True)

    out = _spawn(manager)

    assert "turned off in Settings" in out["error"]
    assert provider.models == []
    assert manager.children_of("parent-1") == []


def test_a_disabled_agent_outranks_the_route_refusal(tmp_path):
    """Both switches off with an override passed: the answer names the enable switch —
    a person must turn the agent back on before any route question matters."""
    provider = _Scripted()
    manager = _manager(tmp_path, provider)
    manager.secrets.put("provider:deepseek", {"api_key": "k"})
    manager.set_subagent_model_selection(False)
    _parent(manager)
    set_subagent_disabled("general", True)

    out = _spawn(manager, provider="deepseek", model="deepseek-flash")

    assert "turned off in Settings" in out["error"]


# -- the rebuild boundary ----------------------------------------------------------------


def test_disabling_takes_effect_immediately_even_on_an_open_engine(tmp_path):
    """The spawn path re-resolves the store, so the switch bites at the next call — the
    engine's baked task tool never gets a chance (the child would be refused anyway)."""
    provider = _Scripted()
    manager = _manager(tmp_path, provider)
    parent = _parent(manager)
    engine = manager.get_engine(parent.session_id)
    assert engine is not None

    set_subagent_disabled("general", True)
    out = _spawn(manager)

    assert "turned off in Settings" in out["error"]


def test_enabling_needs_a_rebuild_for_an_open_engine(tmp_path):
    """The baked agent map keeps the refusal until the engine is rebuilt: enable in
    Settings, reopen the conversation, and the spawn works."""
    provider = _Scripted()
    manager = _manager(tmp_path, provider)
    parent = _parent(manager)
    set_subagent_disabled("general", True)
    engine = manager.get_engine(parent.session_id)
    assert engine is not None

    # The open engine's task tool refuses from its own resolved map...
    tool = engine.registry.get("task").func
    out = tool(description="d", prompt="p", subagent_type="general")
    assert "turned off in Settings" in out["error"]

    # ...and enabling does not update that map in place: the next build does.
    set_subagent_disabled("general", False)
    out = tool(description="d", prompt="p", subagent_type="general")
    assert "turned off in Settings" in out["error"]

    manager._engines.pop(parent.session_id)
    rebuilt = manager.get_engine(parent.session_id)
    assert rebuilt is not None
    tool = rebuilt.registry.get("task").func
    out = tool(description="d", prompt="p", subagent_type="general")
    assert "turned off in Settings" not in str(out.get("error") or "")


def test_selection_off_at_build_shapes_the_schema_until_rebuild(tmp_path):
    """OFF is baked into the tool schema; turning it back on reaches a NEW build only."""
    provider = _Scripted()
    manager = _manager(tmp_path, provider)
    manager.set_subagent_model_selection(False)
    parent = _parent(manager)
    engine = manager.get_engine(parent.session_id)
    properties = engine.registry.get("task").schema["function"]["parameters"]["properties"]
    assert "provider" not in properties

    manager.set_subagent_model_selection(True)
    assert "provider" not in engine.registry.get("task").schema["function"]["parameters"][
        "properties"
    ]

    manager._engines.pop(parent.session_id)
    rebuilt = manager.get_engine(parent.session_id)
    properties = rebuilt.registry.get("task").schema["function"]["parameters"]["properties"]
    assert "provider" in properties and "model" in properties


def test_the_thread_surface_reads_the_flag_at_build(tmp_path):
    """The CLI/thread path has no manager re-check: the flag is the tool's own."""
    tools = task_tools(
        workspace=tmp_path, provider=_Scripted(), model="gpt-5.6", model_selection=False
    )
    task = next(t for t in tools if getattr(t, "__name__", "") == "task")
    out = task(description="d", prompt="p", subagent_type="general", model="gpt-5.6-mini")
    assert "turned off in Settings" in out["error"]


# -- the CLI agents ----------------------------------------------------------------------


def _codex(monkeypatch):
    monkeypatch.setattr(
        task_agents,
        "external_agent_infos",
        lambda: {
            "codex": task_agents.AgentInfo(
                name="codex",
                description="external codex",
                mode=task_agents.MODE_SUBAGENT,
                provider="codex",
                native=True,
            )
        },
    )
    monkeypatch.setattr("harness.orchestration.subagent_external.preflight", lambda provider: None)
    seen: list[dict] = []

    def fake_run(provider, **kwargs):
        seen.append({"provider": provider, **kwargs})
        return {"status": "completed", "report": "OK", "tokens": 0}

    monkeypatch.setattr("harness.orchestration.subagent_external.run_external", fake_run)
    return seen


def test_cli_selection_off_still_spawns_without_a_model(tmp_path, monkeypatch):
    seen = _codex(monkeypatch)
    manager = _manager(tmp_path, _Scripted())
    manager.set_subagent_model_selection(False)
    _parent(manager)

    out = _spawn(manager, agent_name="codex")

    assert out["status"] == "completed"
    assert len(seen) == 1 and seen[0]["model"] == ""


def test_cli_with_a_pinned_model_passes_its_own_not_the_parents(tmp_path, monkeypatch):
    """Settings' pin for a CLI agent (`subagent_models.json`) reaches the CLI; the
    parent's LLM model id never does (owner-report 2026-09-21 matrix)."""
    from harness.orchestration.subagent_models import set_model_override

    set_model_override("codex", "gpt-5.6-luna")
    seen = _codex(monkeypatch)
    manager = _manager(tmp_path, _Scripted())
    _parent(manager)

    out = _spawn(manager, agent_name="codex")

    assert out["status"] == "completed"
    assert seen[0]["model"] == "gpt-5.6-luna"
    assert manager.session_store.load(out["task_id"]).model == "gpt-5.6-luna"


def test_cli_selection_off_refuses_a_model_override(tmp_path, monkeypatch):
    seen = _codex(monkeypatch)
    manager = _manager(tmp_path, _Scripted())
    manager.set_subagent_model_selection(False)
    _parent(manager)

    out = _spawn(manager, agent_name="codex", model="gpt-5.6-luna")

    assert "turned off in Settings" in out["error"]
    assert seen == []


def test_cli_enabled_off_refuses_with_selection_on(tmp_path, monkeypatch):
    seen = _codex(monkeypatch)
    manager = _manager(tmp_path, _Scripted())
    _parent(manager)
    set_subagent_disabled("codex", True)

    out = _spawn(manager, agent_name="codex", model="gpt-5.6-luna")

    # Disabled first: the model question never comes up.
    assert "turned off in Settings" in out["error"]
    assert seen == []
    assert manager.children_of("parent-1") == []


def test_cli_disabling_takes_effect_immediately_on_an_open_engine(tmp_path, monkeypatch):
    seen = _codex(monkeypatch)
    manager = _manager(tmp_path, _Scripted())
    parent = _parent(manager)
    manager.get_engine(parent.session_id)

    set_subagent_disabled("codex", True)
    out = _spawn(manager, agent_name="codex")

    assert "turned off in Settings" in out["error"]
    assert seen == []


def test_cli_enable_after_build_needs_the_rebuild_too(tmp_path, monkeypatch):
    seen = _codex(monkeypatch)
    manager = _manager(tmp_path, _Scripted())
    parent = _parent(manager)
    set_subagent_disabled("codex", True)
    engine = manager.get_engine(parent.session_id)
    tool = engine.registry.get("task").func
    assert "turned off in Settings" in tool(
        description="d", prompt="p", subagent_type="codex"
    )["error"]

    set_subagent_disabled("codex", False)
    manager._engines.pop(parent.session_id)
    rebuilt = manager.get_engine(parent.session_id)
    tool = rebuilt.registry.get("task").func
    out = tool(description="d", prompt="p", subagent_type="codex")
    assert out.get("status") in ("running", "completed")
    assert len(seen) == 1
