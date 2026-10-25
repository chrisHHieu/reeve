"""McpServersMixin: the MCP server list, its configuration, and connecting one server."""

from __future__ import annotations

import time
from typing import Any, Optional

from harness.extensions.mcp import (
    MCPServerDef,
    delete_global_server,
    load_mcp_servers,
    patch_global_server,
    put_global_server,
    read_global,
)

_HTTP_TYPES = {"http", "sse", "streamable-http"}


def _redact(raw: dict[str, Any]) -> dict[str, Any]:
    """A server config that is safe to return over REST: env and header values are masked."""
    out = dict(raw)
    for key in ("env", "headers"):
        if isinstance(out.get(key), dict):
            out[key] = {name: ("***" if value else value) for name, value in out[key].items()}
    return out


class McpServersMixin:
    # -- reading -------------------------------------------------------------------------

    def _declared_server(self, name: str) -> Optional[MCPServerDef]:
        """The configured server called `name` as the default workspace sees it, or None."""
        servers = load_mcp_servers(
            self.default_workspace,
            secrets=self.secrets,
            workspace_trusted=self._mcp_workspace_trusted(self.default_workspace),
        )
        return next((server for server in servers if server.name == name), None)

    def _mcp_row(self, name: str, raw: dict[str, Any], *, builtin: bool, status: str, transport: str, oauth: bool) -> dict:
        return {
            "name": name,
            "enabled": bool(raw.get("enabled", True)),
            "builtin": builtin,
            "transport": transport,
            "requires_approval": bool(raw.get("requires_approval", True)),
            "auth": "oauth" if oauth else None,
            "status": status,
            "auth_hint": name in self._mcp_auth_hints,
            "last_test_at": self._prefs.get("mcp_last_test", {}).get(name),
            "last_error": self._mcp_errors.get(name),
            "tool_count": self.mcp.tool_count(name),
            "config": _redact(raw),
        }

    def _status_of(self, name: str, raw: dict[str, Any], oauth: bool) -> str:
        from harness.extensions.mcp import oauth as mcp_oauth

        if self.mcp.connected(name):
            return "connected"
        if not raw.get("enabled", True):
            return "disabled"
        if name in self._mcp_authorizing:
            return "authorizing"
        if oauth and not mcp_oauth.has_tokens(name, self.secrets):
            return "needs_auth"
        # A startup or connection failure (a stdio crash, an unreachable URL). OAuth servers
        # keep their softer statuses, since acquiring tokens supersedes a stale sign-in error.
        if name in self._mcp_errors and not oauth:
            return "error"
        return "configured"

    def list_mcp(self) -> list[dict[str, Any]]:
        """The servers of the global config plus the built-ins, with their connection status.
        It does not connect.

        Built-ins ship with the app and arm with no config, so they are listed to show what
        really runs, flagged `builtin` for the badge. A config entry of the same name is
        authoritative, including `{"enabled": false}`, which is how a built-in is turned off.
        """
        from harness.extensions.mcp.config import builtin_mcp_servers, current_launch
        from harness.integrations.connectors.descriptors import get_descriptor

        builtins = builtin_mcp_servers(self.secrets)
        configured = read_global()
        rows = []
        for name, raw in configured.items():
            descriptor = get_descriptor(name)
            if descriptor is not None and (descriptor.mcp_url or descriptor.mcp_local):
                # A connector-backed server lives on the Connectors page, not here: a remote one
                # (`mcp_url`) and a local one alike (`mcp_local`, WhatsApp). Only the remote kind
                # was skipped, so WhatsApp showed twice, once as a connector and once as a custom
                # server (owner screenshot, 2026-10-10).
                continue
            if name in builtins:
                raw = current_launch(name, raw)  # the launch line that really runs
            oauth = str(raw.get("auth", "")).lower() == "oauth"
            http = raw.get("url") or str(raw.get("type", "")).lower() in _HTTP_TYPES
            rows.append(
                self._mcp_row(
                    name,
                    raw,
                    builtin=name in builtins,
                    status=self._status_of(name, raw, oauth),
                    transport="http" if http else "stdio",
                    oauth=oauth,
                )
            )
        for name, raw in builtins.items():
            if name in configured or get_descriptor(name) is not None:
                continue  # a file entry already spoke for this name
            # A built-in may ship switched off, and the panel then says so with the same word it
            # uses for a server the person turned off.
            enabled = bool(raw.get("enabled", True))
            status = "connected" if self.mcp.connected(name) else "configured" if enabled else "disabled"
            row = self._mcp_row(name, raw, builtin=True, status=status, transport="stdio", oauth=False)
            row["requires_approval"] = True
            row["auth_hint"] = False
            rows.append(row)
        return rows

    # -- editing the configuration -------------------------------------------------------

    def add_mcp(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        put_global_server(name, config)
        return {"ok": True, "name": name}

    def patch_mcp(self, name: str, changes: dict[str, Any]) -> dict[str, Any]:
        # A built-in has no file entry until someone changes it. The first patch writes the
        # built-in's own config plus the change, so the file override stays a full definition
        # (the off switch included) and not a bare fragment.
        if name not in read_global():
            from harness.extensions.mcp.config import builtin_mcp_servers

            builtin = builtin_mcp_servers(self.secrets).get(name)
            if builtin is not None:
                put_global_server(name, {**builtin, **changes})
                return {"ok": True, "name": name}
        return {"ok": patch_global_server(name, changes), "name": name}

    def delete_mcp(self, name: str) -> dict[str, Any]:
        ok = delete_global_server(name)
        if ok:
            # A server added again under the same name starts clean: not pre-failed, and not
            # pre-trusted by the old entry's test.
            self._mcp_errors.pop(name, None)
            self._mcp_auth_hints.discard(name)
            if self._prefs.get("mcp_last_test", {}).pop(name, None) is not None:
                self._save_prefs()
            self._clear_mcp_notified(name)
            # "Remove" means the server is gone: its connection must not run until the next
            # restart, nor its tokens and client registration stay in the secret store. The
            # route runs in a threadpool, and `forget` only flags the shutdown; the serving task
            # closes its own transport on the loop.
            self.mcp.forget(name)
            from harness.extensions.mcp import oauth as mcp_oauth

            mcp_oauth.sign_out(name, self.secrets)
        return {"ok": ok, "name": name}

    async def reload_mcp(self) -> dict[str, Any]:
        """Drop the live connections, so new sessions reconnect with fresh configuration."""
        await self.mcp.aclose()
        return {"ok": True}

    # -- connecting one server -----------------------------------------------------------

    def begin_mcp_connect(self, name: str) -> None:
        """Flag `authorizing` BEFORE the background connect starts. The GUI's fast poll keys off
        that status, and the first refresh used to outrun the task, so a failing Test showed
        nothing until the slow tick and the button looked dead. Only known names are flagged, so
        an unknown server cannot wedge the flag."""
        if name in read_global():
            self._mcp_authorizing.add(name)

    async def connect_mcp(self, name: str) -> dict[str, Any]:
        """Connect one server NOW. For an OAuth server this may open the browser and wait for
        the loopback callback, so callers run it as a background task and watch `list_mcp` for
        the status to flip."""
        from harness.extensions.mcp import oauth as mcp_oauth

        server = self._declared_server(name)
        if server is None:
            self._mcp_authorizing.discard(name)  # begin_mcp_connect flagged a name never matched
            return {"ok": False, "error": f"unknown MCP server: {name}"}
        self._mcp_authorizing.add(name)
        self._mcp_errors.pop(name, None)
        self._mcp_auth_hints.discard(name)
        try:
            # The one place a browser sign-in may start is an explicit connect. It verifies
            # instead of ensuring, so a live server gets a real round trip and a fresh tool list
            # and not a cached yes.
            connection = await self.mcp.verify(server, interactive=True)
            # "Ready · tested ⟨when⟩" must survive a restart, so it lives in the prefs.
            self._prefs.setdefault("mcp_last_test", {})[name] = int(time.time())
            self._save_prefs()
            self._clear_mcp_notified(name)
            return {"ok": True, "tools": len(connection.tools)}
        except Exception as exc:
            if server.transport == "http" and server.auth != "oauth" and mcp_oauth.is_http_auth_error(exc):
                # An anonymous probe of a guarded server (the add-by-URL flow): the answer is
                # "sign in" and not a raw 401.
                self._mcp_auth_hints.add(name)
                message = "authentication required — sign in to connect"
            else:
                message = str(exc) or exc.__class__.__name__
                tail = self.mcp.last_stderr(name)
                if tail:
                    message = f"{message} — {tail}"
            self._mcp_errors[name] = message[:500]
            return {"ok": False, "error": self._mcp_errors[name]}
        finally:
            self._mcp_authorizing.discard(name)

    async def signout_mcp(self, name: str) -> dict[str, Any]:
        """Drop every live connection (all scopes) and forget the stored OAuth tokens."""
        from harness.extensions.mcp import oauth as mcp_oauth

        self.mcp.forget(name)
        self._mcp_errors.pop(name, None)
        return {"ok": True, "had_tokens": mcp_oauth.sign_out(name, self.secrets)}

    async def mcp_tools(self, name: str) -> dict[str, Any]:
        """Connect one server and list its tools (name and description)."""
        server = self._declared_server(name)
        if server is None:
            return {"name": name, "ok": False, "error": "unknown server", "tools": []}
        try:
            connection = await self.mcp.ensure(server)
        except Exception as exc:
            return {"name": name, "ok": False, "error": str(exc), "tools": []}
        tools = [{"name": tool.name, "description": getattr(tool, "description", "")} for tool in connection.tools]
        return {"name": name, "ok": True, "tools": tools}
