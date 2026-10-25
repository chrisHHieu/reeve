"""Route group moved out of harness/interface/server/app.py `create_app`, unchanged."""
from __future__ import annotations

from __future__ import annotations
from typing import Any, Optional
from fastapi.responses import JSONResponse
from harness.orchestration.subagent_files import SubagentError


def register_core(app, manager):
        @app.get("/v1/subagents")
        def subagents(workspace: Optional[str] = None) -> dict[str, Any]:
            # The `task` agent DEFINITIONS. The GUI's @agent picker (P4) and the task
            # card's agent color read this.
            return {"agents": manager.list_subagents(workspace)}
        @app.get("/v1/subagents/external")
        def subagents_external() -> dict[str, Any]:
            # Install/auth status for the CLI providers (codex / claude-code) — the
            # Subagents page's badge + Sign in / Install actions.
            return {"providers": manager.external_agents()}
        @app.post("/v1/subagents/external/{provider}/{action}")
        def subagents_external_action(provider: str, action: str) -> dict[str, Any]:
            return manager.external_agent_action(provider, action)
        @app.get("/v1/subagents/external/{provider}/models")
        def subagents_external_models(provider: str) -> dict[str, Any]:
            # The CLI account's own model list (Claude: /v1/models with the CLI's OAuth
            # token). `{ok: false, error}` is a value: the GUI falls back to aliases.
            return manager.external_agent_models(provider)
        @app.get("/v1/lsp")
        def lsp_status() -> dict[str, Any]:
            # The shared language-server pool (one client per workspace+language, spawned
            # lazily by the `lsp` tool). The Status popover's LSP tab reads this.
            return manager.lsp_status()

        @app.put("/v1/subagents/{name:path}/model")
        def set_subagent_model(
            name: str, body: dict, workspace: Optional[str] = None
        ) -> Any:
            # Settings ▸ Subagents: the ONE editable field of a built-in (`general`,
            # `explore`) — `{model: string|null}`, null meaning "inherit the session's
            # model". Registered BEFORE the definition PUT on purpose: `{name:path}`
            # swallows slashes, so the broad route would otherwise claim
            # `/v1/subagents/general/model` as a subagent literally named that.
            try:
                return manager.set_builtin_model(name, body, workspace)
            except SubagentError as error:
                return JSONResponse({"error": str(error)}, status_code=error.status)
            except (ValueError, OSError) as error:
                return JSONResponse({"error": str(error)}, status_code=400)

        @app.put("/v1/subagents/{name:path}/enabled")
        def set_subagent_enabled(
            name: str, body: dict, workspace: Optional[str] = None
        ) -> Any:
            # Settings ▸ Subagents: a built-in subagent's enable switch — `{enabled:
            # bool}`. OFF keeps the row (so the switch can flip back) while the task
            # listing, the `task` tool and the spawn all refuse it. Registered BEFORE
            # the definition PUT for the same `{name:path}` precedence reason as /model.
            try:
                return manager.set_subagent_enabled(name, body, workspace)
            except SubagentError as error:
                return JSONResponse({"error": str(error)}, status_code=error.status)
            except (ValueError, OSError) as error:
                return JSONResponse({"error": str(error)}, status_code=400)

        @app.put("/v1/subagents/{name:path}")
        def save_subagent(
            name: str, body: dict, workspace: Optional[str] = None
        ) -> Any:
            # Settings ▸ Subagents: create/replace one markdown definition. `{name:path}`
            # so a nested name (`team/helper`) is one route param; the manager validates
            # the name and the body and writes `<root>/agents/<name>.md` atomically.
            # Every rejection is a JSON `{error}` with a status — the sidecar never
            # raises out of an edit.
            try:
                return manager.save_subagent(name, body, workspace)
            except SubagentError as error:
                return JSONResponse({"error": str(error)}, status_code=error.status)
            except (ValueError, OSError) as error:
                return JSONResponse({"error": str(error)}, status_code=400)

        @app.delete("/v1/subagents/{name:path}")
        def delete_subagent(
            name: str, scope: str = "user", workspace: Optional[str] = None
        ) -> Any:
            # Deleting an override restores the built-in: the file goes, the next read
            # resolves the built-in again. 404 when the scope's file is not there.
            try:
                return manager.delete_subagent(name, scope, workspace)
            except SubagentError as error:
                return JSONResponse({"error": str(error)}, status_code=error.status)
            except (ValueError, OSError) as error:
                return JSONResponse({"error": str(error)}, status_code=400)
        @app.get("/v1/capabilities")
        def capabilities() -> dict[str, Any]:
            # `GET /experimental/capabilities` → `{backgroundSubagents}`
            #. Ours is not behind an experimental flag —
            # background spawns ship unconditionally (the ledger's "Background gate" note) —
            # so the answer is a constant, served so a client can gate its UI on the fact
            # rather than on this build's version.
            return {"background_subagents": True}
        @app.get("/v1/permissions")
        def permissions() -> dict[str, Any]:
            # The parked permission asks (P1, `GET /permission`): child tool
            # asks and `task` spawn asks waiting for a reply. The manager's registry is
            # process-local, so a restart drops them — the turns they belonged to ended
            # with the process (the `interrupted` reconciliation covers those children).
            return {"permissions": manager.pending_permissions()}
        @app.post("/v1/permissions/{request_id}/reply")
        async def permission_reply(request_id: str, body: dict) -> dict[str, Any]:
            # `POST /permission/:id/reply`: `{reply: once|always|reject,
            # feedback?}` → `{ok: true}`, or `{ok: false, error}` for an unknown or
            # already-answered request (which includes one a cancel just resolved).
            body = body or {}
            return manager.reply_permission(
                request_id,
                str(body.get("reply", "")),
                str(body.get("feedback") or ""),
            )
