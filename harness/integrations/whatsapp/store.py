"""The messages this linked device has seen, in SQLite, and what the read tools ask of them.

WhatsApp keeps no history a linked device can query. The phone sends one history sync when the
device pairs and live messages after that, so what this device can read is exactly what was
written here; the store is the source of every read tool. Edits, revocations and reactions are
folded into the message they change rather than kept as messages of their own, the way the app
shows them.

Search matches without accents (`memory_format.fold`): a query typed `duoc` finds `được`. The
store is local, in the account's own folder under the state directory, and it is never sent
anywhere; a read tool returns from it only what the call asked for.

Messages are kept for as long as the person chose on the connector's page (`keep.json` beside the
store; 90 days unless they chose otherwise), and they can clear them without unlinking. Without a
limit the store held every chat the phone ever synced, whether or not anything read it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

from harness.context.memory.memory_format import fold

from .keep import DAY_S, DEFAULT_KEEP_DAYS, KEEP_CHOICES, KEEP_FILE, KeptMessages, keep_days, set_keep_days  # noqa: F401
from .records import Chat, Contact, Message, bare, is_group, number_of

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    jid TEXT PRIMARY KEY, name TEXT DEFAULT '', unread INTEGER, archived INTEGER,
    pinned INTEGER, muted_until INTEGER, last_ts INTEGER
);
CREATE TABLE IF NOT EXISTS messages (
    chat TEXT NOT NULL, id TEXT NOT NULL, sender TEXT NOT NULL, from_me INTEGER NOT NULL,
    ts INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT DEFAULT '', target_id TEXT DEFAULT '',
    sender_name TEXT DEFAULT '', media TEXT, mentions TEXT, edited INTEGER DEFAULT 0,
    revoked INTEGER DEFAULT 0, folded TEXT DEFAULT '',
    PRIMARY KEY (chat, id)
);
CREATE INDEX IF NOT EXISTS messages_by_time ON messages (chat, ts);
CREATE TABLE IF NOT EXISTS reactions (
    chat TEXT NOT NULL, target_id TEXT NOT NULL, sender TEXT NOT NULL, emoji TEXT NOT NULL,
    ts INTEGER NOT NULL, from_me INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (chat, target_id, sender)
);
CREATE TABLE IF NOT EXISTS contacts (
    jid TEXT PRIMARY KEY, name TEXT DEFAULT '', push_name TEXT DEFAULT ''
);
"""

#: Messages a read returns when the call names no limit, and the most it may ask for.
DEFAULT_LIMIT = 40
MAX_LIMIT = 200

def _named(name: str, wanted: str) -> bool:
    """Whether a name answers what was typed: each typed word begins a word of the name, accents
    ignored, the way a phone's contact search works (`an` finds "An Nguyễn" and "Anh Tuấn", not
    "Bình Trần")."""
    words = fold(name).split()
    return all(any(word.startswith(typed) for word in words) for typed in fold(wanted).split())


