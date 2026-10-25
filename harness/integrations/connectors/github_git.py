"""GitHub access for the two tools that touch this machine: clone and pull."""

from __future__ import annotations

import base64
import os
import subprocess
from typing import Any, Optional

from harness.platform.spawn import run as spawn_run
from harness.safety.secrets import SecretStore

from .headers import github_headers


def api_base() -> str:
    return os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


def git_base() -> str:
    return os.environ.get("GITHUB_GIT_URL", "https://github.com").rstrip("/")


def github_auth(
    secrets: SecretStore, install: str = "", *, force: bool = False
) -> tuple[Optional[dict[str, str]], Optional[dict[str, str]]]:
    """(headers, error). A personal access token in `github:default` wins; otherwise the token
    of the device sign-in that the vendor's MCP connection uses."""
    profile = secrets.get("github:default") or {}
    if profile.get("token"):
        return github_headers(profile["token"]), None
    # Signed in by the device grant: the token lives with the MCP connection, not in the
    # profile, and is the one the vendor's server is called with.
    from harness.extensions.mcp import device_auth

    granted = device_auth.bearer_header("github", secrets)
    if granted:
        return github_headers(granted["Authorization"].split(" ", 1)[1]), None
    return None, {"error": "github is not connected; missing token"}


def git_auth_args(secrets: SecretStore, owner: str) -> list[str]:
    """Git options that authenticate one invocation. The token rides an HTTP header on the
    command line and must never reach `.git/config` or a credential store: no token at rest.
    With no token the list only turns the credential helper off, and public repositories clone
    without auth."""
    headers, err = github_auth(secrets, owner)
    if err:
        return ["-c", "credential.helper="]
    token = headers["Authorization"].split(" ", 1)[1]
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraHeader=AUTHORIZATION: basic {basic}", "-c", "credential.helper="]


def run_git(args: list[str], *, cwd: Any = None, timeout: int = 600) -> tuple[str, str]:
    """(stdout, error). It never raises. The error text is cut short and carries no credential,
    because git does not echo header values."""
    try:
        done = spawn_run(
            ["git", *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout
        )
    except FileNotFoundError:
        return "", "git is not installed"
    except subprocess.TimeoutExpired:
        return "", "git timed out"
    if done.returncode != 0:
        return "", (done.stderr or done.stdout).strip()[-500:]
    return done.stdout.strip(), ""
