"""MCP servers: where their config comes from and who may add one, how their tools become the
agent's, how a server that fails says so (once), and how a call survives a dropped connection.

No real server is needed except where a test says so: a failing stdio server is the running Python
told to write to stderr and exit, and a 401 comes from a local HTTP server.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import sys
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from harness.extensions.mcp import build_callables, load_mcp_servers, tool_name
from harness.extensions.mcp.config import MCPServerDef
from harness.extensions.mcp.config import builtin_mcp_servers as real_builtin_mcp_servers
from harness.interface.server.app import create_app
from harness.interface.server.manager import SessionManager
from harness.safety.secrets import SecretStore


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _global_servers(tmp_path, monkeypatch, servers: dict):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    _write_json(tmp_path / "state" / "mcp.json", {"mcpServers": servers})


def _failing(message: str, exit_code: int = 7) -> dict:
    """A stdio server that writes `message` to stderr and exits: the running Python, so it starts
    on every platform (`/bin/sh -c` failed to spawn on Windows before it printed anything)."""
    return {"command": sys.executable, "args": ["-c", f"import sys; sys.stderr.write({message!r}); sys.exit({exit_code})"]}


def _listed_tool(name, schema=None):
    return SimpleNamespace(name=name, description="desc", inputSchema=schema or {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})


def _by_name(servers) -> dict:
    return {server.name: server for server in servers}


# -- where the config comes from -----------------------------------------------------------------


def test_builtin_servers_merge_and_user_override(tmp_path, monkeypatch):
    """Built-ins ship in the merge; a user mcp.json entry of the same name replaces one
    wholesale — including `{"enabled": false}`, the off switch. (The conftest autouse
    fixture clears built-ins; this test patches its own back in to test the merge.)"""
    monkeypatch.setattr("harness.extensions.mcp.config.builtin_mcp_servers", lambda *a, **k: {"browser": {"command": "pw-mcp"}, "computer-use": {"command": "cua"}})
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    servers = _by_name(load_mcp_servers(secrets=SecretStore()))
    assert servers["browser"].command == "pw-mcp"
    assert servers["browser"].enabled is True and servers["browser"].requires_approval is True
    assert servers["computer-use"].enabled is True

    _write_json(tmp_path / "state" / "mcp.json", {"mcpServers": {"computer-use": {"enabled": False}, "browser": {"command": "my-browser"}}})
    servers = _by_name(load_mcp_servers(secrets=SecretStore()))
    assert servers["computer-use"].enabled is False  # the off switch wins
    assert servers["browser"].command == "my-browser"  # replacement wins wholesale


def test_a_trusted_workspace_adds_servers_but_cannot_redefine_a_global_one(tmp_path, monkeypatch):
    _global_servers(tmp_path, monkeypatch, {
        "fs": {"command": "echo", "args": ["global"], "enabled": True},
        "docs": {"type": "http", "url": "https://x/mcp", "enabled": False},
    })
    workspace = tmp_path / "ws"
    _write_json(workspace / ".reeve" / "mcp.json", {"mcpServers": {
        "fs": {"command": "echo", "args": ["workspace-loses"]},
        "ws_only": {"command": "echo", "args": ["ws"], "enabled": True},
    }})
    servers = _by_name(load_mcp_servers(workspace, secrets=SecretStore(), workspace_trusted=True))
    assert servers["fs"].args == ["global"] and servers["fs"].transport == "stdio"
    assert servers["ws_only"].args == ["ws"]
    docs = servers["docs"]
    assert (docs.transport, docs.enabled, docs.requires_approval) == ("http", False, True)


def test_a_cloned_repositorys_servers_load_only_once_it_is_trusted(tmp_path, monkeypatch):
    """#213: a `.reeve/mcp.json` in a repository someone cloned is a command it would run."""
    _global_servers(tmp_path, monkeypatch, {"fs": {"command": "echo", "args": ["global"], "enabled": True}})
    workspace = tmp_path / "ws"
    _write_json(workspace / ".reeve" / "mcp.json", {"mcpServers": {
        "fs": {"command": "echo", "args": ["pwned"]},  # would shadow the global one
        "evil": {"command": "/bin/sh", "args": ["-c", "echo PWNED"], "enabled": True},
    }})
    for trust in ({}, {"workspace_trusted": False}):
        servers = _by_name(load_mcp_servers(workspace, secrets=SecretStore(), **trust))
        assert set(servers) == {"fs"} and servers["fs"].args == ["global"]
    trusted = _by_name(load_mcp_servers(workspace, secrets=SecretStore(), workspace_trusted=True))
    assert trusted["fs"].args == ["global"] and "evil" in trusted


