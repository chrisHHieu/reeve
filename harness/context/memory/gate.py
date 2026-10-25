"""What may enter a project's memory.

A memory is one file per fact, written by the agent doing the work and indexed by one line in
`MEMORY.md`. Five types:

    user       who the person is: role, expertise, preferences
    feedback   guidance they gave on how to work, corrections and confirmed approaches, with why
    project    ongoing work, goals or constraints the code and git do not record, dates absolute
    lesson     what was tried here and failed, the exact error, what fixed it and why
    reference  where something outside the repo lives

Whether a note is worth keeping is the agent's to judge, and the prompt says how. The gate holds
what can be checked mechanically: no secret, no instruction aimed at the model, a type, a body, a
size a note can have, no second file with an indexed description, an index of pointers to files
that exist. What a note stands on is the harness's to record, not the model's to declare
(`admission.py`, `anchors.py`): when it was written, the files it names, whether it was read again.

The progress log (`PROGRESS.md`) is the one file that is not a memory: a checklist for work that
spans sessions, loaded whole into each new one. It is held to a size, and an item is not marked
done while the turn's own work is unchecked (`judge_progress`): a log that says done before the
check ran is what the next session builds on.

An earlier gate required evidence by kind instead (a file for a decision, the person's quoted words for a
correction) and a 1,500-character body. No real session saved a memory in the day it was live.

Pure: text in, a `Refusal` or None out. The callers are the two doors a model writes through — the
tools (`tools/memory_admission.py`) and the `auto_memory` tool (`AutoMemory.admit`). A person
editing in Settings is not gated: it is their memory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from harness.safety.redaction import redact_secrets
from . import memory_format as _fmt
from . import threats

#: The types a memory may be.
KINDS = ("user", "feedback", "project", "lesson", "reference")
#: The earlier format's types. Notes that carry one stay readable and editable by the person; a model
#: rewriting one gives it a type from `KINDS`.
EARLIER_KINDS = ("decision", "dead-end", "how-to", "correction")
#: One memory is one fact. Past this a note is a document: the largest note in the memory the
#: owner compared this one with, a research log of one day, is 10.8k characters.
BODY_MAX_CHARS = 12_000
#: The files of a memory directory that are not memories.
INDEX_NAME = "MEMORY.md"
LEGACY_INDEX_NAME = "MEMORY.legacy.md"
PROGRESS_NAME = "PROGRESS.md"
RESERVED = frozenset((INDEX_NAME, LEGACY_INDEX_NAME, PROGRESS_NAME))
#: The progress log is shown to every new session of the project, whole while it is short
#: (`progress_log.INLINE_CHARS`), and may not grow past this.
PROGRESS_MAX_CHARS = 6_000
#: Where the log is told to shed detail, before it is full: a write past this is saved and its
#: result says how close the log is (`progress_advice`). With only the hard cap, a log sat at
#: 6,000 and every update tipped it over by a few characters: one session was refused 26 times in
#: 15 rounds, each time trimmed just enough to pass (2026-10-18).
PROGRESS_PRUNE_CHARS = 4_800

_INDEX_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s#]+)\)")
_DONE_RE = re.compile(r"^\s*[-*]\s*\[[xX]\]\s*(.+?)\s*$")

_KINDS_HELP = (
    "`user` (who the user is), `feedback` (guidance they gave, with why), `project` (ongoing "
    "work, goals or constraints the code and git do not record), `lesson` (what was tried and "
    "failed here, what fixed it and why) or `reference` (where something outside the repo lives)"
)


@dataclass(frozen=True)
class Refusal:
    """A write that was not admitted. `reason` is the sentence the model reads: it says what
    to change, because a refusal that cannot be acted on costs the same step as the mistake."""

    rule: str
    reason: str


def anchors_of(meta: dict[str, str]) -> list[str]:
    """The paths a memory's frontmatter names (`anchors: a.py, tests/test_a.py`)."""
    raw = str(meta.get("anchors") or meta.get("anchor") or "")
    return [part.strip().strip("`'\"") for part in re.split(r"[,;]", raw) if part.strip().strip("`'\"")]


def kind_of(meta: dict[str, str]) -> str:
    return str(meta.get("type") or "").strip().lower()


