"""The SSH execution world: transport, executor, remote fs, terminals.

Everything here runs against ``FakeSsh``/``FakeRemoteHost`` — no test spawns a real ssh,
reads ~/.ssh, or touches a network. The tests pin the exact argv posture, the sentinel
framing/parsing, the typed failure vocabulary, the executor's cwd/env/scratch contract,
the remote file operations (binary-safe read/write, find, grep -rn) and their containment
refusals, the terminal-through-executor surface, and the factory defaulting to local.
"""

from __future__ import annotations

import subprocess

import pytest

from harness.orchestration.remote_env import RemoteAuthError, RemoteCommandError, RemoteConnectionError, RemoteExecutor, RemoteFileBackend, RemoteHostKeyError, RemoteProtocolError, RemoteSession, RemoteSshMissingError, RemoteTimeoutError, RemoteUnsupportedError, RemoteWorld, frame_command, parse_frame
from harness.testing.fake_ssh import FakeRemoteHost, FakeSsh
from harness.tools.shell import Executor
from harness.tools.terminal import terminal_tools
from harness.orchestration.worlds import LocalWorld, World, remote_world, world_for

PROFILE = {
    "name": "Build server",
    "host": "dev.example.com",
    "user": "dev",
    "port": 2222,
    "identity_file": "~/.ssh/id_ed25519",
}
WORKSPACE = "/home/dev/project"
SCRATCH = "/home/dev/.reeve/scratch/sess-1"


def make_world(*, files=None, session_id="sess-1", verify=True, directories=None):
    host = FakeRemoteHost(directories={WORKSPACE, *(directories or [])})
    for path, content in (files or {}).items():
        host.write(path, content)
    fake = FakeSsh(host=host)
    world = remote_world(
        PROFILE, WORKSPACE, runner=fake, session_id=session_id, verify=verify
    )
    return world, fake, host


def tools_of(world, roots=None):
    return {t.__name__: t for t in world.fs_tools(roots=roots)}


# -- argv posture + framing -----------------------------------------------------------


def test_run_carries_the_phase1_posture_and_frames_the_command():
    world, fake, _host = make_world()
    world.executor.run("echo hi")
    args = fake.last_args
    assert args[0] == "ssh", "the system binary; options are argv entries, never a shell"
    for flag in (
        "BatchMode=yes",
        "StrictHostKeyChecking=yes",
        "ConnectTimeout=10",
    ):
        assert flag in args
    assert fake.last_destination == "dev@dev.example.com"
    assert args[args.index("-p") + 1] == "2222"
    assert args[args.index("-i") + 1].endswith("id_ed25519")
    assert fake.last_remote_command.startswith("sh -c ")
    assert world.session.sentinel in fake.last_remote_command
    assert "echo hi" in fake.last_remote_command


def test_frame_round_trip_and_honest_misses():
    sentinel = "REEVE_REMOTE_RUN_V1_test"
    assert frame_command("true", sentinel).startswith("sh -c ")

    stdout = f"\x00{sentinel}\x00hi\x00{sentinel}\x007\x00/srv\x00"
    frame = parse_frame(stdout, sentinel)
    assert (frame.exit_code, frame.cwd, frame.output, frame.found) == (
        7,
        "/srv",
        "hi",
        True,
    )

    # Output containing the marker must not truncate the body — the trailer is last.
    noisy = f"\x00{sentinel}\x00echo {sentinel}\n\x00{sentinel}\x000\x00/srv\x00"
    noisy_frame = parse_frame(noisy, sentinel)
    assert noisy_frame.output == f"echo {sentinel}\n"
    assert noisy_frame.exit_code == 0 and noisy_frame.cwd == "/srv"

    # A command that never reached the trailer: no invented exit status.
    missing = parse_frame(f"\x00{sentinel}\x00partial", sentinel)
    assert missing.found is False and missing.exit_code is None
    assert missing.output == "partial"
    foreign = parse_frame("hello from the motd\n", sentinel)
    assert foreign.found is False and foreign.output == "hello from the motd\n"


