"""McpBrowserMixin: choosing the backend of the built-in `browser` server."""

from __future__ import annotations

from typing import Any


class McpBrowserMixin:
    def browser_config(self) -> dict[str, Any]:
        """The data of the browser-backend picker: the current choice, the options and the
        credential state. The built-in `browser` server is ONE entry whose child command depends
        on this choice (`mcp/config.browser_preference`), so the Connectors page draws a picker
        and not raw command JSON."""
        from harness.extensions.mcp.config import browser_preference, builtin_mcp_servers

        preference = browser_preference(self.secrets)
        launch = builtin_mcp_servers(self.secrets).get("browser") or {}
        command = " ".join(str(part) for part in [launch.get("command"), *(launch.get("args") or [])] if part)
        return {
            "ok": True,
            "backend": preference["backend"],
            "mode": preference["mode"],
            "endpoint": preference["endpoint"],
            "headless": preference["headless"],
            "connected": self.mcp.connected("browser"),
            "tool_count": self.mcp.tool_count("browser"),
            "launch": command,
        }

    def set_browser_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Save the browser-backend choice in `mcp:browser` and release the live browsers.

        Validation mirrors the launcher's own fallbacks, so the UI cannot save a combination
        that would quietly become something else (attach with no endpoint). A save also drops
        the credentials of the retired backend.
        """
        from harness.extensions.mcp.config.builtin import _BROWSER_BACKENDS, _RETIRED_BROWSER_KEYS

        payload = payload or {}
        profile = dict(self.secrets.get("mcp:browser") or {})
        requested = str(payload.get("backend") or "").strip().lower()
        if requested and requested not in _BROWSER_BACKENDS:
            return {"ok": False, "error": f"unknown backend: {requested}"}
        # A profile saved before a backend was removed still names it. That is not the caller's
        # error, and refusing it would leave the settings unsavable; it reads as Playwright, the
        # way the launcher reads it.
        stored = str(profile.get("backend") or "").strip().lower()
        backend = requested or (stored if stored in _BROWSER_BACKENDS else "playwright")
        mode = str(payload.get("mode") or profile.get("mode") or "isolated").strip().lower()
        if mode not in ("isolated", "attach"):
            return {"ok": False, "error": f"unknown mode: {mode}"}
        endpoint = str(payload.get("endpoint") if "endpoint" in payload else profile.get("endpoint") or "").strip()
        if mode == "attach" and not endpoint:
            return {"ok": False, "error": "Attach mode needs the running browser's endpoint."}
        for key in _RETIRED_BROWSER_KEYS:
            profile.pop(key, None)
        profile.update({"backend": backend, "mode": mode, "endpoint": endpoint})
        if "headless" in payload:
            profile["headless"] = bool(payload["headless"])
        self.secrets.put("mcp:browser", profile)
        # The child command changes with the choice, so the live browsers are released and the
        # next arm spawns the chosen backend. `forget` walks every scope, per-session included.
        self.mcp.forget("browser")
        # `chrome-devtools` follows the same headless and attach settings.
        self.mcp.forget("chrome-devtools")
        return self.browser_config()
