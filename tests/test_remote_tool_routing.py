"""Tool routing for bound sessions: reads, searches, git metadata
and the read-before-edit bookkeeping come from the remote filesystem.

The write/edit half of the catalog already comes from `world.fs_tools()`;
these tools used to open local files directly. With a `RemoteWorld` they are built over
the world's read backend instead, and the local path — no world, or a `LocalWorld` — is
untouched. Everything runs against `FakeSsh`/`FakeRemoteHost`: no ssh, no network.
"""

from __future__ import annotations

import base64
from pathlib import Path

from harness.modes.agents.base import AgentContext
from harness.modes.catalog import _files, _remote_backend, _search
from harness.safety.provenance import resolve_remote
from harness.tools.observed import RULE_NEVER_READ, RULE_STALE, ObservedFiles
from harness.testing.fake_ssh import FakeRemoteHost, FakeSsh
from harness.orchestration.worlds import LocalWorld, remote_world

PROFILE = {"name": "Build server", "host": "dev.example.com", "user": "dev"}
REMOTE = "/home/dev/project"


def _ctx(files=None, directories=None):
    host = FakeRemoteHost(directories={REMOTE, *(directories or [])})
    for path, content in (files or {}).items():
        host.write(path, content)
    fake = FakeSsh(host=host)
    world = remote_world(PROFILE, REMOTE, runner=fake, session_id="sess-tool")
    roots = [{"path": REMOTE, "writable": True, "label": "workspace"}]
    context = AgentContext(
        workspace=None, executor=world.executor, roots=roots, world=world
    )
    return context, world, fake, host


def _by_name(tools) -> dict:
    return {t.__name__: t for t in tools}


# -- read_file -------------------------------------------------------------------------


def test_read_file_reads_the_remote_file(tmp_path):
    context, _world, fake, _host = _ctx(
        files={f"{REMOTE}/notes.txt": "remote one\nremote two\n"}
    )
    # A same-named LOCAL file must not satisfy the read.
    (tmp_path / "notes.txt").write_text("local content\n", encoding="utf-8")

    read = _by_name(_files(context))["read_file"]
    result = read("notes.txt")
    assert result["path"] == "notes.txt"
    assert result["content"] == "     1\tremote one\n     2\tremote two"
    assert result["total_lines"] == 2
    assert "local content" not in result["content"]
    assert any("reeve-remote-op" in call[-1] for call in fake.calls)


def test_read_file_windows_the_remote_file():
    lines = "\n".join(f"line {i}" for i in range(1, 3001)) + "\n"
    context, _world, _fake, _host = _ctx(files={f"{REMOTE}/big.txt": lines})
    read = _by_name(_files(context))["read_file"]

    first = read("big.txt")
    assert first["total_lines"] == 3000
    assert first["end_line"] == 2000
    assert "start_line=2001" in first["note"]

    second = read("big.txt", start_line=2001)
    assert second["start_line"] == 2001
    assert second["end_line"] == 3000


def test_read_file_refusals_are_the_local_shapes():
    context, _world, _fake, _host = _ctx(files={f"{REMOTE}/notes.txt": "x\n"})
    read = _by_name(_files(context))["read_file"]

    escaped = read("../../etc/passwd")
    assert "escapes" in escaped["error"]

    missing = read("missing.txt")
    assert missing["error"] == "File does not exist: missing.txt"


# -- a picture through read_file -------------------------------------------------------


def test_read_file_attaches_a_remote_picture():
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(32))
    context, _world, _fake, _host = _ctx(
        files={f"{REMOTE}/chart.png": png, f"{REMOTE}/notes.txt": "text\n"}
    )
    tools = _by_name(_files(context))
    assert "read_image" not in tools
    read_file = tools["read_file"]

    result = read_file("chart.png")
    assert result["path"] == "chart.png"
    assert result["bytes"] == len(png)
    url = result["_attach"][0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == png

    assert "_attach" not in read_file("notes.txt")
    assert "error" in read_file("missing.png")


# -- grep ------------------------------------------------------------------------------


def test_grep_searches_the_remote_host():
    context, _world, _fake, _host = _ctx(
        files={
            f"{REMOTE}/pkg/b.py": "needle here\nother\n",
            f"{REMOTE}/readme.md": "needle in prose\n",
            f"{REMOTE}/node_modules/x.js": "needle too\n",
        }
    )
    grep = _by_name(_search(context))["grep"]

    result = grep("needle")
    assert result["engine"] == "remote-grep"
    assert result["count"] == 2
    assert {hit["file"] for hit in result["matches"]} == {"pkg/b.py", "readme.md"}

    scoped = grep("needle", glob="*.py")
    assert [hit["file"] for hit in scoped["matches"]] == ["pkg/b.py"]

    assert grep("no-such-term")["count"] == 0
    missing = grep("x", path="missing")
    assert missing["error"] == "Path does not exist: missing"


# -- observed read-before-edit ----------------------------------------------------------


def test_observed_bookkeeping_uses_the_remote_file():
    context, world, _fake, host = _ctx(files={f"{REMOTE}/notes.txt": "one\n"})
    observed = ObservedFiles(
        resolve=lambda raw: resolve_remote(raw, REMOTE),
        access=world.read_backend(context.roots),
    )

    rule, _reason = observed.blame("notes.txt")
    assert rule == RULE_NEVER_READ
    assert observed.blame("brand-new.txt") == ("", "")  # creating is always allowed

    observed.observe("notes.txt")
    assert observed.blame("notes.txt") == ("", "")

    host.write(f"{REMOTE}/notes.txt", "changed by a remote build\n")
    rule, reason = observed.blame("notes.txt")
    assert rule == RULE_STALE
    assert "changed on disk" in reason


# -- the local path is unchanged --------------------------------------------------------


def test_local_world_keeps_the_local_tool_path(tmp_path):
    local_world = LocalWorld(root=tmp_path)
    context = AgentContext(workspace=tmp_path, world=local_world)
    assert _remote_backend(context) is None

    (tmp_path / "local.txt").write_text("local content\n", encoding="utf-8")
    read = _by_name(_files(context))["read_file"]
    assert read("local.txt")["content"].endswith("local content")
    # No fake host, no SSH: the file was read by the local code path.
    assert not Path(REMOTE).exists()
