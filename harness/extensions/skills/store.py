"""The skills a person manages from Settings: listing, writing, moving, removing, switching off.

The pieces around it: `roots.py` knows which folders hold skills, `skillfile.py` reads and writes a
`SKILL.md`, `uploads.py` and `installs.py` bring skills in from a file or from the community.

A switched-off skill is remembered in `state_dir()/skills-settings.json`, never by a marker in its
folder: a project's folder is committed with the project, and one person switching a skill off
must not switch it off for everyone who clones it.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from harness.safety.secrets import state_dir
from harness.session.toggles import SessionToggles

from .base import _read_skill
from .installer import INSTALL_TIMEOUT_SECONDS, skill_install
from .installs import SkillInstallMixin
from .roots import (
    COMPAT_SCOPE,
    GLOBAL_SCOPE,
    PLUGIN_SCOPE,
    PROJECT_SCOPE,
    compat_global_dirs,
    compat_project_dirs,
    default_skill_roots,
    home_dir,
    loader_roots,
)
from .skillfile import compat_read_only_error, record_skill_use, render_skill_md, skill_usage, validate_name, write_skill_md
from .uploads import SkillUploadMixin

logger = logging.getLogger(__name__)

__all__ = [
    "COMPAT_SCOPE",
    "GLOBAL_SCOPE",
    "PLUGIN_SCOPE",
    "PROJECT_SCOPE",
    "SessionSkillStore",
    "SkillStore",
    "compat_global_dirs",
    "compat_project_dirs",
    "default_skill_roots",
    "effective_skills",
    "home_dir",
    "loader_roots",
    "record_skill_use",
    "skill_usage",
    "validate_name",
]

_NEEDS_DESCRIPTION = "Give the skill a one-line description: the agent reads it to decide when the skill applies."
_NEEDS_INSTRUCTIONS = "Give the skill its instructions."
_FRONTMATTER_FIELDS = ("when_to_use", "summary", "license", "compatibility", "metadata", "model_invocable", "user_invocable")


def _listing_row(skill, *, usage: dict, **fields: Any) -> dict[str, Any]:
    """One skill as Settings shows it: what its frontmatter says, where it sits, how much it is used
    (`record_skill_use` counts each load). `instructions` is there so the editor opens filled in."""
    used = usage.get(skill.name) or {}
    row: dict[str, Any] = {"name": skill.name, "description": skill.description, "instructions": skill.instructions}
    row.update({field: getattr(skill, field) for field in _FRONTMATTER_FIELDS})
    row.update(fields)
    row["uses"] = int(used.get("uses") or 0)
    row["last_used_at"] = used.get("last_used_at")
    return row


def _extra_files(folder: Path) -> int:
    """How many files a skill brings besides its SKILL.md, so a folder of scripts and references
    does not list like a one-page skill."""
    try:
        return max(sum(path.is_file() for path in folder.rglob("*")) - 1, 0)
    except OSError:
        return 0


class SkillStore(SkillUploadMixin, SkillInstallMixin):
    """Skill folders in every scope: global, the project's own, the community roots, the plugins'."""

    def __init__(
        self,
        global_dir: Optional[str | Path] = None,
        home_dir: Optional[str | Path] = None,
        install_runner: Optional[Callable[..., Any]] = None,
        install_timeout: float = INSTALL_TIMEOUT_SECONDS,
    ) -> None:
        state = state_dir()
        self.global_dir = Path(global_dir) if global_dir else state / "skills"
        # Where `~` is for the community roots; None is the real home, a test passes its own.
        self.home_dir = Path(home_dir) if home_dir else None
        self._settings_path = state / "skills-settings.json"
        self._staging_dir = state / "skills-staged"
        # A community install writes where it came from into the lock file. The runner is what a
        # test swaps out so no install reaches the network.
        self._lock_path = state / "skills-lock.json"
        self._install_runner = install_runner or skill_install
        self._install_timeout = float(install_timeout)
        self._lock = threading.Lock()

    # -- where skills live ------------------------------------------------------------------

    def project_dir(self, workspace: str | Path) -> Path:
        return Path(workspace).expanduser().resolve() / ".reeve" / "skills"

    def loader_roots(self, workspace: Optional[str | Path] = None) -> list[Any]:
        """The roots a loader reads for this store, the plugins' skill folders included."""
        return loader_roots(workspace, global_dir=self.global_dir, home=self.home_dir)

    def root_dirs(self, workspace: Optional[str | Path] = None) -> list[tuple[Path, str]]:
        """`(folder, scope)` for every root, the one a name loses to first."""
        return default_skill_roots(workspace, global_dir=self.global_dir, home=self.home_dir)

    def _base(self, scope: str, workspace: Optional[str | Path]) -> Path:
        """The folder a new skill of `scope` is written into."""
        if scope == GLOBAL_SCOPE:
            return self.global_dir
        if scope != PROJECT_SCOPE:
            raise ValueError(f"There is no skill scope called {scope!r}.")
        if not workspace:
            raise ValueError("A project skill needs the project's folder.")
        folder = Path(workspace).expanduser()
        if not folder.is_dir():
            raise ValueError(f"No project folder at {workspace}.")
        return self.project_dir(folder)

    def _folder_of(self, base: Path, name: str) -> Path:
        """`base / name`, refused when it resolves outside `base`: a skill folder that is a link to
        somewhere else is not one this store will read or write through."""
        folder = base / name
        try:
            inside, root = folder.resolve(), base.resolve()
        except OSError:
            raise ValueError(f"The folder of skill {name!r} cannot be read.")
        if inside != root / name and root not in inside.parents:
            raise ValueError(f"The folder of skill {name!r} points outside its scope.")
        return folder

    # -- reading ----------------------------------------------------------------------------

    def find(self, name: str, workspace: Optional[str | Path] = None) -> tuple[Path, str]:
        """The copy of `name` the model would load, and its scope. Roots are tried nearest first,
        the loader's own order, so an edit always lands on the copy that is in use."""
        name = validate_name(name)
        for base, scope in reversed(self.root_dirs(workspace)):
            if (base / name / "SKILL.md").is_file():
                return self._folder_of(base, name), scope
        raise ValueError(f"Unknown skill: {name}")

    def rows(self, workspace: Optional[str | Path] = None) -> list[dict[str, Any]]:
        """Every skill for the Settings list. Roots are read farthest first and a later copy of a
        name takes the earlier one's place, so each name shows the copy the model would load."""
        disabled, usage = self.disabled_names(), skill_usage()
        rows: list[dict[str, Any]] = []
        slot: dict[str, int] = {}
        for base, scope in self.root_dirs(workspace):
            for folder in self._skill_folders(base):
                try:
                    skill, frontmatter = _read_skill(folder / "SKILL.md")
                except (OSError, UnicodeDecodeError, ValueError) as exc:
                    logger.warning("skill ignored: %s: %s", folder / "SKILL.md", exc)
                    continue
                row = _listing_row(
                    skill,
                    usage=usage,
                    scope=scope,
                    source=str(frontmatter.get("source") or "").strip() or "local",
                    enabled=skill.name not in disabled,
                    path=str(folder),
                    files=_extra_files(folder),
                    # The tool that wrote a community root owns it; Settings only reads it.
                    compat=scope == COMPAT_SCOPE,
                )
                if skill.name in slot:
                    rows[slot[skill.name]] = row
                else:
                    slot[skill.name] = len(rows)
                    rows.append(row)
        return rows + self._plugin_rows(workspace, disabled, usage, slot)

    @staticmethod
    def _skill_folders(base: Path) -> list[Path]:
        """The folders under `base` that hold a SKILL.md. A root that is not there is no skills,
        quietly: most people have no community roots at all."""
        if not base.is_dir():
            return []
        try:
            return [folder for folder in sorted(base.iterdir()) if (folder / "SKILL.md").is_file()]
        except OSError as exc:
            logger.warning("skills directory ignored: %s: %s", base, exc)
            return []

    def _plugin_rows(self, workspace: Any, disabled: set[str], usage: dict[str, Any], seen: dict[str, int]) -> list[dict[str, Any]]:
        """Skills the enabled plugins ship. They are read-only here, like a community root, and a
        `<plugin>:` name cannot collide with one a person wrote."""
        from ..plugins import resolve_plugins
        from .base import SkillLoader

        rows: list[dict[str, Any]] = []
        for root in resolve_plugins(workspace=workspace).skill_roots:
            loader = SkillLoader([root])
            for name in loader.names():
                skill = loader.get(name)
                if skill is None or name in seen:
                    continue
                row = _listing_row(
                    skill,
                    usage=usage,
                    scope=PLUGIN_SCOPE,
                    source=f"plugin {root.plugin_id or root.plugin}",
                    enabled=name not in disabled,
                    path=str(Path(skill.path).parent) if skill.path else "",
                    files=0,
                    compat=True,
                    plugin=root.plugin_id or f"{root.plugin}@inline",
                )
                row["name"] = name
                rows.append(row)
        return rows

    # -- writing ----------------------------------------------------------------------------

    def _writable(self, name: str, workspace: Optional[str | Path], doing: str) -> tuple[Path, str]:
        """`find`, refusing a copy that sits in a community root."""
        folder, scope = self.find(name, workspace)
        if scope == COMPAT_SCOPE:
            raise ValueError(compat_read_only_error(name, doing))
        return folder, scope

    def create(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        scope: str = GLOBAL_SCOPE,
        workspace: Optional[str | Path] = None,
        source: str = "",
    ) -> dict[str, Any]:
        name = validate_name(name)
        description = (description or "").strip()
        if not description:
            raise ValueError(_NEEDS_DESCRIPTION)
        if not (instructions or "").strip():
            raise ValueError(_NEEDS_INSTRUCTIONS)
        folder = self._folder_of(self._base(scope, workspace), name)
        if (folder / "SKILL.md").is_file():
            raise ValueError(f"A skill named '{name}' already exists in that scope.")
        write_skill_md(folder, name=name, description=description, instructions=instructions, source=source)
        return {"name": name, "scope": scope, "path": str(folder)}

    def update(
        self,
        name: str,
        *,
        description: Optional[str] = None,
        instructions: Optional[str] = None,
        workspace: Optional[str | Path] = None,
    ) -> dict[str, Any]:
        """Change the description, the instructions or both, in place; the skill's other files are
        left alone. The frontmatter is read whole and written back whole, so a key this app does
        not know (`license`, `metadata`, a tool's own) comes through as it was."""
        folder, scope = self._writable(name, workspace, "edited")
        skill_md = folder / "SKILL.md"
        try:
            current, frontmatter = _read_skill(skill_md)
        except OSError as exc:
            raise ValueError(f"The SKILL.md of {name!r} could not be read: {exc}") from exc
        if description is not None:
            if not description.strip():
                raise ValueError(_NEEDS_DESCRIPTION)
            frontmatter["description"] = description.strip()
        if instructions is not None and not instructions.strip():
            raise ValueError(_NEEDS_INSTRUCTIONS)
        # A SKILL.md older than the `name:` key was named by its folder; it keeps that name.
        frontmatter.setdefault("name", current.name)
        body = current.instructions if instructions is None else instructions.strip()
        skill_md.write_text(render_skill_md(frontmatter, body), encoding="utf-8")
        return {"name": current.name, "scope": scope}

    def delete(self, name: str, workspace: Optional[str | Path] = None) -> None:
        folder, _scope = self._writable(name, workspace, "deleted")
        if folder.is_symlink():
            folder.unlink()  # the link goes, never what it points at
        else:
            shutil.rmtree(folder)
        # Removing a skill and uninstalling it are one act, so its install record goes too.
        self._forget_install(name)

    def move(self, name: str, *, to_scope: str, workspace: Optional[str | Path] = None) -> dict[str, Any]:
        folder, from_scope = self._writable(name, workspace, "moved")
        if from_scope != to_scope:
            into = self._base(to_scope, workspace)
            target = self._folder_of(into, name)
            if (target / "SKILL.md").is_file():
                raise ValueError(f"A skill named '{name}' already exists in the target scope.")
            into.mkdir(parents=True, exist_ok=True)
            shutil.move(str(folder), str(target))
        return {"name": name, "scope": to_scope}

    # -- switched off, for this person, across restarts --------------------------------------

    def disabled_names(self) -> set[str]:
        try:
            stored = json.loads(self._settings_path.read_text(encoding="utf-8"))
            return {str(name) for name in stored.get("disabled", [])}
        except (OSError, ValueError, AttributeError):
            return set()

    def set_enabled(self, name: str, enabled: bool, workspace: Optional[str | Path] = None) -> None:
        """Switch a skill on or off. That writes, so a copy in a community root refuses it; a name
        no root has yet is accepted, since switching one off before it is installed is fair."""
        name = validate_name(name)
        try:
            in_compat_root = self.find(name, workspace)[1] == COMPAT_SCOPE
        except ValueError:
            in_compat_root = False
        if in_compat_root:
            raise ValueError(compat_read_only_error(name, "turned on or off"))
        with self._lock:
            disabled = self.disabled_names()
            (disabled.discard if enabled else disabled.add)(name)
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._settings_path.write_text(json.dumps({"disabled": sorted(disabled)}, indent=2), encoding="utf-8")


class SessionSkillStore(SessionToggles):
    """A conversation's skill switches. A skill it never switched follows Settings."""


def effective_skills(*, names: set[str], disabled: set[str], session_overrides: dict[str, bool]) -> set[str]:
    """The skills a conversation is offered. Off anywhere is off: a skill Settings switched off
    cannot be switched back on by one conversation, and one nobody switched is on."""
    return {name for name in names if name not in disabled and session_overrides.get(name, True)}
