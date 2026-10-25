"""The folders a session may work in.

A conversation belongs to no project of its own: it gets a **scratch** folder for itself (the first
root, writable, where things are saved unless asked otherwise) and may be given more, each read-only
or read-write. One `list[RootDir]` is shared, by reference, by the permission engine (what is in
bounds), the file tools (where a path resolves) and the context block (what the agent is told it
has), so a folder added mid-conversation is appended in place and all three see it at once. The
first entry is always the primary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


@dataclass
class RootDir:
    path: Path
    writable: bool = False
    label: str = ""  # display name; defaults to the directory's basename
    # A root the HARNESS owns (the auto-memory directory). The file tools use it like any
    # root, but it is never listed as one of "their" folders and never persisted as a session extra,
    # because it is re-derived on every build.
    managed: bool = False
    # A root the SESSION took: a call reached the folder's project and was allowed. Its
    # read-only start is where it began, not a mark the person wrote, so a write there is asked like
    # any write outside the writable roots and is never refused for it. A person adding the same
    # folder makes it theirs and clears this.
    reached: bool = False

    def __post_init__(self) -> None:
        self.path = Path(self.path).expanduser().resolve()
        self.label = self.label or self.path.name or str(self.path)

    def to_dict(self) -> dict[str, Any]:
        """As a session stores it; `reached` only when set."""
        stored: dict[str, Any] = {"path": str(self.path), "writable": self.writable, "label": self.label}
        if self.reached:
            stored["reached"] = True
        return stored


def _as_root(entry: Any) -> RootDir:
    if isinstance(entry, RootDir):
        return entry
    if isinstance(entry, (str, Path)):
        return RootDir(path=entry)  # a bare path is never a grant to write
    if isinstance(entry, dict):
        given = entry.get
        return RootDir(path=entry["path"], writable=bool(given("writable", False)), label=given("label", ""), reached=bool(given("reached", False)))
    # Any other object is taken for its `.path`, `.writable` and `.reached`.
    return RootDir(path=entry.path, writable=bool(getattr(entry, "writable", False)), reached=bool(getattr(entry, "reached", False)))


def normalize_roots(roots: Iterable[Any] | None) -> list[RootDir]:
    """`RootDir`s from a list that may mix them with `{path, writable, label}` dicts and plain
    paths. A plain str or Path comes out read-only: writing takes a dict or a `RootDir`."""
    return [_as_root(entry) for entry in roots or ()]


def _path_of(entry: Any) -> Any:
    if isinstance(entry, dict):
        return entry.get("path")
    if isinstance(entry, (str, Path)):
        return entry
    return getattr(entry, "path", None)


def allowed_dirs(primary: Any, roots: Iterable[Any] | None = None) -> list[Path]:
    """Every directory a session tool may touch, primary first, resolved FRESH.

    `roots` is the engine's live, mutable list, which `add_root` appends to in place when the person
    grants a folder, so a tool that resolves it on each call sees a grant on its very next call.
    Snapshotting it once when the tool was built is how the file tools kept refusing a folder the
    shell already had: session 435e0be4 asked for `D:\\Project\\reeve`, was granted it, and
    `read_file` still answered "path escapes the session's directories" in the same turn while
    `run_shell` read it fine.
    """
    found = [Path(primary).expanduser().resolve()]
    for entry in roots or []:
        raw = _path_of(entry)
        if not raw:
            continue
        resolved = Path(str(raw)).expanduser().resolve()
        if resolved not in found:
            found.append(resolved)
    return found


def containing_root(target: Path, dirs: Iterable[Path]) -> Optional[Path]:
    """The first of `dirs` that contains `target`, or None when it lies outside all of them."""
    for directory in dirs:
        if target.is_relative_to(directory):
            return directory
    return None


def _inside(inner: Path, outer: Path) -> bool:
    return inner != outer and inner.is_relative_to(outer)


def _effective_writable(root: RootDir, roots: list[RootDir]) -> bool:
    """Writable as the boundary decides it: a writable root anywhere above wins
    (`Boundary.in_read_only_root`)."""
    return root.writable or any(other.writable and _inside(root.path, other.path) for other in roots)


def _tag(index: int, root: RootDir) -> str:
    if index == 0 and root.label == "scratch":
        return " — primary scratch, the default place to save files"
    if index == 0:
        return " — the session's workspace (relative paths resolve here)"
    if root.label == "scratch":
        return (
            " — your scratch directory: temporary files, and artifacts you don't "
            "want to leave inside the workspace"
        )
    return ""


def render_context(roots: list[RootDir]) -> str:
    """The `<system-context>` body listing the directories available this turn; empty with no roots.

    Each line states the access the boundary actually enforces, and a folder inside another listed
    one with the same access is left out, since it adds nothing. Listing
    D:\\Project\\wealthlog\\frontend as "[read-only]" inside the read-write wealthlog workspace told
    the model it could not write there when it could, and it asked the person for write access it
    already had (session b3a8491e-9db).
    """
    if not roots:
        return ""
    lines = ["Directories you can work in with the file and shell tools:"]
    for index, root in enumerate(roots):
        writable = _effective_writable(root, roots)
        if index and any(_inside(root.path, other.path) and _effective_writable(other, roots) == writable for other in roots):
            continue  # inside a listed folder with the same access: one more line, nothing more to know
        if writable:
            access = "read-write"
        elif root.reached:
            # Not refused: a write here is judged as any write outside these folders is, as the
            # permissions line says (asked in workspace-write, run in full access). "Writes need
            # approval", the wording before, was untrue in full access.
            access = "read; a write here is outside these directories"
        else:
            access = "read-only"
        lines.append(f"- {root.path} [{access}]{_tag(index, root)}")
    if any(index and root.label == "scratch" for index, root in enumerate(roots)):
        lines.append(
            "A relative path is taken from the workspace; give an absolute path for any other "
            "directory. Write only in read-write directories. Reports, analyses and anything else "
            "that is not part of the repository go in the scratch directory, where the user sees "
            "them in the Artifacts panel; the workspace gets only the changes that belong there."
        )
    else:
        lines.append(
            "A relative path is taken from the primary directory; give an absolute path for any "
            "other directory. Write only in read-write directories. Ask where a deliverable should "
            "go if the user seems to care; otherwise save it in the primary scratch."
        )
    return "\n".join(lines)
