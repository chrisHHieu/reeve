"""The Windows job-object containment: structural, and never a reason a spawn fails."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from harness.platform.win_job import ProcessJob


def test_attach_is_a_noop_off_windows_or_for_no_pid(monkeypatch):
    if os.name != "nt":
        assert ProcessJob.attach(os.getpid()) is None
    assert ProcessJob.attach(0) is None


@pytest.mark.skipif(os.name != "nt", reason="job objects are Windows-only")
def test_terminate_kills_the_assigned_tree():
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    job = ProcessJob.attach(proc.pid)
    assert job is not None, "the job could not be created (running inside a parent job?)"
    try:
        job.terminate()
        for _ in range(50):
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        assert proc.poll() is not None, "the assigned process survived TerminateJobObject"
    finally:
        job.close()
        if proc.poll() is None:  # pragma: no cover - last-resort cleanup
            proc.kill()


@pytest.mark.skipif(os.name != "nt", reason="job objects are Windows-only")
def test_closing_the_last_handle_kills_what_is_left():
    """KILL_ON_JOB_CLOSE is the crash path: Reeve dies, the handle closes, the tree
    goes with it — no cooperation from any kill path required."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    job = ProcessJob.attach(proc.pid)
    if job is None:
        proc.kill()
        pytest.skip("the job could not be created (running inside a parent job?)")
    job.close()
    try:
        for _ in range(50):
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        assert proc.poll() is not None, "the process outlived the job handle"
    finally:
        if proc.poll() is None:  # pragma: no cover - last-resort cleanup
            proc.kill()
