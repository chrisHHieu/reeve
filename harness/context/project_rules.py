"""Path-scoped rules — `.agents/rules/**/*.md` (memory v2 Phase 2).

A rule file with a `paths:` frontmatter list loads only when the agent works with a
matching file (`nested_rules`, injected at the next turn's start like a nested AGENTS.md);
a rule without `paths:` loads at build (`load_rules`). `.claude/rules/**` is read-only
compatibility — another tool's namespace, read but never written. Split out of
`project.py`, which keeps the AGENTS.md instruction tiers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from harness.session.project import NESTED_PER_PASS, _read, expand_imports
from harness.safety.secrets import state_dir

#: A rule file's optional YAML frontmatter (`---\npaths:\n  - src/**\n---`).
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)

#: How many patterns one rule's brace groups may expand into. This is bounded too
#: (`{a,b}/{c,d}/*.{ts,tsx}` is eight spellings); past the budget the extra patterns are
#: dropped rather than stalling a build.
MAX_RULE_PATTERNS = 64


def render_rule(path_label: str, text: str) -> str:
    """One rule block, named by its path under the rules directory it came from."""
    return f'<rule path="{path_label}">\n{text.strip()}\n</rule>'


# -- `.agents/rules` (memory v2 Phase 2) ---------------------------------------------


def _rules_dirs(
    workspace: Path, *, global_dir: Optional[str | Path] = None
) -> list[tuple[str, Path]]:
    """Rule directories, broadest first: user, the workspace's `.agents/rules`, then the
    read-only `.claude/rules` compat (another tool's namespace)."""
    out: list[tuple[str, Path]] = []
    user = Path(global_dir) if global_dir is not None else state_dir() / "rules"
    if user.is_dir():
        out.append(("user", user))
    project = workspace / ".agents" / "rules"
    if project.is_dir():
        out.append(("project", project))
    compat = workspace / ".claude" / "rules"
    if compat.is_dir():
        out.append(("project", compat))
    return out


def _fallback_paths(header: str) -> list[str]:
    """`paths:` parsed by hand for frontmatter YAML rejects — a glob may start with `*`,
    which YAML reads as an alias, and users write it unquoted anyway (`- *.md`)."""
    out: list[str] = []
    collecting = False
    for raw in header.splitlines():
        stripped = raw.strip()
        if not collecting:
            if stripped.startswith("paths:"):
                rest = stripped.split(":", 1)[1].strip()
                if rest.startswith("["):
                    out.extend(
                        p.strip().strip("'\"")
                        for p in rest.strip("[]").split(",")
                        if p.strip()
                    )
                    break
                if rest:
                    out.append(rest.strip("'\""))
                    break
                collecting = True
            continue
        if stripped.startswith("- "):
            out.append(stripped[2:].strip().strip("'\""))
            continue
        if not stripped or stripped.startswith("#"):
            continue
        break
    return [p for p in out if p]


def _parse_rule(path: Path) -> tuple[list[str], str]:
    """(paths, body) for one rule file. No frontmatter → no paths → unconditional."""
    text = _read(path)
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return [], text
    header = match.group(1)
    body = text[match.end() :]
    raw = None
    try:
        import yaml

        meta = yaml.safe_load(header) or {}
        raw = meta.get("paths") if isinstance(meta, dict) else None
    except Exception:  # noqa: BLE001 — an unquoted glob is valid here, not a crash
        raw = None
    if isinstance(raw, str):
        paths = re.split(r"[,\n]", raw)
    elif isinstance(raw, list):
        paths = [str(p) for p in raw]
    else:
        paths = []
    cleaned = [p.strip() for p in paths if p.strip()]
    if not cleaned:
        cleaned = _fallback_paths(header)
    return cleaned, body


def _expand_braces(pattern: str, *, limit: int = MAX_RULE_PATTERNS) -> list[str]:
    """`src/*.{ts,tsx}` → both spellings, bounded; extra patterns are dropped."""
    out = [pattern]
    while out and "{" in out[0]:
        head = out.pop(0)
        match = re.search(r"\{([^{}]*)\}", head)
        if not match:
            out.append(head)
            break
        for choice in match.group(1).split(","):
            out.append(head[: match.start()] + choice.strip() + head[match.end() :])
            if len(out) >= limit:
                return out
    return out


def _glob_regex(pattern: str) -> str:
    """Translate a rule glob to a regex: `**/` spans directories, `*` stays in one."""
    out = ["^"]
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if pattern[index : index + 2] == "**":
                if pattern[index + 2 : index + 3] == "/":
                    out.append("(?:.*/)?")
                    index += 3
                    continue
                out.append(".*")
                index += 2
                continue
            out.append("[^/]*")
            index += 1
            continue
        if char == "?":
            out.append("[^/]")
            index += 1
            continue
        out.append(re.escape(char))
        index += 1
    out.append("$")
    return "".join(out)


def matches_paths(patterns: Iterable[str], rel_path: str) -> bool:
    """Whether a workspace-relative posix path matches any of a rule's globs."""
    for pattern in patterns:
        for expanded in _expand_braces(pattern):
            if re.match(_glob_regex(expanded), rel_path):
                return True
    return False


def _render_rule_file(
    path: Path, directory: Path, label: str, root: Path
) -> tuple[str, str]:
    """(key, expanded body) for one rule file; key names user rules `user/<name>`."""
    relative = path.relative_to(directory).as_posix()
    key = f"user/{relative}" if label == "user" else relative
    body = expand_imports(
        _read(path),
        source=path,
        workspace=None if label == "user" else root,
        external_ok=label == "user",
    )
    return key, body


def load_rules(workspace: str | Path, *, global_dir: Optional[str | Path] = None) -> str:
    """The UNCONDITIONAL rules (no `paths:`) as one prompt block, broadest first.

    Path-scoped rules never load here — they ride `nested_rules` at the next turn's
    start, once the agent actually works with a matching file.
    """
    root = Path(workspace).expanduser().resolve()
    parts: list[str] = []
    for label, directory in _rules_dirs(root, global_dir=global_dir):
        for path in sorted(directory.rglob("*.md")):
            paths, body = _parse_rule(path)
            if paths or not body.strip():
                continue
            key, expanded = _render_rule_file(path, directory, label, root)
            parts.append(render_rule(key, expanded))
    if not parts:
        return ""
    return "Project rules:\n" + "\n\n".join(parts)


def nested_rules(
    workspace: str | Path,
    observed_paths: Iterable[str],
    *,
    skip: Iterable[str] = (),
    limit: int = NESTED_PER_PASS,
    global_dir: Optional[str | Path] = None,
) -> list[tuple[str, str]]:
    """Path-scoped rules matching the files the agent has observed.

    Same shape and contract as `nested_agents_md`: root-relative posix paths as keys,
    each rule stated once per session, capped per pass. User-scope rules are keyed
    `user/<name>` so they cannot collide with a workspace rule of the same name.
    """
    root = Path(workspace).expanduser().resolve()
    relative_paths: list[str] = []
    for raw in observed_paths or ():
        try:
            path = Path(str(raw)).resolve()
        except (OSError, ValueError):
            continue
        if path.is_relative_to(root) and path != root:
            relative_paths.append(path.relative_to(root).as_posix())
    if not relative_paths:
        return []
    done = set(skip)
    found: dict[str, str] = {}
    order: list[str] = []
    for label, directory in _rules_dirs(root, global_dir=global_dir):
        for path in sorted(directory.rglob("*.md")):
            paths, body = _parse_rule(path)
            if not paths or not body.strip():
                continue
            key = (
                f"user/{path.relative_to(directory).as_posix()}"
                if label == "user"
                else path.relative_to(directory).as_posix()
            )
            if key in done or key in found:
                continue
            if not any(matches_paths(paths, observed) for observed in relative_paths):
                continue
            _, expanded = _render_rule_file(path, directory, label, root)
            found[key] = expanded
            order.append(key)
            if len(order) >= limit:
                return [(k, found[k]) for k in order]
    return [(k, found[k]) for k in order]
