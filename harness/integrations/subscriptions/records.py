"""Which sessions listen to which channels, kept on disk.

A subscription is a saved (session, channel) pair: a durable session choosing to listen to a
messaging channel. Several sessions can listen to one channel. It lasts until the person or the
agent unsubscribes, or the session is deleted. A message wakes the session the way a self-wake does
(steer it if busy, start a background turn if idle), so no live socket is needed.

This is the inbound direction. Inbox routing is the outbound one: it mirrors an agent's approvals
and questions to a chat and matches the reply to the request. Pointing the inbox at a channel the
same session also listens to mixes the two, so keep them on different channels.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Subscription:
    session_id: str
    channel: str  # "<platform>:<chat_id>"
    filter: str = "all"  # room for "mentions only" later; every message is delivered today


class SubscriptionStore:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._subs: list[Subscription] = []
        if self.path and self.path.is_file():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self._subs = [Subscription(**raw) for raw in saved.get("subscriptions", [])]

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"subscriptions": [asdict(sub) for sub in self._subs]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _drop(self, keep) -> bool:
        """Keep the subscriptions `keep` accepts; save and say so if any went."""
        remaining = [sub for sub in self._subs if keep(sub)]
        changed = len(remaining) != len(self._subs)
        if changed:
            self._subs = remaining
            self._save()
        return changed

    def subscribe(self, session_id: str, channel: str, *, filter: str = "all") -> Subscription:
        with self._lock:
            for sub in self._subs:
                if sub.session_id == session_id and sub.channel == channel:
                    sub.filter = filter
                    self._save()
                    return sub
            sub = Subscription(session_id=session_id, channel=channel, filter=filter)
            self._subs.append(sub)
            self._save()
            return sub

    def unsubscribe(self, session_id: str, channel: str) -> bool:
        with self._lock:
            return self._drop(lambda sub: not (sub.session_id == session_id and sub.channel == channel))

    def remove_session(self, session_id: str) -> None:
        """Forget everything a deleted session listened to."""
        with self._lock:
            self._drop(lambda sub: sub.session_id != session_id)

    def for_channel(self, channel: str) -> list[Subscription]:
        return [sub for sub in self._subs if sub.channel == channel]

    def for_session(self, session_id: str) -> list[Subscription]:
        return [sub for sub in self._subs if sub.session_id == session_id]

    def all(self) -> list[Subscription]:
        return list(self._subs)
