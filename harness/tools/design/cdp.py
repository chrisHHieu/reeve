"""The harness browser's DevTools connection, and how it is ended.

Split out of `browser.py`, which drives a load and its passes: this is the wire (one socket,
flattened sessions, replies matched by id) and the stop that takes every process the browser
started with it.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import shutil
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from harness.platform.spawn import run as spawn_run

_COMMAND_TIMEOUT_S = 15.0


class _Cdp:
    """A minimal DevTools client: one socket, flattened sessions, replies matched by id."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.ids = itertools.count(1)
        self.pending: dict[int, asyncio.Future] = {}
        self.listeners: list[Any] = []

    async def pump(self) -> None:
        async for raw in self.ws:
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            ident = message.get("id")
            if ident is not None and ident in self.pending:
                future = self.pending.pop(ident)
                if not future.done():
                    future.set_result(message)
                continue
            for listener in list(self.listeners):
                listener(message)

    async def send(self, method: str, params: Optional[dict] = None, session: Optional[str] = None) -> dict:
        ident = next(self.ids)
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        body: dict[str, Any] = {"id": ident, "method": method, "params": params or {}}
        if session:
            body["sessionId"] = session
        await self.ws.send(json.dumps(body))
        message = await asyncio.wait_for(future, _COMMAND_TIMEOUT_S)
        if "error" in message:
            raise RuntimeError(f"{method}: {message['error'].get('message', message['error'])}")
        return message.get("result") or {}


@dataclass
class Started:
    """Where a launched browser listens, or why it does not."""

    endpoint: str = ""
    error: str = ""
    #: The process we started ended with 0 and another one carries the browser on (`wait_started`).
    handed_off: bool = False


async def wait_started(proc: Any, profile: Path, timeout_s: float) -> Started:
    """Wait for the browser's DevTools address, which it writes into its profile.

    The process started is not always the browser. Edge can start its browser as a new process and
    end the one it was started as with code 0; the new one keeps the arguments and the profile and
    writes the same file. Taken as a failure, every check in the desktop app's sidecar said "the
    browser exited at start (code 0)" for a whole session while each launch left a headless Edge
    running (2026-10-20: 22 of them, all with this profile prefix and their parent gone). Only a
    non-zero exit is a failure; after a zero one the file is still awaited."""
    port_file = profile / "DevToolsActivePort"
    started = Started()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not started.handed_off and proc.poll() is not None:
            if proc.returncode:
                started.error = f"the browser exited at start (code {proc.returncode})"
                return started
            started.handed_off = True
        try:
            lines = port_file.read_text(encoding="utf-8").split("\n")
        except OSError:
            lines = []
        if len(lines) >= 2 and lines[0].strip() and lines[1].strip():
            started.endpoint = f"ws://127.0.0.1:{lines[0].strip()}{lines[1].strip()}"
            return started
        await asyncio.sleep(0.025)
    started.error = f"the browser did not start within {timeout_s:g} s"
    return started


async def close_at(endpoint: str) -> None:
    """Close the browser listening at `endpoint`: the one way to end a browser that is not our
    child (`Started.handed_off`), since a tree kill of the process we started no longer reaches it."""
    import websockets

    try:
        async with websockets.connect(endpoint, open_timeout=3) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Browser.close", "params": {}}))
            await asyncio.wait_for(ws.recv(), 3)
    except Exception:
        pass


def remove_profile(profile: Path, handed_off: bool) -> None:
    """Delete the check's profile. A handed-off browser is not waited for by `_stop`, so its files
    can still be open for a moment after it was closed: try for up to two seconds."""
    deadline = time.monotonic() + (2.0 if handed_off else 0.0)
    while True:
        shutil.rmtree(profile, ignore_errors=True)
        if not profile.exists() or time.monotonic() >= deadline:
            return
        time.sleep(0.1)


def _stop(proc: Any) -> None:
    """End the browser and every process it started. Chromium starts a dozen; a parent stopped
    alone leaves its renderers holding the profile folder open on Windows."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            spawn_run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
