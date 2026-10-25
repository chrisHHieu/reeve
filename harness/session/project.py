"""Project context — AGENTS.md + rules ingestion into the system prompt.

Four instruction tiers, broadest first:

1. **managed** — `<state-dir>/managed/AGENTS.md`, an administrator's policy for everyone
   on the machine. The app only reads it;
2. **user** — the person's SOUL.md (`memory/settings.py`, injected by the engine builder).
   It is the one user-level file; a legacy `<state-dir>/AGENTS.md` is merged into it once;
3. **project** — AGENTS.md (else CLAUDE.md, else `.claude/CLAUDE.md`) in every directory
   from the filesystem root down to the workspace, outermost first so the closest reads
   last and wins;
4. **local** — `AGENTS.local.md` (else `CLAUDE.local.md`) beside each of those: the
   person's own notes for this repository, gitignored, read right after its directory's
   project file.

- **At build** (`load_agents_md`): tiers 1, 3 and 4 for the workspace and its ancestors.
- **On demand** (`nested_agents_md`): the project and local files BELOW the workspace root
  are prompt weight until the agent actually works in that directory.

Each directory's conventions come from the first of `CONVENTION_FILES` it has —
AGENTS.md, else CLAUDE.md, else `.claude/CLAUDE.md` — so a repo written for that tool
is read too; the block's tag names the file it came from. It is discovered from the files
  the session has observed (read or written, `tools/observed.py`) and injected at the NEXT
  turn's start (the engine's `_stamp_nested_agents`) — the same append-only rule the live
  context block follows, because a mid-turn injection would rewrite the cached prefix.

**`@path` imports** inside any instruction or rule file (`expand_imports`, memory v2 Phase
2): expanded where they stand, recursive up to four hops, never inside code spans or
fences. An import that resolves OUTSIDE the workspace is skipped with a marker in a project
file (a committed `@~/.ssh/config` must not load); the user's and the administrator's own
files may read anywhere. Path-scoped rules live in `project_rules.py`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from harness.safety.secrets import state_dir

#: How many nested files one pass may inject. A single `grep` can observe hundreds of
#: directories, and each file is prompt weight on every later request; the pass is capped,
#: and the next turn picks up wherever this one stopped.
NESTED_PER_PASS = 6

#: How deep `@path` imports may chain (a four-hop cap).
MAX_IMPORT_DEPTH = 4

#: `@path/to/file`, not preceded by a word character or backtick. The character class
#: covers what a repo path may hold; a token that does not resolve to a real file is left
#: as literal text, so an email address or a decorator never turns into an import.
_IMPORT_RE = re.compile(r"(?<![`\w])@([\w./~\\-]+)")



def default_managed_agents_path() -> Path:
    """The administrator's instructions for everyone on this machine (tier 1)."""
    return state_dir() / "managed" / "AGENTS.md"


#: The instruction files a directory's conventions may live in, first match wins. A repo
#: written for it keeps them in CLAUDE.md, and one with none of our name was a
#: repo whose conventions the agent never saw: four of the owner's own repos (wealthlog
#: among them) had only CLAUDE.md (2026-09-25 audit, W7). AGENTS.md wins when both exist —
#: a CLAUDE.md beside it is usually a pointer to it (`@AGENTS.md`).
CONVENTION_FILES = ("AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md")


#: A directory's personal, uncommitted conventions (tier 4), first match wins — Claude
#: Code's `CLAUDE.local.md`, under our name first.
LOCAL_FILES = ("AGENTS.local.md", "CLAUDE.local.md")


def _first(directory: Path, names: tuple[str, ...]) -> Optional[Path]:
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def conventions_file(directory: Path) -> Optional[Path]:
    """The file holding `directory`'s conventions, or None."""
    return _first(directory, CONVENTION_FILES)


def local_file(directory: Path) -> Optional[Path]:
    """The file holding `directory`'s personal conventions, or None."""
    return _first(directory, LOCAL_FILES)


