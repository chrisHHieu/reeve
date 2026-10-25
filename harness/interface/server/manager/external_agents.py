"""ExternalAgentsMixin: the external CLI agents (codex, claude-code) Settings can set up."""

from __future__ import annotations

import subprocess
from typing import Any

from harness.platform.spawn import spawn

_PROVIDERS = ("codex", "claude-code")


class ExternalAgentsMixin:
    def external_agents(self) -> list[dict[str, Any]]:
        """Install and sign-in status of the external CLI providers (Settings ▸ Subagents). It is
        best effort: `installed` is the binary on PATH and `signed_in` a credential marker (the
        CLI's own auth file, or an API key in the environment). The values never leave the
        machine."""
        from harness.orchestration.subagent_external import auth_status, available

        rows: list[dict[str, Any]] = []
        for provider in _PROVIDERS:
            path = available(provider)
            rows.append({"provider": provider, "installed": bool(path), "path": path or "", **auth_status(provider)})
        return rows

    def external_agent_action(self, provider: str, action: str) -> dict[str, Any]:
        """Start a CLI sign-in (`login`) or an npm install (`install`), detached. Both open a
        browser or download packages and can take minutes, so the page polls `external_agents`
        for the badge to flip and does not wait on this call."""
        from harness.orchestration.subagent_external import install_argv, login_argv

        if action not in ("login", "install"):
            return {"ok": False, "error": f"unknown action: {action}"}
        argv = login_argv(provider) if action == "login" else install_argv(provider)
        if argv is None:
            return {"ok": False, "error": f"cannot {action} {provider} — the CLI or npm is not available"}
        try:
            spawn(argv, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # noqa: S603
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "started": True, "command": " ".join(argv)}

    def external_agent_models(self, provider: str) -> dict[str, Any]:
        """The provider account's own model list (`GET .../external/{provider}/models`): one HTTP
        call with the CLI's own credential, within 10 seconds. A failure is a value and not an
        exception, so the GUI can fall back to static aliases."""
        from harness.orchestration.subagent_external import list_models

        return list_models(provider)
