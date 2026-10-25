"""neonize's events as records: the one module that reads WhatsApp's protobufs.

A WhatsApp message is a protobuf with one field set for its kind, often wrapped (a disappearing
message, a view-once one, a document with a caption), and an edit, a revocation or a reaction
is a message whose job is to change another one. `describe` unwraps it and says what it is.

Media keeps the message's own protobuf (thumbnails dropped), because fetching the file later
needs its key and path; it rides in the record's `media["proto"]` as base64.
"""

from __future__ import annotations

import base64
from typing import Any, Iterable, Optional

from .records import Chat, Contact, Message, bare

#: Wrappers whose `message` field is the real message.
_WRAPPERS = (
    "ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2", "viewOnceMessageV2Extension",
    "documentWithCaptionMessage", "editedMessage",
)
_MEDIA = {
    "imageMessage": "image", "videoMessage": "video", "documentMessage": "document",
    "stickerMessage": "sticker", "ptvMessage": "video",
}
_REVOKE, _EDIT = 0, 14


def jid_text(jid: Any) -> str:
    """A protobuf JID as the string WhatsApp prints, without its device part."""
    if isinstance(jid, str):
        return bare(jid)
    if jid is None or not getattr(jid, "User", "") and not getattr(jid, "Server", ""):
        return ""
    return bare(f"{jid.User}@{jid.Server}")


def _has(message: Any, name: str) -> bool:
    try:
        return message.HasField(name)
    except ValueError:
        return False


def unwrap(message: Any) -> Any:
    for _ in range(4):
        inner = next((getattr(message, w).message for w in _WRAPPERS if _has(message, w)), None)
        if inner is None:
            return message
        message = inner
    return message


def _context(part: Any) -> tuple[str, list[str]]:
    ctx = getattr(part, "contextInfo", None)
    if ctx is None:
        return "", []
    return ctx.stanzaID or "", [bare(j) for j in ctx.mentionedJID]


def _media_proto(message: Any, field: str) -> str:
    """The message reduced to the one media part, thumbnails dropped, as base64."""
    copy = type(message)()
    getattr(copy, field).CopyFrom(getattr(message, field))
    part = getattr(copy, field)
    for thumb in ("JPEGThumbnail", "pngThumbnail", "thumbnailDirectPath"):
        if thumb in part.DESCRIPTOR.fields_by_name:
            part.ClearField(thumb)
    return base64.b64encode(copy.SerializeToString()).decode("ascii")


def describe(message: Any) -> dict[str, Any]:
    """What one message is: `kind`, `text`, `target_id`, `media`, `mentions`."""
    message = unwrap(message)
    if _has(message, "protocolMessage"):
        proto = message.protocolMessage
        if proto.type == _REVOKE:
            return {"kind": "revoke", "target_id": proto.key.ID}
        if proto.type == _EDIT:
            edited = describe(proto.editedMessage)
            return {"kind": "edit", "target_id": proto.key.ID, "text": edited.get("text", "")}
        return {"kind": "system"}
    if _has(message, "reactionMessage"):
        return {"kind": "reaction", "target_id": message.reactionMessage.key.ID, "text": message.reactionMessage.text}
    if message.conversation:
        return {"kind": "text", "text": message.conversation}
    if _has(message, "extendedTextMessage"):
        part = message.extendedTextMessage
        target, mentions = _context(part)
        return {"kind": "text", "text": part.text, "target_id": target, "mentions": mentions}
    for field, kind in _MEDIA.items():
        if _has(message, field):
            part = getattr(message, field)
            target, mentions = _context(part)
            media: dict[str, Any] = {"field": field, "mimetype": part.mimetype, "proto": _media_proto(message, field)}
            if getattr(part, "fileName", ""):
                media["file_name"] = part.fileName
            if getattr(part, "fileLength", 0):
                media["size"] = int(part.fileLength)
            if getattr(part, "seconds", 0):
                media["seconds"] = int(part.seconds)
            text = getattr(part, "caption", "") or ""
            return {"kind": kind, "text": text, "target_id": target, "mentions": mentions, "media": media}
    if _has(message, "audioMessage"):
        part = message.audioMessage
        target, _ = _context(part)
        media = {
            "field": "audioMessage", "mimetype": part.mimetype, "seconds": int(part.seconds),
            "proto": _media_proto(message, "audioMessage"),
        }
        return {"kind": "voice" if part.PTT else "audio", "target_id": target, "media": media}
    if _has(message, "locationMessage"):
        loc = message.locationMessage
        place = ", ".join(p for p in (loc.name, loc.address) if p)
        text = f"{place} ({loc.degreesLatitude:.5f}, {loc.degreesLongitude:.5f})".strip()
        return {"kind": "location", "text": text}
    if _has(message, "contactMessage"):
        return {"kind": "contact", "text": message.contactMessage.displayName}
    for field in ("pollCreationMessage", "pollCreationMessageV2", "pollCreationMessageV3"):
        if _has(message, field):
            poll = getattr(message, field)
            options = " / ".join(o.optionName for o in poll.options)
            return {"kind": "poll", "text": f"{poll.name}: {options}"}
    if _has(message, "call"):
        return {"kind": "call"}
    return {"kind": "other"}


