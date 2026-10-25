"""GitHub's own MCP server, with the toolsets the person chose and a gate per tool.

Owner, 2026-09-30: use the vendor server's tools instead of rebuilding them, and let the
person switch on the groups a job needs. Two things make that safe to ship: the toolset
header follows the profile at every session build, and a write the server offers asks
first (the entry says `requires_approval: false`, which used to let every write through).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from harness.integrations.connectors import github_toolsets as gt
from harness.integrations.connectors.descriptors import get_descriptor
from harness.integrations.connectors.lifecycle import connector_list
from harness.extensions.mcp.config import MCPServerDef, put_global_server
from harness.extensions.mcp.tools import build_callables
from harness.interface.server.manager import SessionManager


def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)


# -- the catalog ------------------------------------------------------------------


def test_a_new_connection_serves_the_server_defaults_plus_the_sets_the_base_grant_covers():
    ids = gt.enabled_ids({})
    assert {"context", "repos", "issues", "pull_requests", "users"} <= set(ids)
    assert {"actions", "discussions", "labels"} <= set(ids)
    # A set that needs a scope the base grant lacks is off until someone asks for it.
    assert not {"notifications", "gists", "projects", "code_security"} & set(ids)


def test_locked_sets_stay_on_and_unknown_ids_are_dropped():
    ids = gt.enabled_ids({"mcp_toolsets": ["gists", "no_such_set"]})
    assert "gists" in ids and "no_such_set" not in ids
    assert {"repos", "issues", "pull_requests"} <= set(ids)


def test_the_header_is_stable_for_a_given_choice():
    """It is part of the per-session MCP signature: a reordering would look like a change."""
    a = gt.header_for({"mcp_toolsets": ["gists", "actions"]})
    b = gt.header_for({"mcp_toolsets": ["actions", "gists"]})
    assert a == b
    assert a[gt.HEADER].split(",")[0] == "context"


def test_scopes_are_the_base_grant_plus_what_each_chosen_set_needs():
    assert gt.scope_string(gt.default_ids()) == "repo read:org"
    scope = gt.scope_string(["notifications", "gists", "code_security", "dependabot"])
    assert scope.split() == ["repo", "read:org", "notifications", "gist", "security_events"]


def test_a_pasted_token_never_reads_as_missing_scopes():
    """Its scopes are not knowable from here: unknown must not become 'granted nothing'."""
    assert gt.missing_scopes(["notifications"], None) == []
    assert gt.missing_scopes(["notifications"], {"repo"}) == ["read:org", "notifications"]
    assert gt.parse_granted("repo,read:org") == {"repo", "read:org"}


# -- the gate ---------------------------------------------------------------------


class _Tool:
    def __init__(self, name, read_only=None):
        self.name = name
        self.description = f"{name}."
        self.inputSchema = {"type": "object", "properties": {}}
        self.annotations = None if read_only is None else SimpleNamespace(readOnlyHint=read_only)


def _requires(server, *tools):
    loop = asyncio.new_event_loop()
    try:
        fns = build_callables(server, list(tools), lambda *a, **k: None, loop)
    finally:
        loop.close()
    return {f.__aisuite_tool_metadata__.name.split("__")[-1]: f.__aisuite_tool_metadata__.requires_approval for f in fns}


def test_a_hint_gated_server_runs_reads_and_asks_for_everything_else():
    server = MCPServerDef(
        name="github", transport="http", url="https://x", requires_approval=False, approval_by_hints=True
    )
    got = _requires(
        server,
        _Tool("get_file_contents", read_only=True),
        _Tool("merge_pull_request", read_only=False),
        _Tool("mystery_tool"),  # declares nothing: a tool nobody classified is not one to run unattended
    )
    assert got == {"get_file_contents": False, "merge_pull_request": True, "mystery_tool": True}


def test_without_the_flag_a_servers_own_hint_never_clears_a_gate():
    """The standing rule: a third party saying 'read-only' is a claim, not a permission."""
    server = MCPServerDef(name="tracker", transport="http", url="https://x", requires_approval=True)
    assert _requires(server, _Tool("list", read_only=True)) == {"list": True}
    open_server = MCPServerDef(name="tracker", transport="http", url="https://x", requires_approval=False)
    assert _requires(open_server, _Tool("delete_all", read_only=False)) == {"delete_all": False}


# -- the session plan -------------------------------------------------------------


def test_the_plan_applies_the_profiles_toolsets_and_the_approval_rule(tmp_path, monkeypatch):
    """The seeded mcp.json is a cache of a decision the app makes: an install that connected
    before either existed gets both on its next turn."""
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.secrets.put("github:default", {"mode": "mcp", "enabled": True, "mcp_toolsets": ["gists"]})
    manager.secrets.put("mcp-oauth:github", {"tokens": {"access_token": "at"}})
    put_global_server(
        "github",
        {"url": "https://api.githubcopilot.com/mcp/", "auth": "device", "requires_approval": False, "enabled": True},
    )
    monkeypatch.setattr(manager, "effective_connectors", lambda sid, agent=None: {"github"})
    plan = manager._mcp_plan("s1", None, "reeve")
    (server,) = [s for s in plan if s.name == "github"]
    assert "gists" in server.headers[gt.HEADER].split(",")
    assert server.approval_by_hints is True


def test_changing_the_toolsets_changes_the_session_signature(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")

    def sig(headers):
        server = MCPServerDef(name="github", transport="http", url="https://x", headers=headers)
        return manager._mcp_signature([server], "s1")

    assert sig({gt.HEADER: "repos"}) != sig({gt.HEADER: "repos,gists"})


# -- choosing --------------------------------------------------------------------


def test_choosing_a_set_saves_it_and_names_the_scope_the_token_lacks(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.secrets.put("github:default", {"mode": "mcp", "enabled": True})
    manager.secrets.put(
        "mcp-oauth:github", {"tokens": {"access_token": "at", "scope": "repo,read:org"}}
    )
    out = manager.set_connector_toolsets("github", ["notifications"])
    assert out["ok"] and "notifications" in out["enabled"]
    assert out["needs_scopes"] == ["notifications"]
    assert manager.secrets.get("github:default")["mcp_toolsets"] == ["notifications"]


def test_a_pasted_token_is_never_told_to_grant_access(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.secrets.put("github:default", {"mode": "mcp", "enabled": True})
    manager.secrets.put("mcp-oauth:github", {"tokens": {"access_token": "pat"}})  # no scope recorded
    assert manager.set_connector_toolsets("github", ["gists"])["needs_scopes"] == []


def test_an_unknown_set_is_refused_and_nothing_is_saved(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.secrets.put("github:default", {"mode": "mcp", "enabled": True})
    out = manager.set_connector_toolsets("github", ["nope"])
    assert out["ok"] is False
    assert "mcp_toolsets" not in manager.secrets.get("github:default")
    assert manager.set_connector_toolsets("notion", ["x"])["ok"] is False


def test_the_device_grant_asks_for_the_scope_the_chosen_sets_need(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")
    d = get_descriptor("github")
    assert manager._device_scope(d) == "repo read:org"
    manager.secrets.put("github:default", {"mcp_toolsets": ["gists", "notifications"]})
    assert manager._device_scope(d).split() == ["repo", "read:org", "notifications", "gist"]


def test_the_connector_list_reports_what_a_connection_still_needs_only_once_it_is_on_the_server(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")
    row = next(c for c in connector_list(manager.secrets) if c["name"] == "github")
    # Not connected: the groups are listed, and nothing claims a missing permission.
    assert all(t["needs_scopes"] == [] for t in row["toolsets"])
    manager.secrets.put("github:default", {"mode": "mcp", "enabled": True})
    manager.secrets.put("mcp-oauth:github", {"tokens": {"access_token": "at", "scope": "repo,read:org"}})
    row = next(c for c in connector_list(manager.secrets) if c["name"] == "github")
    by_id = {t["id"]: t for t in row["toolsets"]}
    assert by_id["actions"]["enabled"] and not by_id["gists"]["enabled"]
    assert by_id["repos"]["locked"]
    assert by_id["gists"]["needs_scopes"] == ["gist"]


# -- what stays ours ---------------------------------------------------------------


def test_a_connection_on_the_vendor_server_keeps_only_the_tools_that_touch_this_machine(tmp_path, monkeypatch):
    """The server reads and writes GitHub; it cannot put a repository on the person's disk.
    The hand-written issue/PR/search tools are gone for it (owner, 2026-09-30)."""
    from harness.integrations.connectors.catalog import active_tool_defs
    from harness.safety.secrets import SecretStore

    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("github:default", {"mode": "mcp", "enabled": True})
    assert [t.name for t in active_tool_defs(secrets, "github")] == ["github_clone", "github_pull"]
    secrets.put("github:default", {"token": "ghp_x", "enabled": True})  # a token whose arm failed
    assert [t.name for t in active_tool_defs(secrets, "github")] == ["github_clone", "github_pull"]


def test_clone_authenticates_with_the_device_token(tmp_path):
    """No token sits in the profile for a device sign-in; the one the MCP connection uses
    is the one git gets — on the command line only, never in the repository's config."""
    from harness.integrations.connectors.github_git import git_auth_args as _github_git_auth_args
    from harness.safety.secrets import SecretStore

    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("github:default", {"mode": "mcp", "enabled": True})
    secrets.put("mcp-oauth:github", {"tokens": {"access_token": "gho_device"}})
    args = _github_git_auth_args(secrets, "acme")
    assert any(a.startswith("http.extraHeader=AUTHORIZATION: basic ") for a in args)
    assert "credential.helper=" in args