def directory_files(directory: Path) -> list[Path]:
    """A directory's instruction files in reading order: project, then local."""
    return [f for f in (conventions_file(directory), local_file(directory)) if f is not None]


def render_agents_md(label: str, text: str, *, path: str = "", name: str = "AGENTS.md") -> str:
    """One conventions block, labelled, named by its file and (for nested files) pathed."""
    attr = f' path="{path}"' if path else ""
    tag = Path(name).name
    return f"<{label} {tag}{attr}>\n{text.strip()}\n</{label} {tag}>"


#: A block `render_agents_md` or `project_rules.render_rule` wrote: what the context panel
#: counts as instruction files.
_INSTRUCTION_BLOCK = re.compile(
    r'<(?P<label>[a-z]+) (?P<tag>[^\s<>"]+\.md)(?: path="[^"]*")?>\n.*?\n</(?P=label) (?P=tag)>'
    r'|<rule path="[^"]*">\n.*?\n</rule>',
    re.S,
)


def instruction_chars(text: str) -> int:
    """How many characters of `text` are instruction-file blocks (AGENTS.md, CLAUDE.md,
    a rule), as the request carries them."""
    if not isinstance(text, str) or "<" not in text:
        return 0
    return sum(len(found.group(0)) for found in _INSTRUCTION_BLOCK.finditer(text))


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _ancestors(root: Path) -> list[Path]:
    """`root`'s parent chain, closest first, ending at the filesystem root."""
    chain: list[Path] = []
    parent = root.parent
    while parent != parent.parent:
        chain.append(parent)
        parent = parent.parent
    if parent != root:  # the filesystem root itself (D:\\, /) — guard a root workspace
        chain.append(parent)
    return chain


# -- `@path` imports (memory v2 Phase 2) ---------------------------------------------


def _strip_code(line: str) -> str:
    """The line with inline code spans blanked (same length), so `@README` inside
    backticks stays literal text while its offsets survive for the replacement pass."""
    return re.sub(r"`[^`]*`", lambda m: " " * len(m.group(0)), line)


def _resolve_import(token: str, base: Path) -> Optional[Path]:
    candidate = Path(token).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _read_imported(
    target: Path,
    *,
    workspace: Optional[Path],
    depth: int,
    seen: set[str],
    external_ok: bool,
) -> str:
    seen.add(str(target))
    return expand_imports(
        _read(target),
        source=target,
        workspace=workspace,
        depth=depth,
        seen=seen,
        external_ok=external_ok,
    )


def expand_imports(
    text: str,
    *,
    source: Path,
    workspace: Optional[Path] = None,
    depth: int = MAX_IMPORT_DEPTH,
    seen: Optional[set[str]] = None,
    external_ok: bool = False,
) -> str:
    """Expand `@path` imports where they stand inside `text` (the CLAUDE.md
    rule), relative to the file that mentions them, recursively up to `MAX_IMPORT_DEPTH`.

    Fenced blocks and inline code spans are not import sites. A token that does not
    resolve to a file is not an import and stays as written. An import outside
    `workspace` is skipped with a marker unless `external_ok`: a committed project file
    must not pull in the machine's private files; the user-level file may.
    """
    if depth <= 0 or not text or "@" not in text:
        return text
    seen = set() if seen is None else seen
    out: list[str] = []
    fenced = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fenced = not fenced
            out.append(line)
            continue
        if fenced or "@" not in line:
            out.append(line)
            continue
        masked = _strip_code(line)
        replaced = line
        # Right to left so earlier matches keep their offsets in the original line.
        for match in reversed(list(_IMPORT_RE.finditer(masked))):
            token = match.group(1).rstrip(".,;:!?)\"'").rstrip("/")
            if not token:
                continue
            target = _resolve_import(token, source.parent)
            if target is None:
                continue  # not a file: the token stays as written (emails, decorators)
            if (
                workspace is not None
                and not target.is_relative_to(workspace)
                and not external_ok
            ):
                replacement = (
                    f"(import skipped: {token} is outside the workspace — move the file "
                    "inside it, or import it from your SOUL.md)"
                )
            elif str(target) in seen:
                replacement = f"(already imported: {token})"
            else:
                replacement = _read_imported(
                    target,
                    workspace=workspace,
                    depth=depth - 1,
                    seen=seen,
                    external_ok=external_ok,
                )
            replaced = (
                replaced[: match.start(1)] + replacement + replaced[match.end(1) :]
            )
        out.append(replaced)
    return "\n".join(out)


