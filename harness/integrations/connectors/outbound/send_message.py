"""The `send_message` tool, available to every agent.

It is stateless: it parses the `target` token, takes the bot token from the secret store when it
runs (so it is never in the model's context), and sends through a swappable table of senders. It
needs approval outside Auto mode.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import aisuite as ai

from harness.safety.secrets import SecretStore
from harness.tools.contract import ToolContract, ToolPresentation, attach_contract, clip

from ..failures import actionable
from ..messaging.senders import DEFAULT_SENDERS, Sender
from .addressing import bot_token, looks_like_slack_name, parse_or_coerce, resolve_slack_channel

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "send_message",
        "description": (
            "Send a message to a connected chat (Slack or Telegram). `target` is the "
            "reply handle from an inbound message (e.g. 'telegram:12345' or 'slack:C0123', "
            "optionally with a ':<thread>' suffix) — or, for Slack, just the channel NAME "
            "('#general' or 'general'; resolved against the connected workspaces). Use this to "
            "actually reach a person — plain assistant text is not delivered anywhere."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Destination handle 'platform:chat_id[:thread]', e.g. 'telegram:12345'.",
                },
                "text": {"type": "string", "description": "The message text to send."},
            },
            "required": ["target", "text"],
        },
    },
}


# What a person sees (tools/contract.py). `send_message` is the external-risk case the
# intent-level card exists for: the raw arguments say `target="slack:C0123"`, which nobody outside
# engineering can judge, and a sent message cannot be taken back.
#
# `present_call` runs before the tool does, so it reports the target as the model addressed it. A
# bare channel name has not become an id yet, and a presenter must not resolve it: presenters are
# pure, and a card that did network calls would draw the same approval differently on replay.


def _present_call(arguments: dict[str, Any]) -> ToolPresentation:
    target = str(arguments.get("target") or "").strip()
    try:
        platform, chat_id, thread_id = parse_or_coerce(target)
    except ValueError:
        platform, chat_id, thread_id = "", target, None
    summary = f"Send a message to {chat_id or '(no target)'}"
    if platform:
        summary += f" on {platform.title()}"
    if thread_id:
        summary += ", in a thread"
    return ToolPresentation(summary=summary, detail=clip(arguments.get("text")), kind="send")


def _present_result(arguments: dict[str, Any], value: Any) -> ToolPresentation:
    if not isinstance(value, dict):
        return ToolPresentation(summary="Message sent", kind="send")
    error = str(value.get("error") or "").strip()
    if error:
        return ToolPresentation(summary="Message was not sent", detail=clip(error), kind="send")
    target = str(value.get("target") or "").strip()
    return ToolPresentation(summary=f"Message sent to {target}" if target else "Message sent", kind="send")


SEND_MESSAGE_CONTRACT = ToolContract(present_call=_present_call, present_result=_present_result)


def make_send_message_tool(
    secrets: SecretStore, *, senders: Optional[dict[str, Sender]] = None
) -> Callable[..., Any]:
    """`send_message`, bound to a secret store and, optionally, a table of senders."""
    senders = senders if senders is not None else DEFAULT_SENDERS

    def send_message(target: str, text: str) -> dict[str, Any]:
        try:
            platform, chat_id, thread_id = parse_or_coerce(target)
        except ValueError as exc:
            return {"error": str(exc)}
        sender = senders.get(platform)
        if sender is None:
            known = ", ".join(sorted(senders)) or "none"
            return {
                "error": f"there is no connector called {platform!r} — messages can go to: "
                f"{known}. Check the target prefix, or connect it under Settings, Connectors."
            }
        # A channel written as a name ("#general") is looked up. People say the name, not C0123.
        if platform == "slack" and looks_like_slack_name(chat_id):
            chat_id, why = resolve_slack_channel(secrets, chat_id)
            if why:
                return {"error": why}
        token = bot_token(secrets, platform)
        if not token:
            return {"error": f"no bot token for {platform} — connect it first"}
        result = sender(token, chat_id, text, thread_id)
        if result.ok:
            return {"ok": True, "message_id": result.message_id, "target": target}
        # The platform's own word for what went wrong, turned into the next move (`failures.py`).
        # An error it does not recognise comes back unchanged.
        return {"error": actionable(result.error or "send failed", platform=platform)}

    send_message.__name__ = "send_message"
    send_message.__doc__ = _SCHEMA["function"]["description"]
    send_message.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="send_message",
        category="messaging",
        risk_level="medium",
        capabilities=["messaging"],
        requires_approval=True,
    )
    send_message.__tool_schema__ = _SCHEMA
    attach_contract(send_message, SEND_MESSAGE_CONTRACT)
    return send_message
