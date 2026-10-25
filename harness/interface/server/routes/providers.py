"""Routes of the model providers, web search and the ChatGPT-subscription sign-in."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException, Response


def register_providers(app: FastAPI, manager, *, spawn: Callable[..., Any]) -> None:
    @app.get("/v1/web-search")
    def web_search_get() -> dict[str, Any]:
        return manager.get_web_search()

    @app.post("/v1/web-search")
    def web_search_set(body: dict) -> dict[str, Any]:
        body = body or {}
        provider = body.get("provider", "")
        if not provider:
            return {"ok": False, "error": "provider required"}
        return manager.set_web_search(
            provider,
            body.get("api_key"),
            remove_key=bool(body.get("remove_key")),
            tavily_key=body.get("tavily_api_key"),
            remove_tavily_key=bool(body.get("remove_tavily_key")),
        )

    @app.post("/v1/web-search/test")
    async def web_search_test(body: Optional[dict] = None) -> dict[str, Any]:
        # A real search, one or two HTTP calls, so it stays off the event loop.
        return await asyncio.to_thread(manager.test_web_search, (body or {}).get("query"))

    @app.get("/v1/browser/frame-check")
    def browser_frame_check(url: str = "") -> dict:
        """Whether a page outside this computer lets the Browser tab show it in a frame: its
        headers are read through the web guard, its body never (`web/frameable.py`)."""
        from harness.integrations.web.frameable import frame_check

        return frame_check(url)

    @app.get("/v1/favicons/{host}")
    def favicon(host: str) -> Response:
        # A search result's site mark, kept by the search that found it (`web/favicons.py`). Local bytes only: nothing here reaches the site.
        from harness.integrations.web import favicons

        path = favicons.cached(host.lower())
        if path is None:
            raise HTTPException(status_code=404, detail="no icon kept for this host")
        return Response(
            content=path.read_bytes(),
            media_type=favicons.MEDIA[path.suffix.lstrip(".")],
            headers={"Cache-Control": "private, max-age=604800", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/v1/providers")
    def providers_get() -> list[dict[str, Any]]:
        return manager.get_providers()

    @app.get("/v1/providers/{name}/models")
    async def provider_models(name: str, refresh: bool = False) -> dict[str, Any]:
        # A live read (blocking httpx or boto3), so off the event loop. When the provider cannot
        # answer, `models` falls back to the curated suggestions with `source: "curated"` and the
        # setup page still renders.
        return await asyncio.to_thread(manager.provider_models, name, refresh)

    @app.get("/v1/models/catalog")
    def models_catalog_get() -> dict[str, Any]:
        # The picker's searchable list: the configured providers' models with the catalogue's
        # facts (context, inputs, reasoning, cost). The only path allowed to refresh models.dev,
        # as a read the person asked for and never as part of building a session.
        return manager.models_catalog()

    @app.post("/v1/providers")
    def providers_set(body: dict) -> dict[str, Any]:
        body = body or {}
        name = body.get("name", "")
        if not name:
            return {"ok": False, "error": "name required"}
        return manager.set_provider(name, body.get("fields"))

    @app.delete("/v1/providers/{name}")
    def providers_remove(name: str) -> dict[str, Any]:
        return manager.remove_provider(name)

    @app.post("/v1/providers/verify")
    async def providers_verify(body: dict) -> dict[str, Any]:
        # A live read-only credential check (blocking httpx), so off the event loop.
        body = body or {}
        return await asyncio.to_thread(manager.verify_provider, body.get("name", "") or "openai", body.get("fields"))

    @app.post("/v1/providers/openai-codex/signin")
    async def codex_signin() -> dict[str, Any]:
        # Opens the system browser and waits on the loopback callback, which can take minutes, so
        # it runs as a background task and the GUI polls the status route for the flip
        # (authorizing → signed_in, or a last_error). It has the shape of the MCP connect route.
        manager.begin_codex_signin()
        spawn(manager.codex_signin(), what="codex signin")
        return {"ok": True, "started": True}

    @app.get("/v1/providers/openai-codex/status")
    def codex_status() -> dict[str, Any]:
        return manager.codex_status()

    @app.post("/v1/providers/openai-codex/signout")
    def codex_signout() -> dict[str, Any]:
        return manager.codex_signout()

    @app.post("/v1/providers/anthropic-subscription/signin")
    async def anthropic_subscription_signin() -> dict[str, Any]:
        manager.begin_anthropic_subscription_signin()
        spawn(manager.anthropic_subscription_signin(), what="anthropic subscription signin")
        return {"ok": True, "started": True}

    @app.get("/v1/providers/anthropic-subscription/status")
    def anthropic_subscription_status() -> dict[str, Any]:
        return manager.anthropic_subscription_status()

    @app.post("/v1/providers/anthropic-subscription/signout")
    def anthropic_subscription_signout() -> dict[str, Any]:
        return manager.anthropic_subscription_signout()
