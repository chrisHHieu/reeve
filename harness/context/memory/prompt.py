"""What the model is told about the memory it can write.

One directory: this project's. The model reads and writes it with the ordinary file tools (ADR
0058), or with the `auto_memory` tool where the session has no local folder; one text for both.

The text asks for what the measurements say a later session can use: the exact
command, value, path and error and not a paraphrase of them, a fix with its reason, and the files
a note is about named where the harness can see them. It does not ask the model to keep what the
harness keeps for it (dates, what changed, what was read). What the gate refuses (`gate.py`) is
named here in the gate's own terms, so a refusal is never the first a model hears of a rule; the
sizes are the gate's to say when a write misses one.

What holds in every project is the person's own Instructions file, which no tool writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Collection, Optional

from .auto_memory import PROJECT
from .gate import PROGRESS_MAX_CHARS
from .progress_log import INLINE_CHARS


def memory_section(
    project_dir: Optional[Path],
    *,
    can_delete: bool = True,
    via_tool: bool = False,
    tools: Optional[Collection[str]] = None,
) -> str:
    """The instruction for this project's memory directory, or "" when the session has none.

    `can_delete`: the session holds `apply_patch`, the one file tool that deletes. Without it a
    memory is retired by taking its line out of the index, which is what decides what loads.
    `via_tool`: the session reaches the directory through the `auto_memory` tool only.
    `tools`: the names the session holds, when known; the text then names no tool outside them,
    and `can_delete` is read from it."""
    if project_dir is None:
        return ""
    held = None if tools is None else set(tools)
    if held is not None:
        can_delete = "apply_patch" in held
    asking = [name for name in ("run_shell", "run_code") if held is None or name in held]
    asks = (
        f"; `{asking[0]}` and `{asking[1]}` ask"
        if len(asking) == 2
        else f"; `{asking[0]}` asks"
        if asking
        else ""
    )
    if via_tool:
        where = (
            "This project has a memory that outlives the session, kept outside the workspace. "
            "Read and write it with the `auto_memory` tool (`list`, `read`, `write`, `edit`, "
            "`delete`; target `memory` is the index, `progress` the progress log)."
        )
        retire = "delete one that turned out wrong (`auto_memory` `delete`) and take its line out"
        check = "Read the index before you add to it"
        progress = "the `progress` target"
    else:
        where = (
            f"This project has a memory that outlives the session: markdown files in "
            f"`{project_dir}`, written with `write_file` (the directory exists; writes there run "
            "without asking)."
        )
        retire = (
            "delete one that turned out wrong (`apply_patch`, `*** Delete File: <path>`) and take "
            "its line out"
            if can_delete
            else "retire one that turned out wrong by taking its line out (a file the index does "
            "not name is never loaded)"
        )
        check = f"Read the index before you add to it (reading the directory runs without asking{asks})"
        progress = "`PROGRESS.md` in the same directory"
    return f"""\
# Memory

{where} A later session knows of this one only what is saved there and what the repository \
shows. One file holds one fact:

```markdown
---
name: <short-kebab-case-slug>
description: <one line: what a later session would look for it by>
metadata:
  type: user | feedback | project | lesson | reference
---

<the fact>
```

- `user`: who the user is: their role, what they know, how they like to work.
- `feedback`: what they told you about how to work, corrections and approaches they confirmed. \
Add **Why:** and **How to apply:**.
- `project`: work under way, goals, constraints and decisions that the code and git do not \
record, with dates written as dates. Add **Why:** and **How to apply:**.
- `lesson`: something that cost time here: what was tried, the error as it appeared, what fixed \
it, and why.
- `reference`: where something outside the repository lives.

Write the exact thing: the command as it was run, the value, the error text. A later session can \
act on `pytest -q tests/api` and cannot act on "run the tests". Name the files a note is about in \
backticks: the harness watches them and tells a later session when one changed. Leave out what \
the repository already records (its structure, its history, its instruction files) and what only \
this conversation needs. Never save a secret.

Then add `- [Title](file.md) — hook` to `MEMORY.md`, the index loaded each session \
({PROJECT.index_max_lines} lines). {check}; update a file rather than add a second one, and \
{retire}. The harness adds what it knows to an index line (the date of ongoing work, a file that \
changed since, a note written from web content, a note nobody has read for a month): act on it, \
by checking, updating or dropping the note.

Work that will take more than one session keeps a progress log in {progress}: a checklist \
(`- [ ]`, `- [x]`), what is next, what is blocked. A new session is shown it whole up to \
{INLINE_CHARS:,} characters, and past that only its headings and open `- [ ]` items, so what is \
next goes in as an item and the detail of finished work goes in a `project` memory \
({PROGRESS_MAX_CHARS:,} characters at most). Do one item at a time and mark it done only after the \
check that proves it has run: a done mark while this turn's edit is unchecked is refused.

What the user wants in EVERY project goes in their Instructions in Settings, which you cannot \
write: tell them the line."""


__all__ = ["memory_section"]