async def test_an_untrusted_workspaces_server_is_never_started(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    workspace = tmp_path / "cloned-repo"
    _write_json(workspace / ".reeve" / "mcp.json", {"mcpServers": {"totally-normal-tool": {"command": "/bin/sh", "args": ["-c", "echo PWNED"], "enabled": True}}})
    manager = SessionManager(data_dir=tmp_path / "data")
    started: list[str] = []

    async def ensure(server, *, scope: str = "", interactive: bool = False):
        started.append(server.name)
        if server.name == "totally-normal-tool":
            raise AssertionError("an untrusted workspace's server was started")

    monkeypatch.setattr(manager.mcp, "ensure", ensure)
    assert await manager.prepare_mcp_tools("s1", workspace=str(workspace)) == []
    # Built-ins may start; the workspace's server may not.
    assert "totally-normal-tool" not in started and manager.workspace_trust.is_trusted(workspace) is False
    manager.workspace_trust.set_trusted(workspace, True)
    assert await manager.prepare_mcp_tools("s2", workspace=str(workspace)) == []  # it raised, so no tools
    assert "totally-normal-tool" in started


def test_a_header_can_name_an_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_TOKEN", "sekret")
    _global_servers(tmp_path, monkeypatch, {"docs": {"type": "http", "url": "https://x/mcp", "headers": {"Authorization": "Bearer ${DOCS_TOKEN}"}}})
    assert load_mcp_servers(None, secrets=SecretStore())[0].headers["Authorization"] == "Bearer sekret"


# -- the built-in browser and computer-use ---------------------------------------------------------


def _clear_browser_env(monkeypatch) -> None:
    for key in ("REEVE_BROWSER_BACKEND", "REEVE_BROWSER_MODE", "REEVE_BROWSER_ENDPOINT", "REEVE_BROWSER_HEADLESS", "BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"):
        monkeypatch.delenv(key, raising=False)


def test_the_browser_is_playwright(monkeypatch):
    """One browser: Playwright with an isolated, headless browser by default. The Chrome
    DevTools backend was removed on 2026-09-30 (never selected in the session store); a
    choice still naming it launches Playwright."""
    from harness.extensions.mcp.config import browser_preference

    _clear_browser_env(monkeypatch)
    default = real_builtin_mcp_servers()["browser"]
    assert any("playwright" in arg for arg in default["args"])
    assert "--isolated" in default["args"] and "--headless" in default["args"]
    assert browser_preference()["backend"] == "playwright"

    monkeypatch.setenv("REEVE_BROWSER_BACKEND", "chrome-devtools")
    assert browser_preference()["backend"] == "playwright"
    launch = real_builtin_mcp_servers()["browser"]
    assert any("playwright" in arg for arg in launch["args"])
    assert not any("chrome-devtools-mcp" in str(arg) for arg in launch["args"])


def test_attach_mode_drives_the_running_browser(monkeypatch):
    """`mode=attach` reuses an existing (signed-in) browser through its CDP endpoint
    instead of launching an isolated one; an http or a ws:// endpoint both ride
    `--cdp-endpoint`."""
    _clear_browser_env(monkeypatch)
    monkeypatch.setenv("REEVE_BROWSER_MODE", "attach")
    monkeypatch.setenv("REEVE_BROWSER_ENDPOINT", "http://127.0.0.1:9222")
    args = real_builtin_mcp_servers()["browser"]["args"]
    assert "--cdp-endpoint" in args and "--isolated" not in args
    monkeypatch.setenv("REEVE_BROWSER_ENDPOINT", "ws://127.0.0.1:9222/devtools/browser/x")
    args = real_builtin_mcp_servers()["browser"]["args"]
    assert args[args.index("--cdp-endpoint") + 1].startswith("ws://")


def test_attach_without_an_endpoint_falls_back_to_isolated(monkeypatch):
    from harness.extensions.mcp.config import browser_preference

    _clear_browser_env(monkeypatch)
    monkeypatch.setenv("REEVE_BROWSER_MODE", "attach")
    assert browser_preference()["mode"] == "isolated"


def test_the_browser_profile_wins_over_the_environment(tmp_path, monkeypatch):
    from harness.extensions.mcp.config import browser_preference

    _clear_browser_env(monkeypatch)
    monkeypatch.setenv("REEVE_BROWSER_HEADLESS", "1")
    secrets = SecretStore(tmp_path / "s.json")
    secrets.put("mcp:browser", {"backend": "playwright", "headless": False})
    preference = browser_preference(secrets)
    assert preference["backend"] == "playwright" and preference["headless"] is False
    assert "--headless" not in real_builtin_mcp_servers(secrets)["browser"]["args"]


def test_playwright_environment_options_cannot_override_the_launch(monkeypatch):
    """Upstream `PLAYWRIGHT_MCP_*` variables can otherwise replace the configured mode
    or import an unrelated profile; the launch scrubs them."""
    _clear_browser_env(monkeypatch)
    monkeypatch.setenv("PLAYWRIGHT_MCP_HEADLESS", "0")
    assert real_builtin_mcp_servers()["browser"]["env"]["PLAYWRIGHT_MCP_HEADLESS"] == ""


def test_the_removed_stagehand_backend_launches_playwright(monkeypatch):
    """Stagehand was removed on 2026-09-30. An environment still naming it, credentials
    and all, gets Playwright, and nothing reaches Browserbase."""
    from harness.extensions.mcp.config import browser_preference

    _clear_browser_env(monkeypatch)
    monkeypatch.setenv("REEVE_BROWSER_BACKEND", "stagehand")
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb-key")
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "bb-proj")
    assert browser_preference()["backend"] == "playwright"
    launch = real_builtin_mcp_servers()["browser"]
    assert not any("browserbase" in str(arg) for arg in launch["args"])
    assert "BROWSERBASE_API_KEY" not in (launch.get("env") or {})


