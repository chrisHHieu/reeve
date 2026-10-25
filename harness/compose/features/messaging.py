"""Messaging: `send_message`, `send_file` and channel subscriptions."""

from __future__ import annotations

from harness.integrations.connectors import load_settings, make_send_file_tool, make_send_message_tool
from harness.integrations.subscriptions import subscription_tools

from .base import FeatureContext


class Messaging:
    id = "messaging"

    def setup(self, ctx: FeatureContext) -> None:
        """Messaging agents get `send_message`. MyHelper also uses it as the reply path for inbound
        Telegram and Slack super-agent sessions."""
        inputs = ctx.inputs
        if not (ctx.agent.messaging and any(setting.enabled for setting in load_settings(ctx.secrets).values())):
            return
        ctx.registry.register(make_send_message_tool(ctx.secrets))
        # `send_file` hands deliverables into the chat. It has the same targets but its own
        # approval surface, so a thread's standing send_message grant never covers uploads. A remote
        # session gets the tool but it refuses, because its file base is this machine's disk and a
        # remote deliverable lives on the other host (phase 2b-2).
        ctx.registry.register(
            make_send_file_tool(ctx.secrets, workspace=ctx.ws, roots=ctx.root_list or None, remote=ctx.remote)
        )
        # Channel subscriptions (inbound): listen to a channel, catch up, subscribe and unsubscribe.
        # The agent gets a channel from ask_user or from a channel message it is reacting to.
        if inputs.subscription_store is not None and inputs.channel_buffer is not None and inputs.session_id:
            ctx.registry.register_all(
                subscription_tools(inputs.subscription_store, inputs.session_id, inputs.channel_buffer),
                session_bound=True,
            )
