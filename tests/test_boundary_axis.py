"""The reach axis (boundary.py) and the two-axis separation (D4).

`Boundary` holds the pure geometry — which roots exist, which accept writes, which
files are protected — with the consent machinery (modes, allowlists, session grants) nowhere
in it, so a future sandbox provider (D5) can replace the geometry and every reach check
travels with the swap. The engine stamps every decision with the axis that produced it
(`Decision.axis`), and the registry carries each guard's axis (`GuardVerdict.axis`),
so "refused because of REACH" is readable without re-deriving the why.
"""

from __future__ import annotations

from pathlib import Path

from harness.safety.boundary import Boundary, command_text, write_paths
from harness.safety.permissions import DEFAULT_GUARDS, GuardVerdict, Mode, PermissionEngine
from harness.safety.secrets import state_dir


# -- the geometry -------------------------------------------------------------------


def test_candidate_resolves_relative_against_the_workspace(tmp_path):
    b = Boundary(workspace_root=tmp_path)
    assert b.candidate("src/app.py") == (tmp_path / "src" / "app.py").resolve()
    absolute = tmp_path / "other.txt"
    assert b.candidate(str(absolute)) == absolute.resolve()


def test_writable_and_readable_roots_answer_differently(tmp_path):
    other = tmp_path.parent / "other"
    other.mkdir()
    b = Boundary(
        workspace_root=tmp_path,
        roots=[{"path": tmp_path, "writable": True}, {"path": other, "writable": False}],
    )
    inside_read_only = str(other / "note.txt")
    assert b.under_any_root(inside_read_only)
    assert not b.under_writable_root(inside_read_only)
    assert b.under_any_root("src/x.py") and b.under_writable_root("src/x.py")
    assert not b.under_any_root("../outside.txt")


def test_roots_are_seen_by_reference_not_snapshot(tmp_path):
    """The same contract `roots` always had: a list mutated in place (the directory-
    grant flow appends to it) is visible on the very next check."""
    b = Boundary(workspace_root=tmp_path)
    assert not b.under_writable_root(str(tmp_path.parent / "granted"))
    b.roots.append({"path": str(tmp_path.parent / "granted"), "writable": True})
    assert b.under_writable_root(str(tmp_path.parent / "granted"))


def test_settings_hit_finds_writes_and_shell_text(tmp_path):
    b = Boundary(workspace_root=tmp_path)
    target = str(state_dir() / "config.toml")
    assert b.settings_hit("write_file", {"path": target, "content": "x"}, False) == target
    assert b.settings_hit("run_shell", {"command": f'echo x > "{target}"'}, True)
    # An ordinary project path never matches — full-path matching only.
    assert b.settings_hit("write_file", {"path": "src/config.toml", "content": "x"}, False) is None


def test_deferred_execution_hit_and_write_paths_stay_put():
    paths, located = write_paths("apply_patch", {"patch": "*** Begin Patch\n*** Update File: .git/hooks/pre-commit\n@@\n*** End Patch"})
    assert located and paths == [".git/hooks/pre-commit"]
    b = Boundary(workspace_root=Path(".").resolve())
    assert b.deferred_execution_hit(paths)
    assert command_text("run_shell", {"command": "git status"}) == "git status"


# -- the engine stamps the axis ------------------------------------------------------


def _engine(tmp_path, mode=Mode.DANGER_FULL_ACCESS, **kw):
    return PermissionEngine(workspace_root=tmp_path, mode=mode, **kw)


def test_boundary_guards_stamp_boundary(tmp_path):
    cases = [
        ("write_file", {"path": str(state_dir() / "config.toml"), "content": "x"}),
        ("run_shell", {"command": f'echo x > "{state_dir() / "secrets.json"}"'}),
    ]
    for tool, args in cases:
        d = _engine(tmp_path).evaluate(tool, args, None)
        assert not d.allowed and d.axis == "boundary", (tool, d)


def test_an_out_of_root_write_asks_on_the_consent_axis(tmp_path):
    """The reach geometry still NAMES the folder, but the verdict is consent now (ADR
    0025): `workspace-write` asks on the same axis as every other ordinary write, full
    access runs it, and the reach itself is unchanged."""
    d = _engine(tmp_path, Mode.WORKSPACE_WRITE).evaluate(
        "write_file", {"path": "../outside.txt", "content": "x"}, None
    )
    assert d.needs_user and d.axis == "consent"
    assert _engine(tmp_path).evaluate(
        "write_file", {"path": "../outside.txt", "content": "x"}, None
    ).allowed


