"""A child's own worktree, asked for per delegation and handed back by a person.

Child worktrees gave children a worktree behind one global switch, off by default, and left a
kept worktree as "the deliverable: merging is the person's decision". Two gaps: the caller
that knows two children will write the same module could not ask for isolation for THEM,
and a person who does not use git could not take the work. This is both halves:

- `wants_worktree`: a delegation is isolated when the call asks (`isolation="worktree"`),
  when its agent definition does, or when the global switch is on;
- `child_worktree_view` / `apply_child_worktree` / `discard_child_worktree`: what the copy
  holds, and the two answers the Review surface offers. The parent's model is told which
  answer the person gave, on the same live line that names its running children.

The worktree is found from the manager's map, else read back from disk (the path and the
branch encode the child id), so the two answers survive a restart.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from harness.config.config import load_config

logger = logging.getLogger(__name__)

ISOLATION_WORKTREE = "worktree"


class ChildWorktreesMixin:
    def wants_worktree(self, parent: Any, isolation: str = "", agent: Optional[Any] = None) -> bool:
        """Whether this delegation gets its own worktree: asked for on the call, declared
        by the agent's definition, or the session's `[delegation] worktree_isolation`."""
        if isolation == ISOLATION_WORKTREE or getattr(agent, "isolation", "") == ISOLATION_WORKTREE:
            return True
        try:
            config = load_config(parent.workspace)
            table = config.delegation if isinstance(config.delegation, dict) else {}
            return bool(table.get("worktree_isolation", False))
        except Exception:
            return False

    def _worktree_root(self, parent_id: str) -> Optional[Any]:
        """The repo root a session's children's worktrees live under, or None."""
        from harness.orchestration.worktrees import repo_root

        parent = self.session_store.load(parent_id)
        if parent is None or not parent.workspace or parent.environment_id:
            return None
        return repo_root(parent.workspace)

    def child_worktree(self, child_id: str) -> Optional[dict[str, Any]]:
        """The worktree `child_id` worked in, if it still exists."""
        from harness.orchestration.worktree_handback import find_child_worktree

        known = self._child_worktrees.get(child_id)
        if known:
            return known
        child = self.session_store.load(child_id)
        parent = self.session_store.load(child.parent_session) if child and child.parent_session else None
        if child is None or child.kind != "subagent" or parent is None or not parent.workspace:
            return None
        if parent.environment_id:
            return None  # a remote child never had one
        return find_child_worktree(parent.workspace, child_id)

    def child_worktree_view(self, child_id: str) -> dict[str, Any]:
        """`{worktree: null}` or `{worktree: {path, branch, files, busy}}` for the API."""
        from harness.orchestration.worktree_handback import worktree_changes

        info = self.child_worktree(child_id)
        if not info:
            return {"worktree": None}
        changes = worktree_changes(info)
        return {
            "worktree": {
                "path": info.get("path"),
                "branch": info.get("branch"),
                "files": changes.get("files") or [],
                # A child still writing must finish (or be stopped) before its copy is taken.
                "busy": self.is_running(child_id),
                **({"error": changes["error"]} if changes.get("error") else {}),
            }
        }

    def apply_child_worktree(self, child_id: str) -> dict[str, Any]:
        """Put the child's work into the parent's folder, all of it or none."""
        from harness.orchestration.worktree_handback import apply_child_worktree

        info, refusal = self._handback_target(child_id)
        if refusal:
            return {"ok": False, "error": refusal}
        result = apply_child_worktree(info)
        if not result.get("applied"):
            return {"ok": False, "error": result.get("reason") or "the changes could not be applied"}
        self._child_worktrees.pop(child_id, None)
        self._note_handback(child_id, "applied", len(result.get("files") or []))
        return {"ok": True, "files": result.get("files") or []}

    def discard_child_worktree(self, child_id: str) -> dict[str, Any]:
        """Drop the child's copy and the work in it."""
        from harness.orchestration.worktree_handback import discard_child_worktree

        info, refusal = self._handback_target(child_id)
        if refusal:
            return {"ok": False, "error": refusal}
        result = discard_child_worktree(info)
        if not result.get("removed"):
            return {"ok": False, "error": result.get("reason") or "the copy could not be removed"}
        self._child_worktrees.pop(child_id, None)
        self._note_handback(child_id, "discarded", 0)
        return {"ok": True}

    def _handback_target(self, child_id: str) -> tuple[Optional[dict[str, Any]], str]:
        if self.is_running(child_id):
            return None, "this subagent is still working — stop it or wait for it to finish first"
        info = self.child_worktree(child_id)
        if not info:
            return None, "this subagent has no separate copy to hand back"
        return info, ""

    def _note_handback(self, child_id: str, verdict: str, files: int) -> None:
        """Remember the person's answer for the parent's live context (`handback_context`)."""
        child = self.session_store.load(child_id)
        parent_id = child.parent_session if child else None
        if not parent_id:
            return
        with self._subagent_lock:
            self._worktree_handbacks.setdefault(parent_id, {})[child_id] = (verdict, files)
        logger.info("worktree isolation: %s %s by the user", child_id, verdict)


def handback_context(manager: Any, parent_id: str) -> str:
    """What the person did with this session's isolated children's work, for the model:
    the task envelope told it "kept in a worktree, not in your workspace yet", and only a
    person's click changes that."""
    with manager._subagent_lock:
        answers = sorted((manager._worktree_handbacks.get(parent_id) or {}).items())
    if not answers:
        return ""
    rows = []
    for child_id, (verdict, files) in answers:
        if verdict == "applied":
            rows.append(
                f"- {child_id} — the user APPLIED its changes: {files} file(s) are now uncommitted "
                "changes in your workspace; its worktree and branch are gone"
            )
        else:
            rows.append(
                f"- {child_id} — the user DISCARDED its changes: they are not in your workspace, "
                "and its worktree and branch are gone"
            )
    return "Isolated subagent work the user has decided on:\n" + "\n".join(rows)


__all__ = ["ChildWorktreesMixin", "ISOLATION_WORKTREE", "handback_context"]
