"""`/v1/remote-environments` — SSH host profiles and the real Test Connection probe.

The management layer of SSH Remote Environments: profiles are local-only configuration
(destinations and an identity-file PATH — the password never enters the JSON; a remembered
one lives in the OS credential store), `POST …/test` runs the system ssh through the
store's runner seam, with the transient password from the request body or the remembered
secret, and `GET …/directories` runs ONE framed listing command through the same seam for
the workspace picker. No route here starts a session or runs a tool on the host; the
session world is built elsewhere.

The store is instantiated per app and exposed as `app.state.remote_environments` so tests
(and phase 2) can swap the ssh runner without patching module globals.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from harness.orchestration.remote_environments import RemoteEnvironmentStore
from harness.orchestration.remote_env.transport import RemoteEnvError
from ..manager import SessionManager


def register_remote(
    app: FastAPI,
    manager: SessionManager,
    *,
    store: Optional[RemoteEnvironmentStore] = None,
) -> None:
    environments = store or RemoteEnvironmentStore()
    app.state.remote_environments = environments
    # The manager validates bindings and resolves a session's profile at engine-build
    # time through this exact store — tests that swap its runner seam reach the engine
    # build too (the remote world's `verify=True` probe runs through it).
    manager.remote_environments = environments

    def _missing(environment_id: str) -> JSONResponse:
        return JSONResponse(
            {
                "ok": False,
                "error": f"No remote environment with id '{environment_id}'.",
            },
            status_code=404,
        )

    @app.get("/v1/remote-environments")
    def remote_environments_list() -> dict[str, Any]:
        return {"environments": environments.list()}

    @app.post("/v1/remote-environments")
    def remote_environments_create(body: dict) -> Any:
        try:
            profile = environments.create(body or {})
        except ValueError as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
        return {"ok": True, "environment": profile}

    @app.patch("/v1/remote-environments/{environment_id}")
    def remote_environments_update(environment_id: str, body: dict) -> Any:
        try:
            profile = environments.update(environment_id, body or {})
        except KeyError:
            return _missing(environment_id)
        except ValueError as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
        return {"ok": True, "environment": profile}

    @app.delete("/v1/remote-environments/{environment_id}")
    def remote_environments_delete(environment_id: str) -> Any:
        try:
            deleted = environments.delete(environment_id)
        except ValueError as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
        if not deleted:
            return _missing(environment_id)
        return {"ok": True}

    @app.post("/v1/remote-environments/{environment_id}/test")
    def remote_environments_test(
        environment_id: str, body: Optional[dict] = None
    ) -> Any:
        # The optional body carries a TRANSIENT password from the form's Test
        # Connection ("used for this test only"); absent/empty means "use the
        # remembered one". It is never written anywhere.
        password = (body or {}).get("password")
        try:
            return environments.test_connection(environment_id, password=password)
        except KeyError:
            return _missing(environment_id)
        except ValueError as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=400)

    @app.get("/v1/remote-environments/{environment_id}/directories")
    def remote_environments_directories(environment_id: str, path: str = "") -> Any:
        # The environment picker's browser: immediate subdirectories on the host, listed
        # through the same session/runner seam as Test Connection (never a world, never a
        # tool). An omitted/empty `path` means the host's $HOME; every refusal is inline
        # in the transport's or the validator's own words.
        try:
            listing = environments.list_directories(environment_id, path)
        except KeyError:
            return _missing(environment_id)
        except (ValueError, PermissionError) as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
        except RemoteEnvError as error:
            return JSONResponse(
                {"ok": False, "error": error.tool_error}, status_code=400
            )
        return {"ok": True, **listing}
