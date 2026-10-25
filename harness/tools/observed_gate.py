"""The read-before-edit gate as a pipeline check and a recorder (see `observed.py` for
the rules).

Split from the store when the module crossed the architecture ceiling: `observed.py` owns
WHAT is known about a file and WHEN an edit is refused; this module owns the two seams that
ask it — a `check` (asked before the call is approved, and again at dispatch)
and a `tools/post-execute` recorder.

The failure policies differ on purpose and are documented at each: the gate is a policy
and therefore fail-closed at dispatch (a crash refuses the call), the recorder is
bookkeeping and swallows (a failure to record degrades to one extra read later).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional

from .edit_matchers import find_matches
from .observed import READ_TOOLS, ObservedFiles


# -- pipeline listeners --------------------------------------------------------------


def observation_gate(observed: ObservedFiles, locate: Any) -> Any:
    """A pipeline `check` that refuses an unsafe edit.

    A check rather than a pre-execute listener: the engine asks it before the
    approval card, so nobody is asked to allow an overwrite of a file the model never read
    — an approval this gate would then have refused anyway (found live, 2026-09-26).

    `locate(name, arguments) -> (paths, located)` is injected for the same reason
    `snapshot_listener` injects it: `write_paths` lives in the permission layer, and a
    tool module reaching into permissions for one lookup inverts the dependency.

    NOTE ON FAILURE POLICY, and it differs from `snapshot_listener` on purpose. That one
    is not a gate, so it swallows everything and lets the call through — a backup that
    failed is no reason to refuse the work. **This one IS a gate**, so it inherits the
    stage's fail-closed rule: a policy that crashed cannot be read as permission to
    overwrite a file. It does not catch, and `ToolPipeline.dispatch` turns a raise into a
    block. What it does NOT treat as a failure is its own ignorance — see `verdict`.
    """

    def check(execution: Any) -> Any:
        arguments = execution.arguments or {}
        paths, located = locate(execution.name, arguments)
        if located:
            blocked: list[tuple[str, str, str]] = []
            for raw in paths:
                if _matched_edit(execution.name, str(raw), arguments):
                    continue
                region = _edit_region(execution.name, raw, arguments, observed.resolve)
                rule, reason = observed.blame(str(raw), region=region)
                if reason:
                    blocked.append((str(raw), rule, reason))
            if blocked:
                from .pipeline import Block

                return Block(_one_reason(blocked), rule=blocked[0][1])
        return None

    return check


def _matched_edit(name: str, raw: str, arguments: dict[str, Any]) -> bool:
    """Whether this edit of `raw` only replaces text it quotes.

    `replace_in_file` and an `apply_patch` `Update File` hunk change nothing but the text
    they match exactly, so an edit written from memory fails to match and changes nothing;
    the gate added a refusal and a read, never safety — 12 blocks in 36 sessions of the
    2026-09-25 audit, each after the model had seen the file through `sed -n` (W12). A
    whole-file write and a patch that adds over or deletes an existing file keep the gate.
    """
    if name == "replace_in_file":
        return True
    if name != "apply_patch":
        return False
    wanted = raw.replace("\\", "/")
    verbs: set[str] = set()
    for line in str(arguments.get("patch") or "").splitlines():
        for verb in ("Update", "Add", "Delete"):
            prefix = f"*** {verb} File:"
            if line.startswith(prefix):
                path = line[len(prefix):].strip().replace("\\", "/")
                if path and (wanted.endswith("/" + path) or wanted == path):
                    verbs.add(verb)
    return verbs == {"Update"}


def _one_reason(blocked: list[tuple[str, str, str]]) -> str:
    """Every blocked path of one edit, in one refusal.

    The gate used to stop at the first: a 21-file `apply_patch` over 14 unread files named
    one (live eval, deepseek-v4.1-flash, 2026-09-24), so a model that did not guess the
    rest would pay one refusal and one read per file. The first reason is kept whole; the
    others are named by path, and by rule when theirs differs.
    """
    raw, rule, reason = blocked[0]
    if len(blocked) == 1:
        return reason
    same = [path for path, r, _ in blocked[1:] if r == rule]
    other = [f"{path} ({r})" for path, r, _ in blocked[1:] if r != rule]
    parts = [reason]
    if same:
        parts.append("The same applies to: " + ", ".join(same) + ".")
    if other:
        parts.append("Also blocked: " + ", ".join(other) + ".")
    return " ".join(parts)


def observation_recorder(observed: ObservedFiles, locate: Any) -> Any:
    """A `tools/post-execute` listener that records what the agent has now seen.

    Reads and writes both count, and both only on success: a read that failed showed
    nothing, and a write that failed changed nothing. Blocked calls never reach this stage
    at all (`ToolPipeline.dispatch` returns before the post waterfall), so a refusal here
    cannot record the observation that would let the retry through.

    Observing must never cost a call, so unlike the gate this one swallows: hashing is
    bookkeeping, and a failure to record degrades to one extra read later.
    """

    async def listener(execution: Any, result: Any, nxt: Any) -> Any:
        out = await nxt()
        try:
            if getattr(out, "ok", False):
                for raw, covered, total in _observed_entries(execution, out, locate):
                    observed.observe(raw, covered=covered, total=total)
        except Exception:  # noqa: BLE001 — see the note above
            pass
        return out

    return listener


def _read_window(value: Any) -> Optional[tuple[tuple[int, int], int]]:
    """((start, end), total) from a reader's own report, or None when it carries none.

    The reader reports its window (`start_line`/`end_line`/`total_lines`), so the answer is
    a fact rather than a guess; a result without those fields (an older shape, a string
    value) records a FULL observation — fail-open, the module's rule for its own ignorance.
    """
    if not isinstance(value, dict):
        return None
    try:
        start = int(value.get("start_line") or 1)
        end = int(value.get("end_line") or 0)
        total = int(value.get("total_lines") or 0)
    except (TypeError, ValueError):
        return None
    if not total:
        return None
    return (start, end), total


def _edit_region(
    name: str, raw: str, arguments: dict[str, Any], resolve: Any = None
) -> Optional[tuple[int, int]]:
    """The line span a `replace_in_file` touches, when it can be located.

    Locating it is what keeps the guard from over-refusing: the agent may edit text it has
    read even when it has not read the whole file (session 1030c218-40a read the window it
    was editing and was refused anyway). The same matcher ladder the editor uses finds the
    text, so "where does this edit land" has one answer in the codebase rather than two.

    Unknown for every other tool: a whole-file write or a patch touches everything, and
    the caller then requires the whole file to be covered.
    """
    if name != "replace_in_file":
        return None
    old = arguments.get("old")
    if not isinstance(old, str) or not old:
        return None
    target = raw
    if resolve is not None:
        try:
            target = resolve(raw)  # the canonical key the observation store uses
        except Exception:  # noqa: BLE001 — fall back to the path as given
            target = raw
    try:
        text = Path(target).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    matches, _via = find_matches(
        text.replace("\r\n", "\n"), old.replace("\r\n", "\n"), expected=1
    )
    if not matches:
        return None  # not found (or ambiguous): the editor will say so, not this guard
    match = matches[0]
    start = text.count("\n", 0, match.start) + 1
    # The LAST character of the match, not the offset past it: a match ending exactly at a
    # newline belongs to the line that newline terminates.
    last = max(match.start, match.end - 1)
    end = text.count("\n", 0, last) + 1
    return start, end


def _observed_entries(
    execution: Any, result: Any, locate: Any
) -> Iterable[tuple[str, Optional[tuple[int, int]], int]]:
    """Every file this successful call means the agent has now seen, and how well.

    A read is `(path, (start, end), total)`; a write is `(path, None, 0)` — the agent
    produced the whole content, so nothing is a guess.
    """
    arguments = execution.arguments or {}
    argument = READ_TOOLS.get(execution.name)
    if argument is not None:
        value = arguments.get(argument)
        if not value:
            return []
        window = _read_window(getattr(result, "value", None))
        if window is None:
            return [(str(value), None, 0)]
        return [(str(value), window[0], window[1])]
    paths, located = locate(execution.name, arguments)
    return [(str(p), None, 0) for p in paths] if located else []


