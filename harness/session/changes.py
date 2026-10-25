"""What a turn actually changed, from the engine's own records.

At the end of a turn the person is handed a paragraph the model wrote about its own work.
"I converted all 200 files" is a claim, not a receipt — and someone who does not write
software has no way to check it. It is the same asymmetry item 2.4 was built for: approval
asks them to PREDICT, and prose asks them to TRUST. A receipt asks them to RECOGNISE, which
is the only one of the three anybody can actually do.

Everything needed for a receipt is already known here and nowhere else. The engine saw
which calls ran, which of them succeeded, which paths they touched, and — because a
pre-execute listener looks first — whether each of those paths existed a moment earlier.
That last one is the difference between "wrote 5 files" and "created 3, overwrote 2", and
it is the half a person actually reacts to.

Two rules:

**It reports what HAPPENED, never what was asked.** Only calls that came back ok are
counted, which is why recording is split: the listener notes the intent, and the engine
confirms it after the result. A proposal that failed changed nothing and must not appear.

**It never blocks.** Same policy as `tools/undo.py`, and the opposite of
`tools/intents.py`: a receipt that could not be written is no reason to refuse the work.
The failure mode is a missing line, which is exactly today's behaviour.

Deliberately NOT here: any per-tool knowledge of what a send or a command means. That
already exists as the tool's own `present_call` contract, in the user's language, and a
second vocabulary would be a second thing to keep in step.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from harness.orchestration import worktree_state
from harness.tools.file_diff import _MAX_HUNK_CHARS, _count_lines, _diff_counts, _read_capped, hunk_dicts, hunk_weight

@dataclass(frozen=True)
class _Pending:
    """One call, noted before it ran."""

    created: tuple[str, ...] = ()
    overwritten: tuple[str, ...] = ()
    did: str = ""  # the tool's own sentence, for a non-file action


@dataclass
class FileStat:
    """One file's churn this turn — the `+120 −0` on the receipt — plus what it takes
    to undo the last write that touched it."""

    path: str
    added: int = 0
    removed: int = 0
    call_id: str = ""
    undoable: bool = False
    #: "shell" when the change was read from git rather than from a tool that named the
    #: path — a command or a program wrote it, and there is no per-call before-image to undo.
    source: str = ""


@dataclass
class TurnChanges:
    """The receipt for one turn. Empty means nothing consequential happened."""

    created: list[str] = field(default_factory=list)
    overwritten: list[str] = field(default_factory=list)
    did: list[str] = field(default_factory=list)
    stats: list[FileStat] = field(default_factory=list)
    #: Files the turn removed. Only a command can do that today, so only git reports it.
    deleted: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.created or self.overwritten or self.did or self.deleted)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.created:
            out["created"] = self.created
        if self.overwritten:
            out["overwritten"] = self.overwritten
        if self.did:
            out["did"] = self.did
        if self.deleted:
            out["deleted"] = self.deleted
        if self.stats:
            out["stats"] = [
                {
                    "path": s.path,
                    "added": s.added,
                    "removed": s.removed,
                    "call_id": s.call_id,
                    "undoable": s.undoable,
                    **({"source": s.source} if s.source else {}),
                }
                for s in self.stats
            ]
        return out


class ChangeLog:
    """Per-turn record of what the agent changed.

    `locate` is `permissions.write_paths` and `describe` renders a call through its own
    contract — both injected rather than imported, so this module depends on neither the
    permission layer nor the registry for one lookup each. Same reason
    `snapshot_listener` takes `write_paths` as an argument.
    """

    def __init__(
        self,
        *,
        locate: Callable[[str, dict[str, Any]], Any],
        describe: Callable[[str, dict[str, Any]], str],
        consequential: Callable[[str], bool],
        resolve: Optional[Callable[[str], str]] = None,
        undoable: Optional[Callable[[str], bool]] = None,
        root: Optional[str] = None,
        opaque: Optional[Callable[[str], bool]] = None,
    ) -> None:
        # A model writes `notes.txt`, not an absolute path. Without a resolver this asked
        # the wrong question — does `notes.txt` exist relative to THIS PROCESS's cwd —
        # and answered "created" about a file it had just overwritten. A receipt that is
        # confidently wrong is worse than none, which is the whole reason this exists.
        # Caught by one real turn; every test until then passed absolute paths.
        self._resolve = resolve or (lambda raw: raw)
        self._locate = locate
        self._describe = describe
        self._consequential = consequential
        # Whether a call can be undone — consulted at summary time, when the undo store
        # is long since attached to the engine. Optional: a surface without undo shows
        # the churn without the button.
        self._undoable = undoable
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._turn = TurnChanges()
        # Per-path turn-start state, captured the FIRST time this turn touches a path:
        # the before-image the stats are diffed against at summary time. Later writes to
        # the same path must not move the baseline, or a two-call edit would report only
        # the second call's churn.
        self._before: dict[str, Optional[str]] = {}
        # Per-CALL before-state, keyed (call_id, path) and captured on EVERY touch. A
        # different question from `_before` and the reason it exists: the receipt asks
        # "what did this turn change", while a call's diff card asks "what did THIS call
        # change" — and for the second write to one path in a turn, the turn baseline
        # would credit the first call's lines to the second call's card.
        self._call_before: dict[tuple[str, str], Optional[str]] = {}
        self._calls: dict[str, str] = {}
        # A tool that can change files without naming them (a shell, a program) leaves
        # nothing for `note` to locate. For those, the workspace's git state is read before
        # the turn's first such call and again at the end (G4) — see `worktree_state`.
        self._root = root
        self._opaque = opaque
        self._worktree: Optional[worktree_state.Snapshot] = None
        self._worktree_taken = False

    # -- recording ----------------------------------------------------------------

    def begin_turn(self) -> None:
        with self._lock:
            self._pending.clear()
            self._turn = TurnChanges()
            self._before.clear()
            self._call_before.clear()
            self._calls.clear()
            self._worktree = None
            self._worktree_taken = False

    def wants_snapshot(self, tool_name: str) -> bool:
        """Whether this call is the turn's first that may change files it does not name —
        the one moment the git snapshot is taken, so a turn that only edits through the file
        tools never pays for git. Cheap; never raises."""
        if self._worktree_taken or not self._root or self._opaque is None:
            return False
        try:
            return bool(self._opaque(tool_name))
        except Exception:
            return False

    def watch(self, tool_name: str) -> None:
        """Take the workspace's git snapshot when `wants_snapshot` says so. Never raises."""
        if not self.wants_snapshot(tool_name):
            return
        self._worktree_taken = True
        try:
            self._worktree = worktree_state.take(self._root)
        except Exception:
            self._worktree = None

    def note(self, call_id: str, tool_name: str, arguments: dict[str, Any]) -> None:
        """Look at the world BEFORE the call. Never raises — the pipeline stage this runs
        in fails closed, and a receipt is not a gate."""
        if not call_id:
            return
        created: list[str] = []
        overwritten: list[str] = []
        did = ""
        try:
            paths, located = self._locate(tool_name, arguments or {})
            if located and paths:
                for raw in paths:
                    full = self._resolve(str(raw))
                    (overwritten if Path(full).exists() else created).append(full)
            elif self._consequential(tool_name):
                # Not a write: let the tool say what it does, in the words the approval
                # card and the transcript already use.
                did = self._describe(tool_name, arguments or {})
        except Exception:
            return
        if created or overwritten or did:
            with self._lock:
                self._pending[call_id] = _Pending(
                    created=tuple(created), overwritten=tuple(overwritten), did=did
                )
                # The stats baseline: what each path looked like before THIS TURN first
                # touched it. Captured now, before any write lands — later notes of the
                # same path leave the baseline where it is.
                #
                # The same read also serves the per-call baseline, which is why the file
                # is read whenever there is any prior content to read rather than only on
                # the first touch: a card describes one call, so its baseline has to be
                # this call's.
                for full in (*created, *overwritten):
                    current = _read_capped(full) if full in overwritten else None
                    if full not in self._before:
                        self._before[full] = current
                    self._call_before[(str(call_id), full)] = current
                for full in (*created, *overwritten):
                    self._calls[full] = call_id

    def confirm(self, call_id: str) -> None:
        """Promote a noted call to the receipt. Called ONLY for a call that came back ok:
        a proposal that failed changed nothing."""
        if not call_id:
            return
        with self._lock:
            entry = self._pending.pop(str(call_id), None)
            if entry is None:
                return
            for path in entry.created:
                if path not in self._turn.created:
                    self._turn.created.append(path)
            for path in entry.overwritten:
                # A path created earlier in the same turn and written again is still a
                # creation as far as the person is concerned — the file was not theirs.
                if path in self._turn.created or path in self._turn.overwritten:
                    continue
                self._turn.overwritten.append(path)
            if entry.did and entry.did not in self._turn.did:
                self._turn.did.append(entry.did)

    # -- reading ------------------------------------------------------------------

    def summary(self) -> TurnChanges:
        with self._lock:
            out = TurnChanges(
                created=list(self._turn.created),
                overwritten=list(self._turn.overwritten),
                did=list(self._turn.did),
            )
            # The churn per file, diffed against the turn-start snapshot — read once
            # per path, now, because a later turn may change the file again. A file
            # that no longer exists (written, then deleted by the same turn) has no
            # current state to describe and gets no numbers.
            for path in (*out.created, *out.overwritten):
                call_id = self._calls.get(path, "")
                stats = FileStat(
                    path=path,
                    call_id=call_id,
                    undoable=bool(call_id and self._undoable and self._undoable(call_id)),
                )
                current = _read_capped(path)
                if current is not None:
                    baseline = self._before.get(path)
                    stats.added, stats.removed = _diff_counts(baseline, current)
                out.stats.append(stats)
            before = self._worktree
        if before is not None:
            try:
                self._add_unnamed(out, before)
            except Exception:
                pass  # never blocks: the named files stand on their own
        return out

    def _add_unnamed(self, out: TurnChanges, before: worktree_state.Snapshot) -> None:
        """The files a command changed, from git: whatever moved since the snapshot and is
        not already on the receipt through a tool that named it."""
        after = worktree_state.take(self._root)
        if after is None:
            return
        named = [*out.created, *out.overwritten]
        for change in worktree_state.changes(before, after):
            if any(worktree_state.same_path(change.path, p) for p in named):
                continue
            stat = FileStat(path=change.path, source="shell")
            if change.kind == "deleted":
                out.deleted.append(change.path)
                if change.known:
                    stat.removed = _count_lines(change.before) or 0
                out.stats.append(stat)
                continue
            (out.created if change.kind == "created" else out.overwritten).append(change.path)
            # A file whose before-image was too large to keep is listed without numbers:
            # "+0 −0" would claim a precision the receipt never had.
            current = _read_capped(change.path)
            if current is not None and "\x00" in current:
                current = None  # binary: listed, but it has no lines to count
            if change.known and current is not None:
                stat.added, stat.removed = _diff_counts(change.before, current)
                out.stats.append(stat)

    def hunks(self, call_id: str) -> list[dict[str, Any]]:
        """What THIS call changed, as `{path, old_text, new_text}` regions for its card.

        The engine asks for these while recording the call's result, and hands them to the
        tool's presenter as card metadata — the presenter stays pure, and the filesystem
        read happens here, in the module that already reads before/after state.

        Empty for a call that touched no file, for a path that is gone now (written and
        deleted by the same call), for a file past the size cap, and for a change that
        changed nothing. All four mean the same thing to a card: no diff to draw, so the
        row keeps the plain text it would have had.
        """
        if not call_id:
            return []
        with self._lock:
            baselines = {
                path: before
                for (owner, path), before in self._call_before.items()
                if owner == str(call_id)
            }
        out: list[dict[str, Any]] = []
        for path, baseline in baselines.items():
            current = _read_capped(path)
            if current is None:
                continue
            hunks = hunk_dicts(path, baseline, current, _MAX_HUNK_CHARS - hunk_weight(out))
            if hunks is None:
                return []
            out.extend(hunks)
        return out

    def preview(self, tool_name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        """The hunks a whole-file write WILL produce, read before it is approved (U3).

        `write_file`'s presenter may not read the file it overwrites, so its call card showed
        every line as added and then shrank to the real diff once the call ran. This is the
        change actually being consented to, as `hunks` will report it. Empty (keep the tool's
        own card) for any other tool, a new, unreadable or oversized file, a create-only
        write, or no change. Never raises.
        """
        try:
            content = (arguments or {}).get("content")
            if (
                tool_name != "write_file"
                or not isinstance(content, str)
                or arguments.get("overwrite") is False
            ):
                return []
            full = self._resolve(str(arguments.get("path") or ""))
            if not Path(full).is_file():
                return []
            current = _read_capped(full)
            if current is None:
                return []
            return hunk_dicts(full, current, content, _MAX_HUNK_CHARS) or []
        except Exception:
            return []


def change_listener(log: ChangeLog) -> Any:
    """Pre-execute listener: look at the world before the call touches it.

    NOTE ON FAILURE POLICY. `ToolPipeline._run_pre` fails CLOSED, and two of the three
    listeners on it want that (`intent_listener` depends on it). This one must NOT inherit
    it: a receipt that could not be written is no reason to refuse the work, exactly as a
    backup that failed is no reason to refuse a write. `note` swallows everything, and
    this wrapper is the second belt.
    """

    async def listener(execution: Any, nxt: Any) -> Any:
        try:
            # Off the event loop: the first command of a turn reads git, and a large
            # repository can take a moment to answer.
            if log.wants_snapshot(execution.name):
                await asyncio.to_thread(log.watch, execution.name)
        except Exception:
            pass
        try:
            log.note(execution.call_id, execution.name, execution.arguments or {})
        except Exception:
            pass
        return await nxt()

    return listener
