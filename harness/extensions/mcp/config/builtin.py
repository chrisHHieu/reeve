"""The MCP servers Reeve ships with, and the browser choice that shapes two of them."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

from harness.safety.secrets import SecretStore

def _cua_launch() -> Optional[dict[str, Any]]:
    """How to spawn the desktop-control sidecar, or None where it cannot run.

    Runs on every OS: screenshots/pointer/keyboard come from `mss` + `pyautogui` (the
    `computer-use` extra is cross-platform), while the Windows-only UI Automation tools
    self-report as unavailable instead of killing the server. A packaged app spawns the
    frozen `reeve-cua[.exe]` staged next to the sidecar — the frozen main exe ignores
    script arguments (a PyInstaller bootloader runs its own entry, whatever argv says),
    so `sys.executable cua_server.py` is only meaningful in a dev checkout. Missing exe
    in a frozen app → the server is simply absent rather than broken.
    """
    if getattr(sys, "frozen", False):
        exe = "reeve-cua.exe" if sys.platform == "win32" else "reeve-cua"
        cua_exe = Path(sys.executable).resolve().parent / exe
        if not cua_exe.exists():
            return None
        return {"command": str(cua_exe), "args": []}
    # As a MODULE, like the frozen entry (packaging/cua_entry.py): run by script path,
    # the relative import once added here (`from .spawn import spawn`) raised at startup
    # and the dev computer-use server never came up (found 2026-09-25).
    return {"command": sys.executable, "args": ["-m", "harness.integrations.cua_server"]}


def _is_cua_launch(raw: dict[str, Any]) -> bool:
    """Whether a config entry starts Reeve's own desktop-control server, by whichever name it had:
    `-m <package>.cua_server`, a `cua_server.py` script, or the frozen `reeve-cua` exe."""
    args = [str(a) for a in (raw.get("args") or [])]
    if any(a.replace("\\", "/").rsplit("/", 1)[-1] == "cua_server.py" for a in args):
        return True
    if any(a.endswith(".cua_server") or a == "cua_server" for a in args):
        return True
    command = str(raw.get("command") or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return command in ("reeve-cua", "reeve-cua.exe")


def current_launch(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    """A config entry for a built-in, with Reeve's own launch line brought up to date.

    The first change to a built-in writes its whole definition into `mcp.json` (`patch_mcp`), and a
    file entry replaces the built-in wholesale, so the launch line of that day stays: a person who
    turned computer use's approvals off once in a dev checkout kept that checkout's
    `.venv\\python.exe -m <package>.cua_server` after the package was renamed and the app installed,
    and computer use failed to start in every session ("No module named …", 2026-10-20). An entry whose
    launch line is Reeve's own desktop-control server gets today's; the rest of it (enabled,
    approvals) is the person's and stays. A launch line of their own is left alone.

    The browser's entry names the Node and `cli.js` the installer carried, in the folder the app was
    installed to that day: after the move from the Tauri install (`%LOCALAPPDATA%\\Reeve\\sidecar`) to
    Electron's, the browser failed to start in every session ("Cannot find module
    …\\mcp\\node_modules\\@playwright\\mcp\\cli.js", 2026-10-21). An entry that runs the carried
    `cli.js` gets today's Node and `cli.js` (npx where none is carried); its flags (headless, an
    attached browser) are the person's and stay."""
    if name == "browser":
        return _current_browser_launch(raw)
    if name != "computer-use" or not _is_cua_launch(raw):
        return raw
    launch = _cua_launch()
    if launch is None:
        return raw
    return {**raw, "command": launch["command"], "args": list(launch["args"])}


#: The carried Playwright server's path below the sidecar folder (`_bundled_node`).
_CARRIED_CLI = "mcp/node_modules/@playwright/mcp/cli.js"


def _current_browser_launch(raw: dict[str, Any]) -> dict[str, Any]:
    args = [str(a) for a in (raw.get("args") or [])]
    at = next((i for i, a in enumerate(args) if a.replace("\\", "/").lower().endswith(_CARRIED_CLI)), None)
    if at is None:
        return raw
    bundled = _bundled_node()
    head = {"command": bundled[0], "args": [bundled[1]]} if bundled else _npx_launch("@playwright/mcp@latest")
    return {**raw, "command": head["command"], "args": [*head["args"], *args[at + 1 :]]}


