"""Route group moved out of harness/interface/server/app.py `create_app`, unchanged."""
from __future__ import annotations

from __future__ import annotations
import base64
import binascii
from typing import Any


def register_skills(app, manager):
        @app.get("/v1/skills")
        def skills(workspace: str = "") -> dict[str, Any]:
            # `installs` is the community-install lockfile (batch 3/P2): provenance keyed by
            # skill name for the "installed from <source>" badge + remove action.
            return {
                "skills": manager.list_skills(workspace or None),
                "installs": manager.list_skill_installs(),
            }
        @app.post("/v1/skills")
        def create_skill(body: dict) -> dict[str, Any]:
            return manager.create_skill(body or {})
        @app.patch("/v1/skills/{name}")
        def update_skill(name: str, body: dict) -> dict[str, Any]:
            return manager.update_skill(name, body or {})
        @app.delete("/v1/skills/{name}")
        def delete_skill(name: str, workspace: str = "") -> dict[str, Any]:
            return manager.delete_skill(name, workspace or None)
        @app.post("/v1/skills/{name}/move")
        def move_skill(name: str, body: dict) -> dict[str, Any]:
            return manager.move_skill(name, body or {})
        @app.post("/v1/skills/{name}/reveal")
        def reveal_skill(name: str, body: dict) -> dict[str, Any]:
            # "Show folder": open the skill's folder in the OS file manager (local machine).
            return manager.reveal_skill(name, str((body or {}).get("workspace", "")) or None)
        @app.post("/v1/skills/upload")
        def stage_skill_upload(body: dict) -> dict[str, Any]:
            # Stage → preview; nothing is installed until /upload/confirm.
            data_b64 = str((body or {}).get("data_b64", ""))
            if not data_b64:
                return {"ok": False, "error": "No archive supplied."}
            try:
                data = base64.b64decode(data_b64, validate=True)
            except (ValueError, binascii.Error):
                return {"ok": False, "error": "Invalid archive encoding."}
            return manager.stage_skill_upload(data, str((body or {}).get("filename", "")))
        @app.post("/v1/skills/upload/confirm")
        def confirm_skill_upload(body: dict) -> dict[str, Any]:
            return manager.confirm_skill_upload(body or {})
        @app.post("/v1/skills/install")
        def stage_skill_install(body: dict) -> dict[str, Any]:
            # Community install (batch 3/P2): `npx skills add` runs into a staging token dir;
            # nothing lands in the product root until /install/confirm. Tests inject the
            # store's runner seam — no network.
            return manager.stage_skill_install(body or {})
        @app.post("/v1/skills/install/confirm")
        def confirm_skill_install(body: dict) -> dict[str, Any]:
            return manager.confirm_skill_install(body or {})