def test_computer_use_builtin_is_cross_platform_and_frozen_aware(tmp_path, monkeypatch):
    """computer-use runs on every OS now (mss + pyautogui; the UIA tools self-report on
    non-Windows): a dev checkout spawns the sidecar directly, and a packaged app spawns
    the frozen `reeve-cua[.exe]` staged next to the sidecar — the frozen main exe
    ignores script arguments, so `sys.executable cua_server.py` is a dev-checkout launch
    only. A frozen app without the exe ships without the server rather than arming a
    dead one. (Module-level import binds the real function before conftest's autouse
    fixture clears built-ins.)"""
    real = real_builtin_mcp_servers
    assert real()["computer-use"]["command"] == sys.executable
    # As a module, like the frozen entry: by script path the relative import in
    # cua_server raised at startup and the dev server never came up (2026-09-25).
    assert real()["computer-use"]["args"] == ["-m", "harness.integrations.cua_server"]

    # Packaged app: sys.executable is the frozen sidecar; the CUA exe sits beside it.
    sidecar = tmp_path / "install" / "sidecar"
    sidecar.mkdir(parents=True)
    (sidecar / "reeve-server.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(sidecar / "reeve-server.exe"))
    assert "computer-use" not in real()  # exe not staged yet → server absent
    exe = "reeve-cua.exe" if sys.platform == "win32" else "reeve-cua"
    (sidecar / exe).write_bytes(b"")
    cua = real()["computer-use"]
    assert cua["command"].endswith(exe) and cua["args"] == []


def test_the_dev_computer_use_launch_actually_starts(tmp_path):
    """Behaviour, not shape: the dev launch spec is RUN, with stdin closed. The server
    reads EOF and exits cleanly; the script-path spec died on `from .spawn import spawn`
    (exit 1, "attempted relative import with no known parent package") while the old
    shape test passed."""
    import subprocess

    from harness.extensions.mcp.config.builtin import _cua_launch

    launch = _cua_launch()
    run = subprocess.run(
        [launch["command"], *launch["args"]],
        cwd=tmp_path,  # the arm path pins MCP servers to the session workspace
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
    )
    assert run.returncode == 0, run.stderr[-400:]
    assert "Traceback" not in run.stderr


# -- a server's tools as the agent's -------------------------------------------------------------


def test_a_tool_is_named_for_its_server_with_nothing_a_provider_refuses():
    assert tool_name("fs", "read_file") == "mcp__fs__read_file"
    assert "." not in tool_name("a.b", "c.d")


def test_a_tool_carries_the_servers_schema_and_its_approval_setting():
    [tool] = build_callables(MCPServerDef(name="fs", transport="stdio", requires_approval=True), [_listed_tool("read_file")], lambda t, a: None, asyncio.new_event_loop())
    assert tool.__name__ == "mcp__fs__read_file"
    metadata = tool.__aisuite_tool_metadata__
    assert metadata.category == "mcp" and metadata.requires_approval is True
    schema = tool.__tool_schema__["function"]
    assert schema["name"] == "mcp__fs__read_file" and schema["parameters"]["required"] == ["path"]


def test_only_the_included_tools_are_offered():
    server = MCPServerDef(name="fs", transport="stdio", include_tools=["read_file"])
    tools = build_callables(server, [_listed_tool("read_file"), _listed_tool("delete_file")], lambda t, a: None, asyncio.new_event_loop())
    assert [tool.__name__ for tool in tools] == ["mcp__fs__read_file"]


async def test_a_tool_called_from_a_worker_thread_runs_on_the_servers_loop():
    called = []

    async def call(tool, arguments):
        called.append((tool, arguments))
        return {"echo": arguments}

    [tool] = build_callables(MCPServerDef(name="fs", transport="stdio"), [_listed_tool("read_file")], call, asyncio.get_running_loop())
    # The engine runs a tool in a thread; it reaches back into this loop.
    assert await asyncio.to_thread(tool, path="a.txt") == {"echo": {"path": "a.txt"}}
    assert called == [("read_file", {"path": "a.txt"})]


def test_mcp_image_content_becomes_an_attachment():
    from harness.extensions.mcp.client import result_payload

    result = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Captured the screen."), SimpleNamespace(type="image", data="QUJD", mimeType="image/png")],
        isError=False, structuredContent=None,
    )
    payload = result_payload(result)
    assert payload["text"] == "Captured the screen."
    assert payload["_attach"][0]["type"] == "image_url"
    assert payload["_attach"][0]["image_url"]["url"] == "data:image/png;base64,QUJD"


