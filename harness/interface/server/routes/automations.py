"""Route group moved out of harness/interface/server/app.py `create_app`, unchanged."""
from __future__ import annotations

from __future__ import annotations
from typing import Any


def register_automations(app, manager):
        @app.get("/v1/messaging/dm-route")
        def dm_route_get() -> dict[str, Any]:
            return {"dm_session": manager.dm_session()}
        @app.post("/v1/messaging/dm-route")
        def dm_route_set(body: dict) -> dict[str, Any]:
            # A falsy session_id clears the designation (DMs then park as unrouted).
            return manager.set_dm_session((body or {}).get("session_id", ""))
        @app.get("/v1/automations")
        def automations_list() -> dict[str, Any]:
            return manager.list_automations()
        @app.post("/v1/automations")
        def automations_create(body: dict) -> dict[str, Any]:
            return manager.create_automation(body or {})
        @app.get("/v1/automations/{task_id}")
        def automation_get(task_id: str) -> dict[str, Any]:
            return manager.get_automation(task_id)
        @app.patch("/v1/automations/{task_id}")
        def automation_update(task_id: str, body: dict) -> dict[str, Any]:
            return manager.update_automation(task_id, body or {})
        @app.delete("/v1/automations/{task_id}")
        def automation_delete(task_id: str) -> dict[str, Any]:
            return manager.delete_automation(task_id)
        @app.post("/v1/automations/{task_id}/seen")
        def automations_seen(task_id: str) -> dict[str, Any]:
            return manager.mark_automation_seen(task_id)
        @app.post("/v1/automations/{task_id}/run")
        def automation_run(task_id: str) -> dict[str, Any]:
            # Prepare a live manual run; the GUI opens the returned session and drives it.
            return manager.prepare_manual_run(task_id)
        @app.post("/v1/automations/{task_id}/runs/{run_id}/finalize")
        def automation_run_finalize(task_id: str, run_id: str) -> dict[str, Any]:
            return manager.finalize_manual_run(task_id, run_id)
