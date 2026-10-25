"""What the agent created in this session, and the line that says so."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .commands import command_paths, fetched_paths
from .keys import resolve

WRITTEN = "written"
DOWNLOADED = "downloaded"

# Download tools that choose their own target and report it in the result. Reading the result
# records where the bytes landed, which beats guessing an argument name. `web_fetch` is left out on
# purpose: it returns page text and writes no file, so listing it would claim coverage we lack.
_DOWNLOAD_TOOLS = {"github_clone", "github_pull", "email_download_attachment"}


@dataclass(frozen=True)
class Origin:
    """How a path came to exist this session, and at which step."""

    step: int
    kind: str  # WRITTEN or DOWNLOADED


@dataclass(frozen=True)
class Match:
    """A proposed call that names a path this session created."""

    path: str  # as the call wrote it, for the line shown to the person
    origin: Origin
    steps_ago: int

    @property
    def downloaded(self) -> bool:
        return self.origin.kind == DOWNLOADED

    def render(self) -> str:
        """One line in a fixed vocabulary: never file content, never text an outsider wrote."""
        verb = "downloaded" if self.downloaded else "created"
        if self.steps_ago <= 0:
            when = "just now"
        elif self.steps_ago == 1:
            when = "1 step ago"
        else:
            when = f"{self.steps_ago} steps ago"
        return f"{self.path} was {verb} by the agent {when}"


def created_paths(tool_name: str, arguments: dict[str, Any], result: Any) -> tuple[list[str], str]:
    """`(paths, origin)` for a call that just succeeded, or `([], "")` when it created nothing."""
    from ..permissions import write_paths
    from ..risk import WRITE_TOOLS

    if tool_name in WRITE_TOOLS:
        paths, located = write_paths(tool_name, arguments or {})
        return (paths, WRITTEN) if located and paths else ([], "")
    if tool_name in _DOWNLOAD_TOOLS:
        landed = result.get("path") if isinstance(result, dict) else None
        return ([str(landed)], DOWNLOADED) if landed else ([], "")
    if tool_name == "run_shell":
        fetched = fetched_paths(str((arguments or {}).get("command", "")))
        return (fetched, DOWNLOADED) if fetched else ([], "")
    return ([], "")


def referenced_paths(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    """The paths a proposed call would run or act on. Only the shell, for now: it is where a file
    written and then executed lands, and where the command text hides the effect."""
    if tool_name == "run_shell":
        return command_paths(str((arguments or {}).get("command", "")))
    return []


class SessionFiles:
    """The files the agent created, per session. It lives only in memory like the engine's other
    per-session state, so a restart starts clean and does not inherit stale provenance."""

    def __init__(self, workspace_root: Path) -> None:
        self.root = Path(workspace_root)
        self._files: dict[str, Origin] = {}

    def record(self, tool_name: str, arguments: dict[str, Any], result: Any, *, step: int) -> None:
        """Note what a successful call created. Never call it for a failed one: a write that raised
        left nothing on disk to run."""
        paths, kind = created_paths(tool_name, arguments, result)
        for path in paths:
            # A later write or download to the same path replaces the entry, since the newer bytes
            # are the ones that would run.
            self._files[resolve(path, self.root)] = Origin(step=step, kind=kind)

    def match(self, tool_name: str, arguments: dict[str, Any], *, step: int) -> Optional[Match]:
        """The most recently created path the call names, or None. The newest wins because the
        agent controlled its contents most recently."""
        best: Optional[Match] = None
        for path in referenced_paths(tool_name, arguments):
            origin = self._files.get(resolve(path, self.root))
            if origin is None:
                continue
            if best is None or origin.step > best.origin.step:
                best = Match(path=path, origin=origin, steps_ago=max(step - origin.step, 0))
        return best
