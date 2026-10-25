"""The frames a Socket Mode client receives, and the fixed identity they carry.

Everything here is a pure builder: it takes the pieces of a scenario and returns the dict Slack
would have sent, so a test can read what the client was handed without starting a server.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

# Stable identities, so a test can assert on them.
BOT_USER_ID = "U_BOT"
TEAM_ID = "T_FAKE"
APP_ID = "A_FAKE"
VERIFICATION_TOKEN = "fake-verification-token"


def maybe_json(value: Any) -> Any:
    """A form-encoded param arrives as a string, so `blocks` is then JSON text. The SDK client posts
    forms and the stateless senders post JSON; this turns either into the structure."""
    if isinstance(value, str) and value[:1] in "[{":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def hello_frame() -> str:
    """What a new Socket Mode connection is greeted with."""
    return json.dumps({"type": "hello", "num_connections": 1, "connection_info": {"app_id": APP_ID}})


def message_envelope(
    *,
    channel: str,
    channel_type: str,
    user: str,
    text: str,
    ts: str,
    thread_ts: Optional[str] = None,
    bot_user_id: str = BOT_USER_ID,
) -> dict:
    """A user's message, wrapped as an `events_api` envelope."""
    event: dict = {
        "type": "message",
        "channel": channel,
        "channel_type": channel_type,
        "user": user,
        "text": text,
        "ts": ts,
        "event_ts": ts,
    }
    if thread_ts:
        event["thread_ts"] = thread_ts
    return {
        "envelope_id": str(uuid.uuid4()),
        "type": "events_api",
        "accepts_response_payload": False,
        "retry_attempt": 0,
        "retry_reason": "",
        "payload": {
            "token": VERIFICATION_TOKEN,
            "team_id": TEAM_ID,
            "api_app_id": APP_ID,
            "event": event,
            "type": "event_callback",
            "event_id": "Ev" + uuid.uuid4().hex[:10].upper(),
            "event_time": int(time.time()),
            "authorizations": [
                {
                    "enterprise_id": None,
                    "team_id": TEAM_ID,
                    "user_id": bot_user_id,
                    "is_bot": True,
                    "is_enterprise_install": False,
                }
            ],
        },
    }


def button_envelope(
    *,
    channel: str,
    channel_name: str,
    user: str,
    username: str,
    message_ts: str,
    action_id: str,
    value: str,
    action_ts: str,
    response_url_base: str,
    bot_user_id: str = BOT_USER_ID,
) -> dict:
    """A click on a Block Kit button, wrapped as an `interactive` envelope."""
    return {
        "envelope_id": str(uuid.uuid4()),
        "type": "interactive",
        "accepts_response_payload": True,
        "payload": {
            "type": "block_actions",
            "token": VERIFICATION_TOKEN,
            "api_app_id": APP_ID,
            "user": {"id": user, "username": username, "name": username},
            "team": {"id": TEAM_ID, "domain": "fake"},
            "enterprise": None,
            "is_enterprise_install": False,
            "container": {
                "type": "message",
                "message_ts": message_ts,
                "channel_id": channel,
                "is_ephemeral": False,
            },
            "trigger_id": "trigger-" + uuid.uuid4().hex,
            "channel": {"id": channel, "name": channel_name},
            "message": {
                "type": "message",
                "user": bot_user_id,
                "ts": message_ts,
                "text": "",
                "team": TEAM_ID,
                "blocks": [],
            },
            "state": {"values": {}},
            "response_url": f"{response_url_base}responses/{uuid.uuid4().hex}",
            "actions": [
                {
                    "type": "button",
                    "action_id": action_id,
                    "block_id": "blk",
                    "text": {"type": "plain_text", "text": "Button"},
                    "value": value,
                    "action_ts": action_ts,
                }
            ],
        },
    }
