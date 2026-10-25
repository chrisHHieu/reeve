"""The managed `REEVE_*` shell environment.

The property that decides whether this is worth having: a command must be able to READ a
fact about its session instead of being handed it in the command string. Every test below
either proves that, or proves one of the two rules that keep it honest — declared keys, and
values resolved per call. See harness/tools/shell_env.py.
"""

from __future__ import annotations

import os
import sys

import pytest

from harness.config.roots import RootDir
from harness.tools.shell import LocalExecutor
from harness.tools.shell_env import PREFIX, ROOTS, SCRATCH, WORKSPACE, EnvContributor, ShellEnv, describe, export_preamble, session_contributor


def _fixed(name: str, values: dict[str, str]) -> EnvContributor:
    return EnvContributor(
        name=name,
        variables={key: f"{key} means something" for key in values},
        resolve=lambda: dict(values),
    )


# -- declaration -----------------------------------------------------------------------


def test_two_contributors_claiming_one_key_collide_at_registration():
    """Before the first command, not at some later call where one silently wins."""
    env = ShellEnv()
    env.register(_fixed("a", {f"{PREFIX}THING": "1"}))

    with pytest.raises(ValueError, match="already contributed by 'a'"):
        env.register(_fixed("b", {f"{PREFIX}THING": "2"}))


def test_an_unprefixed_key_is_refused():
    """The prefix is what makes the set enumerable and makes it obvious in a command which
    values came from the harness."""
    env = ShellEnv()
    with pytest.raises(ValueError, match="must start with"):
        env.register(_fixed("a", {"PATH": "/nope"}))


def test_an_undeclared_value_is_dropped():
    """The declaration is the contract. Honouring a key nobody declared would make the
    conflict check a formality."""
    env = ShellEnv()
    env.register(
        EnvContributor(
            name="sneaky",
            variables={f"{PREFIX}DECLARED": "declared"},
            resolve=lambda: {f"{PREFIX}DECLARED": "yes", f"{PREFIX}SMUGGLED": "no"},
        )
    )

    assert env.resolve() == {f"{PREFIX}DECLARED": "yes"}


def test_disposing_a_contributor_frees_its_keys():
    """A registration that cannot be undone is a leak the moment anything is scoped to one
    session — the same rule the pipeline's listeners follow."""
    env = ShellEnv()
    dispose = env.register(_fixed("a", {f"{PREFIX}THING": "1"}))

    dispose()

    assert env.resolve() == {}
    env.register(_fixed("b", {f"{PREFIX}THING": "2"}))  # the key is free again


def test_a_failing_contributor_costs_its_variables_and_nothing_else():
    """This is context. A command that would have worked must not be lost because a fact
    about it could not be computed."""
    env = ShellEnv()

    def boom():
        raise RuntimeError("no")

    env.register(
        EnvContributor(name="bad", variables={f"{PREFIX}BAD": "x"}, resolve=boom)
    )
    env.register(_fixed("good", {f"{PREFIX}GOOD": "1"}))

    assert env.resolve() == {f"{PREFIX}GOOD": "1"}


# -- resolved per call -----------------------------------------------------------------


def test_a_folder_granted_mid_session_appears_in_the_next_command(tmp_path):
    """`root_list` is mutated in place while the session runs, so the contributor holds it
    by reference and is asked again each time. A copy taken at build would publish the
    grants the session started with, forever."""
    roots = [RootDir(path=tmp_path, writable=True, label="scratch")]
    env = ShellEnv()
    env.register(session_contributor(workspace=tmp_path, roots=roots))

    before = env.resolve()[ROOTS]

    granted = tmp_path / "granted"
    granted.mkdir()
    roots.append(RootDir(path=granted, writable=False))  # in place, as Slice C does

    after = env.resolve()[ROOTS]
    assert str(granted) not in before
    assert str(granted) in after


