"""Post-edit syntax diagnostics — the provable half of LSP, with nothing to install.

Language-server diagnostics are appended to the result of an edit; that closes a real
loop (a model that breaks the file learns it in the same step) but it needs a server the
user installed, per language, with its own startup cost and failure modes. This is the
deterministic subset that needs none of that: for the formats Python already ships
parsers for, "this file no longer parses" is provable in milliseconds, offline, with zero
configuration — and it is the most common way a half-finished edit is broken.

What it is NOT: type checking, linting, or any cross-file analysis. Those need the real
tools (`run_shell` runs them, and the verification gate nudges toward exactly that). Full
LSP — a language server per workspace, definition/references/hover — stays deferred: it
is a feature with a server dependency, not a hardening step, and the harness's grep/read
already carry ordinary navigation.

A clean file adds nothing to the result. A broken one adds one line naming the file, the
position and the parser's own message — never a rewrite of the tool's output, so the
contract each write tool declares still holds.
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: Formats with a stdlib (or already-declared) parser. Anything else — JavaScript, Go,
#: Rust — has no parser here on purpose: guessing at a grammar would produce false
#: "syntax errors" on valid files, which is worse than saying nothing.
_SUFFIX_PARSERS = {
    ".py": "python",
    ".pyi": "python",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
}

#: Parsing is for a just-written file, so it is small by definition; anything larger is a
#: generated artifact and re-reading it would cost more than the diagnostic is worth.
MAX_BYTES = 2_000_000

#: How many files of one call are checked. A patch can touch many; the first few are
#: where the change the model just made lives.
MAX_FILES_PER_CALL = 3

#: Files whose grammar is JSONC, not JSON: comments and trailing commas are part of the
#: format these tools ship (`tsc` and `pyright` both read them; VS Code writes them).
#: Treating them as strict JSON rejected a perfectly valid `pyrightconfig.json` with a
#: two-line comment on 2026-09-19 — the hook told the model its config "fails to parse"
#: while pyright accepted it, so the model stripped the comments to appease the hook.
_JSONC_FILENAMES = frozenset(
    {
        "pyrightconfig.json",
        "tsconfig.json",
        "jsconfig.json",
        "devcontainer.json",
        ".eslintrc.json",
        ".babelrc.json",
        "settings.json",
        "keybindings.json",
        "launch.json",
        "tasks.json",
    }
)


def is_jsonc(path: str | Path) -> bool:
    """Whether this name's grammar is JSONC (comments + trailing commas allowed)."""
    target = Path(str(path))
    name = target.name.lower()
    if name in _JSONC_FILENAMES:
        return True
    if name.startswith("tsconfig.") and name.endswith(".json"):
        return True  # tsconfig.base.json, tsconfig.build.json, …
    parts = [part.lower() for part in target.parts]
    return ".vscode" in parts or ".devcontainer" in parts


def supports(path: str | Path) -> bool:
    return Path(str(path)).suffix.lower() in _SUFFIX_PARSERS


def check_text(text: str, *, name: str = "file") -> Optional[str]:
    """`"name:line:col: message"` for a parse failure, or None when the text parses.

    `name` is what the message calls the file (a relative path reads better than an
    absolute one). The parser's own message is kept verbatim: it names the expected
    token, which is the part a model needs to fix the edit.
    """
    if "\x00" in text[:4096]:
        return None  # binary-ish content: not ours to parse
    suffix = Path(str(name)).suffix.lower()
    parser = _SUFFIX_PARSERS.get(suffix)
    if parser is None:
        return None
    try:
        if parser == "python":
            ast.parse(text, filename=str(name))
        elif parser == "json":
            json.loads(_strip_jsonc(text) if is_jsonc(name) else text)
        elif parser == "toml":
            _toml_loads(text)
        else:
            import yaml

            yaml.safe_load(text)
    except Exception as exc:  # every parser has its own exception type
        line, col, message = _position(exc)
        return f"{name}:{line}:{col}: {message}"
    return None


def check_file(path: str | Path, *, display: Optional[str] = None) -> Optional[str]:
    """`check_text` for a file on disk; None when it is missing, too large or unsupported."""
    target = Path(str(path))
    if not supports(target):
        return None
    try:
        if target.stat().st_size > MAX_BYTES:
            return None
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return check_text(text, name=display or str(target))


def _strip_jsonc(text: str) -> str:
    """JSONC text as plain JSON: comments removed, trailing commas dropped.

    A scanner rather than a regex pair because `//` inside a string is a URL, not a
    comment, and a `,` inside a string is data — the one thing a regex cannot know.
    """
    out: list[str] = []
    index = 0
    length = len(text)
    in_string = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            while index < length and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            index += 2
            while index + 1 < length and not (
                text[index] == "*" and text[index + 1] == "/"
            ):
                index += 1
            index += 2
            continue
        if char == ",":
            look = index + 1
            while look < length and text[look] in " \t\r\n":
                look += 1
            if look < length and text[look] in "}]":
                index += 1
                continue
        out.append(char)
        index += 1
    return "".join(out)


def _toml_loads(text: str) -> object:
    try:
        import tomllib  # py3.11+
    except ImportError:  # pragma: no cover - 3.10 floor
        import tomli as tomllib  # type: ignore[no-redef]

    return tomllib.loads(text)


def _position(exc: BaseException) -> tuple[int, int, str]:
    """`(line, col, message)` from whichever exception shape the parser raised."""
    # Python: SyntaxError.lineno/offset; json: msg + lineno/colno; yaml: problem_mark.
    line = getattr(exc, "lineno", None)
    col = getattr(exc, "offset", None) or getattr(exc, "colno", None)
    message = getattr(exc, "msg", None) or str(exc)
    mark = getattr(exc, "problem_mark", None)
    if mark is not None:
        line = getattr(mark, "line", 0) + 1
        col = getattr(mark, "column", 0) + 1
        message = getattr(exc, "problem", None) or message
    if line is None:
        line, col = 1, 1
    return int(line), int(col or 1), " ".join(str(message).split())


__all__ = [
    "MAX_BYTES",
    "MAX_FILES_PER_CALL",
    "check_file",
    "check_text",
    "is_jsonc",
    "supports",
]
