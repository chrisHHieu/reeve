"""The parts of a `grep` search that do not depend on its engine: the `type` table, and cutting
one page out of a content search's rows (`window`). Split from `grep_engines.py`."""

from __future__ import annotations

from typing import Any, Optional

#: Matched and context lines are clipped here (the same bound as `grep_engines._LINE_CHARS`).
_LINE_CHARS = 300

#: Lines one multiline match may show. A pattern like `(?s)a.*b` can span a whole file, and
#: the rest of it is `read_file`'s job.
MAX_MATCH_LINES = 20

#: The `type` values `grep` takes, each a set of file-name globs. One table for every engine
#: (ripgrep through `--type-add`, the Python walk, the remote `--include`s), so a type means
#: the same files wherever the search runs. A glob is easy to get subtly wrong: `*.ts` leaves
#: out every `.tsx` file, and nothing says so.
TYPES: dict[str, tuple[str, ...]] = {
    "py": ("*.py", "*.pyi", "*.pyw"),
    "js": ("*.js", "*.mjs", "*.cjs", "*.jsx"),
    "ts": ("*.ts", "*.tsx", "*.mts", "*.cts"),
    "web": ("*.html", "*.htm", "*.css", "*.scss", "*.sass", "*.less", "*.vue", "*.svelte"),
    "go": ("*.go",),
    "rust": ("*.rs",),
    "java": ("*.java",),
    "kotlin": ("*.kt", "*.kts"),
    "c": ("*.c", "*.h"),
    "cpp": ("*.cpp", "*.cc", "*.cxx", "*.hpp", "*.hh", "*.hxx", "*.h"),
    "cs": ("*.cs", "*.csx"),
    "ruby": ("*.rb", "*.rake", "Gemfile", "Rakefile"),
    "php": ("*.php",),
    "swift": ("*.swift",),
    "sh": ("*.sh", "*.bash", "*.zsh"),
    "ps": ("*.ps1", "*.psm1", "*.psd1"),
    "sql": ("*.sql",),
    "md": ("*.md", "*.mdx", "*.markdown"),
    "json": ("*.json", "*.jsonc", "*.json5"),
    "yaml": ("*.yaml", "*.yml"),
    "toml": ("*.toml",),
    "xml": ("*.xml", "*.xsd", "*.xsl"),
    "docker": ("Dockerfile", "*.dockerfile", "Containerfile"),
    "config": ("*.ini", "*.cfg", "*.conf", "*.env", "*.properties"),
}


def type_globs(name: Optional[str]) -> "tuple[tuple[str, ...], Optional[str]]":
    """The globs of a `type`, or `((), error)` for a name the table does not have."""
    if not name:
        return (), None
    globs = TYPES.get(str(name).strip().lower())
    if globs is None:
        return (), f"unknown type {name!r}; known types: {', '.join(sorted(TYPES))}"
    return globs, None


def _is_match(row: dict[str, Any]) -> bool:
    return not row.get("context") and not row.get("cont")


def window(
    rows: list[dict[str, Any]], offset: int, n: int, before: int, after: int
) -> "tuple[list[dict[str, Any]], int, bool]":
    """One page of a content search: `(rows, matches shown, more after this page)`.

    `rows` are in result order: a match, the lines that continue a multiline match
    (`cont`), and context lines (`context`) around them. The page keeps matches
    `offset .. offset+n`, the lines that continue them, and only the context lines within
    `before`/`after` of a kept match in its own file, so a context line of a match on
    another page never reads as part of this one."""
    hits = [i for i, row in enumerate(rows) if _is_match(row)]
    chosen = hits[offset : offset + n]
    more = len(hits) > offset + n
    keep: set[int] = set()
    spans: list[tuple[str, int, int]] = []
    for i in chosen:
        keep.add(i)
        end = rows[i]["line"]
        j = i + 1
        while j < len(rows) and rows[j].get("cont"):
            keep.add(j)
            end = rows[j]["line"]
            j += 1
        spans.append((rows[i]["file"], rows[i]["line"], end))
    for k, row in enumerate(rows):
        if not row.get("context"):
            continue
        line, name = row["line"], row["file"]
        if any(name == f and (start - before <= line < start or end < line <= end + after) for f, start, end in spans):
            keep.add(k)
    return [rows[i] for i in sorted(keep)], len(chosen), more


def match_rows(name: str, start: int, text: str) -> list[dict[str, Any]]:
    """A match as rows: its first line, then each line it continues onto (a multiline
    match), at most `MAX_MATCH_LINES`."""
    lines = text.rstrip("\r\n").split("\n") or [""]
    rows: list[dict[str, Any]] = [{"file": name, "line": start, "text": lines[0].rstrip()[:_LINE_CHARS]}]
    for k, extra in enumerate(lines[1:MAX_MATCH_LINES], 1):
        rows.append({"file": name, "line": start + k, "text": extra.rstrip()[:_LINE_CHARS], "cont": True})
    return rows