# -- typed failures -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("runner", "expected", "needle"),
    [
        (FakeSsh.auth_failure(), RemoteAuthError, "Authentication failed"),
        (FakeSsh.host_key_failure(), RemoteHostKeyError, "known_hosts"),
        (
            FakeSsh(error=subprocess.TimeoutExpired("ssh", 30)),
            RemoteTimeoutError,
            "Timed out",
        ),
        (
            FakeSsh(error=FileNotFoundError("ssh")),
            RemoteSshMissingError,
            "was not found",
        ),
        (
            FakeSsh(
                returncode=255,
                stderr="ssh: connect to host dev.example.com port 2222: Connection refused",
            ),
            RemoteConnectionError,
            "refused",
        ),
    ],
)
def test_typed_failures_map_to_the_phase1_vocabulary(runner, expected, needle):
    session = RemoteSession(PROFILE, runner=runner)
    with pytest.raises(expected) as excinfo:
        session.run("true")
    assert needle in str(excinfo.value)


def test_inspect_requires_a_supported_platform():
    session = RemoteSession(PROFILE, runner=FakeSsh.unsupported())
    with pytest.raises(RemoteUnsupportedError) as excinfo:
        session.inspect()
    assert "FreeBSD" in str(excinfo.value) and "x64 or arm64" in str(excinfo.value)


def test_inspect_refuses_an_invalid_payload():
    session = RemoteSession(PROFILE, runner=FakeSsh(stdout="hello from the motd\n"))
    with pytest.raises(RemoteProtocolError):
        session.inspect()


def test_inspect_populates_the_target_facts():
    session = RemoteSession(
        PROFILE, runner=FakeSsh(platform="Darwin", arch="aarch64", home="/Users/dev")
    )
    parsed = session.inspect()
    assert parsed["platform"] == "darwin" and parsed["arch"] == "arm64"
    assert session.home == "/Users/dev"


def test_remote_world_verifies_before_constructing():
    with pytest.raises(RemoteUnsupportedError):
        remote_world(PROFILE, WORKSPACE, runner=FakeSsh.unsupported(), session_id="s")
    host = FakeRemoteHost(directories={WORKSPACE})
    fake = FakeSsh(host=host)
    world = remote_world(PROFILE, WORKSPACE, runner=fake, session_id="s", verify=False)
    assert fake.calls == [], "verify=False must not probe"
    assert isinstance(world, RemoteWorld)


# -- the executor: cwd, env, scratch, caps, timeouts ----------------------------------


def test_executor_runs_in_the_remote_workspace_and_tracks_cd():
    world, _fake, _host = make_world()
    executor = world.executor
    first = executor.run("pwd")
    assert first["exit_code"] == 0
    assert first["cwd"] == WORKSPACE
    assert first["output"].strip() == WORKSPACE

    assert executor.run("mkdir -p sub")["exit_code"] == 0
    moved = executor.run("cd sub")
    assert moved["exit_code"] == 0 and moved["cwd"] == f"{WORKSPACE}/sub"
    assert executor.run("pwd")["output"].strip() == f"{WORKSPACE}/sub"
    assert executor.run("cd /nowhere")["exit_code"] != 0, "a failed cd is reported"


def test_executor_exports_the_scratch_contract():
    world, fake, host = make_world(session_id="sess-1")
    result = world.executor.run(
        'echo "$REEVE_SCRATCH|$REEVE_WORKSPACE|$REEVE_SESSION"'
    )
    assert result["output"].strip() == f"{SCRATCH}|{WORKSPACE}|sess-1"
    assert SCRATCH in host.directories
    assert host.modes.get(SCRATCH) == 0o700
    # Scratch is created once, not per command.
    assert len([call for call in fake.calls if "reeve-remote-op" in call[-1]]) == 1


def test_executor_bounds_output_with_retention():
    world, _fake, _host = make_world()
    executor = world.executor
    executor.max_output_chars = 200
    result = executor.run("echo " + "x" * 5_000)
    assert result["truncated"] is True
    assert len(result["output"]) <= 200
    assert result["exit_code"] == 0


