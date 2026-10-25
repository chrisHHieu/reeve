"""Remote directory listing — the environment picker's browser.

One SSH round trip answers "what folders live directly under this path on the host":
the requested path is resolved ON THE HOST (``cd -P`` + ``pwd -P``), so ``..`` and
symlinks can never escape the directory the host says was asked for, and the resolved
base is required to be absolute before any entry is built from it. Immediate
subdirectories are listed with POSIX ``find -L`` (symlinks to directories are
directories), bounded both by the command (``head``) and by the parser.

The command carries the ``# reeve-remote-op`` marker understood by
``harness.testing.fake_ssh.FakeRemoteHost`` — inert to a real shell, and what lets the
tests exercise the exact command string with no POSIX emulator.
"""

from __future__ import annotations

import posixpath
from typing import Any, Optional

from ..remote_environments import shell_quote
from .transport import (
    RemoteCommandError,
    RemoteProtocolError,
    RemoteSession,
    op_script,
)

#: The most entries one listing may return (the picker is a menu, not a filesystem dump).
MAX_DIRECTORY_ENTRIES = 500

#: One output line longer than this is not a path a person can use; it is skipped rather
#: than echoed (bounded output on top of the command's own `head`).
_MAX_PATH_CHARS = 8192


def canonical_remote_directory(path: str) -> str:
    """Validate and lexically normalize a requested remote directory path.

    Absolute POSIX only; newlines/NUL are refused because a path that cannot round-trip
    through the command line must not be attempted. The lexical ``normpath`` is only the
    shape check — the authoritative resolution is the host's own ``pwd -P``.
    """
    text = str(path or "").strip()
    if not text:
        raise ValueError("a remote directory path is required")
    if "\0" in text or "\n" in text or "\r" in text:
        raise ValueError("the remote path cannot contain newlines or NUL bytes")
    if not posixpath.isabs(text):
        raise ValueError("the remote path must be absolute (start with /)")
    return posixpath.normpath(text)


def list_directory_script(path: str, limit: int = MAX_DIRECTORY_ENTRIES) -> str:
    """The framed listing command: resolve on the host, print the base, list dirs.

    Exit codes are the parser's stable contract: 2 = missing, 3 = not a directory,
    4 = the host refused the read (permission). Anything else is a remote command
    failure and is reported in the transport's own vocabulary.
    """
    quoted = shell_quote(path)
    bounded = max(1, int(limit))
    command = (
        f"if [ ! -e {quoted} ]; then "
        "printf 'reeve-listdir: no such directory\\n' >&2; exit 2; fi\n"
        f"if [ ! -d {quoted} ]; then "
        "printf 'reeve-listdir: not a directory\\n' >&2; exit 3; fi\n"
        f"resolved=$(cd -P -- {quoted} && pwd -P) || "
        "{ printf 'reeve-listdir: permission denied\\n' >&2; exit 4; }\n"
        "printf '%s\\n' \"$resolved\"\n"
        f"find -L \"$resolved\" -mindepth 1 -maxdepth 1 -type d | head -n {bounded + 1}\n"
    )
    return op_script("listdir", {"path": path, "limit": bounded}, command=command)


def parse_directory_listing(
    output: str, *, limit: int = MAX_DIRECTORY_ENTRIES
) -> dict[str, Any]:
    """``{"path": resolved, "entries": [{"name", "path"}, …]}`` from the command output.

    The first non-empty line is the host-resolved base and MUST be absolute — a host that
    answers with a relative or foreign path is refused rather than trusted, which is what
    makes ``..`` unable to walk the listing out of the requested tree. Entries are the
    lines that sit directly under that base; anything else (stray output, an injected
    line, a nested path, a duplicate) is skipped.
    """
    wanted = max(1, int(limit))
    lines = [line.rstrip("\r").strip() for line in str(output or "").split("\n")]
    lines = [line for line in lines if line]
    if not lines:
        raise RemoteProtocolError(
            "The SSH host returned no directory listing; expected the resolved path."
        )
    resolved = lines[0]
    if (
        not resolved.startswith("/")
        or "\0" in resolved
        or len(resolved) > _MAX_PATH_CHARS
    ):
        raise RemoteProtocolError(
            "The SSH host returned an invalid directory path; expected an absolute "
            "POSIX path."
        )
    prefix = resolved.rstrip("/") + "/"
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in lines[1:]:
        if len(line) > _MAX_PATH_CHARS or "\0" in line or not line.startswith(prefix):
            continue
        name = line[len(prefix):]
        if not name or "/" in name or name in (".", "..") or name in seen:
            continue
        seen.add(name)
        entries.append({"name": name, "path": line})
        if len(entries) >= wanted:
            break
    entries.sort(key=lambda entry: (entry["name"].lower(), entry["name"]))
    return {"path": resolved, "entries": entries}


def list_remote_directories(
    session: RemoteSession,
    path: Optional[str] = None,
    *,
    limit: int = MAX_DIRECTORY_ENTRIES,
) -> dict[str, Any]:
    """List one remote directory's immediate subdirectories.

    An omitted/empty ``path`` means the profile host's ``$HOME`` (the picker's honest
    default). Raises ``ValueError`` for a missing/not-a-directory target, ``PermissionError``
    when the host refuses the read, and the transport's typed errors for connection
    failures — the route maps each to its inline message.
    """
    requested = str(path or "").strip()
    if not requested:
        requested = session.ensure_home()
    target = canonical_remote_directory(requested)
    result = session.run(list_directory_script(target, limit))
    if result.exit_code != 0:
        detail = (result.stderr or result.output or "").strip()
        lowered = detail.lower()
        if result.exit_code == 2 or "no such file" in lowered:
            raise ValueError(f"Directory does not exist: {requested}")
        if result.exit_code == 3 or "not a directory" in lowered:
            raise ValueError(f"Path is not a directory: {requested}")
        if result.exit_code == 4 or "permission denied" in lowered:
            raise PermissionError(f"Permission denied reading {requested}")
        if result.exit_code is None:
            raise RemoteCommandError(
                "the remote host did not report an exit status while listing "
                f"{requested}: {detail or 'the connection closed before the command finished'}",
                output=result.output,
            )
        raise RemoteCommandError(
            f"remote command failed while listing {requested} "
            f"(exit {result.exit_code}): {detail or 'no output'}",
            exit_code=result.exit_code,
            output=result.output,
        )
    return parse_directory_listing(result.output, limit=limit)


__all__ = [
    "MAX_DIRECTORY_ENTRIES",
    "canonical_remote_directory",
    "list_directory_script",
    "list_remote_directories",
    "parse_directory_listing",
]
