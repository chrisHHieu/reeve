"""The harness's own browser for design checks.

A design is an HTML file, and what can be known of it without a person (does it run off a phone,
can its text be read, does Tab reach its buttons) is known by loading it in a browser and measuring.
This browser is the harness's, not the model's: the model has its own (the built-in Playwright MCP
server) and may drive it however it likes, while what this one measures is a fact the model reads
and cannot shape.

It is the Chromium already on the machine (Edge on every Windows install, else Chrome or Chromium,
else a Chromium the Playwright cache holds), driven over the DevTools protocol with `websockets`,
which Reeve already depends on. Nothing is installed for it. Measured on 2026-10-19 (Edge 141 and
Chrome 141, Windows 11): a launch is about 0.4 s and each width about 0.1 s after the first, so a
three-width check is about one second.

One browser per check, in a fresh profile that is deleted after: nothing a page stores outlives
its check, and there is no long-lived process to leak. The page is loaded from disk with the
network blocked (`Network.setBlockedURLs`), the same rule a previewed page follows until the person
lets it out, and what it tried to reach is reported.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from harness.platform.spawn import spawn

from .interact import stretch, try_controls
from .locate import find_browser
from .cdp import Started, _Cdp, _stop, close_at, remove_profile, wait_started
from .shots import VIEW_HEIGHT, capture as _capture, region as _region, shoot as _shoot, thumb as _thumb

logger = logging.getLogger(__name__)

_LAUNCH_TIMEOUT_S = 10.0
_LOAD_TIMEOUT_S = 8.0
#: Each pass after the measuring (clicks, dark, long text) gets this long, then is named unfinished.
_PASS_TIMEOUT_S = 20.0


def _media(scheme: str) -> dict[str, Any]:
    """The media a page is measured under: `scheme`, and motion as a person who has not asked for
    less of it sees it. A headless browser follows the machine on both; a Windows with its animation
    effects off measured every menu as "shows at once" (2026-10-19). Each call sets every feature."""
    return {
        "features": [
            {"name": "prefers-color-scheme", "value": scheme},
            {"name": "prefers-reduced-motion", "value": "no-preference"},
        ]
    }


@dataclass
class WidthRender:
    """One width's load: what the measuring script returned and the screenshot."""

    width: int
    measured: dict[str, Any] = field(default_factory=dict)
    png: bytes = b""


@dataclass
class Render:
    """A page loaded at each width it was asked for."""

    widths: list[WidthRender] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    #: Local files the page links that could not be loaded (file: URLs), on the first load.
    missing: list[str] = field(default_factory=list)
    #: What each control did when clicked, at the narrowest width (`interact.try_controls`).
    interactions: list[dict[str, Any]] = field(default_factory=list)
    #: The measuring script's answer with `prefers-color-scheme: dark`, at the narrowest width.
    dark: Optional[dict[str, Any]] = None
    #: The page with longer text, at the narrowest width (`interact.stretch`).
    long_text: Optional[dict[str, Any]] = None
    #: A small picture of the page at the narrowest width, for the list of versions (JPEG).
    thumb: bytes = b""
    #: Passes that did not finish in their time, by name.
    unfinished: list[str] = field(default_factory=list)
    #: The area asked for with `clip`, at its own size (PNG): a mark the person drew.
    region: bytes = b""
    #: What `capture` asked for, at the first width: the whole page as a PNG ("page"), its first
    #: screen ("view"), one long PDF page ("pdf"), or its HTML as the browser holds it ("html").
    capture: bytes = b""
    error: str = ""


