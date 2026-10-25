"""Talking to the daemon from another process: the session servers and the Connectors page.

The daemon's address and token are in `daemon.json` in the account's folder; a missing file is a
daemon that is not running, said as such, never a hang.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from .daemon import INFO_FILE
from .tools import account_folder

#: A send waits for the typing indicator and WhatsApp's acknowledgement; a read is local.
TIMEOUT_S = 60.0


class DaemonDown(RuntimeError):
    pass


def info(folder: Optional[Path] = None) -> Optional[dict[str, Any]]:
    path = (folder or account_folder()) / INFO_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def request(method: str, path: str, payload: Optional[dict[str, Any]] = None, *, folder: Optional[Path] = None,
            timeout: float = TIMEOUT_S) -> dict[str, Any]:
    found = info(folder)
    if not found:
        raise DaemonDown("WhatsApp is not running: Reeve starts it once the account is linked in Connectors")
    data = json.dumps(payload or {}).encode("utf-8") if method == "POST" else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{found['port']}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {found['token']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - loopback only
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read() or b"{}")
        except ValueError:
            return {"error": f"WhatsApp answered {exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        raise DaemonDown(f"WhatsApp is not answering ({exc}); it restarts with Reeve") from exc
