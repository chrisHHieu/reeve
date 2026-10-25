"""SkillInstallMixin: installing community skills with `npx skills add`.

The flow is run, stage, preview, confirm. The heavy lifting (the command line, running the CLI,
walking the staged folder, hashing) is in `installer.py`. These methods own the promotion into the
product root, the policy on shadowing, and the lockfile that records where each skill came from.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from .base import _CATALOG_DESCRIPTION_MAX_CHARS, _parse_skill
from .installer import (
    DEFAULT_SKILL_AGENT,
    INSTALL_MANIFEST_NAME,
    cli_failure,
    skill_cli_command,
    source_type,
    walk_staged_skills,
)
from .roots import GLOBAL_SCOPE
from .skillfile import validate_name

logger = logging.getLogger(__name__)

_EXPIRED = "Unknown or expired install — run the install again."


class SkillInstallMixin:
    # -- the lockfile --------------------------------------------------------------------

    def installed_map(self) -> dict[str, dict[str, str]]:
        """The `skills-lock.json` entries by skill name, so the GUI can say "installed from …"."""
        installed: dict[str, dict[str, str]] = {}
        for name, entry in (self._read_lock().get("skills") or {}).items():
            if isinstance(entry, dict):
                installed[str(name)] = {
                    key: str(entry.get(key) or "") for key in ("source", "sourceType", "skillPath", "computedHash")
                }
        return installed

    def _read_lock(self) -> dict[str, Any]:
        fresh = {"version": 1, "skills": {}}
        try:
            data = json.loads(self._lock_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return fresh
        except (OSError, ValueError) as exc:
            # A corrupt lock must never keep skills from loading: it warns and starts clean.
            logger.warning("skills lock ignored: %s: %s", self._lock_path, exc)
            return fresh
        if not isinstance(data, dict):
            return fresh
        if not isinstance(data.get("skills"), dict):
            data["skills"] = {}
        data.setdefault("version", 1)
        return data

    def _write_lock(self, data: dict[str, Any]) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        scratch = self._lock_path.with_name(f"{self._lock_path.name}.{uuid.uuid4().hex[:8]}.tmp")
        scratch.write_text(json.dumps(data, indent=2), encoding="utf-8")
        scratch.replace(self._lock_path)

    def _merge_lock(self, entries: dict[str, dict[str, Any]]) -> None:
        """Merge entries into the lockfile. The caller holds `self._lock`."""
        if not entries:
            return
        data = self._read_lock()
        data["skills"].update(entries)
        data["version"] = 1
        self._write_lock(data)

    def _forget_install(self, name: str) -> None:
        with self._lock:
            data = self._read_lock()
            if name not in (data.get("skills") or {}):
                return
            del data["skills"][name]
            if data["skills"]:
                self._write_lock(data)
                return
            # An empty lock is noise and not state, so the file goes (installing again makes it).
            try:
                self._lock_path.unlink()
            except OSError:
                self._write_lock(data)

    # -- staging and previewing ----------------------------------------------------------

    def install_conflict(self, name: str) -> Optional[dict[str, Any]]:
        """What installing `name` into the product global root would do to the copy that exists,
        or None when the name is free. The effective copy decides, as it does in `find()`: a
        product global skill can be replaced (with the confirm flag), while a copy in a community
        folder must not be shadowed in silence."""
        try:
            _folder, scope = self.find(name)
        except ValueError:
            return None
        if scope == GLOBAL_SCOPE:
            return {"scope": scope, "blocked": "", "replaces": True}
        return {
            "scope": scope,
            "blocked": (
                f"'{name}' already comes from a shared skills folder (a community root like "
                "~/.agents/skills, ~/.claude/skills, or another tool's). Installing your own copy "
                "would silently shadow it — move or copy that one into your skills first."
            ),
            "replaces": False,
        }

    def stage_install(
        self,
        source: str,
        *,
        skills: Optional[list[str]] = None,
        agent: str = "",
        runner: Optional[Callable[..., Any]] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Run `npx skills add` into a fresh staging folder and preview what it found.

        Nothing is installed. The preview carries each skill's parsed metadata, its file list,
        size and content hash, any flag for shadowing or replacing, and the token that
        `confirm_install` needs. The CLI's failure text is passed on as it is, with a hint about
        Node when the command was missing, and is never swallowed.
        """
        names: list[str] = []
        for raw in skills or []:
            try:
                names.append(validate_name(str(raw)))
            except ValueError as exc:
                raise ValueError(f"Invalid skill name '{raw}': {exc}") from exc
        command = skill_cli_command(source, skills=names, agent=agent)
        token = uuid.uuid4().hex
        staged = self._staging_dir / token
        staged.mkdir(parents=True, exist_ok=True)
        run = runner or self._install_runner
        seconds = float(self._install_timeout if timeout is None else timeout)
        try:
            result = run(command=command, cwd=staged, timeout=seconds)
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        if result.returncode != 0:
            shutil.rmtree(staged, ignore_errors=True)
            raise ValueError(cli_failure(result, timeout=seconds))
        try:
            found, warnings = walk_staged_skills(
                staged,
                read_skill=_parse_skill,
                catalog_cap=_CATALOG_DESCRIPTION_MAX_CHARS,
                validate_name=validate_name,
            )
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        if not found:
            shutil.rmtree(staged, ignore_errors=True)
            if result.output:
                warnings.append(result.output)
            raise ValueError(
                "The installer finished but didn't add any skills — no SKILL.md, and no "
                "flat .md with a name in its frontmatter."
            )
        previews: list[dict[str, Any]] = []
        for skill in found:
            conflict = self.install_conflict(skill.name) or {}
            row = skill.preview(catalog_cap=_CATALOG_DESCRIPTION_MAX_CHARS)
            row["blocked"] = str(conflict.get("blocked") or "")
            row["replaces"] = bool(conflict.get("replaces"))
            previews.append(row)
        record = {
            "version": 1,
            "source": (source or "").strip(),
            "sourceType": source_type(source),
            "agent": (agent or "").strip() or DEFAULT_SKILL_AGENT,
            "command": command,
            "created_at": time.time(),
            "skills": [skill.manifest_entry() for skill in found],
        }
        (staged / INSTALL_MANIFEST_NAME).write_text(json.dumps(record, indent=2), encoding="utf-8")
        return {
            "token": token,
            "source": record["source"],
            "sourceType": record["sourceType"],
            "agent": record["agent"],
            "command": command,
            "skills": previews,
            "warnings": warnings,
            "installable": all(not row["blocked"] for row in previews),
        }

    # -- confirming ----------------------------------------------------------------------

    def confirm_install(self, token: str, *, overwrite: bool = False) -> list[dict[str, Any]]:
        """Promote a staged install into the product global root and record it in the lock.

        Everything is checked against the live store first (blocked names, consent to replace,
        escapes out of the staging folder) before a single file moves. A copy that is replaced is
        backed up so a partial failure rolls back, and then the lockfile entries are merged.
        """
        staged = self._staging_dir / str(token)
        manifest_path = staged / INSTALL_MANIFEST_NAME
        if not manifest_path.is_file():
            raise ValueError(_EXPIRED)
        try:
            record = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError(_EXPIRED)
        wanted = [str(entry.get("name") or "") for entry in (record.get("skills") or []) if isinstance(entry, dict)]
        if not wanted:
            raise ValueError(_EXPIRED)
        source = str(record.get("source") or "")
        kind = str(record.get("sourceType") or "git")

        fresh, _warnings = walk_staged_skills(
            staged,
            read_skill=_parse_skill,
            catalog_cap=_CATALOG_DESCRIPTION_MAX_CHARS,
            validate_name=validate_name,
        )
        by_name = {skill.name: skill for skill in fresh}
        if any(name not in by_name for name in wanted):
            raise ValueError(_EXPIRED)

        for name in wanted:
            conflict = self.install_conflict(name)
            if conflict and conflict.get("blocked"):
                raise ValueError(str(conflict["blocked"]))
            if conflict and conflict.get("replaces") and not overwrite:
                raise ValueError(
                    f"A skill named '{name}' is already in your skills. Confirm the "
                    "replacement to install over it."
                )

        base = self.global_dir
        base.mkdir(parents=True, exist_ok=True)
        backups = staged / ".replaced"
        placed: list[tuple[Path, Optional[Path]]] = []
        with self._lock:
            try:
                for name in wanted:
                    skill = by_name[name]
                    target = self._folder_of(base, name)
                    backup: Optional[Path] = None
                    if target.exists():
                        backup = backups / name
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        if backup.is_dir():
                            shutil.rmtree(backup, ignore_errors=True)
                        elif backup.exists():
                            backup.unlink()
                        shutil.move(str(target), str(backup))
                    if skill.is_flat:
                        target.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(staged / skill.unit), str(target / "SKILL.md"))
                    else:
                        shutil.move(str(staged / skill.unit), str(target))
                    placed.append((target, backup))
            except BaseException:
                self._rollback_install(placed)
                raise
            self._merge_lock(
                {
                    name: {
                        "source": source,
                        "sourceType": kind,
                        "skillPath": by_name[name].skill_path,
                        "computedHash": by_name[name].computed_hash,
                    }
                    for name in wanted
                }
            )
        shutil.rmtree(staged, ignore_errors=True)
        return [{"name": name, "scope": GLOBAL_SCOPE, "path": str(base / name)} for name in wanted]

    def _rollback_install(self, placed: list[tuple[Path, Optional[Path]]]) -> None:
        """Undo a promotion that stopped half-way: remove what landed and restore what was replaced."""
        for target, backup in reversed(placed):
            try:
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target, ignore_errors=True)
                elif target.exists() or target.is_symlink():
                    target.unlink()
                if backup is not None and backup.exists():
                    shutil.move(str(backup), str(target))
            except OSError:
                logger.exception("install rollback failed for %s", target)

    def discard_install(self, token: str) -> None:
        shutil.rmtree(self._staging_dir / str(token), ignore_errors=True)
