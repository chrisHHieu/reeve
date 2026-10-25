"""The person's terminal marks its prompt and each command (OSC 133 / 633;E), so the panel can
say what is running and how it ended; the agent's PTY shell is never changed."""

from __future__ import annotations

import base64
import re
import shutil
import sys
import time

import pytest

from harness.tools import shell_marks
from harness.tools.pty_session import pty_argv

MARK = re.compile(r"\x1b\](?:133|633);[^\x07]*\x07")


def test_powershell_is_wrapped_after_its_profile_never_instead_of_it():
    argv, env, marked = shell_marks.integration(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    assert marked is True and env == {}
    assert argv[:2] == ["-NoExit", "-EncodedCommand"]
    script = base64.b64decode(argv[2]).decode("utf-16-le")
    # The profile's prompt is kept and wrapped; `-NoProfile` is never passed.
    assert "$global:__reevePrompt = $function:prompt" in script
    assert "-NoProfile" not in " ".join(argv)
    assert "133;D;" in script and "633;E;" in script


def test_bash_sources_the_persons_rc_first(tmp_path, monkeypatch):
    monkeypatch.setattr(shell_marks.tempfile, "gettempdir", lambda: str(tmp_path))
    argv, _env, marked = shell_marks.integration("/usr/bin/bash")
    assert marked is True and argv[0] == "--rcfile" and argv[2] == "-i"
    rc = (tmp_path / "reeve-shell-marks.bash").read_text(encoding="utf-8")
    assert rc.lstrip().startswith("[ -f ~/.bashrc ] && . ~/.bashrc")


def test_cmd_marks_only_its_prompt_and_other_shells_are_left_alone():
    argv, env, marked = shell_marks.integration(r"C:\Windows\System32\cmd.exe")
    assert argv is None and marked is False and "133;A" in env["PROMPT"]
    assert shell_marks.integration("/bin/sh") == (None, {}, False)


def test_the_agents_pty_argv_is_unchanged():
    # Only the panel asks for marks; the argv the agent's `type="pty"` shell uses is the plain one.
    assert pty_argv("powershell.exe") == ["powershell.exe", "-NoLogo"]
    assert pty_argv("/bin/bash") == ["/bin/bash"]


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell.exe"), reason="a real PowerShell")
def test_a_real_powershell_marks_each_command_with_its_line_and_exit_code():
    pytest.importorskip("winpty")
    from harness.tools.pty_session import PtySession

    out: list[str] = []
    term = PtySession(
        session_id="marks-test",
        cwd=".",
        shell_path=shutil.which("powershell.exe"),
        marks=True,
        on_output=lambda chunk, _start, _end: out.append(chunk),
    )
    try:
        assert term.status()["marks"] is True

        def wait_for(needle: str, seconds: float = 25.0) -> None:
            deadline = time.time() + seconds
            while needle not in "".join(out):
                if time.time() > deadline:
                    raise AssertionError(f"no {needle!r} in {''.join(out)[-400:]!r}")
                time.sleep(0.1)

        wait_for("\x1b]133;B\x07")
        term.write("cmd /c exit 3\r")
        wait_for("\x1b]133;D;3\x07")
    finally:
        term.close()
    marks = MARK.findall("".join(out))
    run = marks[marks.index("\x1b]633;E;cmd /c exit 3\x07") :]
    assert run[:3] == ["\x1b]633;E;cmd /c exit 3\x07", "\x1b]133;C\x07", "\x1b]133;D;3\x07"]
