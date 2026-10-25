"""Route group moved out of harness/interface/server/app.py `create_app`, unchanged."""
from __future__ import annotations

from __future__ import annotations
from typing import Any


def register_settings(app, manager):
        @app.get("/v1/settings")
        def settings_get() -> dict[str, Any]:
            return manager.get_settings()
        @app.post("/v1/settings/model-key")
        def settings_set_model_key(body: dict) -> dict[str, Any]:
            return manager.set_model_key((body or {}).get("api_key", ""))
        @app.post("/v1/settings/default-model")
        def settings_set_default_model(body: dict) -> dict[str, Any]:
            return manager.set_default_model((body or {}).get("model", ""))
        @app.post("/v1/settings/default-mode")
        def settings_set_default_mode(body: dict) -> dict[str, Any]:
            return manager.set_default_mode((body or {}).get("mode", ""))
        @app.post("/v1/settings/models/add")
        def settings_models_add(body: dict) -> dict[str, Any]:
            return manager.add_model((body or {}).get("model", ""))
        @app.post("/v1/settings/models/remove")
        def settings_models_remove(body: dict) -> dict[str, Any]:
            return manager.remove_model((body or {}).get("model", ""))
        @app.post("/v1/settings/onboarded")
        def settings_set_onboarded(body: dict) -> dict[str, Any]:
            return manager.set_onboarded(bool((body or {}).get("value", True)))
        @app.post("/v1/settings/experimental-connectors")
        def settings_set_experimental(body: dict) -> dict[str, Any]:
            return manager.set_experimental_connectors(bool((body or {}).get("value")))
        @app.post("/v1/settings/scratch-base")
        def settings_set_scratch_base(body: dict) -> dict[str, Any]:
            return manager.set_scratch_base(str((body or {}).get("path", "")))
        @app.post("/v1/settings/sessions-peek")
        def settings_set_sessions_peek(body: dict) -> dict[str, Any]:
            # Sidebar: sessions shown under Recent before "Show more" (owner ask, 2026-07-03).
            return manager.set_sessions_peek((body or {}).get("sessions_peek", 5))
        @app.post("/v1/settings/sidebar-organize")
        def settings_set_sidebar_organize(body: dict) -> dict[str, Any]:
            # Sidebar: sessions grouped by their folder, or in one list by time.
            return manager.set_sidebar_organize((body or {}).get("sidebar_organize"))
        @app.post("/v1/settings/context-bar")
        def settings_set_context_bar(body: dict) -> dict[str, Any]:
            # Composer: show the context-window fill bar, or just the popover (owner ask).
            return manager.set_context_bar((body or {}).get("context_bar", True))
        @app.post("/v1/settings/tool-discovery")
        def settings_set_tool_discovery(body: dict) -> dict[str, Any]:
            # Progressive tool discovery: MCP schemas leave the wire and the
            # model reaches them through tool_search/tool_describe/tool_call. Applies on
            # the next session build; default off.
            return manager.set_tool_discovery((body or {}).get("enabled", False))
        @app.post("/v1/settings/shell")
        def settings_set_shell(body: dict) -> dict[str, Any]:
            # The default shell for new sessions and the terminal panel; "" is the OS's own.
            return manager.set_default_shell((body or {}).get("shell", ""))
        @app.post("/v1/settings/visuals")
        def settings_set_visuals(body: dict) -> dict[str, Any]:
            # When the model may show a visual: auto, asked or off. Applies on the
            # next session build, like the tool-discovery toggle.
            return manager.set_visuals((body or {}).get("visuals"))
        @app.post("/v1/settings/subagent-model-selection")
        def settings_set_subagent_model_selection(body: dict) -> dict[str, Any]:
            # May a delegation call pick its own
            # provider/model (in-process children) or model (CLI children)? Applies on
            # the next session build, like the tool-mode toggle.
            return manager.set_subagent_model_selection(
                (body or {}).get("enabled", True)
            )
        @app.post("/v1/settings/show-agent")
        def settings_set_show_agent(body: dict) -> dict[str, Any]:
            # The composer "Show agent" toggle (P4): off by default,
            # persisted in prefs. Purely a client affordance.
            return manager.set_show_agent((body or {}).get("show_agent", False))
        @app.post("/v1/settings/pdf")
        def settings_set_pdf(body: dict) -> dict[str, Any]:
            # Token savings (owner ask, 2026-07-17): fallback mode for models without native
            # PDF support + attach-time page/size thresholds.
            b = body or {}
            return manager.set_pdf_settings(
                fallback=b.get("pdf_fallback"),
                max_pages=b.get("pdf_max_pages"),
                max_mb=b.get("pdf_max_mb"),
            )
        # Auto-compaction has NO write endpoint on purpose: it is a system behavior
        # configured in `config.toml` `[compaction]`, not a UI toggle.
        # The read side stays in /v1/settings so diagnostics can report what is in force.
