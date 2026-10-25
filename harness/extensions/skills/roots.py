"""Where skills live: the scopes, the community-standard folders, and the order they shadow each other.

A scope is a folder location, and the folder is the truth. Global skills sit in
`state_dir()/skills`, project skills in `<workspace>/.reeve/skills`, and there is no database.
Every operation is a folder and a `SKILL.md`, which keeps project skills shareable through git for
free.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from harness.safety.secrets import state_dir

GLOBAL_SCOPE = "global"
PROJECT_SCOPE = "project"
#: A skill found in a community-standard folder (`.agents/skills`, `.claude/skills`, and the like).
#: It is read-only here, because the tool that wrote the folder owns it.
COMPAT_SCOPE = "compat"
#: A skill that comes with a plugin: read-only, named `<plugin>:<name>`.
PLUGIN_SCOPE = "plugin"

# The community folders. `npx skills add` and the neighbouring tools write to these places, so
# reading them makes those skills work with no step. Each tuple runs LOW to HIGH within its layer,
# and a higher one wins a name collision. Discovery stays one level deep, with no search through
# `**/SKILL.md`.
_COMPAT_PROJECT_SUBDIRS: tuple[tuple[str, ...], ...] = (
    (".agents", "skills"),
    (".claude", "skills"),
    (".opencode", "skill"),
    (".opencode", "skills"),
)
_COMPAT_GLOBAL_SUBDIRS: tuple[tuple[str, ...], ...] = (
    (".config", "opencode", "skills"),
    (".claude", "skills"),
    (".agents", "skills"),
)


def home_dir() -> Path:
    """The home that anchors the community folders. It is a function, and not `Path.home()` in
    place, so tests can point every global root at a temporary folder and not at a real machine."""
    return Path.home()


def compat_global_dirs(home: Optional[str | Path] = None) -> list[Path]:
    base = Path(home).expanduser() if home else home_dir()
    return [base.joinpath(*parts) for parts in _COMPAT_GLOBAL_SUBDIRS]


def compat_project_dirs(workspace: str | Path) -> list[Path]:
    base = Path(workspace).expanduser()
    return [base.joinpath(*parts) for parts in _COMPAT_PROJECT_SUBDIRS]


def default_skill_roots(
    workspace: Optional[str | Path] = None,
    *,
    global_dir: Optional[str | Path] = None,
    home: Optional[str | Path] = None,
) -> list[tuple[Path, str]]:
    """Every default skill root as `(path, scope)`, LOW to HIGH, a later root shadowing an
    earlier one:

        community global < product global
        < project .agents < project .claude < project .opencode/skill
        < project .opencode/skills < project product

    No feature skills are bundled: a fresh install ships none and the person adds their own. A
    persona's bundled skills are not here either. The engine loads them first and so lowest, which
    lets a person's copy always shadow one from a bundle.
    """
    product_global = Path(global_dir) if global_dir else state_dir() / "skills"
    roots: list[tuple[Path, str]] = [(path, COMPAT_SCOPE) for path in compat_global_dirs(home)]
    roots.append((product_global, GLOBAL_SCOPE))
    if workspace:
        project = Path(workspace).expanduser().resolve()
        roots += [(path, COMPAT_SCOPE) for path in compat_project_dirs(project)]
        roots.append((project / ".reeve" / "skills", PROJECT_SCOPE))
    return roots


def loader_roots(
    workspace: Optional[str | Path] = None,
    *,
    global_dir: Optional[str | Path] = None,
    home: Optional[str | Path] = None,
) -> list[Any]:
    """The loader's chain: `default_skill_roots` with the enabled plugins' skill roots between the
    community globals and the product global,

        community global < plugin < product global < community project < product project

    so a person's own skill always outranks a plugin's, and a plugin skill's `<plugin>:` prefix
    keeps a real collision a deliberate one."""
    from ..plugins import resolve_plugins

    roots = default_skill_roots(workspace, global_dir=global_dir, home=home)
    first_product = next((i for i, (_path, scope) in enumerate(roots) if scope != COMPAT_SCOPE), len(roots))
    plugin_roots = resolve_plugins(workspace=workspace).skill_roots
    return [path for path, _scope in roots[:first_product]] + list(plugin_roots) + [path for path, _scope in roots[first_product:]]