def _bundled_node() -> Optional[tuple[str, str]]:
    """The portable Node + preinstalled @playwright/mcp that installers carry next to
    the sidecar binary (see packaging/build_windows.ps1 / build_dmg.sh). Dev checkouts
    have neither and fall back to npx. Returns (node_path, cli_js_path) or None."""
    root = Path(sys.executable).resolve().parent
    node = root / "node" / ("node.exe" if sys.platform == "win32" else "node")
    if sys.platform == "darwin" and not node.exists():
        node = root / "node" / "bin" / "node"
    cli = root / "mcp" / "node_modules" / "@playwright" / "mcp" / "cli.js"
    if node.exists() and cli.exists():
        return str(node), str(cli)
    return None


def builtin_mcp_servers(secrets: Optional[SecretStore] = None) -> dict[str, dict[str, Any]]:
    """The servers Reeve ships with, in `mcpServers` shape.

    Three of them, all approval-gated at arm time (`requires_approval` defaults true):
      - "browser"  → Microsoft's Playwright MCP, the a11y-snapshot browser. Launch an
        isolated, in-memory browser by default, or attach to an already-running one
        through a CDP/ws endpoint — two browser modes (`browser_preference`). It
        runs on the portable Node the installers bundle next to the sidecar; a dev
        checkout falls back to npx (Node 20+ on PATH).
      - "computer-use" → Reeve's own desktop-control sidecar (cua_server.py) —
        cross-platform since 2026-09-19 (mss + pyautogui; the UI Automation tools are
        Windows-only and self-report), and in a packaged app it runs from the frozen
        `reeve-cua[.exe]`.
      - "chrome-devtools" → Google's Chrome DevTools MCP, the developer-grade inspector
        (performance traces, network request bodies, console, throttling, device emulation)
        that Playwright's own tools do not offer. A SECOND browser beside "browser", so it is
        **off by default**: it runs through `npx` (Node 20+ on PATH; nothing bundles it), and
        a person without Node would otherwise be told it failed to start in every session.
        The Browser settings (headless, attach to a running browser) apply to it too, until a
        file entry of its own holds its launch line (the first switch-on writes one, as for any
        built-in). The
        usage-statistics and CrUX lookups Google's server makes by default are switched off:
        a performance trace would otherwise send the page's URL to Google's CrUX API.

    A user `mcp.json` entry of the same name replaces the built-in wholesale — that is
    also the off switch (`{"enabled": false}`), because a file entry always wins.

    Neither launch passes `--output-dir`: the arm path pins these servers' cwd to the
    SESSION's workspace (`manager._mcp_plan`), so a relative save lands in the folder
    the user opened the session on — readable back by the file tools like every path.
    """
    preference = browser_preference(secrets)
    browser = _browser_launch(preference, _bundled_node())
    # The browser is PER SESSION (one browser per session): two sessions browsing at
    # once must not share tabs, cookies or a viewport. The desktop sidecar below stays
    # global — there is exactly one desktop.
    browser["per_session"] = True
    servers: dict[str, dict[str, Any]] = {"browser": browser}
    cua = _cua_launch()
    if cua is not None:
        servers["computer-use"] = cua
    servers["chrome-devtools"] = _devtools_launch(preference)
    return servers


#: Where the browser choice lives: a SecretStore profile (what a Settings selector
#: would write) with environment variables as the CLI/self-hosted spelling of the
#: same choice. ONE backend at a time, just as a single provider registration would be — two mounted browsers would double the tool names and fight over
#: the same pages, and only one of them can own an attached browser.
#: There were three backends until 2026-09-30: Playwright, Chrome DevTools MCP and
#: Stagehand (Browserbase's cloud browser). Both others were removed at the owner's
#: request. Stagehand was a paid cloud dependency. The DevTools backend was never
#: selected in the real session store, while the model called Playwright's own
#: network/console/evaluate tools 143 times across 19 sessions. A profile still naming
#: either reads as Playwright. DevTools-level inspection (performance traces, request
#: bodies) is a custom MCP server away; the browser capture code still reads the
#: DevTools snapshot dialect, so a DevTools server mounted as `browser` keeps working.
#: Chrome DevTools MCP came back on 2026-10-01 as its own built-in SERVER, `chrome-devtools`
#: (`_devtools_launch`), off by default: not as a backend of `browser`, so the one-browser rule
#: above still holds for the Browser settings and the two never share a name or a page.
_BROWSER_PROFILE = "mcp:browser"
_BROWSER_BACKENDS = ("playwright",)
#: Keys only the removed Stagehand backend read. The next save drops them from the
#: profile, so no credential for a backend that no longer exists stays on disk.
_RETIRED_BROWSER_KEYS = (
    "browserbase_api_key",
    "browserbase_project_id",
    "gemini_api_key",
    "model_name",
    "model_api_key",
)


