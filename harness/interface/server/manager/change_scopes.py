"""ChangeScopesMixin — what Review can compare: the index, the working tree, the
branch against its base, or one commit. Read-only.

`session_changes` / `session_change_diff` (reviews.py) answer "Uncommitted": everything that
differs from HEAD. These answer the other four:

  staged     what the next commit will hold         (`git diff --cached`)
  unstaged   what is edited but not staged          (`git diff`, plus untracked files)
  branch     what a pull request from HEAD would show (`git diff <merge-base> HEAD`)
  commit:<sha>  one commit's own change              (`git show <sha>`, first parent)

The path guard is `SourceControlMixin._scm_paths`'s: workspace-relative or refused. Renames
are read as a delete plus an add (`--no-renames`), so a path is always one file.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional


_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")
#: Untracked files listed at most, so a folder of build output cannot make a list of thousands.
UNTRACKED_LIMIT = 500


class ChangeScopesMixin:
    def _scope_base(self, root: Path) -> Optional[str]:
        """The branch a pull request from HEAD would target: the remote's default branch,
        else `main`, else `master`. None when the repository has none of them."""
        head = (self._git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD") or "").strip()
        for name in (head, "main", "master"):
            if name and self._git(root, "rev-parse", "--verify", "--quiet", name) is not None:
                return name
        return None

    def _scope_command(self, root: Path, scope: str) -> Optional[dict[str, Any]]:
        """The git argv prefix that produces a scope's diff, its label, and its base."""
        prefix = ["-c", "core.quotepath=off"]
        flags = ["--no-color", "--no-ext-diff", "--no-renames"]
        if scope == "staged":
            return {"argv": [*prefix, "diff", *flags, "--cached"], "staged": True}
        if scope == "unstaged":
            return {"argv": [*prefix, "diff", *flags], "staged": False}
        if scope == "branch":
            base = self._scope_base(root)
            if base is None:
                return {"error": "no base branch (main or master) to compare with"}
            merge_base = (self._git(root, "merge-base", "HEAD", base) or "").strip()
            if not merge_base:
                return {"error": f"HEAD and {base} share no history"}
            head = (self._git(root, "rev-parse", "HEAD") or "").strip()
            return {
                "argv": [*prefix, "diff", *flags, merge_base, "HEAD"],
                "base": base,
                # On the base branch itself there is nothing to compare.
                "same": merge_base == head,
            }
        if scope.startswith("commit:"):
            sha = scope[len("commit:"):]
            if not _SHA.match(sha):
                return {"error": "not a commit"}
            if self._git(root, "cat-file", "-e", f"{sha}^{{commit}}") is None:
                return {"error": "no such commit"}
            return {"argv": [*prefix, "show", *flags, "--format=", "--first-parent", "-m", sha]}
        return {"error": "unknown scope"}

    def scope_changes(self, session_id: str, scope: str) -> dict[str, Any]:
        """The changed files of a scope, as `session_changes` lists them."""
        root = self._scm_root(session_id)
        if root is None:
            return {"is_git": False, "files": [], "workspace": None, "scope": scope}
        spec = self._scope_command(root, scope)
        if spec is None or "error" in spec:
            return {"is_git": True, "files": [], "workspace": str(root), "scope": scope,
                    "error": (spec or {}).get("error", "unknown scope")}
        result: dict[str, Any] = {"is_git": True, "workspace": str(root), "scope": scope}
        if "base" in spec:
            result["base"] = spec["base"]
        if spec.get("same"):
            result.update(files=[], note="you are on the base branch")
            return result

        counts: dict[str, list[int]] = {}
        for line in (self._git(root, *spec["argv"], "--numstat") or "").splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            add, dele, path = parts
            counts[path] = [int(add) if add.isdigit() else 0, int(dele) if dele.isdigit() else 0]
        files: list[dict[str, Any]] = []
        for line in (self._git(root, *spec["argv"], "--name-status") or "").splitlines():
            code, _, path = line.partition("\t")
            if not path:
                continue
            state = code[:1] if code[:1] in ("A", "D") else "M"
            add, dele = counts.get(path, [0, 0])
            entry = {"path": path, "status": state, "add": add, "del": dele}
            if "staged" in spec:
                entry["staged"] = spec["staged"]
            files.append(entry)
        if scope == "unstaged":
            fresh = (self._git(root, "-c", "core.quotepath=off", "ls-files", "--others", "--exclude-standard") or "")
            for path in fresh.splitlines()[:UNTRACKED_LIMIT]:
                files.append({"path": path, "status": "A", "add": 0, "del": 0, "staged": False})
        files.sort(key=lambda f: f["path"])
        result["files"] = files
        return result

    def scope_change_diff(self, session_id: str, scope: str, path: str) -> dict[str, Any]:
        """One file's patch within a scope (an untracked file, in `unstaged`, as content)."""
        root = self._scm_root(session_id)
        rels = self._scm_paths([path])
        if root is None:
            return {"ok": False, "error": "not a git repository"}
        if rels is None:
            return {"ok": False, "error": "invalid path"}
        rel = rels[0]
        spec = self._scope_command(root, scope)
        if spec is None or "error" in spec:
            return {"ok": False, "error": (spec or {}).get("error", "unknown scope")}
        patch = "" if spec.get("same") else (self._git(root, *spec["argv"], "--", rel) or "")
        if patch.strip():
            return {"ok": True, "path": rel, "patch": patch, "untracked": False}
        if scope == "unstaged":
            untracked = (self._git(root, "ls-files", "--others", "--exclude-standard", "--", rel) or "").strip()
            if untracked:
                from .untracked import untracked_content

                return {"ok": True, "path": rel, **untracked_content(root / rel)}
        return {"ok": True, "path": rel, "patch": "", "untracked": False}
