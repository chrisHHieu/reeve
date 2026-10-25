"""The live connection: one paired account, its events into the store, its sends out.

neonize's `connect` blocks for the life of the connection, so it runs on a thread of its own and
everything else talks to the client from the caller's thread. The device shows in the phone's
Linked devices as "Reeve" on a desktop, so the person can see it there and remove it.

Pairing is either a QR code the phone scans, or an eight-character code typed on the phone after
"Link with phone number" (whatsmeow's `PairPhone`); the second needs the connection open first,
which is why `pair_phone` waits for it.

Nothing may be asked of the client before it says it is up (a QR code arrived, or it connected):
the Go side builds its client inside `connect`, and a call made earlier dereferences nothing and
kills the process (`GetMe` did, on the first live run, 2026-10-09). `_up` is that signal, and every
call into the client waits for it.

This module performs what it is asked to. Who may be written to, how often, and whether a person
approved it are decided above it (`server.py`, the harness's approval).
"""

from __future__ import annotations

import logging
import mimetypes
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .convert import contacts_from, from_event, from_history, jid_text, media_message
from .records import Message, bare, number_of
from .store import Store, keep_days

logger = logging.getLogger("harness.integrations.whatsapp")

#: How long `pair_phone` waits for the socket before it asks for a code.
CONNECT_WAIT_S = 20.0
#: How long the "typing…" indicator shows before a text goes, in seconds per character, capped:
#: an instant reply reads as a machine, a long wait holds the turn for nothing.
TYPING_PER_CHAR_S = 0.03
TYPING_MAX_S = 3.0


def _jid(text: str) -> Any:
    from neonize.utils.jid import build_jid

    user, _, server = bare(text).partition("@")
    return build_jid(user, server or "s.whatsapp.net")


def _seconds(value: Any) -> int:
    value = int(value or 0)
    return value // 1000 if value > 10**12 else value or int(time.time())