def test_mcp_text_only_result_stays_a_string():
    from harness.extensions.mcp.client import result_payload

    assert result_payload(SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], isError=False, structuredContent=None)) == "ok"


# -- the settings page ---------------------------------------------------------------------------


def test_a_server_is_added_shown_with_its_secrets_masked_switched_off_and_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    client = TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))

    def servers():
        return client.get("/v1/mcp").json()["servers"]

    assert servers() == []
    assert client.post("/v1/mcp", json={"name": "fs", "config": {"command": "echo", "args": ["x"], "env": {"SECRET": "shh"}}}).json()["ok"] is True
    [fs] = servers()
    assert (fs["name"], fs["status"], fs["config"]["env"]["SECRET"]) == ("fs", "configured", "***")
    assert client.patch("/v1/mcp/fs", json={"enabled": False}).json()["ok"] is True
    assert servers()[0]["enabled"] is False
    assert client.delete("/v1/mcp/fs").json()["ok"] is True and servers() == []
    assert client.delete("/v1/mcp/fs").json()["ok"] is False


def test_when_a_server_was_last_tested_survives_a_restart_and_goes_with_the_server(tmp_path, monkeypatch):
    """A server added again is not "tested" until it is."""
    _global_servers(tmp_path, monkeypatch, {"fs": {"command": "echo", "enabled": True}})
    manager = SessionManager(data_dir=tmp_path / "data")
    manager._prefs.setdefault("mcp_last_test", {})["fs"] = 1_700_000_000
    manager._save_prefs()
    reopened = SessionManager(data_dir=tmp_path / "data")
    assert _by_name_rows(reopened)["fs"]["last_test_at"] == 1_700_000_000
    reopened.delete_mcp("fs")
    assert SessionManager(data_dir=tmp_path / "data")._prefs.get("mcp_last_test", {}).get("fs") is None


