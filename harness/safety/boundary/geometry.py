"""The reach of a session: which folders exist to the agent and which accept writes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .protected import is_protected_in_project, protected_dirs, protected_paths, secret_paths
from .tool_paths import command_text, write_paths


def _inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _field(root: Any, name: str, default: Any = None) -> Any:
    if isinstance(root, dict):
        return root.get(name, default)
    return getattr(root, name, default)


def project_folder(folder: Path) -> Path:
    """The project a folder belongs to: the nearest folder at or above it that holds `.git`, else
    the folder itself.

    A reach is granted for a project and not for the folder one file happened to sit in: reading
    `wealthlog/frontend/...` and then `wealthlog/backend/...` added two folders to one session, and
    the Files panel filled with fragments of one repository (owner, 2026-10-18). A repository at
    the home folder or a drive root (a dotfiles repo) is not a project boundary, so a folder under
    one stays itself."""
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        home = None
    for candidate in (folder, *folder.parents):
        if candidate == home or candidate.parent == candidate:
            break
        try:
            if (candidate / ".git").exists():
                return candidate
        except OSError:
            break
    return folder


@dataclass
class Boundary:
    """The reach geometry for one session: which directories exist to the agent, which of them
    accept writes, and which files are out of bounds whoever asks.

    `roots` is the same changeable list the engine holds, kept by reference and read at every check,
    so folders added or removed at runtime, and appends made in place by the directory-grant flow,
    take effect without rebuilding anything. A sandbox provider would replace this object and leave
    the approval engine around it alone."""

    workspace_root: Path
    roots: Optional[list] = None

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root).expanduser().resolve()
        if self.roots is None:
            self.roots = [{"path": self.workspace_root, "writable": True}]

    def _resolved(self) -> list[tuple[Path, bool]]:
        found: list[tuple[Path, bool]] = []
        for root in self.roots or []:
            if isinstance(root, dict):
                path, writable = root["path"], bool(root.get("writable", False))
            elif isinstance(root, (str, Path)):
                path, writable = root, True
            else:  # a RootDir-like object
                path, writable = getattr(root, "path"), bool(getattr(root, "writable", False))
            found.append((Path(path).expanduser().resolve(), writable))
        return found

    def _marked_read_only(self) -> list[Path]:
        """The non-writable roots the person added: a folder the session reached on its own
        (`reached`) is not a mark anybody wrote."""
        return [
            Path(_field(root, "path")).expanduser().resolve()
            for root in self.roots or []
            if not isinstance(root, (str, Path))
            and not _field(root, "writable", False)
            and not _field(root, "reached", False)
        ]

    # -- the geometry ----------------------------------------------------------------------

    def candidate(self, path: str) -> Path:
        """A target path as this world sees it. A relative path resolves against the workspace
        root, and an absolute or `~` path is taken as written."""
        given = Path(path).expanduser()
        return given.resolve() if given.is_absolute() else (self.workspace_root / given).resolve()

    def under_any_root(self, path: str) -> bool:
        """Inside the world at all: the check the read-only session grant relies on."""
        candidate = self.candidate(path)
        return any(_inside(candidate, root) for root, _ in self._resolved())

    def under_writable_root(self, path: str) -> bool:
        """Inside a root that accepts writes: the write boundary."""
        candidate = self.candidate(path)
        return any(_inside(candidate, root) for root, writable in self._resolved() if writable)

    def in_read_only_root(self, path: str) -> bool:
        """Inside a root the person marked non-writable, and not inside a writable one.

        The mark is a constraint they wrote (the folder-grant flow asks for it), so it keeps its
        force in every preset, unlike a path outside every root, which is an ordinary ask.
        A writable root nested inside wins, so an inner folder can be reopened without
        the outer mark swallowing it. A folder added because a read reached it carries
        no mark: a write there is a write outside the writable roots, asked like any other, and
        full access runs it. Read as a mark, it refused every write to a project the agent had
        first read, in full access too (owner, 2026-10-18)."""
        candidate = self.candidate(path)
        if any(_inside(candidate, root) for root, writable in self._resolved() if writable):
            return False
        return any(_inside(candidate, root) for root in self._marked_read_only())

    def settings_hit(self, tool_name: str, arguments: dict[str, Any], is_shell: bool) -> Optional[str]:
        """The protected settings path this call would change, or None.

        For writes the real target is resolved. For the shell only the command text can be
        inspected, so it stops accidents and casual attempts and not a determined adversary, which
        takes an OS sandbox; it is cheap and worth having anyway.

        A shell command is matched on the full path only, never a bare filename: matching
        `secrets.json` anywhere would refuse unrelated work that merely mentions the name. A command
        naming the real settings path is refused whether it reads or writes, because the text does
        not say which, and the careful direction is right for these files."""
        targets = [str(path) for path in protected_paths()]
        folders = [str(path) for path in protected_dirs()]
        if is_shell:
            command = command_text(tool_name, arguments)
            if not command:
                return None
            lowered = command.replace("\\", "/").lower()
            for target in [*targets, *folders]:
                if target.replace("\\", "/").lower() in lowered:
                    return target
            return None
        paths, located = write_paths(tool_name, arguments)
        if not located:
            return None  # an unlocatable write has already failed closed in the caller
        resolved = {str(self.candidate(path)) for path in paths}
        for target in targets:
            if str(Path(target).resolve()) in resolved:
                return target
        for folder in folders:
            base = Path(folder).resolve()
            if any(base in Path(path).parents for path in resolved):
                return folder
        return None

    def outside_roots(self, paths: list[str]) -> list[str]:
        """The paths that lie outside every root, readable or writable."""
        return [path for path in paths if not self.under_any_root(path)]

    def read_folder(self, path: str) -> Path:
        """The folder a reach of `path` is granted as: the project the folder or the file's folder
        belongs to (`project_folder`)."""
        candidate = self.candidate(path)
        return project_folder(candidate if candidate.is_dir() else candidate.parent)

    def secret_hit(self, paths: list[str]) -> Optional[str]:
        """The credentials file a read would open, or None."""
        resolved = {self.candidate(path) for path in paths}
        for secret in secret_paths():
            if secret.resolve() in resolved:
                return str(secret)
        return None

    def deferred_execution_hit(self, paths: list[str]) -> bool:
        """True when a write's target is a project file that runs on a later, innocent-looking
        action (git hooks, CI configs, `.reeve/` policy)."""
        return any(is_protected_in_project(self.candidate(path)) for path in paths)
