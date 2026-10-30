"""Telling a code change from other writes, and whether a command exercised it.

The verification gate asks two things: did this turn change code (a note in `notes.md` is not a code
change), and has something run since that exercises it.
"""

from __future__ import annotations

import re
from typing import Any, Optional

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


def exercises_change(name: str, arguments: Any, stems: "set[str]", folders: "tuple[str, ...]" = ()) -> bool:
    """Whether a successful evidence-tool call counts as having exercised the code change.

    Any `run_shell` used to count, so `cat calc.py` or `git diff` after an edit silenced the
    verification gate (2026-09-25 audit, W13). A shell command now counts when it is a check
    (`verification.is_verification_command`), runs a file changed this turn, meaning its stem
    appears in the command (`python calc.py`, `node build.js`), or runs a script that imports one
    (`python build.py` after an edit to the `extra_sheets.py` it imports). `folders` are where a
    relative script path is looked for. The other evidence tools execute code by nature and still
    count."""
    if name != "run_shell":
        return True
    from harness.loop.verification import is_verification_command

    command = arguments.get("command") if isinstance(arguments, dict) else None
    if not isinstance(command, str):
        return False
    if is_verification_command(command):
        return True
    for segment in re.split(r"&&|\|\||[;|\n]", command):
        lowered = segment.lower()
        words = lowered.split()
        if not words or " ".join(words[:2]).startswith(VIEWERS) or words[0] in VIEWERS:
            continue
        if any(stem and stem in lowered for stem in stems):
            return True
        if _runs_an_importer(segment, stems, folders):
            return True
    return False


# How much of a script is read to see what it imports: imports sit at the top, and a generated
# file of megabytes is not a script anyone runs to check a change.
SCRIPT_SCAN_CHARS = 200_000
_SCRIPT = re.compile(r"""["']?([^\s"'<>|]+\.(?:py|mjs|cjs|js|ts|tsx|jsx|ps1|psm1|sh|bash))["']?""", re.IGNORECASE)
_JS = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")


def _runs_an_importer(segment: str, stems: "set[str]", folders: "tuple[str, ...]") -> bool:
    """Whether a script this segment names imports a file changed this turn, one level deep.

    The gate's first rule only saw a changed file named in the command, so a turn that wrote a
    helper module and ran the script importing it was called unverified (session 90e83048-f57,
    2026-10-29: `extra_sheets.py` written, `python build_proposal.py` run twice with exit 0)."""
    if not stems:
        return False
    for match in _SCRIPT.finditer(segment):
        source = _script_text(match.group(1), folders)
        if source is None:
            continue
        suffix = match.group(1).lower().rsplit(".", 1)[-1]
        for stem in stems:
            if stem and _imports(source, stem, "." + suffix):
                return True
    return False


def _script_text(raw: str, folders: "tuple[str, ...]") -> Optional[str]:
    from pathlib import Path

    spelled = re.sub(r"^/([a-zA-Z])/", r"\1:/", raw)  # Git Bash's /c/Users is C:/Users
    candidate = Path(spelled).expanduser()
    for path in [candidate] if candidate.is_absolute() else [Path(folder) / candidate for folder in folders]:
        try:
            if path.is_file():
                with open(path, encoding="utf-8", errors="replace") as handle:
                    return handle.read(SCRIPT_SCAN_CHARS)
        except OSError:
            continue
    return None


def _imports(source: str, stem: str, suffix: str) -> bool:
    """An import of the module in the script's own language, or the changed file named with its
    extension: `exec(open("extra_sheets.py").read())` in the session that found this, `runpy`, a
    subprocess, PowerShell dot-sourcing, a shell's `source`. A bare mention of the name is neither."""
    name = re.escape(stem)
    named = rf"\b{name}\.(?:{'|'.join(suffix.lstrip('.') for suffix in CODE_SUFFIXES)})\b"
    if re.search(named, source, re.IGNORECASE):
        return True
    if suffix == ".py":
        pattern = rf"^\s*(?:from|import)\s+[^\n#]*\b{name}\b"
    elif suffix in _JS:
        pattern = rf"""(?:require\s*\(|import\s*\(|from\s+|import\s+)['"](?:[^'"]*/)?{name}(?:\.\w+)?['"]"""
    else:
        return False
    return re.search(pattern, source, re.IGNORECASE | re.MULTILINE) is not None
