"""Small pure helpers shared by several manager mixins."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from harness.platform.spawn import run as spawn_run

# A Slack message ts looks like "1700000001.000001" (epoch seconds and microseconds). Other
# platforms use opaque or incrementing ids (a Telegram integer, say), so only that shape is parsed.
_SLACK_TS = re.compile(r"^\d+\.\d+$")


def inbound_epoch(message_id: Optional[str]) -> float:
    """Best-effort epoch seconds for a message: a Slack-style timestamp, else the wall clock."""
    if message_id and _SLACK_TS.match(str(message_id)):
        try:
            return float(message_id)
        except ValueError:
            pass
    return time.time()


def last_assistant_text(messages: list[dict[str, Any]]) -> Optional[str]:
    for message in reversed(messages or []):
        if message.get("role") == "assistant" and message.get("content"):
            return message["content"]
    return None


def recent_files(workspace: str, *, since: float, limit: int = 20) -> list[str]:
    """The files in a task's workspace modified during the run, which are its artifacts."""
    found: list[str] = []
    root = Path(workspace)
    if not root.is_dir():
        return found
    for path in root.rglob("*"):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            if path.is_file() and path.stat().st_mtime >= since - 1:
                found.append(str(path.relative_to(root)))
        except OSError:
            continue
        if len(found) >= limit:
            break
    return found


def git_branch(path: Path) -> Optional[str]:
    try:
        result = spawn_run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path, capture_output=True, text=True, timeout=3
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None
