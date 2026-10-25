"""The permission system guards its own settings, and a file in the project that something runs
later is never approved by a grant.

What this stops: one ordinary-looking command is approved, quietly adds a line to
`risk_overrides.json`, and every session after it is more permissive. That can happen in the default
workspace-write preset, so the guard is a floor, not something one sandbox or one preset gives.
The permission model is the guard stage and consent asks.
"""

from __future__ import annotations

import pytest

from harness.safety.permissions import Mode, PermissionEngine, protected_paths
from harness.safety.secrets import state_dir

# Every preset, the rehearsal on top of each, and the two ways of running unasked that the floor
# has to beat.
EVERY_ARRANGEMENT = [
    {"mode": Mode.READ_ONLY},
    {"plan": True},
    {"mode": Mode.WORKSPACE_WRITE, "plan": True},
    {"mode": Mode.WORKSPACE_WRITE},
    {"mode": Mode.WORKSPACE_WRITE, "auto_allow_tools": {"write_file"}},
    {"mode": Mode.DANGER_FULL_ACCESS, "plan": True},
    {"mode": Mode.DANGER_FULL_ACCESS},
]


def _engine(tmp_path, mode=Mode.WORKSPACE_WRITE, **settings):
    # conftest points the state folder somewhere of the test's own, so the guarded paths are too
    return PermissionEngine(workspace_root=tmp_path, mode=mode, **settings)


def _writes(engine, path):
    return engine.evaluate("write_file", {"path": path, "content": "x"}, None)


def _setting(name):
    return str(state_dir() / name)


# -- Reeve's own settings ------------------------------------------------------------------------


@pytest.mark.parametrize("arrangement", EVERY_ARRANGEMENT)
def test_a_write_or_a_command_that_reaches_the_settings_is_refused_outright(tmp_path, arrangement):
    engine = _engine(tmp_path, **arrangement)
    for verdict in (_writes(engine, _setting("risk_overrides.json")), engine.evaluate("run_shell", {"command": f'echo "{{}}" > "{_setting("risk_overrides.json")}"'}, None)):
        assert not verdict.allowed and not verdict.needs_user  # a refusal, not something to approve


def test_no_way_of_running_unasked_beats_the_floor(tmp_path):
    config = _setting("config.toml")
    assert not _writes(_engine(tmp_path, Mode.DANGER_FULL_ACCESS), config).allowed
    assert not _writes(_engine(tmp_path, auto_allow_tools={"write_file"}), config).allowed
    granted = _engine(tmp_path)
    granted.allow_tool_for_session("write_file")
    assert not _writes(granted, config).allowed


def test_a_patch_that_names_a_setting_in_its_body_is_refused_too(tmp_path):
    patch = f"*** Begin Patch\n*** Update File: {_setting('workspace_trust.json')}\n@@\n-a\n+b\n*** End Patch"
    assert not _engine(tmp_path, Mode.DANGER_FULL_ACCESS).evaluate("apply_patch", {"patch": patch}, None).allowed


def test_the_guarded_files_include_the_grant_and_trust_stores():
    assert {"config.toml", "risk_overrides.json", "workspace_trust.json", "reeve.db"} <= {path.name for path in protected_paths()}


# -- files in the project that run later ---------------------------------------------------------


@pytest.mark.parametrize("path", [".git/hooks/pre-commit", ".github/workflows/ci.yml", ".vscode/tasks.json", ".reeve/config.toml"])
def test_a_file_that_runs_later_asks_a_person_whatever_was_granted(tmp_path, path):
    # it is in the root and writable, yet no grant below full access clears it: a person sees it
    engine = _engine(tmp_path, auto_allow_tools={"write_file"})
    engine.allow_tool_for_session("write_file")
    verdict = _writes(engine, path)
    assert not verdict.allowed and verdict.needs_user, path
    # full access asks nobody: choosing it was the person's answer
    assert _writes(_engine(tmp_path, Mode.DANGER_FULL_ACCESS), path).allowed


@pytest.mark.parametrize("path", ["src/app.py", "docs/pre-commit.md"])
def test_an_ordinary_file_or_one_only_named_like_a_hook_is_not_guarded(tmp_path, path):
    assert _writes(_engine(tmp_path, Mode.DANGER_FULL_ACCESS), path).allowed
