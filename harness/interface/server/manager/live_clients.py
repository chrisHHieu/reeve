"""LiveClientsMixin: the sockets watching the app or one session, and fanning events out to them."""

from __future__ import annotations

from typing import Any


class LiveClientsMixin:
    def register_event_client(self, send: Any) -> None:
        self._event_clients.add(send)

    def unregister_event_client(self, send: Any) -> None:
        self._event_clients.discard(send)

    async def broadcast_event(self, message: dict) -> None:
        """Fan an app-wide event out to every `/ws/events` socket. Best effort: a dead socket is
        dropped and is never fatal to the caller."""
        for send in list(self._event_clients):
            try:
                await send(message)
            except Exception:
                self.unregister_event_client(send)

    def register_session_client(self, session_id: str, send: Any) -> None:
        self._session_clients.setdefault(session_id, set()).add(send)

    def unregister_session_client(self, session_id: str, send: Any) -> None:
        clients = self._session_clients.get(session_id)
        if clients is None:
            return
        clients.discard(send)
        if not clients:
            self._session_clients.pop(session_id, None)

    async def broadcast_session(self, session_id: str, message: dict) -> None:
        """Fan a turn event out to every socket viewing this session. Best effort: a dead socket
        is dropped and is never fatal to the turn, since delivery does not depend on a socket."""
        for send in list(self._session_clients.get(session_id, ())):
            try:
                await send(message)
            except Exception:
                self.unregister_session_client(session_id, send)

    async def _announce_session_event(self, message: dict) -> None:
        """Announce a session lifecycle fact on the APP-WIDE stream, best effort.

        `session_created` and `session_status` are not turn events of one session. They are facts
        every client needs (a sibling window lists sessions without viewing any), so they ride
        `/ws/events`. The extra guard covers a collector that raises before a send is even
        attempted: announcing a session must never be able to fail the work it announces.
        """
        try:
            await self.broadcast_event(message)
        except Exception:
            pass
