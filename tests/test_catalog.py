"""The vetted tool catalog (`modes/catalog`): each capability is registered, the agent's capabilities
expand to exactly the toolset below, a tool whose context is missing is left out (no shell without
an executor, no file tools without a workspace), and the `code_files` variant gives the same one
numbered reader `files` does.
"""

from __future__ import annotations

import pytest

from harness.modes.agents.base import AgentContext
from harness.modes.agents.reeve import REEVE_CAPABILITIES, reeve_agent
from harness.modes.catalog import CATALOG, capability, expand, risk_summary
from harness.safety.risk import RiskClass
from harness.tools.todo import TodoList

# The one session's tools, held exactly. Code joined it on 2026-09-20, bringing the terminals; git
# is read through `run_shell`, so the read-only git tools are not here.
REEVE_TOOLS = {
    "list_files",
    "read_file",  # text, PDF, office files, notebooks and images, one reader
    "edit_notebook",  # a notebook by its cells, never its JSON
    "write_file",
    # no `apply_unified_diff` (the 2026-09-24 audit: 8 of 9 real calls failed, and `apply_patch` and
    # `replace_in_file` make any edit it could)
    "apply_patch",
    "replace_in_file",
    "grep",
    "run_shell",
    "shell_task_output",
    "shell_task_kill",
    *(f"terminal_{verb}" for verb in ("open", "send", "read", "close", "signal", "list")),
    "todo_write",
}


def _names(tools) -> set:
    return {getattr(tool, "__name__", "") for tool in tools}


def _everything(tmp_path) -> AgentContext:
    return AgentContext(workspace=tmp_path, executor=object(), todo=TodoList(), terminals=object())


def _reeve_with(**context) -> set:
    return _names(expand(REEVE_CAPABILITIES, AgentContext(**context)))


def test_the_capabilities_are_registered_and_each_can_build():
    assert {"code_files", "files", "search", "shell", "todo"} <= set(CATALOG) and "git" not in CATALOG
    assert all(entry.id and entry.name and callable(entry.build) for entry in CATALOG.values())


def test_the_agent_expands_to_exactly_its_tools_through_the_catalog_and_the_agent(tmp_path):
    assert _names(expand(REEVE_CAPABILITIES, _everything(tmp_path))) == REEVE_TOOLS
    assert _names(reeve_agent().build_tools(_everything(tmp_path))) == REEVE_TOOLS


def test_both_file_capabilities_give_the_one_windowed_reader(tmp_path):
    # read_file_lines folded into read_file everywhere (owner, 2026-08-20)
    for variant in ("code_files", "files"):
        names = _names(expand([variant], _everything(tmp_path)))
        assert "read_file" in names and "read_file_lines" not in names, variant


def test_a_tool_whose_context_is_missing_is_left_out(tmp_path):
    no_executor = _reeve_with(workspace=tmp_path, executor=None, todo=TodoList())
    assert "run_shell" not in no_executor and "todo_write" in no_executor
    no_todo = _reeve_with(workspace=tmp_path, executor=object(), todo=None)
    assert "todo_write" not in no_todo and "run_shell" in no_todo
    no_workspace = _reeve_with(workspace=None, executor=object(), todo=TodoList())
    assert "list_files" not in no_workspace and "grep" not in no_workspace
    assert "run_shell" in no_workspace and "todo_write" in no_workspace


@pytest.mark.parametrize(("capabilities", "risks"), [(["shell"], {RiskClass.EXEC}), (["code_files"], {RiskClass.READ, RiskClass.WRITE_LOCAL}), (["search"], {RiskClass.READ})])
def test_the_risk_of_a_set_of_capabilities_is_summed_up(capabilities, risks):
    assert risk_summary(capabilities) == risks


def test_an_unknown_capability_is_a_key_error():
    with pytest.raises(KeyError):
        capability("does_not_exist")
