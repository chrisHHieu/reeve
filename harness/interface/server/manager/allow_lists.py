"""AllowListsMixin: who may message the bot, and what unknown senders did.

A connector's allow-list lives in its `<name>:default` secret profile and is mirrored into the
live gateway, so a change takes effect without a restart.
"""

from __future__ import annotations

import json
from typing import Any, Optional



class AllowListsMixin:
    # -- the allow-list ------------------------------------------------------------------

    def allow_user(self, name: str, user_id: str, *, display_name: str = "") -> dict[str, Any]:
        result = self._set_allowed(name, user_id, add=True)
        # A directory pick arrives with the name in hand, and recording it makes the chip
        # readable at once (a message-driven allow learns the name when the message arrives).
        if result.get("ok") and display_name:
            self._note_person(name, user_id, display_name)
        return result

    def disallow_user(self, name: str, user_id: str) -> dict[str, Any]:
        return self._set_allowed(name, user_id, add=False)

    def _set_allowed(self, name: str, user_id: str, *, add: bool) -> dict[str, Any]:
        """Add a sender to, or remove one from, the connector's allow-list."""
        user_id = str(user_id).strip()
        if not user_id:
            return {"ok": False, "error": "user_id required"}
        key = f"{name}:default"
        profile = self.secrets.get(key)
        if not profile:
            return {"ok": False, "error": "connector not connected"}
        allowed = set(profile.get("allowed_users") or [])
        if add:
            allowed.add(user_id)
        else:
            allowed.discard(user_id)
        profile["allowed_users"] = sorted(allowed)
        self.secrets.put(key, profile)
        if self.gateway is not None and name in self.gateway.settings:
            self.gateway.settings[name].allowed_users = set(allowed)
        return {"ok": True, "allowed_users": sorted(allowed)}

    # -- senders who are not on the list -------------------------------------------------

    def _note_person(self, platform: str, user_id: Optional[str], name: Optional[str]) -> None:
        """Remember a sender's display name, persisted, so surfaces keyed by id (the allow-list
        chips above all) can show who a `U07JK…` actually is. Best effort, the newest name wins."""
        if not user_id or not name:
            return
        key = f"{platform}:{user_id}"
        if self._people.get(key) == name:
            return
        self._people[key] = name
        try:
            self._people_path.write_text(json.dumps(self._people))
        except OSError:
            pass

    async def _park_unauthorized(self, event) -> None:
        """The gateway's callback: keep what a sender who is not allowed said (the adapter has
        already resolved names, best effort), so the owner can allow and deliver without asking
        them to send it again."""
        source = event.source
        self._note_person(source.platform, source.user_id, source.user_name)
        self.parked.park(
            platform=source.platform,
            chat_id=source.chat_id,
            chat_name=source.chat_name,
            user_id=source.user_id or "?",
            user_name=source.user_name,
            chat_type=source.chat_type,
            thread_id=source.thread_id,
            text=event.text or "",
        )

    async def resolve_unauthorized(self, name: str, item_id: str, action: str) -> dict[str, Any]:
        """Resolve one parked message. "dismiss" throws it away, "allow" adds the sender to the
        allow-list so future messages flow, and "allow_deliver" also puts the parked message
        through the NORMAL inbound path (buffer and subscriptions) as if it had just arrived."""
        item = self.parked.pop(item_id)
        if item is None or item.platform != name:
            return {"ok": False, "error": "unknown item"}
        if action == "dismiss":
            return {"ok": True}
        if action not in ("allow", "allow_deliver"):
            return {"ok": False, "error": f"unknown action: {action}"}
        allowed = self._set_allowed(name, item.user_id, add=True)
        if not allowed.get("ok"):
            return allowed
        if action == "allow_deliver":
            from harness.integrations.connectors import MessageEvent, SessionSource

            await self._dispatch_inbound(
                MessageEvent(
                    text=item.text,
                    source=SessionSource(
                        platform=item.platform,
                        chat_id=item.chat_id,
                        user_id=item.user_id,
                        user_name=item.user_name,
                        chat_name=item.chat_name,
                        chat_type=item.chat_type,
                        thread_id=item.thread_id,
                    ),
                )
            )
        return {"ok": True}
