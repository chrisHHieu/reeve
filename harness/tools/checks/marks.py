"""Which failure a check reported, as a name that stays the same when the same thing breaks again.

The verification ledger says whether a check passed and what its last line was. That is enough to
tell "the same check said the same thing again", and not enough to tell that a failure met today is
one this project met last week: "1 failed, 66 passed" is every failure of that suite, and stops
matching when a test is added. A mark is what stays: the test that failed, or the file and the rule
a checker reported. Line and column numbers, durations and messages are left out, because they move
while the failure stays.

Marks come in two tiers:

1. **What failed.** `test:`, `type:` and `lint:` marks: a test a runner names, or a file and a rule
   a checker reports. One reader per output format the store has shown.
2. **What kept the check from running.** `setup:` marks, read only when the output names nothing of
   the first tier: a module that would not import, a command the shell does not know, a file type no
   loader takes. The check never got to the code; what fixes it is usually how it is run.

A failed check that names neither has no marks, and nothing is ever matched on it: a match the
harness cannot stand behind is worse than none. That case is counted instead (`unnamed-failures` in
the harness review), so the next format no reader takes shows up as a number.
"""

from __future__ import annotations

import re

#: The most marks kept from one result: a suite with hundreds of failures is not a failure anyone
#: repairs one name at a time.
MAX_MARKS = 40

SETUP = "setup"

_PATH = r"(?:[A-Za-z]:)?[\w./\\@+-]+"

#: `(pattern, kind, groups)`: each match becomes `kind:` + its groups joined by `:`.
_SHAPES: tuple[tuple[re.Pattern[str], str, tuple[int, ...]], ...] = (
    # pytest's short summary: "FAILED tests/test_x.py::test_y - AssertionError: …",
    # "ERROR tests/test_x.py - ImportError: …" (a collection error names the file). The name must
    # look like one (an extension or `::`): "ERROR in ./src" and "FAILED to connect" are sentences.
    (re.compile(r"^(?:FAILED|ERROR) (\S*(?:\.\w+|::)\S*)", re.M), "test", (1,)),
    # jest / vitest: "FAIL src/a.test.ts", " FAIL  src/a.test.ts > suite > case"
    (re.compile(rf"^\s*(?:FAIL|✕|×)\s+({_PATH}\.(?:test|spec)\.[cm]?[jt]sx?)\b", re.M), "test", (1,)),
    # go test: "--- FAIL: TestName (0.00s)"
    (re.compile(r"^\s*--- FAIL: (\w[\w/]*)", re.M), "test", (1,)),
    # cargo test: "test module::name ... FAILED"
    (re.compile(r"^test ([\w:]+) \.\.\. FAILED", re.M), "test", (1,)),
    # tsc: "src/a.ts(12,5): error TS2345: …"
    (re.compile(rf"^({_PATH})\(\d+,\d+\): error (TS\d+)", re.M), "type", (1, 2)),
    # mypy: "pkg/a.py:12: error: … [arg-type]"
    (re.compile(rf"^({_PATH}):\d+(?::\d+)?: error: .*\[([\w-]+)\]\s*$", re.M), "type", (1, 2)),
    # pyright: "  /abs/pkg/a.py:12:5 - error: … (reportArgumentType)"
    (re.compile(rf"^\s*({_PATH}):\d+:\d+ - error: .*\((report\w+)\)\s*$", re.M), "type", (1, 2)),
    # ruff --output-format=concise, flake8, eslint -f unix: "pkg/a.py:12:5: F401 …"
    (re.compile(rf"^({_PATH}):\d+:\d+: ([A-Z]{{1,4}}\d{{1,4}})\b", re.M), "lint", (1, 2)),
    # ruff's default output: "F401 [*] `os` imported but unused" over " --> pkg/a.py:1:8".
    (re.compile(rf"^([A-Z]{{1,4}}\d{{1,4}})\b[^\n]*\n\s*-->\s*({_PATH}):\d+:\d+", re.M), "lint", (2, 1)),
)

#: Node's own runner, its default reporter: after "✖ failing tests:", each failure is
#: "test at <file>:<line>:<col>" over "✖ <name> (<duration>)". The suites around it are listed
#: above the section as "✖ <suite>", and are not failures of their own.
_NODE_SECTION = re.compile(r"^✖ failing tests:\s*$", re.M)
_NODE_FAILURE = re.compile(
    r"^(?:test at (?P<at>\S+?):\d+:\d+[ \t]*\r?\n)?✖ (?P<name>.+?) \(\d+(?:\.\d+)?m?s\)[ \t]*\r?$", re.M
)

#: TAP, the format many runners write when asked (`node --test --test-reporter=tap`, `prove`,
#: `tap`): "not ok 3 - name", a TODO or SKIP directive excepted.
_TAP_FAILURE = re.compile(r"^\s*not ok \d+(?: - (?P<name>[^#\n]+?))?\s*(?:#\s*(?P<directive>\w+)[^\n]*)?$", re.M)

