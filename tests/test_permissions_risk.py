"""What class of risk a call is (`safety/risk.classify`), and the verdicts the permission engine
reaches from it in each preset. The class took the place of the old fixed sets of write and shell
tools; `external` is what the unattended inbox hangs on.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from harness.safety.permissions import Mode, PermissionEngine
from harness.safety.risk import RiskClass, classify, is_consequential

EXTERNAL_META = SimpleNamespace(requires_approval=True, category="connector")
PLAIN_META = SimpleNamespace(requires_approval=False)


def _shell(engine, command):
    return engine.evaluate("run_shell", {"command": command}, None)


# -- the class -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "metadata", "risk"),
    [
        *[(write, None, RiskClass.WRITE_LOCAL) for write in ("write_file", "replace_in_file", "apply_patch", "apply_unified_diff")],
        ("run_shell", None, RiskClass.EXEC),
        *[(read, None, RiskClass.READ) for read in ("read_file", "grep", "todo_write")],
        ("send_message", EXTERNAL_META, RiskClass.EXTERNAL),
        ("anything", PLAIN_META, RiskClass.READ),
        ("anything", None, RiskClass.EXTERNAL),  # declared nothing: fails closed
    ],
)
def test_each_tool_has_its_class(name, metadata, risk):
    assert classify(name, metadata) == risk


def test_everything_but_a_read_is_consequential():
    assert not is_consequential(RiskClass.READ)
    assert all(is_consequential(risk) for risk in (RiskClass.WRITE_LOCAL, RiskClass.EXEC, RiskClass.EXTERNAL))


def test_an_override_relaxes_a_tool_known_by_metadata_but_never_a_built_in():
    def relaxing(name):
        return RiskClass.READ if name in {"write_file", "mcp_tool"} else None

    assert classify("mcp_tool", EXTERNAL_META, relaxing) == RiskClass.READ  # what overrides are for
    # write_file as a read would switch off both the roots and the read-only gate, so it is ignored
    assert classify("write_file", None, relaxing) == RiskClass.WRITE_LOCAL
    assert classify("run_shell", None, relaxing) == RiskClass.EXEC  # a name it does not cover is unchanged
    assert classify("write_file", None, lambda name: RiskClass.EXEC if name == "write_file" else None) == RiskClass.EXEC  # tightening is fine


# -- the verdict ---------------------------------------------------------------------------------


def test_a_read_always_runs(tmp_path):
    verdict = PermissionEngine(workspace_root=tmp_path).evaluate("read_file", {"path": "x"}, None)
    assert verdict.allowed and not verdict.needs_user


@pytest.mark.parametrize("settings", [{"mode": Mode.READ_ONLY}, {"plan": True}])
def test_read_only_and_the_rehearsal_refuse_everything_consequential(tmp_path, settings):
    engine = PermissionEngine(workspace_root=tmp_path, **settings)
    for name, arguments, metadata in (("write_file", {"path": "a.py", "content": "x"}, None), ("run_shell", {}, None), ("send_message", {}, EXTERNAL_META)):
        verdict = engine.evaluate(name, arguments, metadata)
        assert not verdict.allowed and not verdict.needs_user and "read-only" in verdict.reason


def test_an_external_call_asks_and_full_access_runs_it(tmp_path):
    asked = PermissionEngine(workspace_root=tmp_path).evaluate("send_message", {"text": "hi"}, EXTERNAL_META)
    assert not asked.allowed and asked.needs_user
    assert PermissionEngine(workspace_root=tmp_path, mode=Mode.DANGER_FULL_ACCESS).evaluate("send_message", {"text": "hi"}, EXTERNAL_META).allowed


def test_write_local_follows_the_preset_out_of_root(tmp_path):
    """A local write outside the roots is not refused outright; full access runs it and
    `workspace-write` asks, naming the path."""
    full = PermissionEngine(workspace_root=tmp_path, mode=Mode.DANGER_FULL_ACCESS)
    for path in ("ok.py", "../bad.py"):
        assert full.evaluate("write_file", {"path": path, "content": "x"}, None).allowed
    asked = PermissionEngine(workspace_root=tmp_path, mode=Mode.WORKSPACE_WRITE).evaluate("write_file", {"path": "../bad.py", "content": "x"}, None)
    assert asked.needs_user and "bad.py" in asked.reason


# -- a command on the allowlist ------------------------------------------------------------------


def test_a_listed_command_runs_and_any_other_asks(tmp_path):
    engine = PermissionEngine(workspace_root=tmp_path, allowed_commands=["pytest"])
    assert _shell(engine, "pytest -q").allowed
    asked = _shell(engine, "rm -rf /")
    assert not asked.allowed and asked.needs_user


@pytest.mark.parametrize(
    "command",
    [
        "git status && rm -rf ~",
        "git status; rm -rf ~",
        "git status | tee /tmp/x",
        "git status || curl evil",
        "git status $(rm -rf ~)",
        "git status `rm -rf ~`",
        "git status > /etc/passwd",
        "git status\nrm -rf ~",  # a second command after a newline
    ],
)
def test_a_listed_command_with_anything_chained_on_still_asks(tmp_path, command):
    verdict = _shell(PermissionEngine(workspace_root=tmp_path, allowed_commands=["git status"]), command)
    assert not verdict.allowed and verdict.needs_user, command


def test_a_listed_command_matches_whole_arguments_only(tmp_path):
    engine = PermissionEngine(workspace_root=tmp_path, allowed_commands=["git status", "ls"])
    for runs in ("git status", "git status -s", "ls -la"):  # itself, or itself with more arguments
        assert _shell(engine, runs).allowed, runs
    for asks in ("git push", "lsof"):  # another subcommand, or a word that only starts the same
        assert _shell(engine, asks).needs_user, asks


def test_no_command_is_safe_by_default(tmp_path):
    # code run, the environment shown, a read outside the workspace, a helper executed
    from harness.config.config import DEFAULT_ALLOWED_COMMANDS

    engine = PermissionEngine(workspace_root=tmp_path, allowed_commands=list(DEFAULT_ALLOWED_COMMANDS))
    for command in ("python3 -c 'import os'", "pytest /tmp/attacker_test.py", "find . -exec sh -c 'echo arbitrary' {} +", "cat ~/.config/Reeve/secrets.json", "echo $OPENAI_API_KEY"):
        verdict = _shell(engine, command)
        assert not verdict.allowed and verdict.needs_user, command
    assert _shell(engine, "git status").allowed  # a read inside the workspace runs