def browser_preference(secrets: Optional[SecretStore] = None) -> dict[str, Any]:
    """`{backend, mode, endpoint, headless}` — profile first, environment second.

    `mode="isolated"` (the default) launches a private, in-memory browser: no profile
    leaks between sessions or restarts. `mode="attach"` drives an ALREADY RUNNING
    browser through `endpoint` (a CDP URL; for Chrome DevTools also a `ws://` endpoint),
    which is how a signed-in browser with its logins and tabs is reused. Attaching
    nowhere is not a mode: an attach without an endpoint falls back to isolated.
    """

    profile = (secrets.get(_BROWSER_PROFILE) or {}) if secrets is not None else {}
    backend = (
        str(profile.get("backend") or os.environ.get("REEVE_BROWSER_BACKEND") or "playwright")
        .strip()
        .lower()
    )
    if backend not in _BROWSER_BACKENDS:
        backend = "playwright"
    mode = (
        str(profile.get("mode") or os.environ.get("REEVE_BROWSER_MODE") or "isolated")
        .strip()
        .lower()
    )
    if mode not in ("isolated", "attach"):
        mode = "isolated"
    raw_headless = profile.get("headless")
    if raw_headless is None:
        # Unset ⇒ headless (the shipped default). `0/false/no/…` opts into a visible
        # browser; the rail's Browser section is the usual window for a headless run.
        env_headless = os.environ.get("REEVE_BROWSER_HEADLESS", "1").strip().lower()
        raw_headless = env_headless not in ("", "0", "false", "no")
    endpoint = str(
        profile.get("endpoint") or os.environ.get("REEVE_BROWSER_ENDPOINT") or ""
    ).strip()
    if mode == "attach" and not endpoint:
        mode = "isolated"
    return {
        "backend": backend,
        "mode": mode,
        "endpoint": endpoint,
        "headless": bool(raw_headless),
    }


def _npx_launch(package: str) -> dict[str, Any]:
    """`npx -y <package>` for the platform (cmd /c on Windows, like the playwright fallback)."""
    if sys.platform == "win32":
        return {"command": "cmd", "args": ["/c", "npx", "-y", package]}
    return {"command": "npx", "args": ["-y", package]}


def _browser_launch(
    preference: dict[str, Any], bundled: Optional[tuple[str, str]]
) -> dict[str, Any]:
    """Playwright MCP's launch/attach command, in `mcpServers` shape."""
    if bundled is not None:
        node, cli = bundled
        launch = {"command": node, "args": [cli, "--browser", "chromium"]}
    else:
        launch = _npx_launch("@playwright/mcp@latest")
        launch["args"] += ["--browser", "chromium"]
    if preference["mode"] == "attach":
        launch["args"] += ["--cdp-endpoint", preference["endpoint"]]
    else:
        launch["args"].append("--isolated")
        if preference["headless"]:
            launch["args"].append("--headless")
    # Upstream `PLAYWRIGHT_MCP_*` environment options can otherwise replace the
    # configured browser mode or import an unrelated profile .
    launch["env"] = {
        key: "" for key in os.environ if key.upper().startswith("PLAYWRIGHT_MCP_")
    }
    return launch


def _devtools_launch(preference: dict[str, Any]) -> dict[str, Any]:
    """Chrome DevTools MCP's launch/attach command, as its own built-in (off until switched on).

    Flags measured against `chrome-devtools-mcp --help` (2026-10-01). Isolated by default, like
    `browser`: a temporary profile that is deleted with the browser, so sessions share no cookies
    or tabs. Attach reuses the Browser settings' endpoint, `--ws-endpoint` for a `ws://` one."""
    launch = _npx_launch("chrome-devtools-mcp@latest")
    launch["args"] += ["--no-usage-statistics", "--no-performance-crux"]
    if preference["mode"] == "attach":
        endpoint = preference["endpoint"]
        flag = "--ws-endpoint" if endpoint.startswith(("ws://", "wss://")) else "--browser-url"
        launch["args"] += [flag, endpoint]
    else:
        launch["args"] += ["--isolated", f"--headless={'true' if preference['headless'] else 'false'}"]
    # Two sessions browsing at once must not share a browser (the same rule as `browser`).
    launch["per_session"] = True
    launch["enabled"] = False
    return launch
