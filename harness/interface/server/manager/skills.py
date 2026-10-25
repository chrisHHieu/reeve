"""SkillsMixin: skill storage for the Settings screen, and the menu each session sees."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from harness.extensions.skills import SkillLoader, effective_skills
from harness.platform.spawn import spawn



def _result(call: Callable[[], Any], wrap: Callable[[Any], dict[str, Any]] = lambda value: {}) -> dict[str, Any]:
    """Run a store call and answer `{"ok": True, ...}`, or `{"ok": False, "error": ...}` when the
    store refuses with a ValueError (its way of saying what is wrong with the request)."""
    try:
        value = call()
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **wrap(value)}


def _row_for_rail(row: dict[str, Any], enabled: bool) -> dict[str, Any]:
    return {
        "name": row["name"],
        "description": row["description"],
        # A line for people, where the skill has one; the "/" menu prefers it.
        "summary": row.get("summary", ""),
        "scope": row["scope"],
        "enabled": enabled,
        # The composer's `/` popup hides model-only skills; the rail keeps them, badged.
        "model_invocable": row.get("model_invocable", True),
        "user_invocable": row.get("user_invocable", True),
        # The popup ranks by what the worker actually loads.
        "uses": row.get("uses", 0),
        "last_used_at": row.get("last_used_at"),
    }


class SkillsMixin:
    # -- listing and opening -------------------------------------------------------------

    def list_skills(self, workspace: Optional[str] = None) -> list[dict[str, Any]]:
        """The rows of the Settings screen (scope, source, enabled). Given a workspace, that
        project's skills are included and shadow global ones of the same name."""
        return self.skill_store.rows(workspace or None)

    def reveal_skill(self, name: str, workspace: Optional[str] = None) -> dict[str, Any]:
        """Open the skill's folder in the file manager: the window into "the folder is the
        truth". It acts on this machine, for the same reason as revealing an artifact."""
        try:
            folder, _scope = self.skill_store.find(name, workspace or None)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))  # type: ignore[attr-defined]
            else:
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                spawn([opener, str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    # -- the session's menu --------------------------------------------------------------

    def effective_skill_names(
        self, session_id: str, workspace: Optional[str | Path] = None, agent: Optional[str] = None
    ) -> set[str]:
        """The session's skill menu: the merged scopes, minus what Settings disabled, minus the
        session's mutes. The engine's catalog, the rail and the composer popup all read this one
        resolver. No feature skills are bundled: the person installs or writes their own."""
        names = set(SkillLoader(self.skill_store.loader_roots(workspace)).names())
        return effective_skills(
            names=names,
            disabled=self.skill_store.disabled_names(),
            session_overrides=self.session_skills.get(session_id),
        )

    def skill_invocation(
        self, name: str, session_id: str, workspace: Optional[str | Path] = None, agent: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """How a skill in this session's menu may be invoked, or None when it is unknown.

        This is the chokepoint of the force-run path: a skill marked `user-invocable: false` is
        for the model only, so a stale composer sending the `/name` frame is refused. The
        opposite flag, `disable-model-invocation`, is enforced in the engine (catalog and
        `load_skill`) and deliberately not checked here, because a person invoking it is exactly
        what that flag allows."""
        for row in self.skill_store.rows(workspace or None):
            if row["name"] == name:
                return {
                    "scope": row["scope"],
                    "model_invocable": row.get("model_invocable", True),
                    "user_invocable": row.get("user_invocable", True),
                }
        return None

    def session_skills_view(self, session_id: str, workspace: Optional[str] = None) -> dict[str, Any]:
        """The payload of the rail: every in-scope skill Settings has enabled, with its mute
        state."""
        disabled = self.skill_store.disabled_names()
        overrides = self.session_skills.get(session_id)
        rows = [
            _row_for_rail(row, overrides.get(row["name"], True))
            for row in self.skill_store.rows(workspace or None)
            if row["name"] not in disabled
        ]
        return {"skills": rows}

    # -- writing -------------------------------------------------------------------------

    def _scratch_workspace_error(self, workspace: Any) -> Optional[dict[str, Any]]:
        """Refuse to write a skill into a conversation's scratch folder, where it would be
        stranded in a throwaway directory. Checked here so every entry path (the UI, REST, a
        future import) is covered and not only the flows the GUI happens to gate."""
        if not workspace:
            return None
        try:
            folder = Path(str(workspace)).expanduser().resolve()
            if folder.is_relative_to(self.scratch_base().resolve()):
                return {
                    "ok": False,
                    "error": (
                        "That folder is a temporary session space — skills saved there "
                        "would be lost. Save it globally or pick a real project."
                    ),
                }
        except OSError:
            pass
        return None

    def create_skill(self, body: dict[str, Any]) -> dict[str, Any]:
        blocked = self._scratch_workspace_error(body.get("workspace"))
        if blocked:
            return blocked
        return _result(
            lambda: self.skill_store.create(
                name=str(body.get("name", "")),
                description=str(body.get("description", "")),
                instructions=str(body.get("instructions", "")),
                scope=str(body.get("scope", "global") or "global"),
                workspace=body.get("workspace") or None,
            ),
            lambda created: {"skill": created},
        )

    def update_skill(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        def apply() -> None:
            if "enabled" in body:
                self.skill_store.set_enabled(name, bool(body["enabled"]), body.get("workspace") or None)
            if body.get("description") is not None or body.get("instructions") is not None:
                self.skill_store.update(
                    name,
                    description=body.get("description"),
                    instructions=body.get("instructions"),
                    workspace=body.get("workspace") or None,
                )

        return _result(apply)

    def delete_skill(self, name: str, workspace: Optional[str] = None) -> dict[str, Any]:
        return _result(lambda: self.skill_store.delete(name, workspace or None))

    def move_skill(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        # Moving INTO a project must not target a scratch folder. Moving OUT of one is fine: it
        # is the rescue path for skills that were already stranded.
        if str(body.get("scope", "")) == "project":
            blocked = self._scratch_workspace_error(body.get("workspace"))
            if blocked:
                return blocked
        return _result(
            lambda: self.skill_store.move(
                name, to_scope=str(body.get("scope", "")), workspace=body.get("workspace") or None
            ),
            lambda moved: {"skill": moved},
        )

    # -- uploads -------------------------------------------------------------------------

    def stage_skill_upload(self, data: bytes, filename: str = "") -> dict[str, Any]:
        return _result(lambda: self.skill_store.stage_upload(data, filename), lambda preview: dict(preview))

    def confirm_skill_upload(self, body: dict[str, Any]) -> dict[str, Any]:
        blocked = self._scratch_workspace_error(body.get("workspace"))
        if blocked:
            return blocked
        return _result(
            lambda: self.skill_store.confirm_upload(
                str(body.get("token", "")),
                scope=str(body.get("scope", "global") or "global"),
                workspace=body.get("workspace") or None,
            ),
            lambda saved: {"skill": saved},
        )

    # -- community installs --------------------------------------------------------------

    def list_skill_installs(self) -> dict[str, dict[str, Any]]:
        """The lockfile of community installs, keyed by skill name, so the GUI can say
        "installed from <source>" and offer a removal."""
        return self.skill_store.installed_map()

    def stage_skill_install(self, body: dict[str, Any]) -> dict[str, Any]:
        """Run `npx skills add` into a staging directory and return the preview. Nothing is
        installed until the confirm call, and a CLI failure comes back as an actionable error."""
        wanted = body.get("skills") or []
        if not isinstance(wanted, (list, tuple)):
            wanted = [wanted]
        return _result(
            lambda: self.skill_store.stage_install(
                str(body.get("source", "")),
                skills=[str(name) for name in wanted if str(name).strip()],
                agent=str(body.get("agent", "") or ""),
            ),
            lambda preview: dict(preview),
        )

    def confirm_skill_install(self, body: dict[str, Any]) -> dict[str, Any]:
        return _result(
            lambda: self.skill_store.confirm_install(
                str(body.get("token", "")), overwrite=bool(body.get("overwrite", False))
            ),
            lambda saved: {"skills": saved},
        )
