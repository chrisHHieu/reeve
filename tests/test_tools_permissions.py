"""The tool registry, and the permission engine's verdicts on the file and git tools it holds:
what runs, what asks, what a session's grant covers, and what full access still does not.
"""

from __future__ import annotations

from pathlib import Path

import aisuite as ai
import pytest

from harness.safety.permissions import Mode, PermissionEngine
from harness.tools import ToolRegistry

WRITE = {"path": "x.py", "content": "x"}
OUT_OF_ROOT = {"path": "../x.py", "content": "x"}


def _registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(root), allow_write=True))
    registry.register_all(ai.toolkits.git(root=str(root)))
    return registry


def _judged(tmp_path, name, arguments, **settings):
    """The verdict on one call by an engine with these settings, given the tool's own metadata."""
    engine = PermissionEngine(workspace_root=tmp_path, **settings)
    return engine.evaluate(name, arguments, _registry(tmp_path).get(name).metadata)


# -- the registry --------------------------------------------------------------------------------


def test_the_registry_offers_each_tool_with_a_function_schema(tmp_path):
    registry = _registry(tmp_path)
    assert {"read_file", "write_file", "list_files"} <= set(registry.names())
    schema = registry.get("read_file").schema
    assert schema["type"] == "function" and schema["function"]["name"] == "read_file" and "parameters" in schema["function"]


def test_the_registry_runs_a_tool_by_name(tmp_path):
    (tmp_path / "hello.txt").write_text("hi there", encoding="utf-8")
    assert _registry(tmp_path).execute("read_file", {"path": "hello.txt"}) == "hi there"


def test_the_registry_will_not_climb_out_of_the_root_or_run_a_name_it_lacks(tmp_path):
    registry = _registry(tmp_path)
    with pytest.raises((PermissionError, ValueError)):
        registry.execute("read_file", {"path": "../../etc/passwd"})
    with pytest.raises(KeyError):
        registry.execute("nope", {})


# -- the verdicts --------------------------------------------------------------------------------


def test_a_read_runs_and_a_write_asks(tmp_path):
    read = _judged(tmp_path, "read_file", {"path": "x"})
    assert read.allowed and not read.needs_user
    write = _judged(tmp_path, "write_file", WRITE)
    assert not write.allowed and write.needs_user


def test_a_write_out_of_the_root_asks_and_names_the_path(tmp_path):
    # An ask any grant clears like another, not a refusal
    asked = _judged(tmp_path, "write_file", {"path": "../escape.py", "content": "x"})
    assert not asked.allowed and asked.needs_user and "escape" in asked.reason


def test_the_rehearsal_refuses_a_write_without_asking(tmp_path):
    refused = _judged(tmp_path, "write_file", WRITE, plan=True)
    assert not refused.allowed and not refused.needs_user and "read-only" in refused.reason


def test_a_command_on_the_list_runs_and_another_asks(tmp_path):
    engine = PermissionEngine(workspace_root=tmp_path, allowed_commands=["pytest", "ls"])
    assert engine.evaluate("run_shell", {"command": "pytest -q"}, None).allowed
    asked = engine.evaluate("run_shell", {"command": "rm -rf /"}, None)
    assert not asked.allowed and asked.needs_user


def test_a_grant_for_the_session_holds_for_the_tool_or_the_command_it_names(tmp_path):
    engine = PermissionEngine(workspace_root=tmp_path)
    write_file = _registry(tmp_path).get("write_file").metadata
    assert engine.evaluate("write_file", WRITE, write_file).needs_user
    engine.allow_tool_for_session("write_file")
    granted = engine.evaluate("write_file", WRITE, write_file)
    assert granted.allowed and not granted.needs_user
    assert engine.evaluate("run_shell", {"command": "make build"}, None).needs_user
    engine.allow_command_for_session("make build")
    assert engine.evaluate("run_shell", {"command": "make build"}, None).allowed


def test_a_tool_set_to_run_unasked_does_so_out_of_the_root_too_and_others_still_ask(tmp_path):
    settings = {"mode": Mode.WORKSPACE_WRITE, "auto_allow_tools": {"write_file"}}
    write = _judged(tmp_path, "write_file", WRITE, **settings)
    assert write.allowed and not write.needs_user
    shell = PermissionEngine(workspace_root=tmp_path, **settings).evaluate("run_shell", {"command": "rm -rf x"}, None)
    assert not shell.allowed and shell.needs_user
    # out of the root is an ask the setting clears; the floors that protect Reeve stay
    assert _judged(tmp_path, "write_file", OUT_OF_ROOT, **settings).allowed


def test_full_access_writes_in_and_out_of_the_root(tmp_path):
    # It reaches outside the root, and only the floors hold against it
    for arguments in (WRITE, OUT_OF_ROOT):
        assert _judged(tmp_path, "write_file", arguments, mode=Mode.DANGER_FULL_ACCESS).allowed
