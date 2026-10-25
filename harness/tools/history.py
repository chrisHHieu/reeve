"""Search what was actually said, back past the point the model can still see.

Compaction replaces a span of the conversation with a summary in the OUTBOUND view. The
canonical messages are untouched — `apply_to_outbound` is a projection, and the `.jsonl`
on disk still holds every word. So nothing was lost. It just became unreachable: the model
has no way to ask, and a summary cannot answer "what exactly did we agree on the price?".

This is the same shape as `tools/spill.py`, applied to compaction instead of to tool
output. There too the fix was not to keep more in context — it was to make what fell out
of context retrievable, so the cost of losing it drops to one read.

Three decisions worth keeping:

**It scans the files instead of building an index.** An FTS table would be a second copy
of the same bytes, and a second copy is a thing that can be wrong — a sync bug shows up as
a search that quietly misses. Scanning reads the source of truth, so it cannot drift.
Nothing here is latency-critical: the caller already paid a model round-trip to ask.

**Scope is this session plus the other sessions of the same workspace.** A project is a
boundary the user already understands and already sees in the sidebar. "Everything on the
machine" would mean one project's agent reading another's conversations, which is a
consent question nobody asked, and "this session only" would fail the question the feature
exists for — the one asked in a NEW session about last week.

**It returns what was said, verbatim.** Summarising a summary is how the original
information gets lost twice. The excerpt is a window around the match, and the budget is
enforced by clipping the LIST of hits, never by rewriting a hit.
"""

from __future__ import annotations

from typing import Any

import aisuite as ai

from harness.context.memory.memory_format import fold
from .contract import ToolContract, ToolPresentation, clip

#: Sessions read per search, newest first. Well past the point where an older session is
#: still what someone means by "we", and it bounds the work at a few megabytes.
DEFAULT_MAX_SESSIONS = 40

#: Hits returned. Enough to show that an answer repeats, small enough that the model reads
#: them all instead of skimming.
DEFAULT_LIMIT = 8

#: Characters of the surrounding message kept per hit.
DEFAULT_WINDOW = 400

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_history",
        "description": (
            "Search earlier conversations for what was actually said — including parts "
            "of THIS conversation that are no longer in your context because it was "
            "compacted. Covers this session and other sessions in the same project. Use "
            "it whenever the user refers to something decided or discussed before that "
            "you cannot see: it returns their words, not a summary."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Words that would appear in the passage. All of them must "
                        "appear, so use the distinctive ones and leave out filler."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": f"Maximum passages to return (default {DEFAULT_LIMIT}).",
                },
            },
            "required": ["query"],
        },
    },
}