class Link:
    """One account. `start` connects; nothing is sent until a method is called."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.store = Store(folder / "messages.db")
        self._client: Any = None
        self._thread: Optional[threading.Thread] = None
        self._connected = threading.Event()
        # Set once the Go-side client exists: the first QR code, or a connection.
        self._up = threading.Event()
        self.qr: str = ""
        self.paired_at: Optional[float] = None
        self.logged_out = False
        self.history_chunks = 0

    # -- connection ------------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        from neonize.client import NewClient
        from neonize.events import (
            ConnectedEv, HistorySyncEv, LoggedOutEv, MessageEv, PairStatusEv,
        )
        from neonize.proto.waCompanionReg.WAWebProtobufsCompanionReg_pb2 import DeviceProps

        props = DeviceProps(os="Reeve", platformType=DeviceProps.DESKTOP)
        client = NewClient(str(self.folder / "session.db"), props=props)

        def on_qr(_client: Any, data: bytes) -> None:
            self.qr = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
            self._up.set()

        client.qr(on_qr)
        client.event(ConnectedEv)(lambda c, ev: self._on_connected())
        client.event(PairStatusEv)(lambda c, ev: self._on_paired(ev))
        client.event(LoggedOutEv)(lambda c, ev: self._on_logged_out())
        client.event(MessageEv)(lambda c, ev: self._on_message(ev))
        client.event(HistorySyncEv)(lambda c, ev: self._on_history(ev))
        self._client = client
        self._thread = threading.Thread(target=self._run, name="whatsapp-link", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._client.connect()
        except Exception:  # a dead connection is reported by `status`, never raised into a tool
            logger.exception("whatsapp connection ended")
        finally:
            self._connected.clear()

    def stop(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
        self.store.close()

    def logout(self) -> dict[str, Any]:
        """Unlink this device: the phone's Linked devices list drops it, and the session ends."""
        if not self._up.is_set():
            return {"ok": True, "linked": False}
        try:
            self._client.logout()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def status(self) -> dict[str, Any]:
        client = self._client if self._up.is_set() else None
        logged_in = bool(client is not None and getattr(client, "is_logged_in", False))
        me = self.me() if logged_in else ""
        return {
            "paired": logged_in,
            "connected": bool(client is not None and getattr(client, "is_connected", False)),
            **({"number": f"+{number_of(me)}"} if me else {}),
            **({"qr": self.qr} if self.qr and not logged_in else {}),
            **({"logged_out": True} if self.logged_out else {}),
            "history_chunks": self.history_chunks,
        }

    def me(self) -> str:
        if not self._up.is_set():
            return ""
        try:
            me = self._client.get_me() if self._client is not None else None
        except Exception:
            return ""
        return jid_text(getattr(me, "JID", None)) if me is not None else ""

    def pair_phone(self, number: str) -> str:
        """The code to type on the phone (Linked devices → Link with phone number)."""
        from neonize.utils.enum import ClientName

        if not self._up.wait(CONNECT_WAIT_S):
            raise RuntimeError("WhatsApp did not answer in time; check the network and try again")
        digits = "".join(ch for ch in number if ch.isdigit())
        return self._client.PairPhone(digits, True, ClientName.WINDOWS)

    # -- events ----------------------------------------------------------------------------

    def _on_connected(self) -> None:
        self._up.set()
        self._connected.set()
        self.qr = ""
        self._sync_contacts()

    def _on_paired(self, event: Any) -> None:
        self.paired_at = time.time()
        self.qr = ""

    def _on_logged_out(self) -> None:
        self.logged_out = True

    def _on_message(self, event: Any) -> None:
        try:
            record = from_event(event)
            if record is not None:
                self.store.add([record])
        except Exception:
            logger.exception("a WhatsApp message could not be kept")

    def _on_history(self, event: Any) -> None:
        try:
            chats, messages, contacts = from_history(event.Data)
            for chat in chats:
                self.store.put_chat(chat)
            for contact in contacts:
                self.store.put_contact(contact)
            self.store.add(messages)
            self.history_chunks += 1
        except Exception:
            logger.exception("a WhatsApp history chunk could not be kept")
        # The phone's history reaches back as far as it likes; what is older than the person's
        # keep choice goes as it lands, not at the next hourly pass.
        self.apply_keep()
        self._sync_contacts()

    def apply_keep(self) -> int:
        """Drop the messages older than the account's keep choice (`keep.json`, read each time so a
        change on the Connectors page holds without a restart)."""
        try:
            return self.store.keep_for(keep_days(self.folder))
        except Exception:
            logger.exception("old WhatsApp messages could not be dropped")
            return 0

    def _sync_contacts(self) -> None:
        try:
            for contact in contacts_from(self._client.contact.get_all_contacts()):
                self.store.put_contact(contact)
        except Exception:
            logger.debug("contacts were not readable yet", exc_info=True)

    # -- sending ---------------------------------------------------------------------------

    def _typing(self, chat: str, seconds: float) -> None:
        from neonize.utils.enum import ChatPresence, ChatPresenceMedia

        try:
            self._client.send_chat_presence(
                _jid(chat), ChatPresence.CHAT_PRESENCE_COMPOSING, ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT
            )
            time.sleep(max(0.0, min(seconds, TYPING_MAX_S)))
        except Exception:
            logger.debug("typing indicator not shown", exc_info=True)

    def _quote(self, chat: str, reply_to: str) -> Any:
        """The context that makes a message a reply: the quoted id, its sender, its content."""
        from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ContextInfo, Message as E2EMessage

        quoted = self.store.message(chat, reply_to)
        if quoted is None:
            raise ValueError(f"no message {reply_to} in this chat: read the chat for its ids")
        if quoted.get("media"):
            import json

            content = media_message(json.loads(quoted["media"]), E2EMessage)
        else:
            content = E2EMessage(conversation=quoted.get("text") or "")
        sender = self.me() if quoted["from_me"] else quoted["sender"]
        return ContextInfo(stanzaID=reply_to, participant=sender, quotedMessage=content)

    def _kept(self, chat: str, response: Any, kind: str, text: str = "", reply_to: str = "", media: Any = None) -> dict[str, Any]:
        """A sent message does not come back as an event on the device that sent it, so it is
        written to the store here: the read tools then show the reply where it was made."""
        message = Message(
            chat=chat, id=response.ID, sender=self.me(), from_me=True, ts=_seconds(response.Timestamp),
            kind=kind, text=text, target_id=reply_to, media=media,
        )
        self.store.add([message])
        return {"sent": True, "id": response.ID, "chat": chat}

    def send_text(self, chat: str, text: str, *, reply_to: str = "") -> dict[str, Any]:
        from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ExtendedTextMessage, Message as E2EMessage

        self._typing(chat, len(text) * TYPING_PER_CHAR_S)
        if reply_to:
            body = E2EMessage(extendedTextMessage=ExtendedTextMessage(text=text, contextInfo=self._quote(chat, reply_to)))
        else:
            body = E2EMessage(conversation=text)
        response = self._client.send_message(_jid(chat), body)
        return self._kept(chat, response, "text", text, reply_to)

    def send_file(self, chat: str, path: Path, *, caption: str = "", voice: bool = False, reply_to: str = "") -> dict[str, Any]:
        """A file as WhatsApp shows it: an image or video inline, a voice note, or a document."""
        mimetype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        to = _jid(chat)
        quoted = None
        if reply_to:
            quoted = self._quote_event(chat, reply_to)
        if voice or mimetype.startswith("audio/"):
            response = self._client.send_audio(to, str(path), ptt=voice, quoted=quoted)
            kind = "voice" if voice else "audio"
        elif mimetype.startswith("image/") and not mimetype.endswith("gif"):
            response = self._client.send_image(to, str(path), caption=caption or None, quoted=quoted)
            kind = "image"
        elif mimetype.startswith("video/"):
            response = self._client.send_video(to, str(path), caption=caption or None, quoted=quoted)
            kind = "video"
        else:
            response = self._client.send_document(
                to, str(path), caption=caption or None, filename=path.name, mimetype=mimetype, quoted=quoted,
            )
            kind = "document"
        return self._kept(chat, response, kind, caption, reply_to, {"mimetype": mimetype, "file_name": path.name})

    def _quote_event(self, chat: str, reply_to: str) -> Any:
        """neonize's media senders quote a whole event, not a context: one is rebuilt."""
        from neonize.proto.Neonize_pb2 import Message as Event, MessageInfo, MessageSource

        context = self._quote(chat, reply_to)
        quoted = self.store.message(chat, reply_to) or {}
        source = MessageSource(Chat=_jid(chat), Sender=_jid(context.participant), IsFromMe=bool(quoted.get("from_me")))
        return Event(Info=MessageInfo(MessageSource=source, ID=reply_to), Message=context.quotedMessage)

    def react(self, chat: str, message_id: str, emoji: str) -> dict[str, Any]:
        quoted = self.store.message(chat, message_id)
        if quoted is None:
            raise ValueError(f"no message {message_id} in this chat: read the chat for its ids")
        sender = self.me() if quoted["from_me"] else quoted["sender"]
        reaction = self._client.build_reaction(_jid(chat), _jid(sender), message_id, emoji)
        self._client.send_message(_jid(chat), reaction)
        self.store.add([Message(
            chat=chat, id=f"reaction-{message_id}", sender=self.me(), from_me=True, ts=int(time.time()),
            kind="reaction", text=emoji, target_id=message_id,
        )])
        return {"reacted": True, "id": message_id, "emoji": emoji}

    def mark_read(self, chat: str, message_ids: list[str]) -> dict[str, Any]:
        """Blue ticks for messages the person received in this chat."""
        from neonize.utils.enum import ReceiptType

        by_sender: dict[str, list[str]] = {}
        for message_id in message_ids:
            row = self.store.message(chat, message_id)
            if row is not None and not row["from_me"]:
                by_sender.setdefault(row["sender"], []).append(message_id)
        for sender, ids in by_sender.items():
            self._client.mark_read(*ids, chat=_jid(chat), sender=_jid(sender), receipt=ReceiptType.READ)
        return {"read": sum(len(ids) for ids in by_sender.values())}

    def download(self, chat: str, message_id: str, folder: Path) -> Path:
        """A message's file, saved under `folder` with the name it was sent with."""
        import json

        from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message as E2EMessage

        row = self.store.message(chat, message_id)
        if row is None or not row.get("media"):
            raise ValueError(f"message {message_id} carries no file")
        media = json.loads(row["media"])
        extension = mimetypes.guess_extension((media.get("mimetype") or "").split(";")[0].strip()) or ""
        name = media.get("file_name") or f"whatsapp-{message_id}{extension}"
        target = folder / Path(name).name
        folder.mkdir(parents=True, exist_ok=True)
        data = self._client.download_any(media_message(media, E2EMessage))
        if not data:
            raise RuntimeError("WhatsApp no longer holds this file (media expires after some weeks)")
        target.write_bytes(data)
        return target
