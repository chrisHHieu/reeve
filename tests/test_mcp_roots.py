"""A per-session built-in is told the session's folder over MCP `roots/list`.

Playwright MCP saves a relative file name (`browser_take_screenshot` with `filename`) against the
client's first root and falls back to its own working directory. A session with no project folder
works in a scratch folder that is made when its first turn starts, after its browser was started, so
the browser ran in the app's install folder and saved there: the agent asked for
`design/_shot-1440.png`, then could not read it back (2026-10-20).
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from pathlib import Path

import pytest

from harness.extensions.mcp.client import MCPManager
from harness.extensions.mcp.config import MCPServerDef
from harness.extensions.mcp.config import builtin_mcp_servers as real_builtin_mcp_servers
from harness.extensions.mcp.tool_cache import endpoint_digest
from harness.interface.server.manager import SessionManager

# A server whose one tool answers with the roots its client gave it, or "none".
SERVER = textwrap.dedent(
    """
    from mcp.server.fastmcp import Context, FastMCP

    app = FastMCP("roots-probe")

    @app.tool()
    async def roots(ctx: Context) -> str:
        try:
            listed = await ctx.session.list_roots()
        except Exception:
            return "none"
        return " ".join(str(root.uri) for root in listed.roots) or "none"

    app.run()
    """
)


def _server(tmp_path: Path, roots: list[str]) -> MCPServerDef:
    script = tmp_path / "roots_server.py"
    script.write_text(SERVER, encoding="utf-8")
    return MCPServerDef(name="probe", transport="stdio", command=sys.executable, args=[str(script)], roots=roots)


async def _ask_roots(manager: MCPManager, server: MCPServerDef) -> str:
    await manager.ensure(server, scope="s1")
    return str(await manager.call("probe", "roots", {}, scope="s1", timeout=20)).strip()


def test_the_server_hears_the_sessions_folder_even_before_it_is_made(tmp_path):
    folder = tmp_path / "scratch" / "abc123"  # not made: a scratch folder exists from the first turn
    manager = MCPManager()

    async def run():
        try:
            return await _ask_roots(manager, _server(tmp_path, [str(folder)]))
        finally:
            await manager.aclose()

    assert asyncio.run(run()) == folder.as_uri()
    assert not folder.exists()


def test_a_server_given_no_folder_is_offered_no_roots(tmp_path):
    manager = MCPManager()

    async def run():
        try:
            return await _ask_roots(manager, _server(tmp_path, []))
        finally:
            await manager.aclose()

    assert asyncio.run(run()) == "none"


def test_a_new_folder_starts_a_new_server_and_the_same_folder_keeps_it(tmp_path):
    manager = MCPManager()
    first, second = tmp_path / "one", tmp_path / "two"

    async def run():
        try:
            a = await manager.ensure(_server(tmp_path, [str(first)]), scope="s1")
            again = await manager.ensure(_server(tmp_path, [str(first)]), scope="s1")
            moved = await manager.ensure(_server(tmp_path, [str(second)]), scope="s1")
            answer = await manager.call("probe", "roots", {}, scope="s1", timeout=20)
            return a, again, moved, str(answer).strip()
        finally:
            await manager.aclose()

    a, again, moved, answer = asyncio.run(run())
    assert again is a
    assert moved is not a
    assert answer == second.as_uri()


def test_the_kept_tool_list_does_not_depend_on_the_folder(tmp_path):
    # The kept list is named by the endpoint; a name per folder would make every new session wait
    # for its browser's first start instead of reading the list it answered last time.
    one = _server(tmp_path, [str(tmp_path / "one")])
    two = _server(tmp_path, [str(tmp_path / "two")])
    assert endpoint_digest(one) == endpoint_digest(two)


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    # conftest clears built-ins for arming tests; this file is about the built-in browser's plan.
    monkeypatch.setattr("harness.extensions.mcp.config.builtin_mcp_servers", real_builtin_mcp_servers)
    return SessionManager(data_dir=tmp_path / "data")


def test_a_session_without_a_project_folder_gives_its_browser_its_scratch_folder(manager):
    session_id = "a1b2c3d4-001"
    plan = {server.name: server for server in manager._mcp_plan(session_id, None, "reeve")}
    browser = plan["browser"]
    scratch = Path(manager._scratch_path(session_id))
    assert browser.roots == [str(scratch)]
    assert browser.cwd is None  # nothing to start in yet: the folder is made by the first turn
    assert not scratch.exists()


def test_a_project_session_gives_its_browser_the_project_folder(manager, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    plan = {server.name: server for server in manager._mcp_plan("a1b2c3d4-002", str(project), "reeve")}
    assert plan["browser"].roots == [str(project)]
    assert plan["browser"].cwd == str(project)
    # The desktop server is one for the whole app, so no session's folder is said to it.
    assert plan["computer-use"].roots == []