def test_the_toolsets_route_saves_the_choice_and_reports_the_missing_scope(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app

    _state(tmp_path, monkeypatch)
    manager = SessionManager(workspace=tmp_path)
    manager.secrets.put("github:default", {"mode": "mcp", "enabled": True})
    manager.secrets.put("mcp-oauth:github", {"tokens": {"access_token": "at", "scope": "repo,read:org"}})
    with TestClient(create_app(manager)) as client:
        out = client.post("/v1/connectors/github/toolsets", json={"enabled": ["gists", "actions"]}).json()
        assert out["ok"] and out["needs_scopes"] == ["gist"]
        assert client.post("/v1/connectors/github/toolsets", json={"enabled": "gists"}).json()["ok"] is False
        row = next(c for c in client.get("/v1/connectors").json()["connectors"] if c["name"] == "github")
        assert {t["id"] for t in row["toolsets"] if t["enabled"]} >= {"gists", "actions", "repos"}


def test_a_github_connection_page_before_connecting_lists_the_servers_groups(tmp_path, monkeypatch):
    """The Connect page asked 'which tools?' and could answer only with clone and pull, the two
    Reeve adds itself: the server's tools only exist once connected. The groups it serves are
    known now and are what the page shows."""
    _state(tmp_path, monkeypatch)
    manager = SessionManager(data_dir=tmp_path / "data")
    row = next(c for c in connector_list(manager.secrets) if c["name"] == "github")
    assert row["connected"] is False
    ids = {t["id"] for t in row["toolsets"]}
    assert {"repos", "issues", "pull_requests", "actions", "gists"} <= ids
    assert [t["name"] for t in row["tools"]] == ["github_clone", "github_pull"]