def test_the_scratch_root_is_named_by_its_label_not_its_position(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    other = tmp_path / "project"
    other.mkdir()
    roots = [RootDir(path=other, writable=True), RootDir(path=scratch, writable=True, label="scratch")]
    env = ShellEnv()
    env.register(session_contributor(workspace=other, roots=roots))

    assert env.resolve()[SCRATCH] == str(scratch)


def test_a_value_that_does_not_apply_is_absent_not_empty(tmp_path):
    """A command testing `-n "$REEVE_SCRATCH"` must be able to tell "no scratch" from
    "scratch is nowhere"."""
    env = ShellEnv()
    env.register(session_contributor(workspace=tmp_path, roots=[]))

    values = env.resolve()
    assert values[WORKSPACE] == str(tmp_path)
    assert SCRATCH not in values
    assert ROOTS not in values


# -- rendering -------------------------------------------------------------------------


def test_a_value_with_a_quote_cannot_break_out_of_its_line():
    """A path can contain a space, and on POSIX a quote; a value spliced in raw would
    either break the line or execute part of itself."""
    values = {f"{PREFIX}X": "it's here", f"{PREFIX}Y": "a b"}

    posix = export_preamble(values, powershell=False)
    assert posix == (
        f"export {PREFIX}X='it'\\''s here'\n" f"export {PREFIX}Y='a b'\n"
    )
    windows = export_preamble(values, powershell=True)
    assert windows == (f"$env:{PREFIX}X = 'it''s here'\n" f"$env:{PREFIX}Y = 'a b'\n")


def test_nothing_registered_renders_nothing():
    assert export_preamble({}, powershell=False) == ""
    assert describe(ShellEnv()) == ""
    assert describe(None) == ""


def test_the_model_is_told_what_exists(tmp_path):
    env = ShellEnv()
    env.register(session_contributor(workspace=tmp_path, roots=[]))

    text = describe(env)
    assert WORKSPACE in text and SCRATCH in text
    assert "deliverables" in text  # the meaning, not just the name


# -- end to end ------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "win32" and not os.path.exists("/bin/bash"),
    reason="needs a native shell",
)
def test_a_real_command_can_read_the_variable(tmp_path):
    """The one that matters: not that the preamble was written, but that the shell can
    read it back. A string check would pass with the export going to the wrong process."""
    env = ShellEnv()
    env.register(session_contributor(workspace=tmp_path, roots=[]))
    executor = LocalExecutor(cwd=tmp_path, shell_env=env)
    try:
        command = (
            f"echo $env:{WORKSPACE}" if sys.platform == "win32" else f'echo "${WORKSPACE}"'
        )
        out = executor.run(command, timeout=30)
    finally:
        executor.close()

    assert str(tmp_path) in out.get("output", "")


@pytest.mark.skipif(
    sys.platform != "win32" and not os.path.exists("/bin/bash"),
    reason="needs a native shell",
)
def test_the_exports_do_not_answer_for_the_command_s_exit_code(tmp_path):
    """Separate writes, not a prefix on the command: folding an export into the same line
    would let a successful export report success for a command that failed."""
    env = ShellEnv()
    env.register(session_contributor(workspace=tmp_path, roots=[]))
    executor = LocalExecutor(cwd=tmp_path, shell_env=env)
    try:
        # NOT `exit 3`: that ends the persistent shell itself, so no trailer ever comes
        # back and the test would be measuring a dead shell rather than an exit code.
        # A child process that fails is the thing this asserts about.
        failing = 'cmd /c "exit 3"' if sys.platform == "win32" else "(exit 3)"
        out = executor.run(failing, timeout=30)
    finally:
        executor.close()

    assert out.get("exit_code") == 3


@pytest.mark.skipif(
    sys.platform != "win32" and not os.path.exists("/bin/bash"),
    reason="needs a native shell",
)
def test_an_executor_without_a_registry_behaves_exactly_as_before(tmp_path):
    executor = LocalExecutor(cwd=tmp_path)
    try:
        out = executor.run("echo hello", timeout=30)
    finally:
        executor.close()

    assert "hello" in out.get("output", "")