def load_agents_md(workspace: str | Path, *, managed_path: Optional[str | Path] = None) -> str:
    """A system-prompt block from every instruction file that scopes this workspace.

    Order is the precedence: the managed policy first, then ancestors from the outermost
    in, then the workspace root — each directory's project file, then its local file — so
    the most specific text reads last. (The user tier, SOUL.md, has its own block.)
    """
    parts: list[str] = []

    m = Path(managed_path) if managed_path is not None else default_managed_agents_path()
    if m.is_file():
        # The administrator's own file, like the user's: its imports may read anywhere.
        text = expand_imports(_read(m), source=m, workspace=None, external_ok=True)
        if text.strip():
            parts.append(render_agents_md("managed", text))

    root = Path(workspace).expanduser().resolve()
    for ancestor in reversed(_ancestors(root)):
        for candidate in directory_files(ancestor):
            text = expand_imports(_read(candidate), source=candidate, workspace=root)
            parts.append(
                render_agents_md("parent", text, path=str(ancestor), name=candidate.name)
            )

    for candidate in directory_files(root):
        text = expand_imports(_read(candidate), source=candidate, workspace=root)
        label = "local" if candidate.name in LOCAL_FILES else "project"
        parts.append(render_agents_md(label, text, name=candidate.name))

    return "Project conventions:\n" + "\n\n".join(parts) if parts else ""


def nested_agents_md(
    workspace: str | Path,
    observed_paths: Iterable[str],
    *,
    skip: Iterable[str] = (),
    limit: int = NESTED_PER_PASS,
) -> list[tuple[str, str]]:
    """Instruction files scoping the directories the agent has read into.

    For each observed file, every directory on the chain BELOW the workspace root is a
    candidate (the root's own files are already in the system prompt), root-most first so
    a package file reads after the directory above it. A directory's project file and its
    local file (tier 4) arrive together as one text, project first. `skip` holds the
    relative paths already injected this session — a directory is stated once. Paths
    outside the workspace (scratch dirs, granted folders) are not this rule's business.
    Returns `[(relative path, text), …]`, capped at `limit` per call.
    """
    root = Path(workspace).expanduser().resolve()
    done = set(skip)
    found: dict[str, str] = {}
    order: list[str] = []
    for raw in observed_paths or ():
        try:
            path = Path(str(raw)).resolve()
        except (OSError, ValueError):
            continue
        if not path.is_relative_to(root):
            continue
        directory = path if path.is_dir() else path.parent
        chain: list[Path] = []
        cursor = directory
        while cursor != root and cursor.is_relative_to(root):
            chain.append(cursor)
            cursor = cursor.parent
        for node in reversed(chain):  # root-most first: broader conventions read first
            files = directory_files(node)
            if not files:
                continue
            relative = node.relative_to(root).as_posix()
            if relative in done or relative in found:
                continue
            texts = [expand_imports(_read(f), source=f, workspace=root).strip() for f in files]
            text = "\n\n".join(t for t in texts if t)
            if not text.strip():
                continue
            found[relative] = text
            order.append(relative)
            if len(order) >= limit:
                return [(rel, found[rel]) for rel in order]
    return [(rel, found[rel]) for rel in order]
