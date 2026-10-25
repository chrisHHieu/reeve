"""The distribution smoke, adapted to what can be built here: the REAL server process.

`verify_frozen_sidecar.py` covers the packaged exe (it needs a PyInstaller build). What is
missing between that and the unit suite is the WIRING: start the real server the way the
desktop shell does, wait for it to answer, then stop it the way the shell now does —
`POST /v1/shutdown` — and assert the process leaves cleanly. That is also the end-to-end
proof of the graceful-shutdown work: without it, the only evidence was unit tests calling
`drain_running` directly.

A distribution smoke test does the same three things (spawn, poll, SIGTERM);
this is that shape for the Python entry point.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TOKEN = "test-launch-token"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(url: str, *, headers: dict | None = None, method: str = "GET") -> dict:
    request = urllib.request.Request(url, method=method, headers=headers or {})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_the_real_server_starts_answers_and_stops_cleanly(tmp_path):
    port = _free_port()
    state = tmp_path / "state"
    env = {
        **os.environ,
        "REEVE_API_TOKEN": TOKEN,
        "REEVE_STATE_DIR": str(state),
        # The sidecar's parent watchdog must not fire: this process IS the parent.
        "REEVE_PARENT_PID": str(os.getpid()),
        "PYTHONUNBUFFERED": "1",
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "harness.interface.server.run",
            "--cwd",
            str(tmp_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        # 1. It comes up and answers (health is the one tokenless endpoint).
        deadline = time.monotonic() + 60
        up = False
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                # With a token configured the tokenless health answer is minimal — that
                # IS the contract, and it is enough to know the socket serves.
                if _get(f"http://127.0.0.1:{port}/v1/health").get("status") == "ok":
                    up = True
                    break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(0.25)
        assert up, f"the server never answered; output so far:\n{proc.stdout.read() if proc.stdout else ''}"

        # 2. A tokenless read of the app surface is refused (the token is real).
        try:
            _get(f"http://127.0.0.1:{port}/v1/sessions")
            raise AssertionError("a tokenless /v1/sessions call was allowed")
        except urllib.error.HTTPError as exc:
            assert exc.code in (401, 403)

        # 3. The graceful stop: the shell's quit path.
        stopped = _get(
            f"http://127.0.0.1:{port}/v1/shutdown",
            headers={"X-Reeve-Token": TOKEN},
            method="POST",
        )
        assert stopped == {"ok": True}

        # 4. It LEAVES, on its own, with a clean exit code.
        left_by = time.monotonic() + 20
        while time.monotonic() < left_by and proc.poll() is None:
            time.sleep(0.1)
        assert proc.poll() is not None, "the server ignored the shutdown request"
        assert proc.returncode == 0, f"exit code {proc.returncode}"

        # 5. The port is free again — no half-closed listener left behind.
        with socket.socket() as probe:
            probe.settimeout(2)
            probe.bind(("127.0.0.1", port))
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=20)
