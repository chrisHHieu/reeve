"""Auto memory — the notes the agent writes FOR ITSELF, machine-local.

Auto memory: a `MEMORY.md` index plus topic files with frontmatter, in directories kept
OUTSIDE the workspace, so the agent's own notes never show up in the user's `git
status`. The user's own standing instructions live elsewhere (their Instructions file) —
this module is the "the agent writes it" half, not the "you write it" half.

Two directories of one shape, told apart by their `MemoryScope`:

- **project** (`PROJECT`) — `<data>/projects/<key>/memory`: one project's notes, Claude
  Code's auto memory as is (`user | feedback | project | reference`);
- **user** (`USER`) — `<data>/memory`: what a background pass used to keep about the person.
  No session loads or writes it any more; Settings lists what is there.

Layout (``root`` is caller-supplied, normally ``<data>/projects/<project>/memory``):

    MEMORY.md      index — one line per memory. Only the first ``INDEX_MAX_LINES`` lines
                   (or ``INDEX_MAX_BYTES``) are injected each session; everything past the
                   cap is dropped at load, which is why the index must stay short.
    <topic>.md     one memory per file, with YAML frontmatter (``type``, ``modified``),
                   read on demand.

Nothing here is in the prompt except the index; topics cost context only when read.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Iterable

from harness.safety.redaction import redact_secrets
from . import anchors as _anchors
from . import gate as _gate
from .admission import settle
from . import memory_format as _fmt
from . import progress_log as _progress
from . import threats
from .guard import fence_injections
from typing import Any, Optional

import aisuite as ai

#: The index file: one line per memory.
INDEX_NAME = "MEMORY.md"
#: The progress log: a checklist for work that spans sessions, shown to each new one
#: (`progress_log.shown`; `gate.judge_progress` holds its size and what may be marked done).
PROGRESS_NAME = _gate.PROGRESS_NAME
PROGRESS_MAX_CHARS = _gate.PROGRESS_MAX_CHARS

#: What may be loaded from the index at session start. The
#: index is an INDEX: detail belongs in topic files, and the cap is what forces that.
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25_000

#: At this share of either read cap a write's result carries a shortening reminder, while
#: the index can still be tidied cheaply; past the cap the reminder becomes the rewrite
#: directive in `write` (a nudge near the limit, then an error over it).
INDEX_NEAR_LIMIT = 0.8

#: One topic file's ceiling, and one write's ceiling (below it, so an append can always
#: be refused before it is applied).
MAX_FILE_CHARS = 200_000
MAX_WRITE_CHARS = 64_000

#: Search result cap.
MAX_SEARCH_HITS = 20

#: The types a PROJECT directory holds (`gate.KINDS`). A note the person writes in
#: Settings with no type is `feedback`: how they want the work done.
TYPES = _gate.KINDS
DEFAULT_TYPE = "feedback"
#: Types files on disk may still carry from an earlier memory format. Valid to read and for the person to
#: edit; a model rewriting one gives it a type from `TYPES`.
LEGACY_TYPES = _gate.EARLIER_KINDS



@dataclass(frozen=True)
class MemoryScope:
    """What one memory directory holds and how much of its index a session loads."""

    name: str
    types: tuple[str, ...]
    default_type: str
    index_max_lines: int
    index_max_bytes: int


#: One project's notes, its limits included.
PROJECT = MemoryScope("project", TYPES, DEFAULT_TYPE, INDEX_MAX_LINES, INDEX_MAX_BYTES)
#: The notes a background pass used to keep about the person. No session
#: loads or writes them since 0108: what holds in every project is the person's own
#: instructions file. The scope stays so Settings can list, read and delete what is there.
USER = MemoryScope("user", ("user", "feedback"), "user", 50, 8_000)

_SAFE_TOPIC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)


#: One lock per memory file, PROCESS-WIDE and keyed by the RESOLVED path. The engine's
#: batch dispatch runs several low-risk calls in parallel (`engine/helpers.py`, one
#: `asyncio.gather` over worker threads; `auto_memory` is metadata-low-risk so a batch of
#: edits all lands there at once). `AutoMemory` is constructed fresh at each call site —
#: the manager builds one per workspace key, and the tool closure holds its own — so a
#: lock on the INSTANCE would not cover two sessions on the same project, let alone one
#: tool closure, and both writers would read-modify-write the same file with the later
#: write silently dropping the earlier edit (owner-hit 2026-09-17: six `edit` calls in
#: one message all reported `ok`; five deletions were gone and the index had a
#: duplicated tail). The path key is what makes "one writer per file" true regardless
#: of which instance asks.
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Path) -> threading.RLock:
    key = str(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _atomic_write(path: Path, text: str) -> None:
    """Replace `path` atomically: a sibling temp file, then `os.replace`.

    A reader (`read`, `inject_block`, the next lock holder) sees either the old file or
    the complete new one. `Path.write_text` truncates before it writes, so a parallel
    batch could hand a reader an empty or half-written MEMORY.md — the state the lock
    alone cannot cover, because reads do not take the lock.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=".memory-", suffix=".md.tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        delay = 0.02
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                # Judged here or the person's own: not a shell call's doing.
                settle(path, text)
                return
            except PermissionError:
                # Windows refuses a rename over a file another process holds open — a
                # scanner, the Settings memory view mid-read. The same short-lived
                # transient the secret store retries through (`secrets.py:92`); a real
                # permission problem still raises on the last attempt.
                if attempt == 4:
                    raise
                time.sleep(delay)
                delay *= 2
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (meta, body) for a topic file (`memory_format.split`: the nested
    shape, the earlier flat one, or both blocks at once). No frontmatter = all body."""
    return _fmt.split(text)


def _render_frontmatter(meta: dict) -> str:
    return _fmt.render(meta)


#: A markdown link inside a topic file — the `**Liên quan:**` body convention.
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _link_target(target: str) -> str:
    """The file name a link points at, anchors and directories stripped."""
    return target.split("#", 1)[0].strip().rsplit("/", 1)[-1]


#: Where previous versions of a memory file live (a subdirectory, so the listing and the
#: index never see them).
_HISTORY_DIR = ".history"
#: How many previous versions of one memory file are kept.
MAX_VERSIONS = 5


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def keep_version(path: Path, data: bytes) -> None:
    """Keep `data`, what `path` held, in `.history/`, with the newest `MAX_VERSIONS`.

    A rewritten memory is superseded, not erased: a store that overwrites loses what was
    believed before and when it changed (arXiv 2610.04794 measured one overwriting 12.7% of
    its entries). Best-effort — keeping a version must never block the write."""
    try:
        history = path.parent / _HISTORY_DIR
        history.mkdir(parents=True, exist_ok=True)
        (history / f"{path.name}.{_stamp()}.md").write_bytes(data)
        stale = sorted(history.glob(f"{path.name}.*.md"), key=lambda p: p.name)
        for old in stale[:-MAX_VERSIONS]:
            old.unlink(missing_ok=True)
    except OSError:
        return


def _snapshot(path: Path) -> None:
    """Copy the file's current bytes into `.history/` before a destructive change.

    Versioning is the one production guardrail the Anthropic memory talk names that this
    store did not have: memory can be edited by a person or an agent, and a bad edit (or
    a wrong delete) had no way back.
    """
    try:
        if path.is_file():
            keep_version(path, path.read_bytes())
    except OSError:
        return


#: A lock older than this is treated as a crashed writer's and stolen (stale locks are stolen
#: for the same reason: a `mkdir` lock has no owner to clean up after a kill).
_LOCK_STALE_SECONDS = 30.0

#: The optimistic-concurrency refusal, shared by every write path.
_CONFLICT = (
    "the file changed while this write was prepared - re-read it and try again"
)


@contextmanager
def _process_lock(path: Path):
    """A cross-process lock beside the file: `mkdir` is atomic on every platform.

    `_path_lock` serializes threads inside one process; this serializes PROCESSES (the
    sidecar and a CLI can share a state dir). Waits rather than failing — a stale lock
    from a crashed writer is stolen after `_LOCK_STALE_SECONDS`, so this cannot hang
    forever. A directory that cannot be locked (read-only parent) is not fatal: the
    atomic write still never publishes a half file, and `_digest` catches outsiders.
    """
    lock = path.parent / f".{path.name}.lock"
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > _LOCK_STALE_SECONDS:
                    lock.rmdir()
                    continue
            except OSError:
                continue
            time.sleep(0.05)
        except OSError:
            break
    try:
        yield
    finally:
        try:
            lock.rmdir()
        except OSError:
            pass


def _digest(path: Path) -> str:
    """Content hash of the file, or "" when it does not exist yet."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _entries_of(body: str, *, limit: int = 40, chars: int = 300) -> list[str]:
    """The file's blocks (blank-line separated), bounded.

    What the model needs to see to merge a full file in the same turn, without paying for
    the whole file in context: the live entries are returned on overflow for exactly this
    reason.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", body or "") if b.strip()]
    return [b[:chars] for b in blocks[:limit]]


_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalize(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


def _already_covered(body: str, text: str) -> bool:
    """True when `text` repeats something the file already holds.

    Exact after normalization, a long substring of the body, or >=0.85 word overlap with
    one existing block — the duplicate rules at the file layer. Every write is the model's
    own (no background pass writes memory), and this is how a file stops
    accumulating paraphrases.
    """
    new = _normalize(text)
    if not new:
        return True
    whole = _normalize(body)
    if len(new) >= 24 and new in whole:
        return True
    words = set(new.split())
    for block in re.split(r"\n\s*\n", body):
        old = _normalize(block)
        if not old:
            continue
        if old == new:
            return True
        old_words = set(old.split())
        if not old_words:
            continue
        overlap = len(words & old_words) / len(words | old_words)
        if overlap >= 0.85:
            return True
    return False


def _strip_links_to(text: str, names: set) -> str:
    """`text` without links to `names`; the relation line goes when nothing is left.

    Only lines naming `Liên quan` are touched — the convention is a body line, so a
    link is found and rewritten by its own shape, never by frontmatter parsing.
    """
    out: list[str] = []
    for line in text.splitlines():
        if "Liên quan" not in line:
            out.append(line)
            continue
        matches = list(_LINK_RE.finditer(line))
        if not matches:
            out.append(line)
            continue
        kept = [m for m in matches if _link_target(m.group(1)) not in names]
        if len(kept) == len(matches):
            out.append(line)
            continue
        if not kept:
            continue  # nothing left to relate to: drop the whole line
        prefix = line[: matches[0].start()].rstrip()
        out.append(prefix + " " + " · ".join(m.group(0) for m in kept))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _screened(index: str) -> str:
    """The index as the prompt may carry it: a line that reads like an instruction to the
    model is replaced by a visible marker (the file on disk is untouched, so the person can
    still see and fix it). The user index rides into EVERY project, so a page the model
    read once must not be able to plant a standing order there."""
    lines = []
    for line in index.splitlines():
        hits = threats.scan(line)
        lines.append(threats.marker(hits) if hits else line)
    return fence_injections("\n".join(lines))


class AutoMemory:
    """Read/write one machine-local memory directory (a project's, or the user's)."""

    def __init__(self, root: str | Path, scope: MemoryScope = PROJECT) -> None:
        self.root = Path(root)
        self.scope = scope
        #: The gate a MODEL's write passes (`gate.judge`): `(name, text)` to a
        #: refusal or None. Set where a session hands this directory to its tools; a
        #: person editing in Settings has none, because it is their memory.
        self.admit: Optional[Any] = None

    # -- directory ---------------------------------------------------------------

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.scope is PROJECT:
            _anchors.mark_current(self.root)
        return self.root

    def exists(self) -> bool:
        return self.root.is_dir()

    # -- name handling -----------------------------------------------------------

    def resolve_target(self, target: str) -> str:
        """``memory``/``index``/``MEMORY.md`` → the index; ``progress`` → the progress log;
        anything else → a topic file (``.md`` appended when missing)."""
        t = (target or "").strip()
        if t.lower() in ("", "memory", "index", "memory.md"):
            return INDEX_NAME
        if t.lower() in ("progress", "progress.md"):
            return PROGRESS_NAME
        name = t.replace("\\", "/")
        if not name.lower().endswith(".md"):
            name += ".md"
        return name

    def _path(self, name: str) -> Path:
        rel = (name or "").strip().replace("\\", "/")
        if not rel:
            raise ValueError("name required")
        if not _SAFE_TOPIC.match(rel):
            raise ValueError("name may only contain letters, digits, '.', '_' and '-'")
        root = self.root.resolve()
        path = (self.root / rel).resolve()
        if path != root and root not in path.parents:
            raise ValueError("path escapes the memory directory")
        return path

    # -- read --------------------------------------------------------------------

    def list_files(self) -> list[dict]:
        if not self.exists():
            return []
        out: list[dict] = []
        for p in sorted(self.root.glob("*.md")):
            if not p.is_file():
                continue
            name = p.name
            meta, body = _split_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
            st = p.stat()
            out.append(
                {
                    "name": name,
                    "title": self._title(name, body, meta.get("description", "")),
                    "description": meta.get("description", ""),
                    "type": meta.get("type", ""),
                    "modified_at": meta.get("modified")
                    or datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                    "size": st.st_size,
                    "injected": name == INDEX_NAME,
                }
            )
        out.sort(key=lambda f: (f["name"] != INDEX_NAME, f["name"]))
        return out

    def read(self, target: str) -> dict:
        name = self.resolve_target(target)
        path = self._path(name)
        if not path.is_file():
            return {"name": name, "exists": False, "content": ""}
        return {
            "name": name,
            "exists": True,
            "content": path.read_text(encoding="utf-8", errors="replace"),
        }

    # -- write -------------------------------------------------------------------

    def write(
        self, target: str, content: str, mode: str = "append", note_type: str = ""
    ) -> dict:
        text = content or ""
        if not text.strip():
            return {"ok": False, "error": "content required"}
        # A secret must never reach a memory file: they are plaintext on disk, read by
        # every later session, and re-sent to the provider on recall. The prompt already
        # says "never quote them", but a prompt is not a guard (the write path refuses at the
        # write path for exactly this; the pattern table is the compaction redactor's,
        # reused so one list of credential shapes stays authoritative).
        if redact_secrets(text) != text:
            return {
                "ok": False,
                "error": (
                    "refused: this looks like it carries a credential (an API key, token, "
                    "or private key). Memory files are plaintext and are replayed to the "
                    "model — save the fact without the secret, or record where it lives."
                ),
            }
        if len(text) > MAX_WRITE_CHARS:
            return {
                "ok": False,
                "error": (
                    f"content is {len(text)} chars, over the {MAX_WRITE_CHARS} limit — "
                    "split it across topic files."
                ),
            }
        name = self.resolve_target(target)
        path = self._path(name)
        overwrite = str(mode or "append").strip().lower() == "overwrite"

        with _path_lock(path), _process_lock(path):
            if name in (INDEX_NAME, PROGRESS_NAME):
                # The index and the progress log are plain lines, no frontmatter.
                before = _digest(path)
                if overwrite or not path.exists():
                    new = text
                else:
                    existing = path.read_text(encoding="utf-8", errors="replace")
                    sep = "" if existing.endswith("\n") else "\n"
                    new = f"{existing}{sep}{text}"
                if path.exists() and _digest(path) != before:
                    return {"ok": False, "error": _CONFLICT}
                refused = self.admit(name, new) if self.admit is not None else None
                if refused is not None:
                    return {"ok": False, "error": refused.reason, "rule": refused.rule}
                _snapshot(path)
                _atomic_write(path, new)
                res: dict = {
                    "ok": True,
                    "name": name,
                    "mode": "overwrite" if overwrite else "append",
                }
                if name == PROGRESS_NAME:
                    advice = _gate.progress_advice(new)
                    if advice:
                        res["warning"] = advice
                    return res
                over = self._index_over_limit()
                if over:
                    res["index_over_limit"] = True
                    res["warning"] = (
                        f"{INDEX_NAME} is now over its read limit ({over}). Only its first "
                        f"{self.scope.index_max_lines} lines / {self.scope.index_max_bytes} bytes are loaded. Rewrite "
                        "it as an index: one line per memory, move detail into topic files, and "
                        "merge or drop stale entries."
                    )
                else:
                    near = self._index_near_limit()
                    if near:
                        res["warning"] = (
                            f"{INDEX_NAME} is close to its read limit ({near}). Keep it an "
                            "index: one line per memory, move detail into topic files, and "
                            "merge or drop stale entries before the next write."
                        )
                return res

            # Topic file: manage frontmatter (type, modified) around the body.
            kind = (note_type or "").strip().lower()
            if kind and kind not in self.scope.types and kind not in LEGACY_TYPES:
                return {"ok": False, "error": f"type must be one of {', '.join(self.scope.types)}"}
            before = _digest(path)
            if path.exists():
                meta, body = _split_frontmatter(
                    path.read_text(encoding="utf-8", errors="replace")
                )
            else:
                meta, body = {}, ""
            # Content that carries its own frontmatter (the GUI editor saves a whole file;
            # a model writes its own frontmatter) is MERGED, not nested: wrapping it in a
            # second block is how 1-in-3 real files ended up with two (2026-09-24).
            incoming, text = _split_frontmatter(text)
            for key, value in incoming.items():
                if value:
                    meta[key] = value
            if not text.strip() and not incoming:
                return {"ok": False, "error": "content required"}
            meta["type"] = kind or meta.get("type") or self.scope.default_type
            meta["modified"] = _now_iso()
            meta.setdefault("name", Path(name).stem)
            if not meta.get("description"):
                meta["description"] = _fmt.describe(text if overwrite else (body or text))
            if not overwrite and body.strip() and _already_covered(body, text):
                # A duplicate add is dropped and the model is told, so it edits the
                # existing entry instead of piling on a near-copy.
                return {
                    "ok": True,
                    "name": name,
                    "type": meta["type"],
                    "action": "duplicate",
                    "note": (
                        "this fact is already covered in that file — edit the existing "
                        "entry to refine it, or write to a different topic"
                    ),
                }
            if overwrite or not body.strip():
                new_body = text
            else:
                sep = "" if body.endswith("\n") else "\n"
                new_body = f"{body}{sep}{text}"
            rendered = _render_frontmatter(meta) + new_body
            if len(rendered) > MAX_FILE_CHARS:
                # Overflow protocol: the refusal carries the live entries and a
                # same-turn instruction, so the model consolidates instead of guessing
                # what is in the file or retrying blind.
                return {
                    "ok": False,
                    "error": f"{name} is full (the write would exceed {MAX_FILE_CHARS} chars)",
                    "entries": _entries_of(body),
                    "instruction": (
                        "Consolidate now, in this turn: merge overlapping entries, delete "
                        "stale or least important ones (and their index line), then retry "
                        "this write. One memory is ONE fact - if these entries are "
                        "unrelated, split them across topic files instead."
                    ),
                }
            if path.exists() and _digest(path) != before:
                return {"ok": False, "error": _CONFLICT}
            refused = self.admit(name, rendered) if self.admit is not None else None
            if refused is not None:
                return {"ok": False, "error": refused.reason, "rule": refused.rule}
            _snapshot(path)
            _atomic_write(path, rendered)
            return {"ok": True, "name": name, "type": meta["type"]}

    def edit(self, target: str, old_string: str, new_string: str) -> dict:
        if not old_string:
            return {"ok": False, "error": "old_string required"}
        name = self.resolve_target(target)
        path = self._path(name)
        with _path_lock(path), _process_lock(path):
            if not path.is_file():
                return {"ok": False, "error": f"{name} does not exist — use write first"}
            text = path.read_text(encoding="utf-8", errors="replace")
            before = _digest(path)
            if old_string not in text:
                return {"ok": False, "error": "old_string not found"}
            new = text.replace(old_string, new_string or "", 1)
            # Refresh the modified stamp on a topic file (leave the plain files plain).
            if name not in (INDEX_NAME, PROGRESS_NAME):
                meta, body = _split_frontmatter(new)
                meta["modified"] = _now_iso()
                new = _render_frontmatter(meta) + body
            if len(new) > MAX_FILE_CHARS:
                return {
                    "ok": False,
                    "error": f"{name} would exceed the {MAX_FILE_CHARS} limit",
                }
            if _digest(path) != before:
                return {"ok": False, "error": _CONFLICT}
            # An edit is a write: it passes the same gate (it did not, so an edit
            # could take the evidence out of an admitted memory).
            refused = self.admit(name, new) if self.admit is not None else None
            if refused is not None:
                return {"ok": False, "error": refused.reason, "rule": refused.rule}
            _snapshot(path)
            _atomic_write(path, new)
            advice = _gate.progress_advice(new) if name == PROGRESS_NAME else ""
            return {"ok": True, "name": name, **({"warning": advice} if advice else {})}

    def delete(self, target: str) -> dict:
        name = self.resolve_target(target)
        path = self._path(name)
        with _path_lock(path), _process_lock(path):
            if not path.is_file():
                return {"ok": False, "error": f"{name} does not exist"}
            _snapshot(path)
            path.unlink()
        # A link to a file that no longer exists confidently misleads (the session that
        # chose the body-line convention recorded exactly this: "a wrong or dangling
        # related-link is worse than no link"), so every other file's relation line is
        # rewritten here, in the same operation as the delete.
        self.clean_links_to([name])
        return {"ok": True, "name": name}

    def forget(self, target: str) -> dict:
        """Delete a memory AND its index line — what Undo on a save notice means. `delete`
        leaves the index alone (the model maintains it); a person undoing a save should
        not have to."""
        res = self.delete(target)
        if not res.get("ok"):
            return res
        name = res["name"]
        index = self.root / INDEX_NAME
        with _path_lock(index), _process_lock(index):
            if index.is_file():
                text = index.read_text(encoding="utf-8", errors="replace")
                kept = [line for line in text.splitlines() if f"({name})" not in line]
                if len(kept) != len(text.splitlines()):
                    _snapshot(index)
                    _atomic_write(index, "\n".join(kept) + ("\n" if kept else ""))
        return res

    def clean_links_to(self, names: Iterable[str]) -> int:
        """Drop links to `names` from other topic files' `Liên quan` lines.

        Returns how many files were rewritten. The index is skipped (it lists files, it
        does not relate them) and so are the removed files themselves.
        """
        targets = {str(n).strip() for n in names if str(n).strip()}
        if not targets:
            return 0
        touched = 0
        for path in sorted(self.root.glob("*.md")):
            if path.name == INDEX_NAME or path.name in targets:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cleaned = _strip_links_to(text, targets)
            if cleaned != text:
                _snapshot(path)
                _atomic_write(path, cleaned)
                touched += 1
        return touched

    def versions(self, target: str) -> list[str]:
        """Snapshot names for one file, newest first (empty when none)."""
        name = self.resolve_target(target)
        history = self._path(name).parent / _HISTORY_DIR
        try:
            found = sorted(
                (p.name for p in history.glob(f"{name}.*.md")), reverse=True
            )
        except OSError:
            return []
        return found

    def restore(self, target: str, version: str = "") -> dict:
        """Put a snapshot back, newest by default. The current state is snapshotted
        first, so a restore is itself reversible."""
        name = self.resolve_target(target)
        path = self._path(name)
        history = path.parent / _HISTORY_DIR
        wanted = (version or "").strip()
        if wanted:
            source = history / wanted
            if not wanted.startswith(f"{name}.") or not source.is_file():
                return {"ok": False, "error": f"no such version for {name}"}
        else:
            found = self.versions(name)
            if not found:
                return {"ok": False, "error": f"{name} has no saved versions"}
            source = history / found[0]
        with _path_lock(path), _process_lock(path):
            _snapshot(path)
            try:
                _atomic_write(path, source.read_text(encoding="utf-8", errors="replace"))
            except OSError as exc:
                return {"ok": False, "error": f"restore failed: {exc}"}
        return {"ok": True, "name": name, "restored": source.name}

    def promote(self, name: str, *, workspace_root: str | Path) -> dict:
        """Copy one memory file into the project's `.agents/memory/` — the SHARED,
        committed half (memory v2 Phase 4).

        Promotion is a USER decision (a review surface calls this); nothing promotes
        itself, and the copy is one-way — the machine-local file stays canonical, the repo
        copy is a reviewable export stamped with `promoted_at`. The index is refused: it
        names machine-local files, which mean nothing in someone else's checkout."""
        source = self._path(self.resolve_target(name))
        if source.name == INDEX_NAME:
            return {
                "ok": False,
                "error": "MEMORY.md is an index of machine-local files — promote a topic "
                "file instead",
            }
        if not source.is_file():
            return {"ok": False, "error": f"{source.name} does not exist"}
        root = Path(workspace_root).expanduser().resolve()
        target_dir = root / ".agents" / "memory"
        target_dir.mkdir(parents=True, exist_ok=True)
        text = source.read_text(encoding="utf-8", errors="replace")
        meta, body = _split_frontmatter(text)
        if meta:
            meta["promoted_at"] = _now_iso()
            text = _render_frontmatter(meta) + body
        target = target_dir / source.name
        _atomic_write(target, text)
        return {"ok": True, "name": source.name, "target": str(target)}

    def save_memory(self, *, title: str, description: str, note_type: str, body: str) -> dict:
        """One memory: a new `<slug>.md` with name/description/type
        frontmatter, then ONE index line for it. For the one-time move of the old SQLite
        store (`migrate_user`); the model writes the same two things with its tools."""
        kind = (note_type or self.scope.default_type).strip().lower()
        if kind not in self.scope.types and kind not in LEGACY_TYPES:
            return {"ok": False, "error": f"type must be one of {', '.join(self.scope.types)}"}
        self.ensure()
        stem = _fmt.slug(title or description or body)
        name = _fmt.unique_name(self.root, stem)
        meta = {"name": Path(name).stem, "description": description or _fmt.describe(body)}
        res = self.write(name, _fmt.render(meta) + body.strip() + "\n", "overwrite", kind)
        if not res.get("ok"):
            return res
        self.add_index_line(name, title or meta["description"], meta["description"])
        return {"ok": True, "name": name}

    def add_index_line(self, name: str, title: str, hook: str) -> dict:
        """Append `- [Title](name) — hook` to MEMORY.md unless the file is already listed."""
        index = self.read(INDEX_NAME)
        if f"({name})" in index.get("content", ""):
            return {"ok": True, "name": INDEX_NAME, "action": "already_indexed"}
        line = f"- [{' '.join(title.split())[:80]}]({name}) — {' '.join(hook.split())}"
        return self.write(INDEX_NAME, line, mode="append")

    def upgrade(self) -> dict:
        """Migrate this directory to the current file format, once (`memory_format`), move
        aside what a removed background pass left in it, and set aside a project index from
        before memories carried evidence (`anchors`)."""
        retired = _fmt.retire_removed(self.root) if self.root.is_dir() else []
        done = _fmt.upgrade_dir(
            self.root, reserved=(INDEX_NAME, PROGRESS_NAME, "PROPOSALS.md", _gate.LEGACY_INDEX_NAME)
        )
        if retired:
            done["retired"] = retired
        if self.scope is PROJECT:
            done["set_aside"] = _anchors.retire_legacy_index(self.root)
        return done

    # -- search ------------------------------------------------------------------

    def search(self, query: str, limit: int = MAX_SEARCH_HITS) -> list[dict]:
        q = (query or "").strip().lower()
        if not q or not self.exists():
            return []
        cap = max(1, min(int(limit or MAX_SEARCH_HITS), 100))
        hits: list[dict] = []
        for p in sorted(self.root.glob("*.md")):
            if not p.is_file():
                continue
            for i, line in enumerate(
                p.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if q in line.lower():
                    hits.append({"name": p.name, "line": i, "text": line.strip()[:200]})
                    if len(hits) >= cap:
                        return hits
        return hits

    # -- prompt side -------------------------------------------------------------

    def _index_text(self) -> str:
        path = self.root / INDEX_NAME
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def _index_over_limit(self) -> Optional[str]:
        """A short description of how the index exceeds the load cap, else None."""
        text = self._index_text()
        lines = text.count("\n") + (1 if text else 0)
        size = len(text.encode("utf-8"))
        parts = []
        cap_lines, cap_bytes = self.scope.index_max_lines, self.scope.index_max_bytes
        if lines > cap_lines:
            parts.append(f"{lines} lines > {cap_lines}")
        if size > cap_bytes:
            parts.append(f"{size} bytes > {cap_bytes}")
        return " and ".join(parts) if parts else None

    def _index_near_limit(self) -> Optional[str]:
        """How close the index is to either cap, while still under it, else None."""
        text = self._index_text()
        lines = text.count("\n") + (1 if text else 0)
        size = len(text.encode("utf-8"))
        parts = []
        cap_lines, cap_bytes = self.scope.index_max_lines, self.scope.index_max_bytes
        if lines >= cap_lines * INDEX_NEAR_LIMIT:
            parts.append(f"{lines}/{cap_lines} lines")
        if size >= cap_bytes * INDEX_NEAR_LIMIT:
            parts.append(f"{size}/{cap_bytes} bytes")
        return " and ".join(parts) if parts else None

    def inject_block(self, *, via_files: bool = False) -> str:
        """The index only, capped exactly as the session will load it. Topic files are not
        included — the agent reads those on demand (with `read_file` when the memory
        directory is a root; with the `auto_memory` tool otherwise)."""
        text = self._index_text().strip()
        if not text:
            return ""
        lines = text.splitlines()
        truncated = False
        cap_lines, cap_bytes = self.scope.index_max_lines, self.scope.index_max_bytes
        if len(lines) > cap_lines:
            lines = lines[:cap_lines]
            truncated = True
        body = "\n".join(lines)
        if len(body.encode("utf-8")) > cap_bytes:
            body = body.encode("utf-8")[:cap_bytes].decode("utf-8", "ignore")
            truncated = True
        if truncated:
            body = body.rstrip() + "\n… (index truncated at its read limit)"
        # Each line whose memory stands on a file that changed or is gone says so (0108).
        body = _anchors.annotate(_screened(body), self.root)
        if via_files:
            header = (
                f"Contents of {self.root / INDEX_NAME} (your memory index for this project; "
                "the files it names are in the same directory — read them with `read_file`):"
            )
        else:
            header = (
                "Auto memory (your own notes for this project, kept as markdown outside the "
                "workspace). This is the INDEX; topic files it names are read with the "
                "`auto_memory` tool:"
            )
        return f"{header}\n\n{body}"

    def progress_block(self, *, via_files: bool = False) -> str:
        """The progress log as a new session is shown it: whole when short, else its
        headings and open items with the file named to read, and the day the harness last saw it
        written. Work that spans sessions resumes from it instead of re-exploring;
        `gate.judge_progress` keeps an unchecked item from being marked done."""
        path = self.root / PROGRESS_NAME
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return ""
        body, whole = _progress.shown(text, limit=PROGRESS_MAX_CHARS)
        written = str(_anchors.entry_of(self.root, PROGRESS_NAME).get("written") or "")[:10]
        when = f", last written {written}" if written else ""
        if whole:
            return (
                f"Contents of {path} (the progress log of work that spans sessions{when}; resume "
                "from it, and check what it calls done before building on it):\n\n"
                f"{_screened(body)}"
            )
        how = "`read_file`" if via_files else "the `auto_memory` tool (`read`, target `progress`)"
        shown = f"Its headings and open items:\n\n{_screened(body)}" if body else "It has no headings or open items."
        return (
            f"The progress log of work that spans sessions is {path} ({len(text):,} characters{when}). "
            f"{shown}\n\nRead the whole log with {how} before you resume or build on work it lists, "
            "and check what it calls done before building on it."
        )

    def _title(self, name: str, body: str, description: str = "") -> str:
        for line in (body or "").splitlines():
            s = line.strip()
            if s.startswith("#"):
                return s.lstrip("#").strip() or Path(name).stem
        return description or Path(name).stem


_META = dict(category="memory", risk_level="low", capabilities=["remember", "files"])

_OFF = (
    "Auto memory is turned off in Settings (Settings ▸ Memory ▸ Auto memory). Nothing "
    "was written — say so instead of implying you saved it."
)

_GUIDE = (
    "Keep MEMORY.md an index: one line per memory (a title and its topic-file path, or a "
    "one-line fact). Put detail in topic files. When the index is over its read limit the "
    "tool says so — rewrite it then."
)


def auto_memory_tools(files: AutoMemory, *, saving_enabled=None) -> list:
    """The agent's door into auto memory. One tool with an ``action`` (Claude's memory-tool
    shape). Writes consult ``saving_enabled`` LIVE; reads never gate."""

    def _off() -> bool:
        return saving_enabled is not None and not saving_enabled()

    # Per-turn cap: after three refusals on the same file, stop asking the model
    # to consolidate — a fragile loop must not burn the turn. Reset on any successful
    # write or when the target changes (the tools are built per session, which is the
    # granularity this needs).
    full_streak = {"n": 0, "target": ""}

    def auto_memory(
        action: str,
        target: str = "",
        content: str = "",
        mode: str = "append",
        type: str = "",
        old_string: str = "",
        new_string: str = "",
        query: str = "",
    ) -> dict:
        """Read and write your own long-term auto memory for this project (machine-local
        markdown). One memory is one file holding one fact. MEMORY.md is the INDEX shown
        to you every session; topic files are read on demand.

        Save (two steps, both required): (1) write a topic file named for its fact
        (e.g. "no-db-mocks.md"), (2) add ONE index line to "memory":
        "- [Title](no-db-mocks.md) — a specific one-line hook". Keep MEMORY.md
        under 200 lines; update an existing memory rather than duplicating it.

        Args:
            action (str): "read" | "write" | "edit" | "delete" | "list" | "search".
            target (str): "memory" (the MEMORY.md index) or a topic filename like
                "no-db-mocks.md".
            content (str): Text to write (for "write"). One fact per file.
            mode (str): "append" (default) or "overwrite" (for "write").
            type (str): Type of a topic file: "user", "feedback", "project" or
                "reference".
            old_string (str): Exact text to replace (for "edit").
            new_string (str): Replacement text (for "edit").
            query (str): Words to look for (for "search").
        """
        act = (action or "").strip().lower()
        try:
            if act == "list":
                return {"files": files.list_files(), "guide": _GUIDE}
            if act == "read":
                found = files.read(target)
                if found.get("exists") and found["name"] not in _gate.RESERVED:
                    _anchors.note_read(files.root, found["name"])  # what is used
                return found
            if act == "search":
                return {"matches": files.search(query)}
            if act in ("write", "edit", "delete"):
                if _off():
                    return {"ok": False, "error": _OFF}
                if act == "write":
                    result = files.write(target, content, mode, type)
                    if result.get("ok"):
                        full_streak["n"] = 0
                        full_streak["target"] = ""
                    elif result.get("entries") is not None:
                        if full_streak["target"] != target:
                            full_streak["target"] = target
                            full_streak["n"] = 0
                        full_streak["n"] += 1
                        if full_streak["n"] >= 3:
                            result = {
                                "ok": False,
                                "error": (
                                    f"{target} is full and three consolidation attempts "
                                    "did not fit — save skipped this turn. Tell the user "
                                    "which fact could not be saved."
                                ),
                            }
                    return result
                if act == "edit":
                    return files.edit(target, old_string, new_string)
                return files.delete(target)
            return {
                "error": (
                    f"unknown action {action!r} — use read, write, edit, delete, list or search"
                )
            }
        except ValueError as e:
            return {"error": str(e)}

    return [ai.tool(auto_memory, metadata=ai.ToolMetadata(**_META))]
