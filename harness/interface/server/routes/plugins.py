"""Plugins, marketplaces and slash commands (design §11.4).

No route takes plugin content from a client: an install is `{id, scope}` → a staging token
and what to consent to, then the token → installed. Every write goes through
`harness.extensions.plugins.manager`, the same functions the CLI uses.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException


def guarded_download(url: str) -> bytes:
    """An archive source's bytes, fetched the way web_fetch fetches: every redirect hop
    address-checked and pinned (no loopback, private or metadata address), the body read
    under the install size limit."""
    import httpx

    from harness.extensions.plugins.sources import MAX_BYTES
    from harness.integrations.web.guard import get_checked

    with httpx.Client(follow_redirects=False, timeout=60.0) as client:
        resp = get_checked(client, url, stream=True)
        try:
            if resp.status_code != 200:
                raise ValueError(f"the archive answered HTTP {resp.status_code}")
            data = bytearray()
            for chunk in resp.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ValueError("the archive is larger than the install limit")
            return bytes(data)
        finally:
            resp.close()


def register_plugins(app, manager):
    from harness.extensions.plugins import manager as plugins
    from harness.extensions.plugins import marketplace
    from harness.extensions.plugins.marketplace import PluginError

    def _workspace(session: str, workspace: str) -> Optional[str]:
        """The workspace a request is for: the session's own, else the one asked about,
        else the app's default."""
        if session:
            record = manager.session_store.load(session)
            if record is not None and record.workspace:
                return record.workspace
        return workspace or (str(manager.default_workspace) if manager.default_workspace else None)

    def _run(fn, *args, **kwargs) -> Any:
        try:
            return fn(*args, **kwargs)
        except PluginError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/plugins")
    def plugins_view(session: str = "", workspace: str = "") -> dict[str, Any]:
        from harness.extensions.plugins import resolve_plugins

        ws = _workspace(session, workspace)
        return {**resolve_plugins(workspace=ws).as_dict(), "installed": plugins.list_installed(ws)}

    @app.get("/v1/plugins/project")
    def project_plugins(session: str = "", workspace: str = "") -> dict[str, Any]:
        """What this project declares and suggests that is not in place here yet."""
        from harness.extensions.plugins.project import project_plugins

        return project_plugins(_workspace(session, workspace))

    @app.post("/v1/plugins/install")
    def stage_install(body: dict) -> dict[str, Any]:
        body = body or {}
        return _run(
            plugins.stage_install,
            str(body.get("id") or ""),
            scope=str(body.get("scope") or "user"),
            workspace=str(body.get("workspace") or "") or None,
            download=guarded_download,
        )

    @app.post("/v1/plugins/install/{token}/confirm")
    def confirm_install(token: str, body: Optional[dict] = None) -> dict[str, Any]:
        values = (body or {}).get("user_config")
        return _run(plugins.confirm_install, token, values if isinstance(values, dict) else None)

    @app.delete("/v1/plugins/install/{token}")
    def cancel_install(token: str) -> dict[str, Any]:
        _run(plugins.cancel_install, token)
        return {"ok": True}

    @app.patch("/v1/plugins/{plugin_id}")
    def set_enabled(plugin_id: str, body: dict) -> dict[str, Any]:
        from harness.extensions.plugins import store

        body = body or {}
        scope = str(body.get("scope") or "user")
        try:
            store.set_enabled(plugin_id, bool(body.get("enabled")), scope, str(body.get("workspace") or "") or None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "id": plugin_id, "enabled": bool(body.get("enabled")), "scope": scope}

    @app.post("/v1/plugins/{plugin_id}/consent")
    def stage_consent(plugin_id: str, body: Optional[dict] = None) -> dict[str, Any]:
        """Its code for a yes: changed since the last one, or new to this workspace."""
        workspace = str((body or {}).get("workspace") or "") or None
        return _run(plugins.stage_consent, plugin_id, workspace=workspace)

    @app.post("/v1/plugins/{plugin_id}/keep")
    def keep_flagged(plugin_id: str) -> dict[str, Any]:
        """Keep using a plugin its marketplace withdrew."""
        from harness.extensions.plugins import updates

        return _run(updates.keep_flagged, plugin_id)

    @app.post("/v1/plugins/{plugin_id}/update")
    def stage_update(plugin_id: str) -> dict[str, Any]:
        return _run(plugins.stage_update, plugin_id, download=guarded_download)

    @app.delete("/v1/plugins/{plugin_id}")
    def uninstall(plugin_id: str, scope: str = "user", workspace: str = "", keep_data: bool = False) -> dict[str, Any]:
        return _run(plugins.uninstall, plugin_id, scope=scope, workspace=workspace or None, keep_data=keep_data)

    @app.post("/v1/sessions/{session_id}/reload-plugins")
    async def reload_plugins(session_id: str) -> dict[str, Any]:
        """Rebuild this conversation with the plugins as they are now (between turns)."""
        return await manager.reload_plugins(session_id)

    @app.get("/v1/plugin-marketplaces")
    def marketplaces() -> dict[str, Any]:
        return {"marketplaces": marketplace.list_marketplaces()}

    @app.post("/v1/plugin-marketplaces")
    def add_marketplace(body: dict) -> dict[str, Any]:
        return _run(marketplace.add_marketplace, str((body or {}).get("source") or ""), download=guarded_download)

    @app.post("/v1/plugin-marketplaces/{name}/refresh")
    def refresh_marketplace(name: str) -> dict[str, Any]:
        return _run(marketplace.refresh_marketplace, name, download=guarded_download)

    @app.patch("/v1/plugin-marketplaces/{name}")
    def set_marketplace(name: str, body: dict) -> dict[str, Any]:
        """Auto-update on or off for this marketplace (off by default, design §8.4)."""
        from harness.extensions.plugins import updates

        return _run(updates.set_auto_update, name, bool((body or {}).get("autoUpdate")))

    @app.delete("/v1/plugin-marketplaces/{name}")
    def remove_marketplace(name: str) -> dict[str, Any]:
        _run(marketplace.remove_marketplace, name)
        return {"ok": True}

    @app.get("/v1/plugin-marketplaces/{name}/plugins")
    def marketplace_plugins(name: str) -> dict[str, Any]:
        """Discover: what a marketplace offers, with what is already installed marked."""
        from harness.extensions.plugins import store

        entries = _run(marketplace.marketplace_entries, name)
        installed = store.installed()
        return {
            "plugins": [
                {
                    "id": f"{e['name']}@{name}",
                    "name": e["name"],
                    "description": str(e.get("description") or ""),
                    "version": str(e.get("version") or ""),
                    "installed": f"{e['name']}@{name}" in installed,
                }
                for e in entries
            ]
        }

    @app.get("/v1/commands")
    def commands(session: str = "", workspace: str = "") -> dict[str, Any]:
        from harness.extensions.commands import load_commands

        found = load_commands(_workspace(session, workspace))
        return {"commands": [spec.listing() for spec in sorted(found.values(), key=lambda s: s.name)]}

    @app.get("/v1/commands/{name}")
    def command(name: str, session: str = "", workspace: str = "") -> dict[str, Any]:
        from harness.extensions.commands import load_commands

        spec = load_commands(_workspace(session, workspace)).get(name)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"no command named {name}")
        return {**spec.listing(), "body": spec.body}
