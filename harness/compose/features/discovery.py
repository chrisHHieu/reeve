"""Progressive tool discovery: deferrable schemas leave the wire for the `tool_search`
bridge. It is the last feature, because what it defers is every tool the others registered."""

from __future__ import annotations

import logging
from typing import Any, Optional

from .base import FeatureContext

logger = logging.getLogger(__name__)


class Discovery:
    id = "discovery"

    def setup(self, ctx: FeatureContext) -> None:
        apply_tool_discovery(ctx.registry, ctx.inputs.tool_discovery, ctx.inputs.messages)


def apply_tool_discovery(registry: Any, tool_discovery: bool, messages: Optional[list]) -> None:
    """Progressive discovery, which changes what goes on the wire."""
    # Progressive tool discovery is fail-closed at build: no deferrable tool means no saving, so the
    # mode stays off. The bridge is bound again to this registry whenever the mode is on, which is
    # also how a subagent's filtered copy binds it, so a child's `tool_search` can only surface what
    # that child can actually call.
    if tool_discovery and not registry.progressive_discovery:
        from harness.tools.discovery import DISCOVERY_MIN_CHARS, deferred_payload_chars
        from harness.tools.discovery_catalog import COLD_BUILTINS

        # The measured cold built-ins join the MCP tools behind the bridge (an amendment of
        # 2026-09-25), which takes about 5k tokens off every request of every session.
        registry.mark_deferred(COLD_BUILTINS)
        # It has been on by default since the 2026-09-24 audit (a median request carried about 15k
        # tokens of MCP schema), and "on" means "defer when it pays". Below the threshold the bridge
        # and a search round trip would cost more than the schemas it removes. That is logged as info
        # and not as a warning, since with the default on most sessions land here.
        payload = deferred_payload_chars(registry)
        if payload >= DISCOVERY_MIN_CHARS:
            registry.set_progressive_discovery(True)
        elif not registry.deferred_names():
            logger.info("tool_discovery is on but no tool declares deferral (no MCP tools); staying eager")
        else:
            logger.info(
                "tool_discovery is on but the deferrable schemas are small (%d chars < %d); staying eager",
                payload,
                DISCOVERY_MIN_CHARS,
            )
    if registry.progressive_discovery:
        from harness.tools.discovery import discovery_tools, restore_loaded

        registry.register_all(discovery_tools(registry))
        # A registry is rebuilt with its engine, and what the session had loaded is read back from its
        # thread, so the rebuilt tool list is the one the conversation was on.
        restore_loaded(registry, messages)