#: The second tier: what kept a check from running at all. `(pattern, what, group)`.
_SETUP_SHAPES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    # Python
    (re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'"), "module", "{0}"),
    (re.compile(r"ImportError: cannot import name '(\w+)' from '([\w.]+)'"), "import", "{1}.{0}"),
    # Node
    (re.compile(r"Cannot find (?:module|package) '([^']+)'"), "module", "{0}"),
    (re.compile(r"Unknown file extension \"(\.\w+)\""), "extension", "{0}"),
    # A shell that does not know the command: bash, PowerShell, cmd.
    (re.compile(r"(?:^|: )([\w.-]+): command not found", re.M), "command", "{0}"),
    (re.compile(r"The term '([^']+)' is not recognized as"), "command", "{0}"),
    (re.compile(r"'([^']+)' is not recognized as an internal or external command"), "command", "{0}"),
    # pytest pointed at a path that is not there
    (re.compile(r"ERROR: file or directory not found: (\S+)"), "path", "{0}"),
    # A `cd` before the check that went nowhere, so the check never ran: bash, PowerShell.
    (re.compile(r"\bcd: ([^:\n]+): No such file or directory"), "path", "{0}"),
    (re.compile(r"Cannot find path '([^']+)' because it does not exist"), "path", "{0}"),
    # The check ran and found no test to run (pytest's exit 5, vitest, jest).
    (re.compile(r"^(?:=+ )?no tests ran\b", re.M | re.I), "tests", "none"),
    (re.compile(r"^No test files found", re.M), "tests", "none"),
    (re.compile(r"^No tests found", re.M), "tests", "none"),
)


def _name(text: str) -> str:
    """One spelling for a path whichever way its slashes lean, without a leading `./` or `file:///`."""
    text = text.strip().replace("\\", "/")
    if text.startswith("file:///"):
        text = text[len("file:///"):]
    # An escaped backslash in the runner's output (`tests\\a.test.mjs`) is one separator.
    text = re.sub(r"/{2,}", "/", text)
    return text[2:] if text.startswith("./") else text


#: A test name that is a test file: Node names a file that failed to load after the file itself.
_TEST_FILE = re.compile(r"^[\w.@+-]+(?:[/\\]+[\w.@+-]+)*\.(?:test|spec)\.[cm]?[jt]sx?$")


def _test_mark(at: str | None, name: str) -> str:
    """One test's mark: `file::name`, or the file alone when the failure is the file's own."""
    name = " ".join(name.split())
    if _TEST_FILE.match(name):
        return f"test:{_name(name)}"
    return f"test:{_name(at)}::{name}" if at else f"test:{name}"


def _first_tier(text: str) -> list[str]:
    found: list[str] = []

    def add(mark: str) -> None:
        if mark not in found and len(found) < MAX_MARKS:
            found.append(mark)

    for pattern, kind, groups in _SHAPES:
        for match in pattern.finditer(text):
            add(kind + ":" + ":".join(_name(match.group(group)) for group in groups))
    section = None
    for section in _NODE_SECTION.finditer(text):
        pass
    if section is not None:
        for match in _NODE_FAILURE.finditer(text, section.end()):
            add(_test_mark(match.group("at"), match.group("name")))
    for match in _TAP_FAILURE.finditer(text):
        directive = (match.group("directive") or "").upper()
        if directive in ("TODO", "SKIP") or not match.group("name"):
            continue
        add(_test_mark(None, match.group("name")))
    return found


def _second_tier(text: str) -> list[str]:
    found: list[str] = []
    for pattern, what, shape in _SETUP_SHAPES:
        for match in pattern.finditer(text):
            mark = f"{SETUP}:{what}:" + _name(shape.format(*match.groups()))
            if mark not in found:
                found.append(mark)
            if len(found) >= MAX_MARKS:
                return found
    return found


def failure_marks(content: str) -> frozenset[str]:
    """The failures a check's output names, as stable marks; empty when it names none. What failed
    when the output says; otherwise what kept the check from running."""
    text = content or ""
    return frozenset(_first_tier(text) or _second_tier(text))


def is_setup(mark: str) -> bool:
    """Whether a mark is about the check not running, rather than about what it found."""
    return mark.startswith(SETUP + ":")


_SETUP_SAID = {
    "module": "module {} not found",
    "import": "cannot import {}",
    "extension": "no loader for {} files",
    "command": "command {} not found",
    "path": "{} not found",
    "tests": "no tests were found to run",
}


def spoken(mark: str) -> str:
    """A mark as a person would say it: the name, without the kind it was filed under."""
    if is_setup(mark):
        _, what, name = mark.split(":", 2)
        return _SETUP_SAID.get(what, "{}").format(name)
    return mark.split(":", 1)[1] if ":" in mark else mark


__all__ = ["MAX_MARKS", "SETUP", "failure_marks", "is_setup", "spoken"]