def _by_name_rows(manager) -> dict:
    return {row["name"]: row for row in manager.list_mcp()}


def test_removing_a_server_closes_its_connection_and_forgets_its_sign_in(tmp_path):
    from harness.extensions.mcp import oauth as mcp_oauth
    from harness.extensions.mcp.client import Conn
    from harness.extensions.mcp.config import put_global_server

    manager = SessionManager(data_dir=tmp_path / "data")
    put_global_server("gone-srv", {"url": "https://x.example/mcp", "auth": "oauth"})
    manager.secrets.put(mcp_oauth.PROFILE_PREFIX + "gone-srv", {"tokens": {"access_token": "A"}})
    connection = Conn(SimpleNamespace(), tools=[])
    manager.mcp._conns[("", "gone-srv")] = connection
    assert manager.delete_mcp("gone-srv")["ok"]
    assert connection.shutdown.is_set()
    assert manager.secrets.get(mcp_oauth.PROFILE_PREFIX + "gone-srv") is None


# -- a server that fails -------------------------------------------------------------------------


async def test_a_server_that_dies_before_it_answers_leaves_its_stderr(tmp_path):
    from harness.extensions.mcp.client import MCPManager

    manager = MCPManager()
    launch = _failing("usage: doomed --flag")
    with pytest.raises(Exception):
        await manager.ensure(MCPServerDef(name="doomed", transport="stdio", command=launch["command"], args=launch["args"]))
    assert "usage: doomed --flag" in (manager.last_stderr("doomed") or "")


async def test_a_failing_server_is_marked_failed_and_told_to_the_session_once(tmp_path, monkeypatch):
    """Drill 2026-08-20: a server that crashed at startup used to be skipped without a word."""
    _global_servers(tmp_path, monkeypatch, {"sales-db": {"enabled": True, **_failing("boom: bad args", 2)}})
    manager = SessionManager(data_dir=tmp_path / "data")
    assert await manager.prepare_mcp_tools("s1", workspace=str(tmp_path / "wsp")) == []
    assert "boom: bad args" in (manager._mcp_errors.get("sales-db") or "")
    row = _by_name_rows(manager)["sales-db"]
    assert row["status"] == "error" and "boom: bad args" in (row["last_error"] or "")
    told = manager.pop_mcp_failures("s1")
    assert [name for name, _ in told] == ["sales-db"] and "boom: bad args" in (told[0][1] or "")
    assert manager.pop_mcp_failures("s1") == []


