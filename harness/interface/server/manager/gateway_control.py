"""GatewayControlMixin: starting, reloading and stopping the messaging gateway."""

from __future__ import annotations

import asyncio

from harness.integrations.connectors import Gateway, load_settings, make_adapter


class GatewayControlMixin:
    async def start_gateway(self) -> list[str]:
        """Build the messaging gateway and start the enabled listeners. Inbound messages go to
        durable sessions: a channel message to its subscribers, a direct message to the
        designated DM session (else it is parked). Returns the platforms whose listeners came up."""
        # Team steering and kicks are dispatched from tool threads, which need the app's loop.
        self._loop = asyncio.get_running_loop()
        self.scheduler.start()  # the automations tick, independent of any connector
        if not self._metrics_ticker_started:
            # The one-time backfill and slow refresh of the Ops fold. It starts here and not in
            # `__init__`, so a test that builds a manager never spawns the thread.
            self._metrics_ticker_started = True
            self._start_metrics_ticker()
        return await self._build_and_start_gateway()

    async def refresh_gateway(self) -> list[str]:
        """Reload the listeners with fresh secrets, after a connector connects or disconnects,
        so a pasted token takes effect at once. A platform socket (Slack Socket Mode)
        authenticates when it connects, so new credentials mean reopening it. The adapters are
        replaced in-process and the sidecar never restarts."""
        await self.stop_gateway()
        started = await self._build_and_start_gateway()
        print(f"[reeve] messaging gateway reloaded: {', '.join(started) or 'none'}")
        return started

    async def _build_and_start_gateway(self) -> list[str]:
        settings = load_settings(self.secrets)
        self.gateway = Gateway(
            secrets=self.secrets,
            settings=settings,
            handler=self._dispatch_inbound,
            on_unauthorized=self._park_unauthorized,
        )
        for platform, platform_settings in settings.items():
            if not platform_settings.enabled:
                continue
            profile = self.secrets.get(f"{platform}:default") or {}
            adapter = make_adapter(platform, profile, secrets=self.secrets)
            if adapter is not None:
                self.gateway.register(adapter)
        return await self.gateway.start()

    async def stop_gateway(self) -> None:
        if self.gateway is not None:
            await self.gateway.stop()
            self.gateway = None
