"""The search engines behind `grep`: ripgrep (the one an install carries, else the one on PATH),
and a Python walk when there is neither.

Split out of `tools/search.py` (which keeps the tool, its schema and its presentation)
when `grep` gained output modes. Three shapes, one per mode, identical on both engines:

    content             {"count", "matches": [{file, line, text[, context|cont]}], "truncated", "offset"}
    files_with_matches  {"count", "files": [path, …] newest first, "truncated", "offset"}
    count               {"count": total, "counts": [{file, count}], "truncated", "offset"}

Every shape carries `truncated`, decided by seeing one result past the page — never by
the caller's arguments (the 2026-09-24 audit found the default cap cut silently).

A page is `offset` results in, `n` long, and the next page starts where it ends. That only
holds if a search lists its results in the same order every time, so content results are
sorted by path and line (ripgrep `--sort path`, a sorted walk): ripgrep's parallel order
differs between two runs of one search.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

from harness.platform.spawn import run as spawn_run

from .grep_pages import MAX_MATCH_LINES, TYPES, match_rows, type_globs, window  # noqa: F401  (re-exported)

CONTENT = "content"
FILES = "files_with_matches"
COUNT = "count"
MODES = (CONTENT, FILES, COUNT)
#: Matches returned when the model names no cap, and the most it may ask for.
_DEFAULT_MAX_RESULTS = 100
_MAX_RESULTS = 1000
#: Context lines around a match, at most. Beyond this the caller wants `read_file`.
MAX_CONTEXT = 10
#: Matched and context lines are clipped here, as they always were.
_LINE_CHARS = 300
def carried_rg() -> Optional[str]:
    """The ripgrep an installer carries next to the sidecar binary (`sidecar/rg/rg[.exe]`,
    staged by packaging/build_windows.ps1 and build_dmg.sh), or None in a dev checkout.

    It comes before the one on PATH: Windows and macOS ship no ripgrep, so without it a
    person's `grep` ran the Python walk (12.7 s against 0.2 s over this repository, measured
    2026-10-11, and it read binaries and ignored files ripgrep leaves out). Carried, every
    install searches with one engine at one version, whatever the machine has."""
    name = "rg.exe" if sys.platform == "win32" else "rg"
    carried = Path(sys.executable).resolve().parent / "rg" / name
    return str(carried) if carried.is_file() else None


def _rel(path: str, root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(root))
    except (ValueError, OSError):
        return path


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _newest_first(paths: Iterable[str], root: Path) -> list[str]:
    """Paths ordered the way search results usually are: most recently modified
    first (the file being worked on is usually the one wanted), then by name."""
    return sorted(paths, key=lambda p: (-_mtime(root / p), p))


def _parse_rg(
    stdout: str,
    root: Path,
    n: int,
    context: int = 0,
    *,
    where: Optional[Path] = None,
    offset: int = 0,
    before: Optional[int] = None,
    after: Optional[int] = None,
) -> dict[str, Any]:
    r"""Parse ripgrep's `--json` stream: `type: "match"` for a hit, `"context"` for a line
    around one.

    Structured on purpose. The previous parser split `path:line:text` on the first colon,
    which is not a separator on Windows — `D:\Project\…` made every match report file
    "D" and line 0. Nothing raised; the tool just stopped being able to answer "where".
    """
    before = context if before is None else before
    after = context if after is None else after
    rows: list[dict[str, Any]] = []
    found = 0
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue  # a non-JSON line is not a match; never a reason to fail the search
        kind = event.get("type")
        if kind not in ("match", "context"):
            continue
        if kind == "match" and found > offset + n:
            # Enough to fill the page and know one more exists. ripgrep's own
            # `--max-count` is per FILE, so this is the only place the total cap lives.
            break
        data = event.get("data") or {}
        path = ((data.get("path") or {}).get("text") or "").strip()
        text = (data.get("lines") or {}).get("text") or ""
        number = data.get("line_number")
        # A relative path is relative to where ripgrep ran (`where`), not to this process's
        # cwd; joining an absolute one leaves it unchanged.
        name = _rel(str(where / path) if where is not None else path, root)
        start = int(number) if isinstance(number, int) else 0
        if kind == "context":
            rows.append({"file": name, "line": start, "text": text.rstrip()[:_LINE_CHARS], "context": True})
        else:
            found += 1
            rows.extend(match_rows(name, start, text))
    shown, count, more = window(rows, offset, n, before, after)
    return {"count": count, "matches": shown, "truncated": more, "offset": offset}


def glob_scope(root: Path, base: Path, glob: Optional[str]) -> tuple[Path, str, list[str]]:
    """Where ripgrep runs from, what it is pointed at, and the include globs.

    ripgrep matches a glob that holds a slash against the path it prints, which is relative
    to its own cwd. Models write such globs two ways: relative to the WORKSPACE
    (`harness/**/*.py` with `path="harness/context/memory"`) and, less often, relative to the path
    they searched (`sub/*.py` with `path="pkg"`). Run from the workspace root, the first
    spelling matches as written, and the second is added as a further include with the
    path in front — two includes are an OR. A search outside the workspace (a granted
    folder) runs from its own folder, where only the path-relative spelling means anything.
    """
    folder = base if base.is_dir() else base.parent
    globs = [glob] if glob else []
    try:
        rel = folder.relative_to(root).as_posix()
    except ValueError:
        return folder, ("." if base.is_dir() else base.name), globs
    target = base.relative_to(root).as_posix() if base != root else "."
    if glob and "/" in glob and not glob.startswith("!") and rel not in ("", "."):
        globs.append(f"{rel}/{glob.lstrip('/')}")
    return root, target, globs


def glob_matches(glob: str, path: Path, root: Path, base: Path) -> bool:
    """The Python fallback's reading of the same rule: a name glob matches the file name, a
    folder glob matches the path from the workspace or from the searched folder. `**/` may
    stand for no folder at all, as it does for ripgrep."""
    if "/" not in glob:
        return fnmatch.fnmatch(path.name, glob)
    folder = base if base.is_dir() else base.parent
    spellings = {glob.lstrip("/"), glob.lstrip("/").replace("**/", "")}
    for anchor in (root, folder):
        try:
            rel = path.relative_to(anchor).as_posix()
        except ValueError:
            continue
        if any(fnmatch.fnmatch(rel, g) for g in spellings):
            return True
    return False


def rg_search(
    rg: str,
    *,
    root: Path,
    base: Path,
    pattern: str,
    glob: Optional[str],
    n: int,
    mode: str,
    context: int,
    exclude_globs: list[str],
    offset: int = 0,
    before: Optional[int] = None,
    after: Optional[int] = None,
    ignore_case: bool = False,
    multiline: bool = False,
    types: tuple[str, ...] = (),
) -> dict[str, Any]:
    """One ripgrep run in the requested mode. Raises OSError on a ripgrep failure."""
    cmd = [
        rg,
        "--color=never",
        # ripgrep honours `.gitignore` only inside a git repository by default; the
        # description promises ".gitignore-aware" for every workspace.
        "--no-require-git",
        # Dotfiles are searched like any file (`.github/workflows/ci.yml`, `.env.example`):
        # ripgrep skips them by default, while `list_files` and the Python fallback list
        # them — a search that cannot see the CI config it was pointed at (2026-09-25
        # audit, W4). `.git` itself stays out through the exclusion globs.
        "--hidden",
    ]
    if ignore_case:
        cmd.append("--ignore-case")
    if multiline:
        # `.` crosses lines too, the way a pattern written for multiline search means it.
        cmd += ["--multiline", "--multiline-dotall"]
    if types:
        # The type is ours (`TYPES`), handed to ripgrep under one name, so the files it
        # covers are the same on every engine; it ANDs with `glob`, as ripgrep's own does.
        for type_glob in types:
            cmd += ["--type-add", f"reeve:{type_glob}"]
        cmd += ["--type", "reeve"]
    if mode == FILES:
        cmd.append("--files-with-matches")
    elif mode == COUNT:
        # `path:count` per line; the count is split off the RIGHT, so a Windows drive
        # colon in the path cannot be mistaken for the separator.
        cmd += ["--count-matches", "--with-filename"]
    else:
        # Structured output, because the text form is ambiguous on Windows (`D:\…`). Sorted,
        # so the next page (`offset`) continues this one.
        cmd += ["--json", "--sort", "path", "--max-count", str(offset + n + 1)]
        lines_before = context if before is None else before
        lines_after = context if after is None else after
        if lines_before:
            cmd += ["--before-context", str(lines_before)]
        if lines_after:
            cmd += ["--after-context", str(lines_after)]
    cmd += ["-e", pattern]
    where, target, globs = glob_scope(root, base, glob)
    for include in globs:
        cmd += ["--glob", include]
    # Exclusions last: ripgrep resolves conflicting globs with the later one winning.
    for glob_pattern in exclude_globs:
        cmd += ["--glob", glob_pattern]
    # Searched FROM the workspace (see `glob_scope`), never by an absolute path: ripgrep
    # matches a glob that holds a slash against the path it prints, relative only under its
    # own cwd. Every session whose workspace was not the server's cwd got zero matches for
    # such a glob — 65 greps, most of them empty, in one 80-step eval run (2026-09-25).
    cmd.append(target)
    out = spawn_run(
        cmd,
        cwd=str(where),
        capture_output=True,
        # ripgrep emits UTF-8; the locale codec (cp1252 here) turned every Vietnamese
        # match line into mojibake, with nothing raised.
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if out.returncode not in (0, 1):  # 1 = no matches
        raise OSError((out.stderr or "ripgrep error").strip()[:300])
    stdout = out.stdout or ""
    if mode == FILES:
        paths = [_rel(str(where / line.strip()), root) for line in stdout.splitlines() if line.strip()]
        return _files_result(_newest_first(paths, root), offset, n)
    if mode == COUNT:
        rows: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            path, sep, number = line.rstrip().rpartition(":")
            if sep and number.isdigit():
                rows.append({"file": _rel(str(where / path), root), "count": int(number)})
        return _count_result(rows, offset, n)
    return _parse_rg(stdout, root, n, context, where=where, offset=offset, before=before, after=after)


def _files_result(ordered: list[str], offset: int, n: int) -> dict[str, Any]:
    page = ordered[offset : offset + n]
    return {"count": len(page), "files": page, "truncated": len(ordered) > offset + n, "offset": offset}


def _count_result(rows: list[dict[str, Any]], offset: int, n: int) -> dict[str, Any]:
    """Most matches first; `count` is the total over every file, shown or not."""
    rows.sort(key=lambda r: (-r["count"], r["file"]))
    return {
        "count": sum(r["count"] for r in rows),
        "counts": rows[offset : offset + n],
        "truncated": len(rows) > offset + n,
        "offset": offset,
    }


def py_search(
    *,
    root: Path,
    base: Path,
    pattern: str,
    glob: Optional[str],
    n: int,
    mode: str,
    context: int,
    ignore_dirs: set[str],
    offset: int = 0,
    before: Optional[int] = None,
    after: Optional[int] = None,
    ignore_case: bool = False,
    multiline: bool = False,
    types: tuple[str, ...] = (),
) -> dict[str, Any]:
    """The same three shapes from a Python walk (no ripgrep on the machine), in the same
    order as ripgrep's sorted run: folders and files by name."""
    flags = (re.IGNORECASE if ignore_case else 0) | ((re.DOTALL | re.MULTILINE) if multiline else 0)
    try:
        rx = re.compile(pattern, flags)
    except re.error as exc:
        return {"error": f"invalid regex: {exc}", "count": 0, "matches": []}
    lines_before = context if before is None else before
    lines_after = context if after is None else after
    rows: list[dict[str, Any]] = []
    found = 0
    files: list[str] = []
    counts: list[dict[str, Any]] = []
    walk = [(base.parent, [], [base.name])] if base.is_file() else os.walk(base)
    for dirpath, dirs, names in walk:
        dirs[:] = sorted(d for d in dirs if d not in ignore_dirs)
        for name in sorted(names):
            fp = Path(dirpath) / name
            if glob and not glob_matches(glob, fp, root, base):
                continue
            if types and not any(fnmatch.fnmatch(name, t) for t in types):
                continue
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                    whole = fh.read()
            except OSError:
                continue
            lines = whole.splitlines()
            if multiline:
                starts = [(whole.count("\n", 0, m.start()), m.group(0)) for m in rx.finditer(whole) if m.group(0)]
            else:
                starts = [(i, text) for i, text in enumerate(lines) if rx.search(text)]
            if not starts:
                continue
            rel = _rel(str(fp), root)
            if mode == FILES:
                files.append(rel)
                continue
            if mode == COUNT:
                counts.append({"file": rel, "count": len(starts)})
                continue
            covered: set[int] = set()
            matched_lines = {i for i, _ in starts}
            for i, text in starts:
                span = text.count("\n") + 1 if multiline else 1
                body = "\n".join(lines[i : i + span]) if multiline else lines[i]
                for j in range(max(0, i - lines_before), i):
                    if j not in covered and j not in matched_lines:
                        covered.add(j)
                        rows.append({"file": rel, "line": j + 1, "text": lines[j].rstrip()[:_LINE_CHARS], "context": True})
                rows.extend(match_rows(rel, i + 1, body))
                covered.update(range(i, i + span))
                for j in range(i + span, min(len(lines), i + span + lines_after)):
                    if j not in covered and j not in matched_lines:
                        covered.add(j)
                        rows.append({"file": rel, "line": j + 1, "text": lines[j].rstrip()[:_LINE_CHARS], "context": True})
                found += 1
            if found > offset + n:
                break
        if mode == CONTENT and found > offset + n:
            break
    if mode == FILES:
        return _files_result(_newest_first(files, root), offset, n)
    if mode == COUNT:
        return _count_result(counts, offset, n)
    rows.sort(key=lambda r: (r["file"], r["line"]))
    shown, count, more = window(rows, offset, n, lines_before, lines_after)
    return {"count": count, "matches": shown, "truncated": more, "offset": offset}