def _folded(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def _unsafe(text: str, what: str) -> Optional[Refusal]:
    if redact_secrets(text) != text:
        return Refusal(
            "memory-secret",
            f"this looks like it carries a credential. {what} is plaintext and is sent to the "
            "model in later sessions: save where the secret lives, not the secret.",
        )
    if threats.first_threat(text):
        return Refusal(
            "memory-threat",
            f"this reads like an instruction to the model, not a fact. {what} is replayed into "
            "later sessions, so it is not saved.",
        )
    return None


def judge(
    name: str,
    text: str,
    *,
    anchor: Callable[[str], Optional[Path]] = lambda raw: None,
    index_text: str = "",
) -> Optional[Refusal]:
    """Why this memory file may not be saved as `text`, or None when it may. `anchor(raw)` is
    the path inside one of the session's folders that `raw` names, or None."""
    unsafe = _unsafe(text, "A memory file")
    if unsafe is not None:
        return unsafe
    meta, body = _fmt.split(text)
    kind, body = kind_of(meta), body.strip()
    if kind not in KINDS:
        said = f"`{kind}` is not one" if kind else "this file names none"
        return Refusal(
            "memory-kind",
            f"a memory says its type under `metadata:` as `type:`, and {said}. The types: "
            f"{_KINDS_HELP}.",
        )
    if not body:
        return Refusal("memory-one-fact", "the memory has no body: write the fact under the frontmatter.")
    if len(body) > BODY_MAX_CHARS:
        return Refusal(
            "memory-one-fact",
            f"the body is {len(body):,} characters, over {BODY_MAX_CHARS:,}: one memory is one "
            "fact. Split it into files that each hold one, or keep what a later session needs.",
        )
    missing = [raw for raw in anchors_of(meta) if anchor(raw) is None]
    if missing:
        return Refusal(
            "memory-anchor",
            f"`anchors:` names {', '.join(missing)}, which is not a file or directory in this "
            "session's folders. Name the path as you would read it, or leave `anchors:` out.",
        )
    hook = _folded(meta.get("description") or "")
    if hook:
        for line in (index_text or "").splitlines():
            linked = _INDEX_LINK_RE.search(line)
            if linked and linked.group(1) != name and hook in _folded(line):
                return Refusal(
                    "memory-duplicate",
                    f"{linked.group(1)} is already indexed with this description. Update that "
                    "file instead of adding another.",
                )
    return None


def done_items(text: str) -> set[str]:
    """The items a progress log marks done (`- [x] …`), by their text."""
    return {_folded(m.group(1)) for line in (text or "").splitlines() if (m := _DONE_RE.match(line))}


def progress_advice(text: str) -> str:
    """What a saved progress log past `PROGRESS_PRUNE_CHARS` is told, or "": prune now, once and
    well, instead of being refused a few characters over the cap at the next update."""
    if len(text) <= PROGRESS_PRUNE_CHARS:
        return ""
    return (
        f"The progress log is {len(text):,} of the {PROGRESS_MAX_CHARS:,} characters it may hold. "
        "Before the next update, move the detail of finished items into a `project` memory and "
        f"keep what is next and what is blocked, so it is back under {PROGRESS_PRUNE_CHARS:,}."
    )


def judge_progress(text: str, *, before: str = "", unsettled: str = "") -> Optional[Refusal]:
    """Why the progress log may not read `text`, or None.

    `unsettled` is the engine's own state (`_work_unsettled`): `unverified` while the
    turn holds an edit nothing ran after, `check_failed` while its last check failed. An item
    newly marked done then is a claim the harness can see is unproven."""
    unsafe = _unsafe(text, "The progress log")
    if unsafe is not None:
        return unsafe
    if len(text) > PROGRESS_MAX_CHARS:
        return Refusal(
            "memory-progress",
            f"the progress log is {len(text):,} characters, {len(text) - PROGRESS_MAX_CHARS:,} over the "
            f"{PROGRESS_MAX_CHARS:,} it may hold. Bring it under {PROGRESS_PRUNE_CHARS:,} so the next "
            "updates fit: keep what is next and what is blocked; drop the detail of finished items, "
            "or move what must be kept into a `project` memory.",
        )
    newly = done_items(text) - done_items(before)
    if newly and unsettled:
        why = (
            "this turn's last check failed"
            if unsettled == "check_failed"
            else "this turn changed code and nothing has run since"
        )
        return Refusal(
            "memory-unverified",
            f"{len(newly)} item(s) are marked done, and {why}. Run the check that proves the "
            "work, then mark it: the next session builds on what this log says is done.",
        )
    return None


def judge_index(text: str, *, names: set[str]) -> Optional[Refusal]:
    """Why the index may not read `text`, or None. An index line points at a memory file."""
    if redact_secrets(text) != text:
        return Refusal("memory-secret", "the index would carry a credential. Say where it lives instead.")
    if _fmt.split(text)[0]:
        return Refusal("memory-index", f"{INDEX_NAME} is a list of one-line pointers and has no frontmatter.")
    for line in text.splitlines():
        if not line.lstrip().startswith("- "):
            continue
        linked = _INDEX_LINK_RE.search(line)
        if linked is None:
            return Refusal(
                "memory-index",
                f"an index line points at a memory file: `- [Title](file.md) — hook`. This one "
                f"links nothing: {' '.join(line.split())[:80]!r}. Write the fact as a file first.",
            )
        target = linked.group(1)
        if "/" not in target and "\\" not in target and target.endswith(".md") and target not in names:
            return Refusal(
                "memory-index",
                f"the index names {target}, which is not in the memory directory. Write the file, "
                "then its line.",
            )
    return None


__all__ = [
    "BODY_MAX_CHARS",
    "EARLIER_KINDS",
    "INDEX_NAME",
    "KINDS",
    "LEGACY_INDEX_NAME",
    "PROGRESS_MAX_CHARS",
    "PROGRESS_NAME",
    "RESERVED",
    "Refusal",
    "anchors_of",
    "done_items",
    "judge",
    "judge_index",
    "judge_progress",
    "PROGRESS_PRUNE_CHARS",
    "progress_advice",
    "kind_of",
]
