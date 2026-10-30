"""What the agent has actually LOOKED AT — and whether the file still looks like that.

Two ways an edit goes wrong silently, and neither is a permission question:

**Editing a file it never read.** `write_file` rewrites a path whole. A model that never
read the file writes what it GUESSED was there, and the guess overwrites real content. The
call succeeds, the result says the path was written, and nothing in the transcript looks
wrong until someone opens the file.

**Editing a file that changed since it looked.** The agent reads `config.py`, runs a build
that rewrites it, then edits from the version in its context. The edit lands on top of
changes it never saw. Same shape: a successful call that destroyed something.

`undo.py` can take either one back, but only once a person notices. This refuses first, and
the two belong together for the reason undo's own docstring gives: approval asks a person
to PREDICT, undo asks them to RECOGNISE, and this asks neither — it is a question the
runtime can answer by itself, from facts it already has.

The file-observation policy ("observed-state + read-before-edit +
version-guarded write/edit"). reeve's write tools come from aisuite's toolkit and
carry no guard at all, so there was nothing here to reconcile with.

Three properties decide whether this is worth having:

**It stores a digest, never content.** The bytes already exist on disk and in the
transcript; a third copy is a third thing that can be wrong, and this one would go stale
by design.

**It fails OPEN on its own ignorance, and CLOSED only on knowledge.** A file it cannot
hash, or one observed by a previous process, is not evidence of a stale edit — refusing
there would block work to defend a check that never ran. It blocks only when it can say
which fact was violated.

**A file that does not exist is not a file it failed to read.** Creating is always allowed;
there was nothing to have looked at.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .coverage import Coverage

#: Tools whose success means the agent has now SEEN this file, mapped to the argument that
#: names it. Reading is the only way to observe; a tool that merely mentions a path (grep's
#: match list, `list_files`) has shown the agent the NAME, not the contents, and treating
#: that as a read is exactly the guess this module exists to refuse.
READ_TOOLS: dict[str, str] = {"read_file": "path"}

#: Above this, `_digest` gives up rather than hashing. A source file is kilobytes; a file
#: this size is a database or an artifact, and neither is something a model read into its
#: context to edit by hand. The read-before-edit rule still applies to it — only the
#: version comparison is skipped, and it says so by returning None.
MAX_DIGEST_BYTES = 8 * 1024 * 1024

_CHUNK = 1024 * 1024

#: Recorded for a file the agent read in an EARLIER process (seeded from the transcript on
#: resume). The read is a fact — it is in the history the model still carries — but what
#: the file looked like then is not recoverable, so the version comparison has nothing to
#: compare against and is skipped. Distinct from "never read", which blocks.
UNVERIFIED = ""

#: Names for the two refusals, carried on the pipeline's `Block` so the model-facing
#: error says WHICH policy stopped the call (`blocked by read-before-edit: …`) instead of
#: the bare "tool call blocked" that session 435e0be4 retried five times. Stable,
#: content-free names: a rule must never leak file contents into the transcript.
RULE_NEVER_READ = "read-before-edit"
RULE_STALE = "stale-file"
#: A read that covered only part of the file. The window's text is what the model has in
#: context, so an edit to the REST of the file is written from a guess — the same failure
#: `RULE_NEVER_READ` refuses, one step later. Refused only when the file would have fitted
#: in one read (`FULL_READ_MAX_LINES`); past that, refusing would make a large file
#: uneditable by any tool that passes through this gate.
RULE_PARTIAL_VIEW = "partial-read"
#: A file whose read is no longer in the model's context: a compaction or a fold replaced
#: the turns that held it. The read is a fact about the session and no longer a
#: fact about what the model can see, and a whole-file write is written from what it sees.
RULE_OUT_OF_VIEW = "out-of-view"

#: The reader's own window ceiling (`tools/files.py::_DEFAULT_MAX_LINES`). A file at or
#: under this could have been read whole in one call, so a partial view of it is a choice,
#: not a constraint. If the reader's default moves, this number moves with it.
FULL_READ_MAX_LINES = 2_000


def _digest(target: Path) -> Optional[str]:
    """The file's content hash, or None when it cannot be one.

    None is not a failure to report: every caller reads it as "no comparison is possible
    here", which is the honest answer for an unreadable file, a directory, or something
    too large to hash.
    """
    try:
        if not target.is_file():
            return None
        if target.stat().st_size > MAX_DIGEST_BYTES:
            return None
        h = hashlib.sha256()
        with open(target, "rb") as f:
            while chunk := f.read(_CHUNK):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, ValueError):
        return None


def _next_step(shown: bool, region: Optional[tuple[int, int]], why: str) -> str:
    """What a refusal tells the model to do. It used to be "read it again", and a
    model that read a window again was refused again (session 90e83048-f57: 30 lines of 181
    before a whole-file write), so a write that replaces the file asks for all of it."""
    if shown:
        return (" It is shown below as it stands now, whole: write your change against "
                "that text, not against the version you remember.")
    if region is not None:
        return f" Read that part first: {why}"
    return f" Read the whole file first, with no line limit, since this call replaces all of it: {why}"


@dataclass
class ObservedFiles:
    """Per-session memory of which files the agent has seen, and as what.

    `resolve` is injected rather than imported: one canonical key per file already exists
    in `provenance.resolve`, and a second spelling of the same rule here is how `./a.py`
    and `a.py` end up as two entries that disagree.
    """

    resolve: Callable[[str], str]
    #: The filesystem behind `resolve`'s keys — the remote world's backend for an SSH
    #: session, None for the local disk. It must answer `digest(key)`, `stat_kind(key)`
    #: (`f`/`d`/`e`/`n`); the local path stays exactly as it was, byte for byte.
    access: Optional[Any] = None
    _seen: dict[str, str] = field(default_factory=dict)
    #: path -> what has been seen of it, line by line (see `Coverage`). Cleared for a file
    #: whose digest changed: the old windows describe a version that no longer exists.
    _coverage: dict[str, Coverage] = field(default_factory=dict)
    #: Files seen whose text has since left the model's context (`leave_view`).
    _cut: set[str] = field(default_factory=set)
    #: While a message's calls run: the files that were out of view when that message was
    #: written (`calls_written`). None between messages, when `_cut` is the answer.
    _frame: Optional[set[str]] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- the filesystem behind the keys --------------------------------------------

    def _digest(self, key: str) -> Optional[str]:
        if self.access is not None:
            return self.access.digest(key)
        return _digest(Path(key))

    def _kind(self, key: str) -> str:
        """`f`ile, `d`ir, `e`xists-other, or `n`othing — never raises."""
        if self.access is not None:
            try:
                return self.access.stat_kind(key)
            except Exception:  # noqa: BLE001 — an unanswerable host is not evidence
                return "n"
        try:
            target = Path(key)
            if target.is_file():
                return "f"
            if target.is_dir():
                return "d"
            return "e" if target.exists() else "n"
        except OSError:
            return "n"

    # -- recording ----------------------------------------------------------------

    def _line_count(self, key: str) -> Optional[int]:
        """How many lines the file has, or None when this world cannot say cheaply.

        Counted the READER's way (`read_file` iterates lines, so a file ending in a
        newline has N lines and not N+1) — the number a person sees in a refusal has to
        match the number the read reported, or the message contradicts the tool.

        None means "no judgement", the same fail-open reading `_digest`'s None gets: a
        remote backend has no line-count call, and inventing one to police a guard that
        only matters for local edits would be the tail wagging the dog.
        """
        if self.access is not None:
            return None
        try:
            raw = Path(key).read_bytes()
        except (OSError, ValueError):
            return None
        return raw.count(b"\n") + (0 if raw.endswith(b"\n") else 1)

    def observe(
        self,
        raw: str,
        *,
        covered: Optional[tuple[int, int]] = None,
        total: Optional[int] = None,
        digest: Optional[str] = None,
    ) -> None:
        """Note that the agent has just seen this file as it currently stands.

        Called after a successful read AND after a successful write: a file the agent
        just wrote is one it knows the contents of exactly, and requiring it to read back
        its own write would be a round-trip to learn what it already said.

        `covered`/`total` are the reader's own report of its window (`start_line`/
        `end_line`/`total_lines`). A write passes neither, which marks the coverage full —
        the agent produced the whole content.

        `digest` is the version seen, when the caller holds it: a refusal that showed the
        file records what it showed, so a change since then still reads as one.
        """
        try:
            key = self.resolve(raw)
        except Exception:  # noqa: BLE001 — an unresolvable path is simply not recorded
            return
        digest = self._digest(key) if digest is None else digest
        with self._lock:
            self._cut.discard(key)  # seen again: its text is in the context once more
            if self._frame is not None:
                self._frame.discard(key)
            if digest is not None:
                previous = self._seen.get(key)
                self._seen[key] = digest
                if covered is None:
                    self._coverage[key] = Coverage(full=True)
                else:
                    if previous != digest:
                        self._coverage.pop(key, None)  # a new version: old windows are void
                    entry = self._coverage.setdefault(key, Coverage())
                    entry.add(int(covered[0]), int(covered[1]))
                    if total:
                        entry.total = int(total)
            elif self._kind(key) == "f":
                # Seen, but not hashable — too large, or unreadable. The read-before-edit
                # fact is still TRUE and must be recorded; only the version comparison has
                # nothing to compare. Recording nothing here was worse than useless: a file
                # over `MAX_DIGEST_BYTES` could then never be edited at all, because every
                # read of it left the gate still saying "you have not read it". Caught by
                # its own test rather than in the field, which is the point of writing one
                # that can fail.
                self._seen[key] = UNVERIFIED
            # A file that vanished between the call and this record keeps whatever it had:
            # dropping the observation would turn a successful delete into "never read".

    def in_view_keys(self) -> list[str]:
        """The paths read or written whose text is still in the model's context: everything
        seen, less what a cut took out (`leave_view`). Read by the nested-AGENTS pass
        (`loop/turn_stamps._stamp_nested_agents`): a directory the context has left is not
        its business until a file there is read or written again."""
        with self._lock:
            return [key for key in self._seen if key not in self._cut]

    def observe_unverified(self, raw: str) -> None:
        """Note a read this process did not witness — see `UNVERIFIED`."""
        try:
            key = self.resolve(raw)
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            self._seen.setdefault(key, UNVERIFIED)

    def leave_view(self, keep: Optional[Iterable[str]]) -> int:
        """The model's context was cut (a compaction, a fold): every file seen so far is
        out of view, except those read or written in what was kept. Returns how many are.

        Every guard here rests on one premise, stated where a resumed session is seeded:
        the read is still in the model's context. A cut is the moment that stops being
        true, and nothing told this store (the read tracker is reset at the same
        boundary, `reset_file_dedup`)."""
        kept: set[str] = set()
        if keep is None:  # no cut stands: everything seen is in view
            with self._lock:
                self._cut = set()
            return 0
        for raw in keep:
            try:
                kept.add(self.resolve(str(raw)))
            except Exception:  # noqa: BLE001 — an unresolvable path keeps nothing
                continue
        with self._lock:
            self._cut = set(self._seen) - kept
            return len(self._cut)

    def calls_written(self) -> None:
        """A message's calls are about to run: they are judged against the view that
        message was written from, whatever happens to the view while they run.

        The calls of one message are composed together and run one after another. A cut
        that lands between two of them (a model's own fold, sent with the write that
        starts its next piece of work) did not change what the model saw when it wrote the
        second. Both `out-of-view` refusals in the store by 2026-10-06 were that."""
        with self._lock:
            self._frame = set(self._cut)

    def settle_view(self) -> None:
        """The message's calls are done: the next message is written from the view as it
        now stands."""
        with self._lock:
            self._frame = None

    # -- the rule -----------------------------------------------------------------

    def blame(
        self, raw: str, *, region: Optional[tuple[int, int]] = None, shown: bool = False
    ) -> tuple[str, str]:
        """(rule, reason) for refusing this edit, or ("", "") when it may proceed.

        The rule is what the pipeline's `Block` carries; the reason is what the model
        reads. Kept as one method rather than two so the answer to "why" and the answer
        to "which policy" can never disagree. `shown` is whether the refusal carries the
        file as it stands, which changes what the model is told to do next.

        `region` is the line span this edit actually touches, when the caller could locate
        it (a `replace_in_file` whose `old` text was found). A covered region is enough:
        the guard exists so nobody edits text they have not seen, and text they HAVE seen
        needs no second read — which is the difference the first version of this got wrong
        (session 1030c218-40a). Without a region the whole file has to be covered, because
        a whole-file rewrite touches everything.
        """
        try:
            key = self.resolve(raw)
        except Exception:  # noqa: BLE001
            return ("", "")  # cannot key it, cannot judge it
        kind = self._kind(key)
        if kind == "n":
            return ("", "")  # creating: there was nothing to have read
        if kind == "d":
            return ("", "")  # not this module's question
        with self._lock:
            seen = self._seen.get(key)
            coverage = self._coverage.get(key)
            cut = key in (self._cut if self._frame is None else self._frame)
        if seen is not None and cut:
            return (
                RULE_OUT_OF_VIEW,
                f"what you read of {raw} is no longer in your context: the turns that held "
                "it were folded or compacted."
                + _next_step(shown, region, "a whole file written from memory replaces "
                             "whatever you do not remember."),
            )
        if seen is None:
            return (
                RULE_NEVER_READ,
                f"{raw} already exists and you have not read it in this session."
                + _next_step(shown, region, "writing from a guess overwrites content you "
                             "have not seen."),
            )
        if seen is UNVERIFIED or seen == UNVERIFIED:
            return ("", "")
        current = self._digest(key)
        if current is None or current == seen:
            # Not stale. The question is only whether the text being touched was seen.
            if coverage is not None and not coverage.is_full():
                if region is not None and coverage.covers(*region):
                    return ("", "")  # exactly the lines the agent read
                lines = self._line_count(key)
                # Past the reader's window ceiling the whole file CANNOT be read in one
                # call, so refusing would make it uneditable by any gated tool. The rule
                # yields to the constraint and the digest check still applies.
                if region is None and lines is not None and lines > FULL_READ_MAX_LINES:
                    return ("", "")
                where = (
                    f"lines {region[0]}-{region[1]} are not in a window you read"
                    if region is not None
                    else f"it has {lines if lines is not None else 'more'} lines and you "
                    "read only part of it"
                )
                return (
                    RULE_PARTIAL_VIEW,
                    f"you have not read the part of {raw} this edit touches ({where})."
                    + _next_step(shown, region, "an edit written against text you have "
                                 "not seen would discard whatever is there."),
                )
            return ("", "")
        return (
            RULE_STALE,
            f"{raw} has changed on disk since you read it — something else wrote to it "
            "(a command you ran, a build, or the user)."
            + _next_step(shown, region, "the version in your context is no longer the one "
                         "on disk, and an edit written against it would discard whatever changed."),
        )

    def verdict(self, raw: str) -> Optional[str]:
        """Why this file may not be edited, or None when it may.

        The string reaches the model verbatim, so it names the file the way the model
        named it and says what to do next — a refusal the model cannot act on costs the
        same turn as the mistake it prevented.
        """
        return self.blame(raw)[1] or None
