"""A plugin agent's run settings reach the Reeve fields that mean the same (Claude Code →
Reeve): `maxTurns` → its step cap, `effort` → its thinking level, `background: true` → every
delegation to it runs in the background where the session can be told, `isolation: worktree` →
its own worktree. They were skipped as "no counterpart" after Reeve had all four."""

from __future__ import annotations

import time

from harness.extensions.plugins.compat import agent_from_claude
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.tools.task import task_tools
from harness.tools.task_agents import resolve_agents


def test_the_four_settings_are_carried_and_nothing_of_them_is_reported_lost():
    entry, skipped = agent_from_claude(
        {"description": "Reviews", "maxTurns": 12, "effort": "High", "background": True, "isolation": "worktree",
         "memory": "project"},
        "Review the change.",
        plugin="release-helper",
    )
    assert entry["steps"] == 12 and entry["variant"] == "high"
    assert entry["background"] is True and entry["isolation"] == "worktree"
    assert skipped == ["memory: not supported"]


def test_a_value_reeve_cannot_honour_is_skipped_not_approximated():
    entry, skipped = agent_from_claude(
        {"maxTurns": "lots", "isolation": "container", "background": "false"}, "", plugin="p"
    )
    assert "steps" not in entry and "isolation" not in entry and entry["background"] is False
    assert any("maxTurns" in item for item in skipped) and any("only worktree" in item for item in skipped)


def _config(background=True):
    return {"agents": {"scout": {"description": "Looks around", "mode": "subagent", "background": background,
                                 "steps": 7, "variant": "high", "isolation": "worktree"}}}


def test_the_reeve_definition_reads_them():
    scout = resolve_agents(_config())["scout"]
    assert scout.background is True and scout.steps == 7 and scout.variant == "high" and scout.isolation == "worktree"
    assert resolve_agents(_config(background="false"))["scout"].background is False


class _Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        time.sleep(0.2)
        return AssistantTurn(text="found it", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


class _Wakes:
    def __init__(self):
        self.calls = []

    def add_completion(self, session_id, job_id, *, note="", depth=0):
        self.calls.append(job_id)


def _task(tmp_path, **kwargs):
    tools = task_tools(workspace=tmp_path, provider=_Provider(), model="gpt-5.6", config=_config(), **kwargs)
    return next(tool for tool in tools if getattr(tool, "__name__", "") == "task")


def test_a_background_agent_runs_in_the_background_where_the_session_can_be_told(tmp_path):
    result = _task(tmp_path, wake_store=_Wakes(), session_id="s1")(description="d", prompt="p", subagent_type="scout")
    assert result["status"] == "running"


def test_without_a_session_to_tell_it_runs_in_the_foreground(tmp_path):
    result = _task(tmp_path)(description="d", prompt="p", subagent_type="scout")
    assert "error" not in result and result.get("status") != "running"
    assert "found it" in str(result.get("report", result))