class _TimeoutSession:
    home = "/home/dev"

    def ensure_home(self):
        return self.home

    def run(self, command, *, timeout=None, input_text=None):
        raise RemoteTimeoutError("Timed out after 5s waiting for dev. Check it.")


def test_executor_timeout_is_a_timed_out_result_not_a_crash():
    executor = RemoteExecutor(_TimeoutSession(), cwd=WORKSPACE, session_id="s")
    result = executor.run("sleep 100", timeout=5)
    assert result["timed_out"] is True
    assert result["exit_code"] is None
    assert "timed out" in result["error"]


def test_executor_maps_connection_failures_to_the_tool_error_key():
    session = RemoteSession(PROFILE, runner=FakeSsh.auth_failure())
    executor = RemoteExecutor(session, cwd=WORKSPACE, session_id="s")
    result = executor.run("pwd")
    assert result["error"] and "Authentication failed" in result["error"]
    assert "exit_code" in result and result["exit_code"] is None


# -- remote files: read/write/list/lines/search ---------------------------------------


def test_fs_write_read_round_trip_is_binary_safe():
    world, _fake, host = make_world()
    tools = tools_of(world)
    text = "Xin chào\nß — 😀\nsecond line\n"
    assert tools["write_file"]("notes/hello.txt", text) == "notes/hello.txt"
    assert host.read(f"{WORKSPACE}/notes/hello.txt").decode("utf-8") == text
    assert tools["read_file"]("notes/hello.txt") == text

    backend = RemoteFileBackend(world.session, WORKSPACE)
    raw = bytes(range(256))
    backend.write_bytes(f"{WORKSPACE}/raw.bin", raw)
    assert backend.read_bytes(f"{WORKSPACE}/raw.bin") == raw


def test_fs_list_filters_ignored_dirs_and_honours_patterns():
    world, _fake, _host = make_world(
        files={
            f"{WORKSPACE}/a.py": "a\n",
            f"{WORKSPACE}/pkg/b.py": "needle here\n",
            f"{WORKSPACE}/node_modules/x.js": "x\n",
            f"{WORKSPACE}/.git/config": "git\n",
        }
    )
    tools = tools_of(world)
    assert tools["list_files"]() == ["a.py", "pkg/b.py"]
    assert tools["list_files"](pattern="*.py") == ["a.py", "pkg/b.py"]
    assert tools["list_files"](recursive=False) == ["a.py"]
    assert tools["list_files"]("pkg") == ["pkg/b.py"]


def test_fs_read_file_lines_windows():
    world, _fake, _host = make_world(files={f"{WORKSPACE}/pkg/b.py": "one\ntwo\nthree\n"})
    result = tools_of(world)["read_file_lines"]("pkg/b.py", start_line=2, max_lines=1)
    assert result == {
        "path": "pkg/b.py",
        "start_line": 2,
        "end_line": 2,
        "total_lines": 3,
        "content": "two",
    }


def test_fs_search_files_grep_rn_shape():
    world, _fake, _host = make_world(
        files={
            f"{WORKSPACE}/pkg/b.py": "needle here\nother\n",
            f"{WORKSPACE}/node_modules/x.js": "needle too\n",
            f"{WORKSPACE}/readme.md": "needle in prose\n",
        }
    )
    tools = tools_of(world)
    assert tools["search_files"]("needle") == [
        {"path": "pkg/b.py", "line": 1, "text": "needle here"},
        {"path": "readme.md", "line": 1, "text": "needle in prose"},
    ]
    assert tools["search_files"]("needle", pattern="*.py") == [
        {"path": "pkg/b.py", "line": 1, "text": "needle here"}
    ]
    assert tools["search_files"]("absent") == []


def test_fs_replace_in_file_counts_before_writing():
    world, _fake, _host = make_world(files={f"{WORKSPACE}/f.txt": "one two one\n"})
    tools = tools_of(world)
    with pytest.raises(ValueError) as excinfo:
        tools["replace_in_file"]("f.txt", "one", "ONE")
    assert "Expected 1 replacement(s), found 2" in str(excinfo.value)
    result = tools["replace_in_file"]("f.txt", "one", "ONE", expected_replacements=2)
    assert result["replacements"] == 2
    assert tools["read_file"]("f.txt") == "ONE two ONE\n"


