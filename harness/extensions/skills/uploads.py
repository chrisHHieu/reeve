"""SkillUploadMixin: adding a skill from a file the person supplies.

An upload is staged first (parse, preview, confirm), so the person always reviews exactly what will
be saved before anything lands in a scope folder. The staged content sits under
`state_dir()/skills-staged/<token>` until it is confirmed or discarded.
"""

from __future__ import annotations

import io
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any, Optional

from .base import _parse_skill
from .roots import GLOBAL_SCOPE
from .skillfile import frontmatter_source, validate_name, write_skill_md


def _preview(token: str, skill, files: list[str]) -> dict[str, Any]:
    return {
        "token": token,
        "name": skill.name,
        "description": skill.description,
        "instructions": skill.instructions,
        "files": files,
    }


def _is_noise(entry: str) -> bool:
    """A macOS Finder zip adds `__MACOSX/` shadow entries (`._*`) and `.DS_Store`. That is
    metadata and not skill content, so a zip made on a Mac installs clean without it."""
    parts = Path(entry).parts
    name = Path(entry).name
    return entry.endswith("/") or "__MACOSX" in parts or name == ".DS_Store" or name.startswith("._")


class SkillUploadMixin:
    def stage_upload(self, data: bytes, filename: str = "") -> dict[str, Any]:
        """Stage an upload and return the parsed preview. It takes a `.zip` (a folder skill) or a
        bare `SKILL.md` with YAML frontmatter, and nothing is installed until `confirm_upload`. A
        `.skill` file is a renamed zip and still unpacks, though it is not advertised."""
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return self._stage_single_md(data, filename)
        names = [entry for entry in archive.namelist() if not _is_noise(entry)]
        for entry in names:
            path = Path(entry)
            if path.is_absolute() or ".." in path.parts or (path.parts and ":" in path.parts[0]):
                raise ValueError("Archive contains unsafe paths.")
        # The SKILL.md has to be at the root or inside exactly one top-level folder.
        skill_files = [entry for entry in names if Path(entry).name == "SKILL.md"]
        tops = {Path(entry).parts[0] if len(Path(entry).parts) > 1 else "" for entry in skill_files}
        if not skill_files or len(tops) != 1:
            raise ValueError("Archive must contain exactly one skill (one SKILL.md).")
        top = tops.pop()
        token = uuid.uuid4().hex
        staged = self._staging_dir / token
        staged.mkdir(parents=True, exist_ok=True)
        for entry in names:
            parts = Path(entry).parts
            relative = Path(*parts[1:]) if top and parts[0] == top else Path(entry)
            if not str(relative):
                continue
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
        skill = _parse_skill(staged / "SKILL.md")
        try:
            validate_name(skill.name if skill.name else staged.name)
        except ValueError:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        extras = sorted(
            str(path.relative_to(staged)) for path in staged.rglob("*") if path.is_file() and path.name != "SKILL.md"
        )
        return _preview(token, skill, extras) | {"name": skill.name if skill.name else staged.name}

    def _stage_single_md(self, data: bytes, filename: str) -> dict[str, Any]:
        """A bare `.md`: one SKILL.md and no resources. The frontmatter has to carry the name,
        since there is no folder to fall back to."""
        if filename.lower().endswith((".zip", ".skill")):
            raise ValueError("Not a valid .zip archive.")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Not a valid skill file — upload a .zip or a SKILL.md.")
        token = uuid.uuid4().hex
        staged = self._staging_dir / token
        staged.mkdir(parents=True, exist_ok=True)
        (staged / "SKILL.md").write_text(text, encoding="utf-8")
        skill = _parse_skill(staged / "SKILL.md")
        if skill.name == token:  # no name in the frontmatter, so the parser fell back to the folder
            shutil.rmtree(staged, ignore_errors=True)
            raise ValueError("The .md file needs YAML frontmatter with at least a skill name.")
        try:
            validate_name(skill.name)
        except ValueError:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        return _preview(token, skill, [])

    def confirm_upload(
        self, token: str, *, scope: str = GLOBAL_SCOPE, workspace: Optional[str | Path] = None
    ) -> dict[str, Any]:
        staged = self._staging_dir / str(token)
        if not (staged / "SKILL.md").is_file():
            raise ValueError("Unknown or expired upload.")
        skill = _parse_skill(staged / "SKILL.md")
        name = validate_name(skill.name)
        base = self._base(scope, workspace)
        folder = self._folder_of(base, name)
        if (folder / "SKILL.md").is_file():
            raise ValueError(f"A skill named '{name}' already exists in that scope.")
        base.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged), str(folder))
        # Provenance is stamped so Settings can tell an upload from a skill written here.
        if not frontmatter_source(folder / "SKILL.md"):
            write_skill_md(
                folder,
                name=name,
                description=skill.description,
                instructions=skill.instructions,
                source="uploaded",
                extra=skill.extra,  # foreign keys in the upload ride through untouched
            )
        return {"name": name, "scope": scope, "path": str(folder)}

    def discard_upload(self, token: str) -> None:
        shutil.rmtree(self._staging_dir / str(token), ignore_errors=True)
