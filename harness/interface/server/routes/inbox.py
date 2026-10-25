"""Routes of the parked prompts (the ones that wait on the person) and of channel subscriptions."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import FastAPI

from harness.integrations.subscriptions import resolve_channel

from .session_controls import register_session_controls

_NAME_LOOKUP_HINT = (
    "Channel names can't be looked up — paste the channel ID (channel name ▸ About) "
    "or the channel's Copy-link URL."
)


def _with_session_context(manager, item, *, whole_inbox: bool):
    """The item as a dict plus the context of the session that asked (its title, agent and
    workspace), so a list of them needs no second lookup. None for an orphan to be dropped."""
    row = asdict(item)
    record = manager.session_store.load(item.session_id)
    if (
        record is None
        and whole_inbox
        and item.state == "pending"
        # A live engine with no record yet (a brand-new session whose first turn is running)
        # is not an orphan, hence this guard.
        and item.session_id not in manager._engines
    ):
        # Sessions deleted before `delete_session` closed their items leave prompts that can
        # never be answered; they are cleaned up the first time anyone looks.
        manager.inbox.resolve_session(item.session_id)
        return None
    row["session_title"] = (record.title if record else None) or item.session_id
    row["session_agent"] = record.agent if record else None
    row["session_workspace"] = record.workspace if record else None
    row["session_exists"] = record is not None
    return row


def register_inbox(app: FastAPI, manager) -> None:
    @app.get("/v1/inbox")
    def inbox(session_id: str = "", state: str = "") -> dict[str, Any]:
        # Asked for by one session: what that conversation shows above its composer. Without a
        # session it is every parked prompt, each with the session that asked.
        items = manager.inbox.list(session_id=session_id or None, state=state or None)
        rows = (_with_session_context(manager, item, whole_inbox=not session_id) for item in items)
        return {"items": [row for row in rows if row is not None]}

    @app.post("/v1/inbox/{item_id}/resolve")
    async def resolve_inbox_item(item_id: str, body: dict) -> dict[str, Any]:
        # Idempotent, first responder wins: ok=False means it was already resolved elsewhere. It
        # goes through `resolve_inbox`, so a prompt orphaned by a restart resumes its turn.
        return {"ok": await manager.resolve_inbox(item_id, str(body.get("resolution", "deny")))}

    @app.get("/v1/subscriptions")
    def subscriptions() -> dict[str, Any]:
        # A global, view-only list of (session → channel) subscriptions with the session's title
        # and agent.
        rows: list[dict[str, Any]] = []
        for sub in manager.subscriptions.all():
            record = manager.session_store.load(sub.session_id)
            agent = record.agent if record else ""
            rows.append(
                {
                    "session_id": sub.session_id,
                    "session_title": (record.title if record else None) or sub.session_id,
                    "agent": agent,
                    "channel": sub.channel,
                    # The display name ("#reeve-test") once an inbound message carried one; the
                    # address stays the identifier.
                    "channel_name": manager.channel_buffer.name_for(sub.channel),
                }
            )
        return {"subscriptions": rows}

    @app.get("/v1/channels/recent")
    def recent_channels() -> dict[str, Any]:
        # The picker's "recently seen" source: channels the bot has received messages from.
        return {"channels": manager.channel_buffer.channels()}

    @app.get("/v1/unrouted")
    def unrouted() -> dict[str, Any]:
        # The dead-letter view: inbound messages with nowhere to go, and background-turn failures.
        return {"items": manager.unrouted.list()}

    @app.post("/v1/subscriptions")
    def subscribe(body: dict) -> dict[str, Any]:
        session_id = str(body.get("session_id", "")).strip()
        raw = str(body.get("channel", ""))
        address = resolve_channel(raw)
        if not session_id or not address or ":" not in address:
            if raw.strip().startswith("#"):
                # A bare #name cannot be looked up here, and storing it literally would make a
                # subscription that never matches real traffic.
                return {"ok": False, "error": _NAME_LOOKUP_HINT}
            return {"ok": False, "error": "need a session_id and a channel"}
        manager.subscriptions.subscribe(session_id, address)
        return {"ok": True, "channel": address}

    @app.post("/v1/subscriptions/remove")
    def unsubscribe(body: dict) -> dict[str, Any]:
        session_id = str(body.get("session_id", "")).strip()
        address = resolve_channel(str(body.get("channel", "")))
        return {"ok": True, "removed": manager.subscriptions.unsubscribe(session_id, address)}

    @app.get("/v1/inbox/reconcile")
    def reconcile_inbox(session_id: str) -> dict[str, Any]:
        # Called when a session returns to attended control, to surface what is pending and
        # recap it inline.
        return manager.inbox.reconcile_on_resume(session_id)

    register_session_controls(app, manager)
