"""`list_files` — the file listing the model reads, gitignore-aware and honest about caps.

Split out of `tools/files.py` (the reader/writer module) when it replaced aisuite's toolkit
listing; the render and presenters stay beside the other file-tool contracts there.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import aisuite as ai

from harness.config.roots import allowed_dirs, containing_root
from harness.platform.spawn import run as spawn_run
from .contract import attach_contract
from .files.access import escape_refusal as _escapes
from .files.contracts import BORROWED_CONTRACTS as _BORROWED_CONTRACTS
from .files.schemas import LIST_FILES_SCHEMA as _LIST_FILES_SCHEMA


#: Paths `list_files` returns when the model names no cap.
_LIST_DEFAULT_MAX = 100
#: Above this many candidates the listing is ordered by name rather than by mtime: a
#: stat per file is the cost of "newest first", and a huge tree should not pay it on
#: every call. Said in the result, so the order is never implied.
_MTIME_SORT_MAX = 20_000
#: The Python fallback's walk stops here (no ripgrep on the machine): a bound on the
#: work, reported as truncation like any other cap.
_WALK_MAX = 100_000


def list_files_tools(
    workspace: str, roots: Optional[list] = None, backend: Optional[Any] = None
) -> list:
    """`list_files` that says when it stopped short, and lists what matters first.

    Replaces aisuite's toolkit `list_files`, which returned the first `max_results` paths in
    `rglob` order, then sorted THOSE by name — an arbitrary subset presented as a sorted
    whole — with no word that more existed, `.gitignore` ignored, and `node_modules` walked
    in full (2026-09-24 audit). Here: ripgrep's `--files` (gitignore-aware, the same
    exclusions `grep` uses, hidden config dirs like `.github` kept), newest first like
    a glob tool, and `truncated`/`total` in the result so the render can say
    "showing 100 of 1,234". A remote world keeps its backend's listing, asked for one past
    the cap so truncation is known rather than guessed.
    """
    root = Path(workspace).resolve()

    def _label(path: Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)

    def _remote(path: str, pattern: str, recursive: bool, n: int) -> dict[str, Any]:
        try:
            found = backend.list_files(
                path=path, pattern=pattern, recursive=recursive, max_results=n + 1
            )
        except (PermissionError, ValueError) as exc:
            return {"error": str(exc)}
        except OSError as exc:
            return {"error": f"list failed: {exc}"}
        found = [str(p) for p in found or []]
        return {
            "files": found[:n],
            "count": min(len(found), n),
            "truncated": len(found) > n,
            "order": "name",
        }

    def _rg_candidates(base: Path, pattern: str, recursive: bool) -> Optional[list[Path]]:
        """Every file under `base` ripgrep would search, or None without ripgrep."""
        from .grep_engines import carried_rg, glob_scope
        from .search.ignore import ignore_globs as _ignore_globs

        rg = carried_rg() or shutil.which("rg")
        if not rg:
            return None
        # `--no-require-git`: ripgrep otherwise honours `.gitignore` only inside a git
        # repository, and a workspace that is a plain folder still means what it says.
        cmd = [rg, "--files", "--hidden", "--no-require-git", "--color=never"]
        where, target, globs = glob_scope(root, base, pattern if pattern != "*" else None)
        for include in globs:
            cmd += ["--glob", include]
        # Exclusions last: ripgrep resolves conflicting globs with the later one winning.
        for glob_pattern in _ignore_globs(base):
            cmd += ["--glob", glob_pattern]
        if not recursive:
            cmd += ["--max-depth", "1"]
        # Listed from the workspace (see `grep_engines.glob_scope`): a glob with a folder in
        # it (`harness/context/memory/*.py`) only matches paths ripgrep prints relative to its cwd.
        cmd.append(target)
        out = spawn_run(
            cmd, cwd=str(where), capture_output=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        if out.returncode not in (0, 1):  # 1 = nothing matched
            raise OSError((out.stderr or "ripgrep error").strip()[:300])
        return [where / line.strip() for line in (out.stdout or "").splitlines() if line.strip()]

    def _walk_candidates(base: Path, pattern: str, recursive: bool) -> tuple[list[Path], bool]:
        from .grep_engines import glob_matches
        from .search.ignore import IGNORE_DIRS as _IGNORE_DIRS

        found: list[Path] = []
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS] if recursive else []
            for name in files:
                if pattern and pattern != "*" and not glob_matches(
                    pattern, Path(dirpath) / name, root, base
                ):
                    continue
                found.append(Path(dirpath) / name)
                if len(found) >= _WALK_MAX:
                    return found, True
        return found, False

    def list_files(
        path: str = ".",
        pattern: str = "*",
        recursive: bool = True,
        max_results: int = _LIST_DEFAULT_MAX,
    ) -> dict[str, Any]:
        n = (
            max_results
            if isinstance(max_results, int) and max_results > 0
            else _LIST_DEFAULT_MAX
        )
        pattern = str(pattern or "*")
        recursive = recursive is not False
        if backend is not None:
            return _remote(path or ".", pattern, recursive, n)
        base = (root / (path or ".")).resolve()
        allowed = allowed_dirs(root, roots)
        if containing_root(base, allowed) is None:
            return {"error": _escapes(allowed)}
        if not base.exists():
            from .path_hints import missing

            return {"error": missing(base, str(path or "."), "folder")}
        if not base.is_dir():
            return {"error": f"Path is not a directory: {path}"}
        try:
            candidates = _rg_candidates(base, pattern, recursive)
            walk_capped = False
            if candidates is None:
                candidates, walk_capped = _walk_candidates(base, pattern, recursive)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"error": f"list failed: {exc}"}
        order = "newest first"
        if len(candidates) <= _MTIME_SORT_MAX:

            def _mtime(p: Path) -> float:
                try:
                    return p.stat().st_mtime
                except OSError:
                    return 0.0

            candidates.sort(key=lambda p: (-_mtime(p), str(p)))
        else:
            candidates.sort(key=str)
            order = "name"
        shown = [_label(p) for p in candidates[:n]]
        return {
            "files": shown,
            "count": len(shown),
            "total": len(candidates),
            "truncated": walk_capped or len(candidates) > n,
            "order": order,
        }

    list_files.__name__ = "list_files"
    list_files.__doc__ = _LIST_FILES_SCHEMA["function"]["description"]
    list_files.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="list_files",
        category="filesystem",
        risk_level="low",
        capabilities=["read"],
        requires_approval=False,
    )
    list_files.__tool_schema__ = _LIST_FILES_SCHEMA
    attach_contract(list_files, _BORROWED_CONTRACTS["list_files"])
    return [list_files]
