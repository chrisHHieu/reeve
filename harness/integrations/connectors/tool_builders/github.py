"""The GitHub tools that act on this machine: clone and pull.

Everything that reads or writes GitHub itself belongs to the vendor's MCP server, with its tools
and groups. A server cannot put a repository on the person's disk, so these two stay ours.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from ..github_git import git_auth_args, git_base, run_git
from ..toolkit import attach, function_schema


def github_tools(secrets: Any, *, roots: Optional[list[Any]] = None, remote: bool = False) -> list[Callable[..., Any]]:
    def writable_target(raw: str, *, default_name: str = "") -> tuple[Optional[Path], Optional[dict[str, Any]]]:
        """A directory inside a writable granted root. A clone or a pull never touches anything
        the person has not shared with the session."""
        if remote:
            return None, {
                "error": "cloning a repository writes to this computer, which is not "
                "available in a remote session — run the clone on the remote host "
                "through run_shell instead."
            }
        writable = [root.path for root in (roots or []) if root.writable]
        if not writable:
            return None, {"error": "no writable session directory to clone into"}
        path = Path(str(raw)).expanduser().resolve() if raw else (writable[0] / default_name).resolve()
        if not any(path.is_relative_to(root) for root in writable):
            return None, {"error": f"{path} is outside the session's writable directories"}
        return path, None

    def head_of(path: Path) -> str:
        return run_git(["rev-parse", "--short", "HEAD"], cwd=path)[0]

    def github_clone(owner: str, repo: str, directory: str = "") -> dict[str, Any]:
        target, refused = writable_target(directory, default_name=repo)
        if refused:
            return refused
        if target.exists() and any(target.iterdir()):
            return {"error": f"{target} already exists and is not empty (use github_pull?)"}
        _, failure = run_git([*git_auth_args(secrets, owner), "clone", f"{git_base()}/{owner}/{repo}.git", str(target)])
        if failure:
            return {"error": f"clone failed: {failure}"}
        # The token travels as a header on the command line only, so nothing secret can be in the
        # clone's config. That is checked, because the rule is no token at rest.
        config = (target / ".git" / "config").read_text()
        if "AUTHORIZATION" in config or "x-access-token" in config:
            shutil.rmtree(target)
            return {"error": "clone aborted: credentials would have persisted"}
        return {"ok": True, "path": str(target), "head": head_of(target)}

    def github_pull(directory: str) -> dict[str, Any]:
        target, refused = writable_target(directory)
        if refused:
            return refused
        if not (target / ".git").exists():
            return {"error": f"{target} is not a git repository"}
        origin, failure = run_git(["remote", "get-url", "origin"], cwd=target)
        if failure:
            return {"error": f"no origin remote: {failure}"}
        found = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", origin)
        owner = found.group(1) if found else ""
        _, failure = run_git([*git_auth_args(secrets, owner), "-C", str(target), "pull", "--ff-only"])
        if failure:
            return {"error": f"pull failed: {failure}"}
        return {"ok": True, "path": str(target), "head": head_of(target)}

    return [
        attach(
            github_clone,
            function_schema(
                "github_clone",
                "Clone a GitHub repository into a session folder so the agent can "
                "explore the code locally. Private repos use a short-lived token "
                "that is never written to disk. Requires user approval.",
                {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "directory": {
                        "type": "string",
                        "description": "target path inside a granted folder (default: <primary>/<repo>)",
                    },
                },
                ["owner", "repo"],
            ),
            approval=True,
            caps=["github", "read"],
        ),
        attach(
            github_pull,
            function_schema(
                "github_pull",
                "Fast-forward an existing clone in a session folder to the latest "
                "upstream commits. Requires user approval.",
                {"directory": {"type": "string"}},
                ["directory"],
            ),
            approval=True,
            caps=["github", "read"],
        ),
    ]
