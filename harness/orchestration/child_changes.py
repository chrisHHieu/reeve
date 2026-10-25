"""What a delegated child changed, for the parent that delegated it (orchestration audit O3).

The parent used to receive only the child's own last message. The child's turns already
keep an objective record — each turn's receipt is persisted into its transcript as a
`changes` notice — so the parent is handed that record next to the report, and
can check a "done" against the files that actually moved without spending a turn.

Read from the transcript rather than from the child's `ChangeLog`, which forgets a turn
when the next one begins: a child answering a structured-output correction runs a second
turn, and the first turn's edits are the ones that matter.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

#: Files named in the parent's block. A child that rewrote a whole tree is summarised past
#: this — the count stays exact, the list does not grow without bound.
_MAX_LISTED = 40


def changed_files(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """`[{path, change, added, removed}]` over every `changes` notice in `messages`.

    `change` is `created`, `modified` or `deleted`. A path touched in several turns keeps
    its first kind (a file this child created is still a creation when it edits it again)
    and sums its counts.
    """
    out: dict[str, dict[str, Any]] = {}
    for message in messages:
        if message.get("role") != "notice" or message.get("kind") != "changes":
            continue
        receipt = message.get("changes")
        if not isinstance(receipt, dict):
            continue
        kinds = {
            **{p: "modified" for p in receipt.get("overwritten") or []},
            **{p: "deleted" for p in receipt.get("deleted") or []},
            **{p: "created" for p in receipt.get("created") or []},
        }
        stats = {
            s.get("path"): s for s in receipt.get("stats") or [] if isinstance(s, dict)
        }
        for path in list(kinds) + [p for p in stats if p not in kinds]:
            if not isinstance(path, str) or not path:
                continue
            entry = out.setdefault(
                path,
                {"path": path, "change": kinds.get(path, "modified"), "added": 0, "removed": 0},
            )
            stat = stats.get(path) or {}
            entry["added"] += int(stat.get("added") or 0)
            entry["removed"] += int(stat.get("removed") or 0)
    return list(out.values())


def changes_block(
    files: list[dict[str, Any]],
    *,
    overlaps: Optional[list[dict[str, str]]] = None,
    worktree: Optional[dict[str, Any]] = None,
    unisolated: str = "",
) -> str:
    """The `<changes>` block the parent model reads inside the task envelope.

    Always a block, even for no files: "this task changed no files" is the fact that
    exposes a child reporting a fix it never made.
    """
    lines = ["<changes>"]
    if files:
        lines.append(f"Files this task changed ({len(files)}):")
        for f in files[:_MAX_LISTED]:
            counts = " ".join(
                part
                for part in (
                    f"+{f['added']}" if f.get("added") else "",
                    f"-{f['removed']}" if f.get("removed") else "",
                )
                if part
            )
            lines.append(f"- {f['change']} {f['path']}" + (f" ({counts})" if counts else ""))
        if len(files) > _MAX_LISTED:
            lines.append(f"- … and {len(files) - _MAX_LISTED} more")
    else:
        lines.append("This task changed no files.")
    for overlap in overlaps or []:
        lines.append(
            f"Warning: {overlap['path']} was also changed by task {overlap['task_id']} in "
            "this turn, in the same folder — check the two edits did not overwrite each other."
        )
    if worktree:
        lines.append(
            f"Its work is kept in the git worktree {worktree.get('path')}"
            + (f" on branch {worktree['branch']}" if worktree.get("branch") else "")
            + " — it is NOT in your workspace yet. The user is offered Apply and Discard for "
            "it; do not merge or copy it yourself unless they ask, and say that it is waiting."
        )
    if unisolated:
        # Isolation was ASKED for and could not be set up: the caller planned on
        # a separate copy, and the child wrote into the shared folder instead.
        lines.append(
            f"Note: the worktree asked for could not be set up ({unisolated}), so this task "
            "worked in your own folder — its changes are already there."
        )
    lines.append("</changes>")
    return "\n".join(lines)