def _text_of(content: Any) -> str:
    """The readable text of a message, whatever shape it arrived in."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def _excerpt(text: str, folded: str, terms: list[str], window: int) -> str:
    """A window around the first term that appears, on whole-ish boundaries.

    `terms` are found in `folded` (diacritics stripped), but the window is cut from the
    original text — the hit is quoted back to the model as it was said, accents and all.
    Folding can change a string's length only if its source was not already NFC, so the
    guard falls back to quoting the folded text rather than slicing at a misaligned
    offset.
    """
    at = min((folded.find(t) for t in terms if folded.find(t) >= 0), default=0)
    source = text if len(folded) == len(text) else folded
    start = max(0, at - window // 3)
    end = min(len(source), start + window)
    piece = source[start:end].strip()
    return ("…" if start > 0 else "") + piece + ("…" if end < len(source) else "")


class HistorySearch:
    """Bound to one session: its own history, plus its project's other sessions.

    Holds the store rather than a snapshot, so a search late in a long session sees the
    turns that happened after the engine was built.
    """

    def __init__(
        self,
        store: Any,
        *,
        session_id: str = "",
        workspace: str = "",
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        window: int = DEFAULT_WINDOW,
    ) -> None:
        self.store = store
        self.session_id = session_id or ""
        self.workspace = workspace or ""
        self.max_sessions = max(1, int(max_sessions))
        self.window = max(80, int(window))

    def _session_ids(self) -> list[str]:
        """This session first — it is what "we" most often means — then the project's
        others, newest first."""
        ids: list[str] = [self.session_id] if self.session_id else []
        if not self.workspace:
            return ids
        try:
            # Roots only, explicitly: what a person "said before" is their
            # own conversation; a `task` child's transcript belongs to the parent turn
            # that spawned it, and searching it would quote an internal step back as
            # something the user decided.
            records = self.store.list(
                workspace=self.workspace, include_children=False
            )
        except Exception:
            return ids
        for record in records:
            sid = getattr(record, "session_id", "")
            if sid and sid not in ids and not sid.startswith("__"):
                ids.append(sid)
        return ids[: self.max_sessions]

    def search(self, query: str, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        # Matching happens on folded text (see `memory.search.fold`): a query typed
        # without tone marks still finds what was said with them. Quoting stays verbatim.
        terms = [fold(t) for t in str(query or "").split() if t]
        if not terms:
            return {
                "error": "search_history needs something to look for — pass the "
                "distinctive words you expect in the passage."
            }
        limit = max(1, min(int(limit or DEFAULT_LIMIT), 25))

        hits: list[dict[str, Any]] = []
        scanned = 0
        for sid in self._session_ids():
            if len(hits) >= limit:
                break
            try:
                record = self.store.load(sid)
            except Exception:
                continue
            if record is None:
                continue
            scanned += 1
            title = record.title or sid
            when = (record.updated_at or "")[:10]
            # Newest first inside a session: for "what did we decide", the last time it
            # was said is the answer, and the earlier times are how it got there.
            for message in reversed(record.messages or []):
                role = message.get("role")
                if role not in ("user", "assistant"):
                    # Tool results are excluded on purpose: they are machine output, they
                    # dominate a transcript by volume, and they are not what anyone means
                    # by "what we said".
                    continue
                text = _text_of(message.get("content"))
                if not text:
                    continue
                folded = fold(text)
                if not all(term in folded for term in terms):
                    continue
                hits.append(
                    {
                        "session": title,
                        "when": when,
                        "who": "the user" if role == "user" else "you",
                        "said": _excerpt(text, folded, terms, self.window),
                        **({"this_session": True} if sid == self.session_id else {}),
                    }
                )
                if len(hits) >= limit:
                    break

        return {
            "query": query,
            "searched_sessions": scanned,
            "hits": hits,
            **(
                {}
                if hits
                else {
                    "note": "nothing matched. Every word in the query has to appear — try "
                    "fewer, more distinctive ones."
                }
            ),
        }


def _present_call(arguments: dict[str, Any]) -> ToolPresentation:
    query = str(arguments.get("query") or "").strip()
    return ToolPresentation(
        summary=f"Look back through earlier conversations for “{clip(query, 60)}”"
        if query
        else "Look back through earlier conversations",
        kind="search",
    )


def _present_result(arguments: dict[str, Any], value: Any) -> ToolPresentation:
    if not isinstance(value, dict) or value.get("error"):
        return ToolPresentation(summary="Could not search earlier conversations", kind="search")
    hits = value.get("hits") or []
    if not hits:
        return ToolPresentation(
            summary="Nothing found in earlier conversations",
            detail=f"searched {value.get('searched_sessions', 0)} conversations",
            kind="search",
        )
    count = len(hits)
    where = {str(h.get("session") or "") for h in hits}
    return ToolPresentation(
        summary=f"Found {count} earlier passage{'s' if count != 1 else ''}",
        detail=", ".join(sorted(w for w in where if w))[:200],
        kind="search",
    )


SEARCH_HISTORY_CONTRACT = ToolContract(
    present_call=_present_call,
    present_result=_present_result,
    timeout_ms=60_000,
)


def history_tools(history: HistorySearch) -> list:
    def search_history(query: str, limit: int = DEFAULT_LIMIT) -> dict:
        """Search earlier conversations for what was actually said.

        Args:
            query: words that must all appear in the passage
            limit: maximum passages to return
        """
        return history.search(query, limit)

    wrapped = ai.tool(
        search_history,
        metadata=ai.ToolMetadata(
            category="memory",
            risk_level="low",
            capabilities=["history"],
        ),
    )
    wrapped.__tool_schema__ = _SCHEMA
    from .contract import attach_contract

    attach_contract(wrapped, SEARCH_HISTORY_CONTRACT)
    return [wrapped]
