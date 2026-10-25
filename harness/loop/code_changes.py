"""Telling a code change from other writes, and whether a command exercised it.

The verification gate asks two things: did this turn change code (a note in `notes.md` is not a code
change), and has something run since that exercises it.
"""

from __future__ import annotations

import re
from typing import Any

from harness.safety.risk import WRITE_TOOLS

# Tools whose success counts as evidence that a change was exercised.
EVIDENCE_TOOLS = {"run_shell", "run_code", "terminal_send", "shell_task_output"}

# Every tool that can edit a file. This was `{"write_file"}` until the gate was audited and the hole
# found: `replace_in_file`, which `write_file`'s own description tells the model to prefer for a
# fragment edit, and both patch tools set nothing. The most common edit path in the product could
# change a `.py` and finish with the gate silent. A gate watching one of four doors is not a gate.
# The risk table's own list: a fifth write tool joins both or neither.
CODE_WRITE_TOOLS = WRITE_TOOLS

# How far into a patch payload paths are looked for. The payload is written by the model and has no
# bound, while the answer to "does this touch code?" is settled by the first few headers.
PATCH_SCAN_LINES = 4_000

# Where the patch tools name their files: the envelope lines of `apply_patch`, plus a unified diff's
# `---` and `+++` headers. Both diff headers matter, for different cases: an addition's target is
# only on the `+++` side, a deletion's only on the `---` side (its `+++` is `/dev/null`), and a
# rename has one on each. Scanning one side would report a deleted module as untouched.
PATCH_FILE_PREFIXES = ("*** Update File:", "*** Add File:", "*** Delete File:")

CODE_SUFFIXES = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs", ".go", ".java",
    ".kt", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".sql",
    ".sh", ".bash", ".ps1", ".psm1", ".bat", ".cmd", ".ipynb",
)

# Commands that look at a file and do not run it. Naming the changed file in one of these is not
# evidence that the change works.
VIEWERS = (
    "cat", "type", "get-content", "gc", "head", "tail", "less", "more", "sed", "awk",
    "grep", "rg", "findstr", "select-string", "sls", "wc", "ls", "dir", "echo", "diff",
    "git diff", "git show", "git log", "git status", "git add", "git blame",
)


def code_paths(name: str, arguments: Any) -> list[str]:
    """The files a write tool aimed at, which decide whether the gate applies.

    Two of the four write tools name their target as an argument. The two patch tools name it inside
    the payload, one header line per file, so their paths are read out of the text. That is the only
    way to answer "did this touch code?" for them, and it is safe to rely on: a patch tool reports
    success only once its envelope parsed, and `_note_tool_outcome` never looks at a failed call."""
    if not isinstance(arguments, dict):
        return []
    named = arguments.get("path")
    if isinstance(named, str):
        return [named]
    payload = arguments.get("patch") if name == "apply_patch" else arguments.get("diff")
    if not isinstance(payload, str):
        return []
    found: list[str] = []
    for line in payload.splitlines()[:PATCH_SCAN_LINES]:
        text = line.strip()
        for prefix in PATCH_FILE_PREFIXES:
            if text.startswith(prefix):
                found.append(text[len(prefix) :].strip())
        if text.startswith(("+++ ", "--- ")):
            target = text[4:].split("\t")[0].strip()
            if target[:2] in ("a/", "b/"):
                target = target[2:]
            if target and target != "/dev/null":
                found.append(target)
    # An unchanged path appears in both diff headers. The answer is a boolean, but a caller reading
    # the list should not have to know that.
    return list(dict.fromkeys(found))


def is_code_write(name: str, arguments: Any) -> bool:
    """A write whose target has a code suffix: the gate covers code and not prose."""
    return any(path.lower().endswith(CODE_SUFFIXES) for path in code_paths(name, arguments))


def exercises_change(name: str, arguments: Any, stems: "set[str]") -> bool:
    """Whether a successful evidence-tool call counts as having exercised the code change.

    Any `run_shell` used to count, so `cat calc.py` or `git diff` after an edit silenced the
    verification gate (2026-09-25 audit, W13). A shell command now counts when it is a check
    (`verification.is_verification_command`) or runs a file changed this turn, meaning its stem
    appears in the command (`python calc.py`, `node build.js`). The other evidence tools execute code
    by nature and still count."""
    if name != "run_shell":
        return True
    from harness.loop.verification import is_verification_command

    command = arguments.get("command") if isinstance(arguments, dict) else None
    if not isinstance(command, str):
        return False
    if is_verification_command(command):
        return True
    for segment in re.split(r"&&|\|\||[;|\n]", command.lower()):
        words = segment.split()
        if not words or " ".join(words[:2]).startswith(VIEWERS) or words[0] in VIEWERS:
            continue
        if any(stem and stem in segment for stem in stems):
            return True
    return False
