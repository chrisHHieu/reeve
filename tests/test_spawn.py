"""The spawn seam: conventions, passthrough, and the invariant.

Every child process Reeve owns goes through `harness/platform/spawn.py`; the last test is the
one that keeps it that way — a new `subprocess.Popen`/`run` under `harness/` is how the
next per-process policy loses its hook point again.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from harness.platform.spawn import run as spawn_run
from harness.platform.spawn import spawn

REPO = Path(__file__).resolve().parent.parent


def test_spawn_runs_a_child_and_captures_stdout():
    proc = spawn([sys.executable, "-c", "print('hi')"], stdout=subprocess.PIPE, text=True)
    out, _ = proc.communicate(timeout=30)
    assert proc.returncode == 0
    assert out.strip() == "hi"


def test_run_passes_env_through():
    proc = spawn_run(
        [sys.executable, "-c", "import os;print(os.environ['REEVE_SPAWN_TEST'])"],
        env={**os.environ, "REEVE_SPAWN_TEST": "42"},
        capture_output=True,
        text=True,
    )
    assert proc.stdout.strip() == "42"


def test_cwd_and_kwargs_pass_through(tmp_path):
    proc = spawn(
        [sys.executable, "-c", "import os;print(os.getcwd())"],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        text=True,
    )
    out, _ = proc.communicate(timeout=30)
    assert Path(out.strip()).resolve() == tmp_path.resolve()


def _record_popen(monkeypatch) -> dict:
    """Capture what the seam passes to Popen (Popen does not expose its flags back)."""
    captured: dict = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return captured


def test_children_get_their_own_group_and_no_console(monkeypatch):
    captured = _record_popen(monkeypatch)
    spawn(["x"])
    if os.name == "nt":
        assert captured["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
        assert captured["creationflags"] & subprocess.CREATE_NO_WINDOW
    else:
        assert captured["start_new_session"] is True


def test_flags_can_be_turned_off_for_a_console_or_ungrouped_child(monkeypatch):
    captured = _record_popen(monkeypatch)
    spawn(["x"], new_group=False, no_window=False)
    if os.name == "nt":
        assert not captured.get("creationflags", 0)
    else:
        assert "start_new_session" not in captured


def test_every_owned_spawn_goes_through_the_seam():
    offenders: list[str] = []
    for path in sorted(p for pkg in ("harness",) for p in (REPO / pkg).rglob("*.py")):
        if path.name == "spawn.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "subprocess.Popen(" in text or "subprocess.run(" in text:
            offenders.append(path.relative_to(REPO).as_posix())
    assert offenders == []
