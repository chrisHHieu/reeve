"""The `ready` frame a socket opens with: the session's state as the server knows it."""

from __future__ import annotations

from typing import Any

from ..manager.anchor import session_workspace


def ready_frame(manager, engine, session_id: str) -> dict[str, Any]:
    # A session bound to another machine works in a POSIX path over there. The GUI builds
    # `user@host:path` from this field, since the `workspace` frame is the local anchor and a
    # stale local spelling must not reach the remote readout.
    world = getattr(engine, "world", None)
    remote_workspace = str(getattr(world, "root", "") or "") if getattr(world, "is_remote", False) else ""
    workspace = session_workspace(engine)
    return {
        "type": "ready",
        "data": {
            "session_id": session_id,
            "agent": getattr(engine, "agent_name", "code"),
            "model": engine.model,
            "mode": engine.permissions.mode.value,
            # The plan overlay is separate from the mode and hard-denies consequential actions
            # on top of it.
            "plan": bool(engine.permissions.plan),
            # None means the model's own default.
            "thinking": getattr(engine, "thinking_level", None),
            # The primary root rather than the shell's cwd, which moves when the agent cd's.
            "workspace": workspace,
            "remote_workspace": remote_workspace or None,
            # The shell a command that names none runs in, pinned when this world was built.
            # The default is a Setting, so the OS alone does not say it: a bash
            # session's rows were drawn as PowerShell (owner, 2026-10-15).
            "shell": _default_shell(world),
            # A temporary folder's path is never shown; this flag is how the GUI knows to say
            # "Temporary folder" and offer to save it as a project.
            "temp_workspace": manager.is_temp_workspace(workspace),
            "command_trust": manager.workspace_command_trust(
                str(getattr(engine, "audit_context", {}).get("workspace", ""))
            ),
            # Whether a turn is already in flight, so a reload mid-turn does not draw an idle
            # composer whose next message bounces with "already running a turn".
            "running": manager.is_running(session_id),
            # Where this session compacts (the context gauge's denominator). Every
            # assistant_message carries it too, so a model switch moves it.
            "compact_at": (
                engine._history_trigger_tokens() if hasattr(engine, "_history_trigger_tokens") else None
            ),
        },
    }


def _default_shell(world) -> str | None:
    """The local world's default shell kind, or None for a remote world (POSIX over there)."""
    if world is None or getattr(world, "is_remote", False):
        return None
    try:
        pinned = getattr(getattr(world, "executor", None), "default_shell", None)
        kind = pinned() if callable(pinned) else None
    except Exception:  # noqa: BLE001 - a fact for the card, never a reason to fail a connect
        return None
    return kind if isinstance(kind, str) and kind else None
