"""The web: `web_search` and `web_fetch`."""

from __future__ import annotations

from harness.integrations.web import PageAsker, make_web_fetch_tool, make_web_search_tool

from .base import FeatureContext


class Web:
    id = "web"

    def setup(self, ctx: FeatureContext) -> None:
        """Web search and fetch for every agent (keyless DuckDuckGo by default)."""
        secrets = ctx.secrets
        ctx.registry.register(make_web_search_tool(secrets))
        # `web_fetch(prompt=…)` has a model read the page. The asker is bound to the
        # session's provider and model once the engine exists, and the model is read when a page is
        # asked about, so a mid-session switch of model is followed.
        asker = PageAsker()
        ctx.registry.register(make_web_fetch_tool(ctx.spill, asker=asker))
        ctx.on_engine_ready(lambda engine: asker.bind(ctx.provider, lambda: engine.model))

        # The web_search approval card names the live destination ("Queries go to your configured
        # search provider (currently: ‹name›)"). It is resolved when the card is raised and not
        # at the start of the session, so a mid-session Settings change shows through.
        def search_provider(_arguments: dict) -> dict:
            from harness.integrations.web import provider_name

            return {"search_provider": provider_name(secrets)}

        ctx.approval_extra("web_search", search_provider)
