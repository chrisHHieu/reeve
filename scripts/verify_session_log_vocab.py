"""Verify the session-log vocabulary cannot drift (D7, the catalog-and-verify habit).

`harness/session/session_log.py` documents the event vocabulary in its docstring. This script
cross-checks that table against REALITY, both directions, using the AST (call sites
span lines, so text matching misses most of them):

  · every `*.log.append("type", …)` under `harness/` must be DOCUMENTED — a new event
    type that skips the table is exactly how the vocabulary rots;
  · every documented type must be WRITTEN somewhere — a table entry nothing writes is
    fiction, and a reader would trust it.

Run standalone (`python scripts/verify_session_log_vocab.py`) or through
`tests/test_session_log_vocab.py`, which pins it into the suite.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
#: The package while its components move into `harness/`: both trees are the engine.
PACKAGES = [REPO / "harness"]
SESSION_LOG = REPO / "harness" / "session" / "session_log.py"


def written_event_types() -> dict[str, list[str]]:
    """{event type: [file:line, …]} for every `X.log.append("type", …)` call under
    the package, found through the AST so multi-line calls are counted."""
    found: dict[str, list[str]] = {}
    for path in sorted(p for package in PACKAGES if package.is_dir() for p in package.rglob("*.py")):
        if path == SESSION_LOG:
            continue  # the vocabulary's home, not a writer
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "append":
                continue
            receiver = node.func.value
            receiver_name = getattr(receiver, "attr", None) or getattr(receiver, "id", "")
            if receiver_name != "log":
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            event_type = node.args[0].value
            if isinstance(event_type, str):
                found.setdefault(event_type, []).append(
                    f"{path.relative_to(REPO).as_posix()}:{node.lineno}"
                )
    return found


def documented_event_types() -> set[str]:
    """The vocabulary table in session_log.py's docstring. Only the TABLE (the lines
    between the first `turn/start` entry and the prose after it) is scanned — prose
    elsewhere mentions slash-words that are not event names. Event names contain a
    slash; `a/b|c|d` enumerates siblings sharing the first segment; `notice` is the one
    bare name."""
    source = SESSION_LOG.read_text(encoding="utf-8")
    doc = ast.get_docstring(ast.parse(source)) or ""
    lines = doc.splitlines()
    start = next(i for i, line in enumerate(lines) if "turn/start" in line)
    end = next(i for i, line in enumerate(lines) if "Two rules hold" in line)
    table = "\n".join(lines[start:end])

    types: set[str] = set()
    # A table entry STARTS at line start or right after a `}` (the two-column rows);
    # slash-words inside the entries' trailing comments are prose, not event names.
    for match in re.finditer(r"(?:^|(?<=\}))\s*([a-z][a-z0-9_-]*(?:/[a-z0-9_|-]+)+)", table, re.M):
        token = match.group(1)
        parts = token.split("|")
        prefix = parts[0].split("/")[0] + "/"
        types.add(parts[0])
        types.update(part if "/" in part else prefix + part for part in parts[1:])
    types.add("notice")
    return types


def verify() -> list[str]:
    """Every problem found, or an empty list. Both directions have teeth."""
    problems: list[str] = []
    written = written_event_types()
    documented = documented_event_types()

    for event_type in sorted(set(written) - documented):
        where = ", ".join(written[event_type][:3])
        problems.append(
            f"undocumented: log.append({event_type!r}) at {where} — "
            "add it to the vocabulary in harness/session/session_log.py"
        )
    for event_type in sorted(documented - set(written)):
        problems.append(
            f"never written: {event_type!r} is in the vocabulary but no code logs it — "
            "write it, or take it off the table"
        )
    return problems


def main() -> int:
    problems = verify()
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    count = len(written_event_types())
    print(f"session-log vocabulary OK — {count} event types, table matches reality")
    return 0


if __name__ == "__main__":
    sys.exit(main())