class Store(KeptMessages):
    """One account's messages. Safe to share between the event thread and the tool calls."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        # The daemon writes while the server reads what is kept and may clear it: a second
        # connection waits for the first rather than failing at once.
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=15.0)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- writing ---------------------------------------------------------------------------

    def add(self, messages: Iterable[Message]) -> int:
        """Keep each message, folding an edit, a revocation or a reaction into its target.
        Returns how many messages were new. Seeing a message twice (the history sync and a
        live delivery both carry it) changes nothing."""
        added = 0
        with self._lock, self._db:
            for m in messages:
                chat, sender = bare(m.chat), bare(m.sender)
                if m.kind == "reaction":
                    if m.text:
                        self._db.execute(
                            "INSERT OR REPLACE INTO reactions VALUES (?,?,?,?,?,?)",
                            (chat, m.target_id, sender, m.text, m.ts, int(m.from_me)),
                        )
                    else:  # an empty reaction takes the earlier one back
                        self._db.execute(
                            "DELETE FROM reactions WHERE chat=? AND target_id=? AND sender=?",
                            (chat, m.target_id, sender),
                        )
                    continue
                if m.kind == "edit":
                    self._db.execute(
                        "UPDATE messages SET text=?, folded=?, edited=1 WHERE chat=? AND id=?",
                        (m.text, fold(m.text), chat, m.target_id),
                    )
                    continue
                if m.kind == "revoke":
                    self._db.execute(
                        "UPDATE messages SET text='', folded='', revoked=1, media=NULL WHERE chat=? AND id=?",
                        (chat, m.target_id),
                    )
                    continue
                cursor = self._db.execute(
                    "INSERT OR IGNORE INTO messages (chat, id, sender, from_me, ts, kind, text, "
                    "target_id, sender_name, media, mentions, folded) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        chat, m.id, sender, int(m.from_me), m.ts, m.kind, m.text, m.target_id,
                        m.sender_name, json.dumps(m.media) if m.media else None,
                        json.dumps(m.mentions) if m.mentions else None,
                        fold(f"{m.text} {(m.media or {}).get('file_name', '')}"),
                    ),
                )
                added += cursor.rowcount
                self._db.execute(
                    "INSERT INTO chats (jid, last_ts) VALUES (?, ?) ON CONFLICT(jid) DO UPDATE SET "
                    "last_ts = MAX(COALESCE(last_ts, 0), excluded.last_ts)",
                    (chat, m.ts),
                )
                if m.sender_name and not m.from_me and not is_group(sender):
                    self._db.execute(
                        "INSERT INTO contacts (jid, push_name) VALUES (?, ?) ON CONFLICT(jid) DO "
                        "UPDATE SET push_name = excluded.push_name",
                        (sender, m.sender_name),
                    )
        return added

    def put_chat(self, chat: Chat) -> None:
        """What the phone says of a chat. A field left None keeps what the store had."""
        with self._lock, self._db:
            self._db.execute("INSERT OR IGNORE INTO chats (jid) VALUES (?)", (bare(chat.jid),))
            for column in ("name", "unread", "archived", "pinned", "muted_until", "last_ts"):
                value = getattr(chat, column)
                if value is None or (column == "name" and not value):
                    continue
                if column == "last_ts":
                    self._db.execute(
                        "UPDATE chats SET last_ts = MAX(COALESCE(last_ts, 0), ?) WHERE jid=?",
                        (value, bare(chat.jid)),
                    )
                else:
                    self._db.execute(
                        f"UPDATE chats SET {column}=? WHERE jid=?",  # noqa: S608 - a fixed column name
                        (int(value) if isinstance(value, bool) else value, bare(chat.jid)),
                    )

    def put_contact(self, contact: Contact) -> None:
        with self._lock, self._db:
            jid = bare(contact.jid)
            self._db.execute("INSERT OR IGNORE INTO contacts (jid) VALUES (?)", (jid,))
            if contact.name:
                self._db.execute("UPDATE contacts SET name=? WHERE jid=?", (contact.name, jid))
            if contact.push_name:
                self._db.execute("UPDATE contacts SET push_name=? WHERE jid=?", (contact.push_name, jid))

    # -- naming ----------------------------------------------------------------------------

    def name_of(self, jid: str) -> str:
        """What the person would call this chat or person: the saved name, the chat's name,
        the name they set themselves, then the number."""
        jid = bare(jid)
        with self._lock:
            contact = self._db.execute("SELECT name, push_name FROM contacts WHERE jid=?", (jid,)).fetchone()
            chat = self._db.execute("SELECT name FROM chats WHERE jid=?", (jid,)).fetchone()
        for value in (
            contact["name"] if contact else "",
            chat["name"] if chat else "",
            contact["push_name"] if contact else "",
        ):
            if value:
                return value
        number = number_of(jid)
        return f"+{number}" if number else jid

    def known(self, jid: str) -> bool:
        """Whether this is someone the person already talks to: a saved contact, or a chat that
        holds a message from them. A send goes only to one: never a cold number."""
        jid = bare(jid)
        with self._lock:
            saved = self._db.execute(
                "SELECT 1 FROM contacts WHERE jid=? AND name != ''", (jid,)
            ).fetchone()
            spoke = self._db.execute(
                "SELECT 1 FROM messages WHERE chat=? AND from_me=0 LIMIT 1", (jid,)
            ).fetchone()
        return bool(saved or spoke)

    def resolve(self, who: str, *, country_code: str = "") -> tuple[Optional[str], list[str]]:
        """A chat from what the model wrote: a JID, a phone number, or a name. Returns the JID,
        or None and the candidates when a name matches several (or none).

        A number written the local way (`0901 234 567`) takes the account's own country code,
        which is how the person would dial it from their phone."""
        who = who.strip()
        if "@" in who:
            return bare(who), []
        compact = "".join(ch for ch in who if ch not in " -.()")
        if compact.lstrip("+").isdigit() and len(compact.lstrip("+")) >= 7:
            digits = compact.lstrip("+")
            if not compact.startswith("+") and digits.startswith("0") and country_code:
                digits = country_code + digits[1:]
            return f"{digits}@s.whatsapp.net", []
        wanted = fold(who)
        with self._lock:
            jids = [r["jid"] for r in self._db.execute("SELECT jid FROM chats UNION SELECT jid FROM contacts")]
        named = {jid: self.name_of(jid) for jid in jids}
        exact = [jid for jid, name in named.items() if fold(name) == wanted]
        if len(exact) == 1:
            return exact[0], []
        partial = exact or [jid for jid, name in named.items() if wanted and _named(name, who)]
        if len(partial) == 1:
            return partial[0], []
        return None, [f"{named[jid]} ({jid})" for jid in partial[:10]]

    # -- reading ---------------------------------------------------------------------------

    def chats(self, *, limit: int = 30, query: str = "", unread_only: bool = False) -> list[dict[str, Any]]:
        """Chats, the most recent first, each with its last message."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM chats WHERE last_ts IS NOT NULL ORDER BY last_ts DESC"
            ).fetchall()
        out: list[dict[str, Any]] = []
        wanted = fold(query)
        for row in rows:
            name = self.name_of(row["jid"])
            if wanted and not _named(name, query):
                continue
            if unread_only and not row["unread"]:
                continue
            last = self.messages(row["jid"], limit=1)
            out.append({
                "chat": row["jid"], "name": name, "group": is_group(row["jid"]),
                **({"unread": row["unread"]} if row["unread"] else {}),
                **({"pinned": True} if row["pinned"] else {}),
                **({"archived": True} if row["archived"] else {}),
                "last": last[-1] if last else None,
            })
            if len(out) >= max(1, min(limit, MAX_LIMIT)):
                break
        return out

    def messages(
        self, chat: str, *, limit: int = DEFAULT_LIMIT, before: Optional[int] = None,
        after: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """One chat's messages in time order: the newest `limit` of them, bounded by time."""
        where, args = ["chat=?"], [bare(chat)]
        if before is not None:
            where.append("ts < ?")
            args.append(before)
        if after is not None:
            where.append("ts > ?")
            args.append(after)
        limit = max(1, min(int(limit), MAX_LIMIT))
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM messages WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT ?",  # noqa: S608
                (*args, limit),
            ).fetchall()
        return [self._shown(row) for row in reversed(rows)]

    def search(
        self, query: str, *, chat: str = "", sender: str = "", since: Optional[int] = None,
        until: Optional[int] = None, limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Messages holding every word of `query`, accents ignored, the newest first."""
        terms = [fold(t) for t in query.split() if t.strip()]
        where, args = [], []
        for term in terms:
            where.append("folded LIKE ?")
            args.append(f"%{term}%")
        for column, value in (("chat", chat), ("sender", sender)):
            if value:
                where.append(f"{column} = ?")
                args.append(bare(value))
        if since is not None:
            where.append("ts >= ?")
            args.append(since)
        if until is not None:
            where.append("ts <= ?")
            args.append(until)
        if not where:
            return []
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM messages WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT ?",  # noqa: S608
                (*args, max(1, min(int(limit), MAX_LIMIT))),
            ).fetchall()
        return [{**self._shown(row), "chat": row["chat"], "chat_name": self.name_of(row["chat"])} for row in rows]

    def around(self, chat: str, message_id: str, *, span: int = 5) -> list[dict[str, Any]]:
        """The messages before and after one, for the conversation it sits in."""
        with self._lock:
            row = self._db.execute(
                "SELECT ts FROM messages WHERE chat=? AND id=?", (bare(chat), message_id)
            ).fetchone()
        if row is None:
            return []
        earlier = self.messages(chat, limit=span + 1, before=row["ts"] + 1)
        later = self.messages(chat, limit=10_000, after=row["ts"])[:span]
        return earlier + later

    def contacts(self, query: str = "", *, limit: int = 30) -> list[dict[str, Any]]:
        wanted = fold(query)
        with self._lock:
            rows = self._db.execute("SELECT jid FROM contacts").fetchall()
        out = []
        for row in rows:
            name = self.name_of(row["jid"])
            number = number_of(row["jid"])
            if wanted and not _named(name, query) and wanted not in number:
                continue
            out.append({"jid": row["jid"], "name": name, **({"number": f"+{number}"} if number else {})})
        out.sort(key=lambda c: fold(c["name"]))
        return out[: max(1, min(limit, MAX_LIMIT))]

    def message(self, chat: str, message_id: str) -> Optional[dict[str, Any]]:
        """One message as stored, with its media details: what a reply quotes and what a
        download fetches."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM messages WHERE chat=? AND id=?", (bare(chat), message_id)
            ).fetchone()
        return dict(row) if row else None

    def _shown(self, row: sqlite3.Row) -> dict[str, Any]:
        """A message as a read tool returns it: who, when, what, and what happened to it."""
        with self._lock:
            reactions = self._db.execute(
                "SELECT sender, emoji, from_me FROM reactions WHERE chat=? AND target_id=?",
                (row["chat"], row["id"]),
            ).fetchall()
        shown: dict[str, Any] = {
            "id": row["id"],
            "ts": row["ts"],
            "from": "me" if row["from_me"] else (row["sender_name"] or self.name_of(row["sender"])),
            "kind": row["kind"],
        }
        if row["revoked"]:
            shown["deleted"] = True
        elif row["text"]:
            shown["text"] = row["text"]
        if row["edited"]:
            shown["edited"] = True
        if row["target_id"]:
            shown["reply_to"] = row["target_id"]
        if row["media"] and not row["revoked"]:
            media = json.loads(row["media"])
            shown["media"] = {k: v for k, v in media.items() if k in ("mimetype", "file_name", "seconds", "size")}
        if reactions:
            shown["reactions"] = [
                f"{r['emoji']} {'me' if r['from_me'] else self.name_of(r['sender'])}" for r in reactions
            ]
        return shown
