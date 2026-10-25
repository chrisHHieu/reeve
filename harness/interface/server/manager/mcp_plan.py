"""McpPlanMixin: which MCP servers a session is entitled to, and what its tool set depends on."""

from __future__ import annotations

from typing import Optional

from harness.extensions.mcp import MCPServerDef, load_mcp_servers


class McpPlanMixin:
    def _mcp_plan(self, session_id: str, workspace: Optional[str], agent: str) -> list[MCPServerDef]:
        """The servers this session may use, after every gate. It touches no network.

        It is separate from arming so that the session's signature (`_mcp_signature`) comes from
        the very gating that arms it. A signature computed on its own would drift the moment a
        gate changed, and the drift is silent: the session would keep tools it no longer
        qualifies for, or miss ones it does.
        """
        from harness.extensions.mcp import oauth as mcp_oauth
        from harness.extensions.mcp.config import builtin_mcp_servers
        from harness.integrations.connectors.descriptors import get_descriptor, mcp_url_for

        effective: Optional[set[str]] = None  # worked out lazily, once
        plan: list[MCPServerDef] = []
        for server in load_mcp_servers(
            workspace, secrets=self.secrets, workspace_trusted=self._mcp_workspace_trusted(workspace)
        ):
            if not server.enabled:
                continue
            if server.auth in ("oauth", "pkce") and not mcp_oauth.has_tokens(server.name, self.secrets):
                # A turn never starts an interactive sign-in. A server without tokens would open
                # a browser and hold every session for the whole flow timeout, which is how one
                # leftover config from a failed one-click once froze all new sessions. Sign-in
                # starts only from an explicit connect in Settings or Connectors.
                continue
            descriptor = get_descriptor(server.name)
            if descriptor is not None and getattr(descriptor, "mcp_local", None) is not None:
                # A first-party server on this machine: gated by the connector like a
                # vendor's, launched as the descriptor says today, one per session in the session's
                # folder, so a tool that touches files knows whose folder it is.
                if effective is None:
                    effective = self.effective_connectors(session_id)
                spec = descriptor.mcp_local()
                if server.name not in effective or spec is None:
                    continue
                server.command, server.args = spec["command"], list(spec.get("args") or [])
                server.include_tools = None
                server.per_session = True
                if workspace is not None:
                    server.cwd = str(workspace)
                if descriptor.mcp_approval_by_hints:
                    server.approval_by_hints = True
                plan.append(server)
                continue
            if descriptor is not None and descriptor.mcp_url:
                # A connector-backed server. The only gate left is the connector's own on/off for
                # this session, and what the server offers the session gets: its tools classify
                # themselves through MCP annotations (readOnlyHint / destructiveHint), and a
                # hand-kept include list only ever bounded our ability to keep up with the
                # vendor's renames.
                if effective is None:
                    effective = self.effective_connectors(session_id)
                if server.name not in effective:
                    continue
                # What mcp.json holds for a connector-backed server is a cache of decisions the
                # app makes, not a preference of the person's, so the descriptor wins on every
                # turn. An install that connected under an older design may still carry an
                # include list of retired names, which would filter everything out; and one that
                # connected before an endpoint moved would call the old URL forever.
                server.include_tools = None
                # The person's choice among several endpoints lives in the profile as a variant
                # key, which the descriptor turns into today's URL, so a vendor moving an
                # endpoint still reaches every install and the choice survives it.
                profile = self.secrets.get(f"{server.name}:default")
                server.url = mcp_url_for(descriptor, profile)
                # The toolset header and the approval rule are the descriptor's decisions too:
                # an install that connected before either existed gets both on its next turn.
                if descriptor.mcp_toolsets is not None:
                    server.headers = {**(server.headers or {}), **descriptor.mcp_toolsets.header_for(profile)}
                if descriptor.mcp_approval_by_hints:
                    server.approval_by_hints = True
            if server.name in builtin_mcp_servers(self.secrets):
                # A built-in sidecar has no cwd of its own and would inherit the server's launch
                # directory, so a relative save landed in the repo while the session lived
                # elsewhere. The session's workspace is where every other tool resolves paths.
                if workspace is not None:
                    server.cwd = str(workspace)
                # A session with no project folder works in its scratch folder, which is made when
                # its first turn starts, so it cannot be the cwd of a browser started when the
                # conversation opened: that browser saved into the app's install folder
                # (2026-10-20). Its root is said over MCP instead, made or not.
                if server.per_session:
                    server.roots = [str(workspace) if workspace is not None else self._scratch_path(session_id)]
            plan.append(server)
        return plan

    def _mcp_signature(self, plan: list[MCPServerDef], session_id: str) -> tuple:
        """What the session's MCP tool set is a function of; cheap enough to compute each turn.

        There are two halves, and the second is easy to miss. The CONFIG half catches a server
        added, removed, disabled, re-pointed or re-scoped. The LIVE half catches the other way
        the tools legitimately change: a server that was down when the session armed and that
        the person has since fixed from the Connectors page. Without it a repaired server would
        need a brand-new session.

        Liveness is judged in the scope the server uses (its own session for a per-session
        server), so session A's live browser never masks session B's dead one. It also decides
        what does NOT happen: a server that keeps failing looks the same as last turn and is not
        retried, so a dead server can never make every turn pay a connection timeout.
        """
        configured = tuple(
            (
                s.name,
                s.transport,
                s.command,
                tuple(s.args),
                s.url,
                tuple(sorted((s.headers or {}).keys())),
                # The toolset choice is no secret and is what changes the tool set, so its VALUE
                # is part of the signature, unlike the other headers'.
                (s.headers or {}).get("X-MCP-Toolsets"),
                s.auth,
                s.requires_approval,
                s.approval_by_hints,
                s.per_session,
                tuple(s.roots or ()),
                None if s.include_tools is None else tuple(s.include_tools),
                None if s.exclude_tools is None else tuple(s.exclude_tools),
            )
            for s in plan
        )
        live = tuple(
            sorted(
                s.name
                for s in plan
                if self.mcp.connected(s.name, scope=session_id if s.per_session else "")
            )
        )
        return configured, live