def test_fs_apply_patch_is_the_shared_core_on_the_remote_host():
    """The remote world's `apply_patch` is the local one's core — same matcher,
    nothing written until every file applies, each file back in its own convention."""
    world, _fake, host = make_world(files={
        f"{WORKSPACE}/win.py": b"\xef\xbb\xbfa = 1\r\nb = 2\r\n",
        f"{WORKSPACE}/ws.py": "def f():   \n    return 1\n",
    })
    tools = tools_of(world)
    result = tools["apply_patch"](
        "*** Begin Patch\n"
        "*** Update File: win.py\n@@\n-a = 1\n+a = 10\n b = 2\n"
        "*** Update File: ws.py\n@@\n def f():\n-    return 1\n+    return 2\n"
        "*** End Patch\n"
    )
    assert host.read(f"{WORKSPACE}/win.py") == b"\xef\xbb\xbfa = 10\r\nb = 2\r\n"
    assert host.read_text(f"{WORKSPACE}/ws.py") == "def f():\n    return 2\n"
    assert "ignoring trailing whitespace" in result["matched_via"]
    with pytest.raises(ValueError, match="Patch context does not match ws.py"):
        tools["apply_patch"](
            "*** Begin Patch\n"
            "*** Update File: win.py\n@@\n-a = 10\n+a = 11\n"
            "*** Update File: ws.py\n@@\n-nothing like this\n+x\n"
            "*** End Patch\n"
        )
    assert host.read(f"{WORKSPACE}/win.py") == b"\xef\xbb\xbfa = 10\r\nb = 2\r\n"


def test_fs_apply_patch_and_unified_diff():
    world, _fake, _host = make_world(
        files={f"{WORKSPACE}/app.py": "def main():\n    return 1\n"}
    )
    tools = tools_of(world)
    patched = tools["apply_patch"](
        "*** Begin Patch\n"
        "*** Update File: app.py\n"
        "@@\n"
        " def main():\n"
        "-    return 1\n"
        "+    return 2\n"
        "*** Add File: new.txt\n"
        "+hello\n"
        "*** End Patch\n"
    )
    assert patched["changed_files"] == ["app.py", "new.txt"]
    assert patched["added_files"] == ["new.txt"]
    assert tools["read_file"]("app.py") == "def main():\n    return 2\n"

    diffed = tools["apply_unified_diff"](
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def main():\n"
        "-    return 2\n"
        "+    return 3\n"
    )
    assert diffed["changed_files"] == ["app.py"] and diffed["hunk_count"] == 1
    assert tools["read_file"]("app.py") == "def main():\n    return 3\n"


# -- containment and refusals ----------------------------------------------------------


def test_fs_refusals_read_like_the_local_ones_and_cost_no_round_trip():
    world, fake, _host = make_world()
    tools = tools_of(world)
    before = len(fake.calls)
    with pytest.raises(PermissionError) as excinfo:
        tools["read_file"]("../../etc/passwd")
    assert "Path escapes allowed roots" in str(excinfo.value)
    with pytest.raises(PermissionError):
        tools["write_file"]("/tmp/evil.txt", "x")
    assert len(fake.calls) == before, "containment is resolved without talking to the host"

    with pytest.raises(ValueError) as missing:
        tools["read_file"]("missing.txt")
    assert "File does not exist: missing.txt" in str(missing.value)
    with pytest.raises(ValueError) as not_dir:
        tools["list_files"]("missing")
    assert "Path does not exist: missing" in str(not_dir.value)