async def test_the_test_button_reports_the_same_stderr_and_a_removed_server_takes_its_error_along(tmp_path, monkeypatch):
    """Add, test, fix, without opening a session."""
    _global_servers(tmp_path, monkeypatch, {"doomed": {"enabled": True, **_failing("usage: doomed --flag")}})
    manager = SessionManager(data_dir=tmp_path / "data")
    result = await manager.connect_mcp("doomed")
    assert result["ok"] is False and "usage: doomed --flag" in result["error"]
    row = _by_name_rows(manager)["doomed"]
    assert row["status"] == "error" and row["auth_hint"] is False
    manager.delete_mcp("doomed")
    assert manager._mcp_errors.get("doomed") is None


async def test_a_401_is_reported_as_needing_a_sign_in(tmp_path, monkeypatch):
    """The page then offers the sign-in, instead of an HTTP error dump."""

    class _Refuses(http.server.BaseHTTPRequestHandler):
        def _refuse(self):
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = do_DELETE = _refuse

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Refuses)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _global_servers(tmp_path, monkeypatch, {"guarded": {"url": f"http://127.0.0.1:{server.server_address[1]}/mcp", "enabled": True}})
        manager = SessionManager(data_dir=tmp_path / "data")
        result = await manager.connect_mcp("guarded")
        assert result["ok"] is False and "sign in" in result["error"]
        row = _by_name_rows(manager)["guarded"]
        assert (row["auth_hint"], row["status"], row["last_test_at"]) == (True, "error", None)
    finally:
        server.shutdown()
        server.server_close()


# -- telling a failure once ----------------------------------------------------------------------


def _one_flaky_server(monkeypatch):
    # A real MCPServerDef: the manager reads fields off it that a hand-built stub would lack.
    server = MCPServerDef(name="flaky", transport="stdio")
    monkeypatch.setattr("harness.interface.server.manager.mcp_plan.load_mcp_servers", lambda *a, **k: [server])


def _fails_with(manager, monkeypatch, errors: list[str]):
    async def ensure(server, **kwargs):
        if errors:
            raise RuntimeError(errors[0])
        return SimpleNamespace(tools=[])

    monkeypatch.setattr(manager.mcp, "ensure", ensure)
    monkeypatch.setattr(manager.mcp, "last_stderr", lambda name, **kwargs: None)


async def _told(manager, session, tmp_path) -> list[str]:
    await manager.prepare_mcp_tools(session, workspace=str(tmp_path))
    return [name for name, _ in manager.pop_mcp_failures(session)]


async def test_a_server_that_stays_broken_is_told_once_and_again_when_its_error_changes(tmp_path, monkeypatch):
    """Owner, 2026-08-21: tell the change, not the state; the settings page shows the standing error."""
    _one_flaky_server(monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")
    errors = ["boom one"]
    _fails_with(manager, monkeypatch, errors)
    assert await _told(manager, "s1", tmp_path) == ["flaky"]
    assert await _told(manager, "s2", tmp_path) == [] and manager._mcp_errors.get("flaky")
    errors[0] = "boom two"
    assert await _told(manager, "s3", tmp_path) == ["flaky"]
    errors.clear()  # it recovers, and the next failure is new
    assert await _told(manager, "s4", tmp_path) == []
    errors.append("boom three")
    assert await _told(manager, "s5", tmp_path) == ["flaky"]


async def test_a_failure_already_told_is_not_told_again_after_a_restart(tmp_path, monkeypatch):
    _one_flaky_server(monkeypatch)
    first = SessionManager(data_dir=tmp_path / "data")
    _fails_with(first, monkeypatch, ["same boom"])
    assert await _told(first, "s1", tmp_path) == ["flaky"]
    reopened = SessionManager(data_dir=tmp_path / "data")
    _fails_with(reopened, monkeypatch, ["same boom"])
    assert await _told(reopened, "s2", tmp_path) == []


async def test_addresses_and_pids_in_an_error_do_not_make_it_a_new_one(tmp_path, monkeypatch):
    """Owner-hit 2026-08-21: a botocore error with a 0x… address was told after every restart."""
    _one_flaky_server(monkeypatch)
    errors = ["credential process <function f at 0x102ab40f0> pid 84121"]
    first = SessionManager(data_dir=tmp_path / "data")
    _fails_with(first, monkeypatch, errors)
    assert await _told(first, "s1", tmp_path) == ["flaky"]
    errors[0] = "credential process <function f at 0x1070340f0> pid 99017"
    reopened = SessionManager(data_dir=tmp_path / "data")
    _fails_with(reopened, monkeypatch, errors)
    assert await _told(reopened, "s2", tmp_path) == []
    errors[0] = "connection refused"
    assert await _told(reopened, "s3", tmp_path) == ["flaky"]


# -- reconnect-before-call, one retry on a confirmed disconnect ------------------------


def _conn(session):
    return SimpleNamespace(session=session, key="k", tools=[], shutdown=asyncio.Event())


class _Session:
    """A fake MCP session whose call_tool fails the first time with a chosen error."""

    def __init__(self, error: Exception | None = None, answer="ok"):
        self.error = error
        self.answer = answer
        self.calls = 0

    async def call_tool(self, tool, arguments):
        self.calls += 1
        if self.error is not None and self.calls == 1:
            raise self.error
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.answer)])