def test_read_only_plan_refuses_on_the_boundary_axis(tmp_path):
    """Under the plan overlay the world does not accept the action at all — reach, not a
    request for consent."""
    d = _engine(tmp_path, plan=True).evaluate(
        "write_file", {"path": "src/app.py", "content": "x"}, None
    )
    assert not d.allowed and not d.needs_user and d.axis == "boundary"


def test_consent_guards_stamp_consent(tmp_path):
    # Below full access, where a human-only floor is still an ask.
    d = _engine(tmp_path, Mode.WORKSPACE_WRITE).evaluate(
        "write_file", {"path": ".git/hooks/pre-commit", "content": "x"}, None
    )
    assert not d.allowed and d.needs_user and d.human_only and d.axis == "consent"

    d = _engine(tmp_path, Mode.WORKSPACE_WRITE).evaluate("save_skill", {"name": "s"}, None)
    assert d.axis == "consent"  # human-only ask; the reach was never the problem


def test_policy_stage_decisions_are_consent_by_definition(tmp_path):
    eng = _engine(tmp_path, Mode.WORKSPACE_WRITE)
    assert eng.evaluate("write_file", {"path": "src/app.py", "content": "x"}, None).axis == "consent"
    assert eng.evaluate("read_file", {"path": "src/app.py"}, None).axis == "consent"


def test_the_registry_carries_each_guards_axis():
    """Axis lives on the registry, not inside the guards — one place per guard, and a
    new guard cannot forget it (it defaults to consent, the conservative side: reach
    claims must be made explicitly)."""
    assert getattr(DEFAULT_GUARDS[0], "axis") == "boundary"
    # Boundary guards come first and consent guards after (`guards.py`, registration order).
    axes = [getattr(guard, "axis", "consent") for guard in DEFAULT_GUARDS]
    assert axes == sorted(axes), "a boundary guard registered after a consent guard"
    assert {guard.__name__ for guard in DEFAULT_GUARDS if guard.axis == "consent"} == {
        "deferred_execution",
        "persistent_authority",
    }
    d = GuardVerdict.deny("x")
    assert d.axis == "consent", "an unstamped verdict defaults to the consent side"


# -- the D5 seam: swap the geometry, keep the consent --------------------------------


def test_swapping_the_boundary_moves_the_reach_not_the_consent(tmp_path):
    """The D5 proof in one frame: a remote-sandbox provider replaces `engine.boundary`
    with geometry over ITS world. Every reach verdict follows the swap; the consent
    machinery — modes, grants, the ask on ordinary writes in interactive — is
    untouched."""
    remote = Path(tmp_path.parent / "remote-sandbox")
    remote.mkdir()
    eng = _engine(tmp_path, Mode.WORKSPACE_WRITE)
    local_file = str(tmp_path / "src" / "app.py")

    # Local geometry: a path inside the workspace is in-bounds (asks in interactive).
    assert eng.boundary.under_writable_root(local_file)
    assert not eng.boundary.under_writable_root(str(remote / "app.py"))

    eng.boundary = Boundary(workspace_root=remote)
    # Relative paths now resolve against the REMOTE primary; the LOCAL file — named
    # absolutely — is out of reach, and the floors moved with the geometry:
    assert eng.boundary.under_writable_root(str(remote / "app.py"))
    assert not eng.boundary.under_writable_root(local_file)
    d = eng.evaluate("write_file", {"path": str(remote / "app.py"), "content": "x"}, None)
    assert d.needs_user and d.axis == "consent", "in-bounds write, interactive asks"
    d = eng.evaluate("write_file", {"path": local_file, "content": "x"}, None)
    assert d.needs_user and d.axis == "consent", "out of reach now, but still an ask"
    assert not eng.boundary.under_writable_root(local_file)
    # And the settings floor still denies outright, whichever world it is asked in.
    d = eng.evaluate(
        "write_file", {"path": str(state_dir() / "config.toml"), "content": "x"}, None
    )
    assert not d.allowed and d.axis == "boundary"


def test_windows_path_spellings_compare_equal():
    """A Git-Bash drive alias and an extended-length prefix must normalise to the same
    comparison key: without it `/c/Users/x` looked like a drive-relative
    path and a legitimate in-root write read as out-of-root."""
    import os

    if os.name != "nt":
        pytest.skip("Windows-only path spellings")
    from harness.safety.boundary.spelling import normalise as _normalise

    assert _normalise("/c/Users/sam/x.txt") == _normalise("C:\\Users\\sam\\x.txt")
    assert _normalise("\\\\?\\C:\\Users\\x.txt") == _normalise("C:\\Users\\x.txt")
    assert _normalise("\\\\?\\UNC\\server\\share\\x.txt") == _normalise("\\\\server\\share\\x.txt")

import pytest
