"""Add or remove the optional Slack user token (search, reminders, status)."""
from __future__ import annotations

import asyncio
from typing import Any


def register_slack_user_token(app, manager):
    @app.post("/v1/connectors/slack/user-token")
    async def slack_user_token(body: dict) -> dict[str, Any]:
        """`{token}`. An empty token removes it. The check is a network call."""
        from harness.integrations.connectors.slack_user_token import set_user_token

        return await asyncio.to_thread(set_user_token, manager.secrets, str(body.get("token") or ""))
