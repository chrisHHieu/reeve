"""The harness browser when the process it starts is not the browser.

Edge can carry its browser on in a new process and end the one it was started as with code 0. In
the desktop app's sidecar every design check of a session then said "the browser exited at start
(code 0)", and each launch left a headless Edge running with nobody to close it (2026-10-20).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from harness.tools.design import browser as browser_mod
from harness.tools.design import find_browser
from harness.tools.design.cdp import wait_started

# Starts the browser it is given as a detached process, writes that process's id, and ends with 0.
HANDOFF = """
import os, subprocess, sys
flags = 0
kwargs = {}
if os.name == "nt":
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_BREAKAWAY_FROM_JOB
else:
    kwargs["start_new_session"] = True
argv = sys.argv[2:]
try:
    child = subprocess.Popen(argv, creationflags=flags, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
except OSError:
    # A job that forbids breakaway: start it inside, still as a process of its own.
    flags &= ~getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    child = subprocess.Popen(argv, creationflags=flags, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
open(sys.argv[1], "w").write(str(child.pid))
sys.exit(0)
"""


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _render_handed_off(tmp_path, monkeypatch):
    """A check whose launch hands the browser to a detached process; returns it and that process."""
    launcher = tmp_path / "handoff.py"
    launcher.write_text(HANDOFF, encoding="utf-8")
    pid_file = tmp_path / "child.pid"
    real_spawn = browser_mod.spawn

    def handing_off(argv, **kwargs):
        return real_spawn([sys.executable, str(launcher), str(pid_file), *argv], **kwargs)

    monkeypatch.setattr(browser_mod, "spawn", handing_off)
    page = tmp_path / "page.html"
    page.write_text("<!doctype html><title>t</title><h1>Plans</h1>", encoding="utf-8")
    render = browser_mod.render_page(page, [390], "({ title: document.title })", shots=False)
    return render, int(pid_file.read_text())


def _gone(pid: int) -> bool:
    deadline = time.monotonic() + 10
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    return not _alive(pid)


@pytest.mark.skipif(find_browser() is None, reason="no Chromium on this machine")
def test_a_browser_carried_on_by_another_process_is_measured_and_closed(tmp_path, monkeypatch):
    render, child = _render_handed_off(tmp_path, monkeypatch)

    assert render.error == ""
    assert render.widths and render.widths[0].measured == {"title": "t"}
    assert _gone(child), "the browser the launcher handed off to was left running"


@pytest.mark.skipif(find_browser() is None, reason="no Chromium on this machine")
def test_a_handed_off_browser_is_closed_when_the_check_could_not_close_it(tmp_path, monkeypatch):
    # The check's own Browser.close fails (a pass hung, the socket broke): the process we started is
    # long gone, so a tree kill of it reaches nothing, and the browser is closed at its address.
    class _NoClose(browser_mod._Cdp):
        async def send(self, method, params=None, session=None):
            if method == "Browser.close":
                raise RuntimeError("Browser.close: no reply")
            return await super().send(method, params, session)

    monkeypatch.setattr(browser_mod, "_Cdp", _NoClose)
    render, child = _render_handed_off(tmp_path, monkeypatch)

    assert render.error == ""
    assert _gone(child), "the browser the launcher handed off to was left running"


class _Proc:
    def __init__(self, code):
        self.returncode = code

    def poll(self):
        return self.returncode


def test_a_non_zero_exit_at_start_is_still_a_failure(tmp_path):
    started = asyncio.run(wait_started(_Proc(3), tmp_path, 1.0))
    assert started.error == "the browser exited at start (code 3)"
    assert not started.endpoint


def test_after_a_zero_exit_the_port_file_is_still_awaited(tmp_path):
    async def later():
        await asyncio.sleep(0.2)
        (tmp_path / "DevToolsActivePort").write_text("41234\n/devtools/browser/abc\n", encoding="utf-8")

    async def run():
        writer = asyncio.create_task(later())
        started = await wait_started(_Proc(0), tmp_path, 3.0)
        await writer
        return started

    started = asyncio.run(run())
    assert started.error == ""
    assert started.handed_off is True
    assert started.endpoint == "ws://127.0.0.1:41234/devtools/browser/abc"


def test_a_zero_exit_with_no_browser_after_it_times_out(tmp_path):
    started = asyncio.run(wait_started(_Proc(0), tmp_path, 0.3))
    assert started.error == "the browser did not start within 0.3 s"
    assert started.handed_off is True


def test_the_process_started_with_zero_is_not_reported_as_running(tmp_path):
    # The plain launch (no hand-off) keeps its old shape: the process we started is the browser.
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        started = asyncio.run(wait_started(proc, tmp_path, 0.2))
        assert started.handed_off is False
        assert "did not start" in started.error
    finally:
        proc.kill()
        proc.wait()