async def _render(
    browser: Path,
    url: str,
    widths: list[int],
    script: str,
    shots: bool,
    passes: frozenset[str],
    clip: Optional[dict] = None,
    capture: Optional[str] = None,
    network: bool = False,
) -> Render:
    import websockets

    out = Render()
    profile = tempfile.mkdtemp(prefix="reeve-design-")
    proc = spawn(
        [
            str(browser),
            "--headless=new",
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-sync",
            "--disable-background-networking",
            "--disable-component-update",
            "--hide-scrollbars",
            "--mute-audio",
            "about:blank",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    started = Started()
    closed = False
    try:
        started = await wait_started(proc, Path(profile), _LAUNCH_TIMEOUT_S)
        if started.error:
            out.error = started.error
            return out
        async with websockets.connect(
            started.endpoint, max_size=128 * 1024 * 1024, open_timeout=_LAUNCH_TIMEOUT_S
        ) as ws:
            cdp = _Cdp(ws)
            pump = asyncio.create_task(cdp.pump())
            try:
                await _measure_all(cdp, out, url, widths, script, shots, passes, clip, capture, network)
            finally:
                try:
                    await asyncio.wait_for(cdp.send("Browser.close"), 3)
                    closed = True
                except Exception:
                    pass
                pump.cancel()
    finally:
        if started.handed_off and started.endpoint and not closed:
            await close_at(started.endpoint)
        _stop(proc)
        remove_profile(Path(profile), started.handed_off)
    return out


async def _measure_all(
    cdp: _Cdp, out: Render, url: str, widths: list[int], script: str, shots: bool, passes: frozenset[str],
    clip: Optional[dict] = None, capture: Optional[str] = None, network: bool = False,
) -> None:
    target = (await cdp.send("Target.createTarget", {"url": "about:blank"}))["targetId"]
    session = (await cdp.send("Target.attachToTarget", {"targetId": target, "flatten": True}))["sessionId"]
    loaded = asyncio.Event()
    # Errors and blocked requests are the page's, not one width's: they are counted on the first
    # load only, or every width would repeat them.
    first = {"load": True}

    def listen(message: dict) -> None:
        if message.get("sessionId") != session:
            return
        method = message.get("method")
        params = message.get("params") or {}
        if method == "Page.loadEventFired":
            loaded.set()
        elif not first["load"]:
            return
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            text = (details.get("exception") or {}).get("description") or details.get("text") or "an uncaught error"
            out.console_errors.append(str(text).splitlines()[0][:200])
        elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
            parts = [str(arg.get("value", arg.get("description", ""))) for arg in params.get("args") or []]
            out.console_errors.append(" ".join(parts).strip()[:200] or "console.error")
        elif method == "Network.loadingFailed" and params.get("blockedReason"):
            out.blocked.append(str(params.get("requestId")))
        elif method == "Network.loadingFailed" and not params.get("canceled"):
            out.missing.append(str(params.get("requestId")))

    cdp.listeners.append(listen)
    blocked_urls: dict[str, str] = {}

    def record_request(message: dict) -> None:
        if message.get("sessionId") == session and message.get("method") == "Network.requestWillBeSent":
            params = message.get("params") or {}
            blocked_urls[str(params.get("requestId"))] = str((params.get("request") or {}).get("url") or "")

    cdp.listeners.append(record_request)
    for method in ("Page.enable", "Runtime.enable", "Network.enable"):
        await cdp.send(method, session=session)
    if not network:
        await cdp.send(
            "Network.setBlockedURLs", {"urls": ["http://*", "https://*", "ws://*", "wss://*"]}, session=session
        )
    # A headless browser follows the machine's theme; the page is measured in the light scheme unless
    # a pass asks for dark (measured 2026-10-19 on a Windows set to dark: every page measured dark).
    await cdp.send("Emulation.setEmulatedMedia", _media("light"), session=session)
    async def send(method: str, params: Optional[dict] = None) -> dict:
        return await cdp.send(method, params, session=session)

    async def load(width: int) -> None:
        await send(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": VIEW_HEIGHT, "deviceScaleFactor": 1, "mobile": width < 600},
        )
        loaded.clear()
        await send("Page.navigate", {"url": url})
        try:
            await asyncio.wait_for(loaded.wait(), _LOAD_TIMEOUT_S)
        except asyncio.TimeoutError:
            pass
        # Fonts settle, then two frames so a layout that a script finishes on load is the one measured.
        await send(
            "Runtime.evaluate",
            {
                "expression": "Promise.race([document.fonts ? document.fonts.ready : null, new Promise(r => setTimeout(r, 800))])"
                ".then(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(true)))))",
                "awaitPromise": True,
                "returnByValue": True,
            },
        )

    async def measure() -> dict:
        result = await send("Runtime.evaluate", {"expression": script, "returnByValue": True, "awaitPromise": True})
        value = (result.get("result") or {}).get("value")
        return value if isinstance(value, dict) else {}

    for index, width in enumerate(widths):
        first["load"] = index == 0
        await load(width)
        render = WidthRender(width=width, measured=await measure())
        if shots:
            render.png = await _shoot(cdp, session, width)
        out.widths.append(render)
    if clip is not None:
        # Only the area: no thumbnail and no passes.
        out.region = await _region(cdp, session, clip)
        return
    if capture is not None:
        out.capture = await _capture(send, capture, widths[0], {"media": "screen", **_media("light")})
        return
    first["load"] = False
    narrow = min(widths)
    try:
        await load(narrow)
        out.thumb = await _thumb(cdp, session, narrow)
    except Exception:
        logger.debug("design: no thumbnail", exc_info=True)

    async def narrow_load() -> None:
        await load(narrow)

    async def run(name: str, work) -> None:
        """One pass, in its own time: a pass that runs long is named, and what came before stays."""
        try:
            await asyncio.wait_for(work(), _PASS_TIMEOUT_S)
        except asyncio.TimeoutError:
            out.unfinished.append(name)
        except Exception:
            logger.debug("design: the %s pass failed", name, exc_info=True)
            out.unfinished.append(name)

    if "interact" in passes:

        async def clicks() -> None:
            out.interactions = await try_controls(send, narrow_load, narrow, VIEW_HEIGHT)

        await run("interactions", clicks)
    if "dark" in passes:

        async def dark() -> None:
            await send("Emulation.setEmulatedMedia", _media("dark"))
            try:
                await load(narrow)
                out.dark = await measure()
            finally:
                await send("Emulation.setEmulatedMedia", _media("light"))

        await run("dark", dark)
    if "long_text" in passes:

        async def longer() -> None:
            out.long_text = await stretch(send, narrow_load)

        await run("long text", longer)
    out.blocked = sorted({blocked_urls.get(ident, "") for ident in out.blocked} - {""})
    # A local file the page links that is not there (a stylesheet, a font, a picture).
    out.missing = sorted({url for url in (blocked_urls.get(ident, "") for ident in out.missing) if url.startswith("file:")})


def render_page(
    path: Path,
    widths: list[int],
    script: str,
    *,
    shots: bool = True,
    browser: Optional[Path] = None,
    passes: frozenset[str] = frozenset(),
    timeout_s: float = 60.0,
    clip: Optional[dict] = None,
    capture: Optional[str] = None,
    network: bool = False,
) -> Render:
    """Load `path` at each of `widths`, run `script` (an expression that evaluates to a JSON value)
    after each load, and shoot the page. `passes` adds what `interact.py` does at the narrowest width:
    "interact", "dark", "long_text". With `clip` (`x`, `y`, `w`, `h` in page px), the area is shot
    after the first width instead, and nothing more is done; with `capture`, the same for what it
    names (`Render.capture`). The network stays blocked unless `network` (the person let the page
    out). Never raises: a browser that is missing, will not start or hangs comes back as
    `Render.error`, because a check that costs the call is worse than none."""
    return _run(path.resolve().as_uri(), widths, script, shots, browser, passes, timeout_s, clip, capture, network)


def render_url(
    url: str,
    width: int,
    script: str = "0",
    *,
    capture: Optional[str] = None,
    browser: Optional[Path] = None,
    timeout_s: float = 45.0,
) -> Render:
    """Load a web address (http or https only) at `width` with the network on, in a fresh profile:
    the Browser tab's page as Reeve's own browser sees it, signed out of everything. Never raises."""
    if not url.lower().startswith(("http://", "https://")):
        return Render(error="only http and https addresses are opened")
    return _run(url, [width], script, False, browser, frozenset(), timeout_s, None, capture, True)


def render_app(
    url: str,
    widths: list[int],
    script: str,
    *,
    shots: bool = True,
    browser: Optional[Path] = None,
    timeout_s: float = 60.0,
    capture: Optional[str] = None,
) -> Render:
    """Load the person's running app (an http address on this machine; the caller checks which) at
    each of `widths` with the network on, run `script` after each load and shoot it: the app side of
    `compare_design`, and with `capture` the review's picture of it. No passes. Never raises."""
    if not url.lower().startswith(("http://", "https://")):
        return Render(error="only http and https addresses are opened")
    return _run(url, widths, script, shots, browser, frozenset(), timeout_s, None, capture, True)


def _run(
    url: str,
    widths: list[int],
    script: str,
    shots: bool,
    browser: Optional[Path],
    passes: frozenset[str],
    timeout_s: float,
    clip: Optional[dict],
    capture: Optional[str],
    network: bool,
) -> Render:
    chosen = browser or find_browser()
    if chosen is None:
        return Render(error="no Chromium browser (Edge, Chrome or Chromium) was found on this machine")

    async def bounded() -> Render:
        return await asyncio.wait_for(
            _render(chosen, url, widths, script, shots, passes, clip, capture, network), timeout_s
        )

    try:
        return asyncio.run(bounded())
    except asyncio.TimeoutError:
        return Render(error=f"the page did not finish loading within {int(timeout_s)} s")
    except Exception as exc:  # the check is an aside on a write that already succeeded
        logger.debug("design: render failed", exc_info=True)
        return Render(error=f"the browser failed: {type(exc).__name__}: {exc}"[:300])
