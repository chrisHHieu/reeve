"""Community skill installs — a thin wrapper around the `npx skills` CLI (batch 3/P2).

The community-standard installer is `npx skills add <source>` (vercel-labs/skills). We run it
as a thin wrapper (`npx -y skills@latest` + `--agent` injection + install/uninstall aliases),
with three safety
properties this module exists to guarantee:

* **Copy mode, never symlinks** — `--copy` is always passed, so nothing in the machine's real
  agent folders becomes a link farm back into a temp dir. (Windows without Developer Mode
  cannot create symlinks at all.)
* **A staging cwd, never the user's project** — the CLI runs with ``cwd`` = a fresh token dir
  under ``state_dir()/skills-staged/<token>``. The CLI's project scope resolves every agent
  folder relative to cwd, so the whole install lands inside one disposable directory which the
  preview/confirm flow below vets before anything is promoted into the product root.
* **No shell, bounded, and loud on failure** — argv is a list (no injection through `source`),
  the run has a timeout, and a missing `npx`/Node produces an actionable error instead of a
  silent skip.

What is deliberately NOT here: a URL `index.json` discovery protocol
(fetch an index, then download each skill's files).
It adds an HTTP fetch + index schema + per-file download path, and the `skills` CLI already
accepts GitHub repos, git URLs, and subpaths as sources. Deferred; revisit if URL-only skill
catalogs become a real user flow.

Lockfile format (``state_dir()/skills-lock.json``)::

    {"version": 1, "skills": {"<name>": {"source", "sourceType", "skillPath",
                                         "computedHash"}}}

``sourceType`` is one of ``"github" | "url" | "git"``; ``computedHash`` is a sha256 over the
skill's files (paths + bytes, sorted), prefixed ``sha256:`` — enough to answer "did the
installed copy drift from what we recorded".
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

#: The community CLI ships as the `skills` npm package; the moving tag is pinned.
SKILLS_CLI_PACKAGE = "skills@latest"
#: The community-neutral target: `-a universal` is the CLI's key for the `.agents/skills`
#: folder (the agents.md ecosystem's shared location; several agents alias onto it).
#: The neutral one is injected so no product-specific folder is
#: ever touched. A caller that wants a different local agent can name it.
DEFAULT_SKILL_AGENT = "universal"
#: A repo download + npm resolution; generous but bounded so a hung npx never wedges a request.
INSTALL_TIMEOUT_SECONDS = 300.0

#: Written at the staging root so confirm knows what was previewed without trusting the client.
INSTALL_MANIFEST_NAME = "install-manifest.json"

_SOURCE_MAX = 500

_MISSING_NODE_HINT = (
    "Node.js doesn't seem to be installed (or `npx` isn't on PATH). Install Node.js 20+ "
    "from https://nodejs.org and try again."
)

#: CLI failures that mean "the command itself wasn't found" (cmd.exe: 9009; POSIX shells: 127)
_NOT_FOUND_CODES = frozenset({127, 9009})
_NOT_FOUND_RE = re.compile(r"is not recognized|command not found|no such file", re.I)


@dataclass
class CliResult:
    """One completed CLI run — stdout/stderr captured, exit code preserved."""

    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def output(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


def npx_prefix() -> list[str]:
    """The platform's npx launch prefix.

    Windows has no ``npx.exe``: CreateProcess cannot run the ``npx.cmd`` shim directly, so the
    run goes through ``cmd /c`` (the same shape harness/extensions/mcp/config.py uses for the browser MCP
    server). Every other platform execs ``npx`` directly.
    """
    if sys.platform == "win32":
        return ["cmd", "/c", "npx"]
    return ["npx"]


def skill_cli_command(
    source: str,
    *,
    skills: Optional[Iterable[str]] = None,
    agent: Optional[str] = None,
    npx: Optional[list[str]] = None,
) -> list[str]:
    """The exact argv: ``npx -y skills@latest add <source> --copy -y [-s <name>…] [-a <agent>]``.

    ``npx`` is injectable so tests can pin the platform launch prefix without patching sys.
    """
    src = (source or "").strip()
    if not src:
        raise ValueError("A source is required — owner/repo, a git/URL, or a subpath in a repo.")
    if len(src) > _SOURCE_MAX:
        raise ValueError("That source is too long — paste a repository or URL, not a block of text.")
    if src.startswith("-") or "\n" in src or "\r" in src:
        raise ValueError("That source doesn't look like a repository or URL.")
    args = ["-y", SKILLS_CLI_PACKAGE, "add", src, "--copy", "-y"]
    for name in skills or []:
        clean = str(name or "").strip()
        if not clean or clean.startswith("-"):
            raise ValueError("Skill names may only contain letters, digits, dots, dashes, underscores.")
        args += ["-s", clean]
    target = (agent or DEFAULT_SKILL_AGENT).strip() or DEFAULT_SKILL_AGENT
    args += ["-a", target]
    return [*(npx if npx is not None else npx_prefix()), *args]


def skill_install(
    *,
    command: list[str],
    cwd: Path,
    timeout: float = INSTALL_TIMEOUT_SECONDS,
) -> CliResult:
    """The runner seam. Runs the community CLI with NO shell and returns its result.

    Spawn-level failures (missing binary, timeout) raise a ``ValueError`` with an actionable
    message; a non-zero exit is returned so the caller can surface the CLI's own stderr.
    Tests inject their own runner with this exact keyword signature.
    """
    from harness.platform.spawn import run as spawn_run

    try:
        proc = spawn_run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,  # explicit: argv is a list and must stay one
        )
    except FileNotFoundError as exc:
        raise ValueError(_MISSING_NODE_HINT) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            f"The installer timed out after {int(timeout)}s. Check your network connection "
            "and try again."
        ) from exc
    except OSError as exc:
        raise ValueError(f"Couldn't run the skills installer: {exc}") from exc
    return CliResult(
        returncode=int(proc.returncode or 0),
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


def cli_failure(result: CliResult, *, timeout: float) -> str:
    """A human error for a non-zero CLI exit, with the missing-Node case called out."""
    lines = [ln for ln in (result.stderr or result.stdout or "").strip().splitlines() if ln.strip()]
    tail = "\n".join(lines[-8:]) or "(no output)"
    hint = ""
    if result.returncode in _NOT_FOUND_CODES or _NOT_FOUND_RE.search(tail):
        hint = " " + _MISSING_NODE_HINT
    return f"The skills installer failed (exit {result.returncode}):\n{tail}{hint}"


def source_type(source: str) -> str:
    """``"github" | "url" | "git"`` for the lockfile.

    A bare ``owner/repo`` or a github.com URL is ``github``; any other scheme'd URL is ``url``;
    a ``git+``/``git@``/``.git`` source (or anything unrecognizable) is ``git``.
    """
    s = (source or "").strip()
    if s.startswith(("git+", "git@")) or s.endswith(".git"):
        return "git"
    if re.match(r"^https?://github\.com/", s, re.I):
        return "github"
    if re.match(r"^[a-z][a-z0-9+.-]*://", s, re.I):
        return "url"
    if re.match(r"^[\w.-]+/[\w.-]+$", s):
        return "github"
    return "git"


def _escapes(path: Path, root: Path) -> bool:
    """True when ``path`` (following symlinks) resolves outside ``root`` — the zip-slip guard,
    re-used for a filesystem walk instead of an archive."""
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError:
        return True
    return resolved != base and base not in resolved.parents


def _walk_files(root: Path) -> list[Path]:
    """Every file under ``root``, symlink-aware: a link that resolves outside the staging dir
    is rejected (with the upload flow's zip-slip posture), a link that stays inside is kept.
    The install manifest confirm wrote is not skill content and never counts."""
    out: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name == INSTALL_MANIFEST_NAME:
                continue
            if entry.is_symlink() and _escapes(entry, root):
                raise ValueError(
                    "The installer left a path that points outside its staging folder — "
                    "refusing to preview it."
                )
            try:
                if entry.is_dir():
                    stack.append(entry)
                elif entry.is_file():
                    out.append(entry)
            except OSError:
                continue
    return out


def _unit_files(root: Path, skill_dirs: list[Path]) -> dict[Path, list[Path]]:
    """Map each skill directory to its files (SKILL.md included)."""
    return {d: [f for f in root if d == f or d in f.parents] for d in skill_dirs}


def compute_skill_hash(unit: Path, *, is_flat: bool) -> str:
    """``sha256:<hex>`` over a skill's files, sorted by their unit-relative posix path.

    Paths are hashed alongside bytes so two skills with identical content but different
    resource layouts never collide."""
    base = unit.parent if is_flat else unit
    files = [unit] if is_flat else sorted(
        (p for p in _walk_files(unit)), key=lambda p: str(p.relative_to(base)).replace("\\", "/")
    )
    digest = hashlib.sha256()
    for path in files:
        rel = str(path.relative_to(base)).replace("\\", "/")
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError as exc:
            raise ValueError(f"Couldn't read {rel} while hashing the skill: {exc}") from exc
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


@dataclass
class StagedSkill:
    """One skill found in the staging dir, ready to preview."""

    name: str
    description: str
    instructions: str
    license: str
    metadata: dict[str, str]
    #: posix path of the skill folder (or of the flat .md) relative to the staging root
    unit: str
    #: posix path of its SKILL.md / flat .md relative to the staging root
    skill_path: str
    is_flat: bool
    files: list[str]
    size: int
    computed_hash: str

    def manifest_entry(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "unit": self.unit,
            "skillPath": self.skill_path,
            "isFlat": self.is_flat,
            "computedHash": self.computed_hash,
        }

    def preview(self, *, catalog_cap: int) -> dict[str, Any]:
        normalized = re.sub(r"\s+", " ", self.description or "").strip()
        return {
            "name": self.name,
            "description": self.description,
            "description_length": len(normalized),
            "description_capped": len(normalized) > catalog_cap,
            "license": self.license,
            "metadata": self.metadata,
            "files": self.files,
            "size": self.size,
            "skillPath": self.skill_path,
            "computedHash": self.computed_hash,
        }


def walk_staged_skills(
    staged: Path,
    *,
    read_skill: Callable[..., Any],
    catalog_cap: int,
    validate_name: Callable[[str], str],
) -> tuple[list[StagedSkill], list[str]]:
    """Find and strictly parse every skill the CLI wrote under ``staged``.

    Returns ``(skills, warnings)``. Discovery mirrors the loader's one-level posture without
    trusting the CLI's layout: every directory containing ``SKILL.md`` is a skill (nested
    skill dirs collapse into their shallowest ancestor), and a flat ``<name>.md`` outside any
    skill dir is a skill too (the flat form). Resource ``.md`` files inside a skill folder
    are never mistaken for skills. A corrupt file is contained as a warning, exactly like a
    discovery pass; if nothing usable survives, the caller errors out.
    """
    warnings: list[str] = []
    files = _walk_files(staged)
    md_files = [p for p in files if p.suffix.lower() == ".md"]
    skill_mds = [p for p in md_files if p.name == "SKILL.md"]

    dirs = sorted(
        {p.parent for p in skill_mds if p.parent != staged},
        key=lambda p: (len(p.parts), str(p).lower()),
    )
    top_dirs: list[Path] = []
    for d in dirs:
        if any(d != other and d.is_relative_to(other) for other in top_dirs):
            continue  # nested inside an already-claimed skill folder
        top_dirs.append(d)
    top_dirs_set = set(top_dirs)

    units: list[tuple[Path, bool]] = [(d, False) for d in top_dirs]
    for p in md_files:
        if p.name == "SKILL.md":
            if p.parent == staged:
                # A root-level SKILL.md: the agent layout never produces one, so preview it
                # as a single-file skill rather than claiming the whole staging root.
                units.append((p, True))
            continue
        if any(d in p.parents for d in top_dirs_set):
            continue  # a resource of a found skill
        units.append((p, True))

    skills: list[StagedSkill] = []
    seen: set[str] = set()
    for unit, is_flat in units:
        md = unit if is_flat else unit / "SKILL.md"
        rel_md = str(md.relative_to(staged)).replace("\\", "/")
        try:
            parsed = read_skill(md, require_name=is_flat)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            warnings.append(f"Skipped {rel_md}: {exc}")
            continue
        try:
            name = validate_name(parsed.name)
        except ValueError as exc:
            warnings.append(f"Skipped {rel_md}: {exc}")
            continue
        if name in seen:
            warnings.append(f"Skipped {rel_md}: another skill in this install already uses the name '{name}'.")
            continue
        seen.add(name)
        unit_files = [md] if is_flat else sorted(
            (p for p in files if p.parent == unit or unit in p.parents),
            key=lambda p: str(p.relative_to(unit)).replace("\\", "/"),
        )
        rel_files = [
            str(p.relative_to(unit.parent if is_flat else unit)).replace("\\", "/")
            for p in unit_files
            if p != md
        ]
        size = 0
        for p in unit_files:
            try:
                size += p.stat().st_size
            except OSError:
                continue
        skills.append(
            StagedSkill(
                name=name,
                description=parsed.description,
                instructions=parsed.instructions,
                license=parsed.license,
                metadata=dict(parsed.metadata or {}),
                unit=str(unit.relative_to(staged)).replace("\\", "/"),
                skill_path=rel_md,
                is_flat=is_flat,
                files=rel_files,
                size=size,
                computed_hash=compute_skill_hash(unit, is_flat=is_flat),
            )
        )
    return skills, warnings
