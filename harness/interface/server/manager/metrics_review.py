"""The Metrics page's self-review: the failure shapes and the claims' verdicts, kept on disk.

The review takes minutes over a real store (`observability/self_review.py`), so the page never waits
for it: it shows the last result with its age, and a request that finds it older than `REFRESH_S`
starts a new one in a child process (low priority, no window) and gets the old one meanwhile. When
the child lands the page is told (`metrics_changed`). The child is a process and not a thread: a
thread would hold the interpreter lock for those minutes and stall every turn streaming beside it.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from harness.platform import spawn

logger = logging.getLogger("harness.interface.server")

#: How old a result may be before a request starts the next one.
REFRESH_S = 6 * 3600
#: A run that takes longer than this is stopped; the last result stays.
TIMEOUT_S = 20 * 60


def _command(state: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--self-review", str(state)]
    return [sys.executable, "-m", "harness.observability.self_review", str(state)]


def _flags() -> int:
    """Below normal priority on Windows; the seam adds the hidden console."""
    return getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) if os.name == "nt" else 0


class SelfReview:
    def __init__(self, state: Path, *, on_done: Optional[Callable[[], None]] = None) -> None:
        self.state = state
        self.path = state / "metrics-review.json"
        self.on_done = on_done
        self._lock = threading.Lock()
        self._running = False

    def read(self) -> Optional[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def payload(self) -> dict[str, Any]:
        """What the page draws: the last result (or none yet), and whether a run is under way.
        A stale or missing result starts one."""
        data = self.read()
        tried = float((data or {}).get("tried_at") or (data or {}).get("at") or 0)
        stale = time.time() - tried > REFRESH_S
        if stale:
            self.start()
        return {**(data or {}), "running": self._running}

    def start(self) -> bool:
        """Start a run unless one is under way. True when this call started it. `REEVE_SELF_REVIEW=off`
        turns runs off (the test suite does; so may a machine where minutes of CPU are unwelcome)."""
        if os.environ.get("REEVE_SELF_REVIEW", "").lower() in ("off", "0", "false"):
            return False
        with self._lock:
            if self._running:
                return False
            self._running = True
        threading.Thread(target=self._run, name="reeve-self-review", daemon=True).start()
        return True

    def _run(self) -> None:
        try:
            done = spawn.run(
                _command(self.state), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=TIMEOUT_S, creationflags=_flags(),
            )
            lines = [line for line in (done.stdout or "").splitlines() if line.strip()]
            result = json.loads(lines[-1]) if lines else {"error": f"exit {done.returncode}"}
            if not isinstance(result, dict):
                result = {"error": "unreadable result"}
        except subprocess.TimeoutExpired:
            result = {"error": "timed out"}
        except Exception as exc:  # noqa: BLE001 — a report must never break the server
            result = {"error": type(exc).__name__}
        if "error" in result:
            # The last good result stays, with the failure beside it. `tried_at` makes the next try
            # wait as long as a refresh would: a review that cannot run must not restart on every
            # request.
            kept = {key: value for key, value in (self.read() or {}).items() if key != "error"}
            result = {**kept, "error": result["error"]}
            logger.warning("self-review failed: %s", result["error"])
        result["tried_at"] = time.time()
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(result), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            logger.warning("self-review result could not be saved", exc_info=True)
        finally:
            with self._lock:
                self._running = False
        if self.on_done is not None:
            try:
                self.on_done()
            except Exception:  # noqa: BLE001
                logger.debug("self-review announce failed", exc_info=True)


__all__ = ["REFRESH_S", "SelfReview"]
