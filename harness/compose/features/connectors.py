"""Connectors: the tools of every connected service the session may use.

They are one registry group (`CONNECTOR_GROUP`), so a session already open follows the Connectors
page: before each turn the manager compares what is connected now with what the session was given
(`connector_signature`) and, when they differ, swaps the group in place (`resync`), the way MCP
servers are re-armed. A connector connected mid-conversation is there at the next message, with no
new session, no restart, and nothing else of the session rebuilt (terminals and shells keep running).
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from harness.integrations.connectors import make_integration_tools

from ..session_helpers import enabled_connector_tools
from .base import FeatureContext

CONNECTOR_GROUP = "connectors"


def _entitled(secrets: Any, agent_connectors: Any, connector_filter: Optional[set[str]]) -> tuple[set[str], set[str]]:
    """(connectors, tool names) this session may use now."""
    enabled_connectors, enabled_tools = enabled_connector_tools(secrets)
    # Least privilege: an agent with an allowlist gets only the connectors it declared.
    # An undeclared connector's tools never enter the session, whatever the person has
    # connected. True is for the general agent, which legitimately drives whatever is connected.
    if agent_connectors is not True:
        enabled_connectors = enabled_connectors & set(agent_connectors or ())
    # The per-session connection hierarchy: when the caller supplies the
    # session's effective connector set, it is intersected so only connectors that are
    # effectively enabled expose tools. None leaves CLI and direct callers without a per-session
    # restriction.
    if connector_filter is not None:
        enabled_connectors = enabled_connectors & connector_filter
    return enabled_connectors, enabled_tools


def connector_signature(secrets: Any, agent_connectors: Any, connector_filter: Optional[set[str]]) -> tuple:
    """What decides the session's connector tools: which connectors and which of their tools are
    on. Credentials are read when a tool is called, so a new token or account changes nothing here."""
    connectors, tools = _entitled(secrets, agent_connectors, connector_filter)
    return tuple(sorted(connectors)), tuple(sorted(t for t in tools))


class Connectors:
    id = "connectors"

    def setup(self, ctx: FeatureContext) -> None:
        agent = ctx.agent
        if not agent.connectors:
            return
        secrets, roots, remote = ctx.secrets, ctx.root_list, ctx.remote

        def build(connector_filter: Optional[set[str]]) -> tuple[tuple, list[Callable[..., Any]]]:
            connectors, tools = _entitled(secrets, agent.connectors, connector_filter)
            built = make_integration_tools(
                secrets,
                enabled_connectors=connectors,
                enabled_tools=tools,
                # The session's roots list itself, so a folder added mid-session is one a later
                # rebuild of the group can attach from.
                roots=roots or None,
                # Connector tools that save or clone onto local disk must refuse for a remote world
                # and not resolve a POSIX root here (2b-2).
                remote=remote,
            )
            return (tuple(sorted(connectors)), tuple(sorted(tools))), built

        signature, built = build(ctx.inputs.connector_filter)
        ctx.registry.sync_group(CONNECTOR_GROUP, built)
        # What the manager's pre-turn check reads: the inputs this group was built from, and how
        # to build it again for a new connector set.
        ctx.registry.connector_signature = signature
        ctx.registry.connector_build = build
        ctx.registry.connector_agent = agent.connectors
        # The door the manager calls before each turn, carried by the registry so the server never
        # imports this layer (interface comes before compose in docs/architecture.md).
        registry = ctx.registry
        registry.connector_resync = lambda connector_filter: resync(registry, secrets, connector_filter)


def resync(registry: Any, secrets: Any, connector_filter: Optional[set[str]]) -> tuple[list[str], list[str]]:
    """Bring a live session's connector group up to what is connected now; (added, removed) tool
    names. Nothing is built when nothing changed, which is every turn but the one after a change."""
    build = getattr(registry, "connector_build", None)
    if build is None:
        return [], []
    now = connector_signature(secrets, registry.connector_agent, connector_filter)
    if now == getattr(registry, "connector_signature", None):
        return [], []
    signature, built = build(connector_filter)
    added, removed, _refused = registry.sync_group(CONNECTOR_GROUP, built)
    registry.connector_signature = signature
    return added, removed
