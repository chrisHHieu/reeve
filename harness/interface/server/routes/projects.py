"""Route group moved out of harness/interface/server/app.py `create_app`, unchanged."""
from __future__ import annotations

from __future__ import annotations
from typing import Any


def register_projects(app, manager):
        @app.get("/v1/sessions/{session_id}/project-menu")
        def project_menu(session_id: str, kind: str = "memory") -> dict[str, Any]:
            return manager.project_menu(session_id, kind)
        @app.put("/v1/sessions/{session_id}/bindings")
        def put_binding(session_id: str, body: dict) -> dict[str, Any]:
            body = body or {}
            name = body.get("name")
            return manager.set_binding(
                session_id, str(body.get("kind", "")), str(name) if name else None
            )
        @app.post("/v1/sessions/{session_id}/project-name")
        def name_project(session_id: str, body: dict) -> dict[str, Any]:
            body = body or {}
            return manager.name_current_project(
                session_id, str(body.get("kind", "")), str(body.get("name", ""))
            )
