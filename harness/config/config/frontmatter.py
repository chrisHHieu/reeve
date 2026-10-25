"""A small YAML-frontmatter reader: just the subset the markdown agent keys need."""

from __future__ import annotations

from pathlib import Path
from typing import Any

_BLOCK_MARKERS = ("|", ">", "|-", ">-", "|+", ">+")


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def scalar(raw: str) -> Any:
    """A bare or quoted value as the Python value it reads as."""
    value = raw.strip()
    if value in ("", "~", "null"):
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    for convert in (int, float):
        try:
            return convert(value)
        except ValueError:
            continue
    return value


def _block_scalar(
    lines: list[str], start: int, parent_indent: int, *, folded: bool, keep: bool
) -> tuple[str, int]:
    """A `|` or `>` block: the more-indented lines that follow, dedented and stripped."""
    taken: list[str] = []
    position = start
    while position < len(lines):
        line = lines[position]
        if line.strip() and _indent_of(line) <= parent_indent:
            break
        taken.append(line if line.strip() else "")
        position += 1
    indents = [_indent_of(line) for line in taken if line.strip()]
    margin = min(indents) if indents else 0
    text = "\n".join(line[margin:] if len(line) >= margin else "" for line in taken)
    if folded:
        text = " ".join(part.strip() for part in text.split("\n"))
    text = text.rstrip("\n")
    if keep:
        # `|+` keeps trailing newlines, and this reader carries one back. `|` and `|-` read the
        # same here: nothing in agent frontmatter depends on a final newline.
        text += "\n"
    return text, position


def _skip_blank(lines: list[str], position: int) -> int:
    while position < len(lines) and (not lines[position].strip() or lines[position].strip().startswith("#")):
        position += 1
    return position


def parse_mapping(lines: list[str], start: int, indent: int) -> tuple[dict[str, Any], int]:
    """A mapping at `indent`: `key: value` scalars (quoted or bare), nested mappings by indentation
    (the `permission` / `tools` tables), `|` / `>` block scalars, comments and blank lines.

    Anything else RAISES and the caller reports and skips the file: a partial parse would quietly
    half-configure an agent, which is worse than not loading it.
    """
    result: dict[str, Any] = {}
    position = start
    while position < len(lines):
        line = lines[position]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            position += 1
            continue
        if line.startswith("\t"):
            raise ValueError("tab indentation is not supported")
        here = _indent_of(line)
        if here < indent:
            break
        if here > indent:
            raise ValueError(f"unexpected indentation: {stripped!r}")
        if ":" not in stripped:
            raise ValueError(f"not a `key: value` line: {stripped!r}")
        key, _, rest = stripped.partition(":")
        key, rest = key.strip().strip("\"'"), rest.strip()
        if rest in _BLOCK_MARKERS:
            result[key], position = _block_scalar(
                lines, position + 1, here, folded=rest.startswith(">"), keep=rest.endswith("+")
            )
        elif rest:
            result[key] = scalar(rest)
            position += 1
        else:
            # `key:` and nothing after it: an empty value, or a nested mapping that follows.
            following = _skip_blank(lines, position + 1)
            if following >= len(lines) or _indent_of(lines[following]) <= here:
                result[key] = None
                position = following
            else:
                result[key], position = parse_mapping(lines, following, _indent_of(lines[following]))
    return result, position


def parse_agent_markdown(path: Path) -> dict[str, Any]:
    """One `.md` agent: its frontmatter keys, plus the body as `prompt`.

    A non-empty body replaces any `prompt` the frontmatter declared; an empty body leaves a
    declared one in place.
    """
    lines = path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    if lines[0].strip() != "---":
        raise ValueError("missing `---` frontmatter opener")
    closing = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if closing is None:
        raise ValueError("unterminated frontmatter")
    entry, _ = parse_mapping(lines[1:closing], 0, 0)
    body = "\n".join(lines[closing + 1 :]).strip()
    if body:
        entry["prompt"] = body
    return entry
