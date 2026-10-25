"""Per-session switches: muted skills and connector overrides."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def register_session_controls(app: FastAPI, manager) -> None:
    @app.get("/v1/sessions/{session_id}/skills")
    def session_skills(session_id: str, workspace: str = "") -> dict[str, Any]:
        # Read by the rail's skills group and by the composer popup.
        return manager.session_skills_view(session_id, workspace or None)

    @app.post("/v1/sessions/{session_id}/skills")
    def set_session_skill(session_id: str, body: dict) -> dict[str, Any]:
        # A session mute. `clear` drops the override so the session inherits again; otherwise it
        # is an explicit on or off. Nothing on disk changes, since Settings owns permanent state.
        body = body or {}
        skill = str(body.get("skill", "")).strip()
        if not skill:
            return {"ok": False, "error": "skill required"}
        if body.get("clear"):
            manager.session_skills.clear(session_id, skill)
        else:
            manager.session_skills.set(session_id, skill, bool(body.get("enabled", False)))
        return manager.session_skills_view(session_id, str(body.get("workspace", "")) or None)

    @app.get("/v1/sessions/{session_id}/connections")
    def session_connections(session_id: str) -> dict[str, Any]:
        # The drawer's payload.
        return manager.session_connections_view(session_id)

    @app.post("/v1/sessions/{session_id}/connections")
    def set_session_connection(session_id: str, body: dict) -> dict[str, Any]:
        # A session override, with the same `clear` rule as skills. The refreshed view comes back
        # so the drawer can redraw.
        body = body or {}
        connector = str(body.get("connector", "")).strip()
        if not connector:
            return {"ok": False, "error": "connector required"}
        if body.get("clear"):
            manager.session_connections.clear(session_id, connector)
        else:
            manager.session_connections.set(session_id, connector, bool(body.get("enabled", False)))
        return {"ok": True, "connections": manager.session_connections_view(session_id)}
