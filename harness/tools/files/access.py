"""The sentence a file tool gives for a path that is outside every folder the session holds."""

from __future__ import annotations

from pathlib import Path


def escape_refusal(allowed: list[Path]) -> str:
    """The one refusal for a path outside every root, the same from every file tool.

    It names the folders that would have worked. A bare "path escapes the session's directories"
    left the agent with a grant it could not find and no way to tell which spelling it was missing
    (one session retried paths inside a folder the shell had already read).

    It used to name a remedy, a tool that asked the person for the folder. The model never once
    called it (48 refusals in the store from 2026-09-26, 0 calls; 19 next calls were `run_shell`
    reading around the boundary), so the remedy is now the call itself: a file tool's call that
    reaches another folder is judged by the permission engine, and once allowed the folder is added
    to the session before the tool runs. This refusal is left for a call that reached a
    tool without that step (a remote session's own scoping, a host with no roots list)."""
    return "path is outside the session's folders: " + ", ".join(str(p) for p in allowed)