def test_fs_read_only_roots_refuse_writes():
    world, _fake, _host = make_world(files={"/home/dev/ro/read.txt": "hi\n"})
    read_only = [{"path": "/home/dev/ro", "writable": False}]
    assert "write_file" not in {t.__name__ for t in world.fs_tools(roots=read_only)}
    assert tools_of(world, roots=read_only)["read_file"]("read.txt") == "hi\n"

    # With a writable root present the write tools exist, but a path in the read-only
    # root is refused with the local toolkit's exact message.
    roots = [{"path": WORKSPACE, "writable": True}, *read_only]
    backend = RemoteFileBackend(world.session, WORKSPACE, roots=roots)
    with pytest.raises(PermissionError) as excinfo:
        backend.write_file("/home/dev/ro/read.txt", "nope")
    assert "read-only directory" in str(excinfo.value)


def test_fs_scratch_and_paths_can_use_tilde():
    world, _fake, _host = make_world()
    backend = RemoteFileBackend(
        world.session, WORKSPACE, roots=[{"path": "/home/dev", "writable": True}]
    )
    backend.write_file("~/notes.txt", "home\n")
    assert backend.read_file("~/notes.txt") == "home\n"


# -- terminals -------------------------------------------------------------------------


def test_terminals_run_each_command_through_the_remote_executor():
    world, _fake, _host = make_world()
    sessions = world.terminals
    opened = sessions.open(name="build")
    terminal = sessions.get(opened["id"])
    assert terminal is not None

    sent = terminal.send("echo terminal-ok")
    assert sent["reason"] == "idle"
    assert sent["exit_code"] == 0
    assert "terminal-ok" in sent["output"]
    page = terminal.read(count=500)
    assert "terminal-ok" in page["output"]
    assert page["line_end"] == page["total_lines"]  # newest-relative page

    # cwd is tracked per terminal because the executor's frame reports $PWD.
    assert terminal.send("cd ..")["reason"] == "idle"
    assert terminal.cwd == "/home/dev"

    immutable = terminal.write("raw keystrokes")
    assert "persistent PTY" in immutable["error"]
    assert "persistent PTY" in terminal.interrupt()["error"]
    assert "persistent PTY" in terminal.signal("SIGINT")["error"]
    assert sessions.close(terminal.id)["closed"] is True
    assert sessions.get(terminal.id) is None

    # A remote PTY is phase 2b-2: refused by name, never faked.
    assert "type='pty'" in sessions.open(type="pty")["error"]


def test_terminal_tools_compose_over_the_remote_terminals():
    world, _fake, _host = make_world()
    tools = {t.__name__: t for t in terminal_tools(world.terminals)}
    opened = tools["terminal_open"](name="t")
    result = tools["terminal_send"](id=opened["id"], text="echo via-tools")
    assert "via-tools" in result["output"]
    assert tools["terminal_list"]()["terminals"]


# -- the world protocol + the construction seam ----------------------------------------


def test_remote_world_satisfies_the_world_protocol():
    world, _fake, _host = make_world()
    assert isinstance(world, World)
    assert isinstance(world.executor, Executor)
    assert world.root == WORKSPACE
    assert {t.__name__ for t in world.fs_tools()} == {
        "list_files",
        "read_file",
        "read_file_lines",
        "search_files",
        "write_file",
        "apply_unified_diff",
        "apply_patch",
        "replace_in_file",
    }


def test_world_for_defaults_to_local(tmp_path):
    local = world_for(tmp_path)
    assert isinstance(local, LocalWorld)
    assert local.root == tmp_path.resolve()
    assert world_for(None) is None


def test_remote_session_requires_a_host():
    with pytest.raises(ValueError):
        RemoteSession({"name": "no host"})


def test_command_error_is_typed_for_file_ops():
    class _FailingSession(RemoteSession):
        def run(self, command, *, timeout=None, input_text=None):
            from harness.orchestration.remote_env import RemoteCommandResult

            return RemoteCommandResult(
                command=command,
                exit_code=1,
                output="",
                stderr="mkdir: permission denied",
                cwd=WORKSPACE,
                returncode=0,
                frame_found=True,
            )

    backend = RemoteFileBackend(_FailingSession(PROFILE), WORKSPACE)
    with pytest.raises(RemoteCommandError) as excinfo:
        backend.make_dir("/home/dev/project/x")
    assert "permission denied" in str(excinfo.value)