def _connecting(manager, monkeypatch, sessions):
    """`ensure` connects to the next of `sessions` (the last one again when they run out)."""
    count = {"n": 0}

    async def ensure(server, *, scope="", interactive=False):
        count["n"] += 1
        session = sessions.pop(0) if len(sessions) > 1 else sessions[0]
        manager._conns[manager._key(scope, server.name)] = _conn(session)
        return manager._conns[manager._key(scope, server.name)]

    monkeypatch.setattr(manager, "ensure", ensure)
    return count


def _server():
    return MCPServerDef(name="srv", transport="stdio", command=sys.executable, args=[])


def test_a_dead_connection_is_reconnected_before_the_call(tmp_path, monkeypatch):
    """The case the user hit: the server died quietly, the next call said "not connected"
    and the session had to be re-armed by hand."""
    from harness.extensions.mcp.client import MCPManager

    manager = MCPManager()
    session = _Session()
    connects = _connecting(manager, monkeypatch, [session])
    payload = asyncio.run(manager.call("srv", "do", {}, scope="", server=_server()))
    assert connects["n"] == 1, "the call reconnected instead of raising"
    assert session.calls == 1 and "ok" in json.dumps(payload)


def test_a_disconnect_mid_call_is_retried_once_on_a_fresh_connection(monkeypatch):
    from harness.extensions.mcp.client import MCPManager

    manager = MCPManager()
    dead, fresh = _Session(error=RuntimeError("transport is closed")), _Session(answer="second try")
    _connecting(manager, monkeypatch, [dead, fresh])
    monkeypatch.setattr(manager, "_drop", lambda key: asyncio.sleep(0))
    payload = asyncio.run(manager.call("srv", "do", {}, scope="", server=_server()))
    assert dead.calls == 1 and fresh.calls == 1 and "second try" in json.dumps(payload)


def test_an_ordinary_tool_error_is_not_retried(monkeypatch):
    """Reconnecting on every failure would hide a broken tool behind a reconnect loop."""
    from harness.extensions.mcp.client import MCPManager

    manager = MCPManager()
    session = _Session(error=ValueError("the tool rejected your arguments"))
    connects = _connecting(manager, monkeypatch, [session])
    with pytest.raises(ValueError):
        asyncio.run(manager.call("srv", "do", {}, scope="", server=_server()))
    assert connects["n"] == 1, "only the pre-call connect, no retry"
    assert session.calls == 1


def test_without_a_server_def_the_old_error_is_kept():
    """A caller that cannot name the server (an ad-hoc call) must not get a silent
    reconnect attempt it did not ask for."""
    from harness.extensions.mcp.client import MCPManager

    with pytest.raises(RuntimeError, match="not connected"):
        asyncio.run(MCPManager().call("srv", "do", {}))
