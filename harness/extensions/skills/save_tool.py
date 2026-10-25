"""The `save_skill` tool: how the worker proposes a skill it has written.

The person reviews the name, description, full instructions and any bundled files on an approval
card before anything is saved. The tool's ARGUMENTS are that review surface, which is why its
schema carries the whole instruction body and the file list.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable, Optional

import aisuite as ai

from .skillfile import validate_name
from .store import SkillStore

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "save_skill",
        "description": (
            "Propose adding a finished skill to the user's skills. The user reviews the "
            "name, description, full instructions, and any bundled files on an approval "
            "card before anything is saved; once they approve, the skill is usable in "
            "every conversation. Use this after building or refining a skill in "
            "conversation, and offer it in words like: 'Want me to add <name> to your "
            "skills?' — say 'your skills', never the app name; say 'add', never "
            "'install'. If a skill with this name already exists, approving overwrites "
            "its instructions and adds the files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short folder-safe skill name (letters, digits, dots, dashes, underscores).",
                },
                "description": {
                    "type": "string",
                    "description": "One line saying when the skill applies — this is its menu entry.",
                },
                "instructions": {
                    "type": "string",
                    "description": "The full instruction body (markdown). Becomes SKILL.md.",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional paths of files in this session's folders to bundle into "
                        "the skill (scripts, examples, README). Copied in by basename."
                    ),
                },
            },
            "required": ["name", "description", "instructions"],
        },
    },
}

_SAVED_NOTE = (
    "Saved to the user's skills — usable in every conversation from now on. "
    "Confirm in one short sentence. To browse the installed files, point the "
    "user to Settings > Skills (the file-count chip opens the folder) — do NOT "
    "link the workspace build folder as an artifact; folders don't open there."
)


def _vet_bundle(files: Optional[list[str]], allowed: list[Path]) -> tuple[list[tuple[Path, str]], Optional[str]]:
    """Resolve and check the files to bundle BEFORE anything is written, so a bad file never
    leaves a half-written skill behind. Returns `(chosen, error)`."""
    chosen: list[tuple[Path, str]] = []
    for raw in files or []:
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            if not allowed:
                return [], f"File is outside this session's folders: {raw}"
            path = allowed[0] / path
        try:
            resolved = path.resolve()
        except OSError:
            return [], f"Unreadable file: {raw}"
        if not resolved.is_file():
            return [], f"Not a file: {raw}"
        if not any(folder == resolved or folder in resolved.parents for folder in allowed):
            return [], f"File is outside this session's folders: {raw}"
        base = resolved.name
        if base.lower() == "skill.md":
            # The instructions argument BECOMES SKILL.md, and models routinely draft one in the
            # workspace and bundle it. It is skipped quietly: an error here cost the person a
            # second approval round for a retry that heals itself.
            continue
        if any(base == existing for _path, existing in chosen):
            return [], f"Duplicate bundled filename: {base}"
        chosen.append((resolved, base))
    return chosen, None


def save_skill_tool(
    store: Optional[SkillStore] = None, *, allowed_dirs: Optional[list[str | Path]] = None
) -> Callable:
    """Build the `save_skill` tool. It requires approval, so every call goes through the standard
    approval card. Bundled files may only be read from `allowed_dirs`, the session's roots, because
    the worker must never bundle arbitrary paths of the machine into a skill."""
    store = store or SkillStore()
    allowed: list[Path] = []
    for folder in allowed_dirs or []:
        try:
            allowed.append(Path(folder).expanduser().resolve())
        except OSError:
            continue

    def save_skill(
        name: str, description: str = "", instructions: str = "", files: Optional[list[str]] = None
    ) -> dict[str, Any]:
        try:
            name = validate_name(name)
        except ValueError as exc:
            return {"error": str(exc)}
        if not (description or "").strip():
            return {"error": "A one-line description is required — it becomes the skill's menu entry."}
        if not (instructions or "").strip():
            return {"error": "Skill instructions are required."}
        bundle, problem = _vet_bundle(files, allowed)
        if problem:
            return {"error": problem}

        # A skill the worker writes always lands GLOBAL, never in a throwaway location.
        try:
            folder, _scope = store.find(name)
            action = "updated"
            store.update(name, description=description.strip(), instructions=instructions)
        except ValueError:
            action = "added"
            folder = Path(store.create(name=name, description=description.strip(), instructions=instructions)["path"])
        for source, base in bundle:
            shutil.copy2(source, folder / base)
        return {"ok": True, "name": name, "action": action, "files": [base for _src, base in bundle], "note": _SAVED_NOTE}

    save_skill.__name__ = "save_skill"
    save_skill.__doc__ = _SCHEMA["function"]["description"]
    save_skill.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="save_skill",
        category="skills",
        risk_level="medium",
        capabilities=["save_skill"],
        requires_approval=True,
    )
    save_skill.__tool_schema__ = _SCHEMA
    return save_skill