def from_event(event: Any) -> Optional[Message]:
    """A live message (`MessageEv`), or None for what is no message of its own."""
    info = event.Info
    source = info.MessageSource
    said = describe(event.Message)
    if said["kind"] == "system":
        return None
    return Message(
        chat=jid_text(source.Chat),
        id=info.ID,
        sender=jid_text(source.Sender),
        from_me=bool(source.IsFromMe),
        ts=int(info.Timestamp),
        sender_name=info.Pushname,
        **said,
    )


def from_history(data: Any) -> tuple[list[Chat], list[Message], list[Contact]]:
    """One history-sync chunk (`HistorySyncEv.Data`): its chats, their messages, and the names
    people set for themselves."""
    chats: list[Chat] = []
    messages: list[Message] = []
    for conversation in data.conversations:
        chat = jid_text(conversation.ID)
        chats.append(Chat(
            jid=chat,
            name=conversation.name or conversation.displayName or "",
            unread=int(conversation.unreadCount),
            archived=bool(conversation.archived),
            pinned=bool(conversation.pinned),
            muted_until=int(conversation.muteEndTime) or None,
            last_ts=int(conversation.conversationTimestamp) or None,
        ))
        messages.extend(m for m in (_from_web(chat, h.message) for h in conversation.messages) if m)
    contacts = [Contact(jid=jid_text(p.ID), push_name=p.pushname) for p in data.pushnames if p.pushname]
    return chats, messages, contacts


def _from_web(chat: str, web: Any) -> Optional[Message]:
    """A message as the history sync carries it (`WebMessageInfo`)."""
    if not _has(web, "message"):
        return None
    said = describe(web.message)
    if said["kind"] == "system":
        return None
    key = web.key
    sender = key.participant or web.participant or (chat if not key.fromMe else "")
    return Message(
        chat=chat,
        id=key.ID,
        sender=bare(sender) if sender else "",
        from_me=bool(key.fromMe),
        ts=int(web.messageTimestamp),
        sender_name=web.pushName,
        **said,
    )


def media_message(media: dict[str, Any], message_type: type) -> Any:
    """The protobuf a download needs, rebuilt from what the store kept."""
    message = message_type()
    message.ParseFromString(base64.b64decode(media["proto"]))
    return message


def contacts_from(entries: Iterable[Any]) -> list[Contact]:
    """The phone's address book, as the contact store returns it."""
    out = []
    for entry in entries:
        info = getattr(entry, "Info", entry)
        name = getattr(info, "FullName", "") or getattr(info, "FirstName", "")
        push = getattr(info, "PushName", "")
        jid = jid_text(getattr(entry, "JID", None))
        if jid and (name or push):
            out.append(Contact(jid=jid, name=name, push_name=push))
    return out
