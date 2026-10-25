"""BrowserPreviewMixin: what the rail shows about the workspace branch and the agent's browser."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from harness.platform.spawn import run as spawn_run

# The rail mirrors the browser only while the model's own calls are this recent.
_RECENT_CALL_SECONDS = 120

_ACTIVE_TAB = re.compile(r"\[active\]\s*([^(\n]*?)\s*\((http[^)]+)\)")
_ANY_URL = re.compile(r"(https?://\S+)")


def _tab_of(blocks) -> tuple[str, str]:
    """(title, url) of the active tab in `browser_tabs`' listing, which has one line per tab:
    "- 0: [active] Title (url)". Failing that, the first URL anywhere in it."""
    url = title = ""
    for block in blocks or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        found = _ACTIVE_TAB.search(text)
        if found:
            return found.group(1).strip(), found.group(2).strip()
        found = _ANY_URL.search(text)
        if found:
            url = found.group(1)
    return title, url


class BrowserPreviewMixin:
    def workspace_branch(self, path: str) -> dict[str, Any]:
        """The checked-out git branch of a workspace folder: a header chip and nothing more.
        A folder that is not a repository answers None, and the chip is simply not drawn."""
        try:
            folder = Path(str(path)).expanduser()
            if not folder.is_dir():
                return {"branch": None}
            out = spawn_run(
                ["git", "-C", str(folder), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return {"branch": out.stdout.strip() or None}
        except Exception:
            return {"branch": None}

    async def browser_preview(self) -> dict[str, Any]:
        """Mirror the browser server for the rail: the active tab's URL and a fresh screenshot.
        It answers `active: false` and not an error when nothing is open, since an empty
        section is the honest state and not a failure to nag about."""
        # The browser is per session, so several connections can be live at once, and the rail
        # mirrors the one the model used most recently.
        scope, connection = "", None
        for key, candidate in self.mcp._conns.items():
            if key[1] != "browser":
                continue
            if connection is None or self.mcp.last_model_call.get(key, 0) >= self.mcp.last_model_call.get(
                (scope, "browser"), 0
            ):
                scope, connection = key[0], candidate
        if connection is None:
            return {"active": False, "reason": "browser not connected"}
        # Mirroring may WATCH an active browser and must never be what WAKES one: a poll that
        # launched Chrome and took screenshots on an idle machine is why this gate exists.
        # Anything the rail needs beyond it waits for the next turn.
        if time.monotonic() - self.mcp.last_model_call.get((scope, "browser"), 0) > _RECENT_CALL_SECONDS:
            return {"active": False, "reason": "the agent hasn't used the browser recently"}
        try:
            tabs = await asyncio.wait_for(connection.session.call_tool("browser_tabs", {"action": "list"}), timeout=15)
            title, url = _tab_of(tabs.content)
            shot = await asyncio.wait_for(connection.session.call_tool("browser_take_screenshot", {}), timeout=25)
            for block in shot.content or []:
                if getattr(block, "type", "") == "image" and getattr(block, "data", None):
                    mime = getattr(block, "mimeType", "image/png") or "image/png"
                    return {"active": True, "url": url, "title": title, "image": f"data:{mime};base64,{block.data}"}
            return {"active": False, "reason": "the browser returned no image"}
        except Exception as exc:
            return {"active": False, "reason": str(exc)[:200]}
