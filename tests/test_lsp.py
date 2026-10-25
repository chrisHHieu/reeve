"""LSP client, server resolution, and the `lsp` tool — all hermetic.

The end-to-end half talks to `tests/fixtures/fake_lsp.py`, a stdlib child that speaks
the real wire protocol; no actual language server has to be installed for these tests to
pass. `REEVE_STATE_DIR` is redirected per test by `conftest.py`, so the config table
that points at the fixture lives in a temp directory too.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

from harness.config.config import load_config
from harness.integrations.lsp.client import LSPClient, encode_message, path_to_uri, read_message, to_lsp_position, uri_to_path
from harness.integrations.lsp.manager import LSPManager
from harness.integrations.lsp.servers import ServerNotFound, language_for, lsp_enabled, resolve_server, supported_extensions
from harness.integrations.lsp.tool import lsp_tools
from harness.safety.risk import RiskClass, classify
from harness.safety.secrets import state_dir
from harness.tools.contract import contract_of

FIXTURE = Path(__file__).parent / "fixtures" / "fake_lsp.py"


# -- framing ---------------------------------------------------------------------


def test_encode_read_message_round_trip():
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "text": "héllo"}}
    assert read_message(io.BytesIO(encode_message(payload))) == payload


def test_read_message_tolerates_extra_headers_and_lf_endings():
    payload = {"jsonrpc": "2.0", "id": 7, "result": []}
    body = json.dumps(payload).encode("utf-8")
    wire = (
        b"Content-Type: application/vscode-jsonrpc; charset=utf-8\n"
        b"content-length: " + str(len(body)).encode("ascii") + b"\n"
        b"\n" + body
    )
    assert read_message(io.BytesIO(wire)) == payload


def test_read_message_returns_none_at_clean_eof():
    assert read_message(io.BytesIO(b"")) is None


def test_read_message_rejects_missing_content_length():
    from harness.integrations.lsp.client import LSPProtocolError

    with pytest.raises(LSPProtocolError):
        read_message(io.BytesIO(b"Content-Type: application/json\r\n\r\n{}"))


# -- URIs and positions ------------------------------------------------------------


def test_uri_round_trip(tmp_path):
    target = tmp_path / "with space" / "demo.py"
    target.parent.mkdir()
    target.write_text("x = 1\n", encoding="utf-8")
    uri = path_to_uri(target)
    assert uri.startswith("file://")
    assert uri_to_path(uri) == str(target.resolve())


def test_uri_to_path_passes_external_uris_through():
    assert uri_to_path("untitled:Untitled-1") == "untitled:Untitled-1"


def test_uri_key_normalizes_the_spellings_servers_use(tmp_path):
    """Pyright answers with the drive colon percent-encoded and lowercased while we sent
    `file:///C:/…`; diagnostics keyed on the server's spelling landed where nothing read
    them. The key is the PATH, normcased, so both spellings collide into one."""
    from harness.integrations.lsp.client import uri_key

    target = tmp_path / "a.py"
    ours = path_to_uri(target)  # file:///C:/Users/…
    # The shape pyright publishes: the PATH part lowercased with the drive colon
    # percent-encoded (`file:///c%3A/…`) — the `file:` scheme's own colon is untouched.
    pyright = "file:///" + ours[len("file:///"):].replace(":", "%3A", 1).lower()
    assert pyright != ours
    assert uri_key(pyright) == uri_key(ours)
    # A non-file URI has no path to normalize and stays verbatim.
    assert uri_key("untitled:Untitled-1") == "untitled:Untitled-1"


def test_operation_capabilities_match_the_spec_shape():
    """These are top-level `ServerCapabilities` fields; the lookup used to say
    `textDocument.definitionProvider`, which made every real server look unsupported while
    the fake mirror passed. Pin the exact paths."""
    from harness.integrations.lsp.client import _OPERATION_CAPABILITIES

    assert _OPERATION_CAPABILITIES == {
        "definition": ("definitionProvider",),
        "references": ("referencesProvider",),
        "hover": ("hoverProvider",),
        "document_symbol": ("documentSymbolProvider",),
        "workspace_symbol": ("workspaceSymbolProvider",),
    }


def test_position_conversion_is_one_based_on_the_way_in():
    assert to_lsp_position(1, 1) == {"line": 0, "character": 0}
    assert to_lsp_position(5, 3) == {"line": 4, "character": 2}
    # A position before the file start clamps rather than raising: the caller meant the
    # start of the file.
    assert to_lsp_position(0, -2) == {"line": 0, "character": 0}


# -- config and server resolution ---------------------------------------------------


def test_config_override_wins_and_keeps_its_arguments(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        f"[lsp.servers.python]\ncommand = ['{sys.executable}', '--stdio', '--extra']\n"
    )
    spec = resolve_server("python", ws, config=load_config(ws, global_path=config))
    assert spec.source == "config"
    assert spec.command[0] == sys.executable
    assert spec.command[1:] == ["--stdio", "--extra"]


def test_typescript_gets_a_tsserver_path_via_initialization_options(tmp_path):
    """`typescript-language-server` 6 dropped `--tsserver-path`; the path a bare repo
    needs goes through `initializationOptions.tsserver.path`, and a workspace-local
    typescript wins over the global install."""
    ws = tmp_path / "ws"
    (ws / "node_modules" / ".bin").mkdir(parents=True)
    (ws / "node_modules" / "typescript" / "lib").mkdir(parents=True)
    tsserver = ws / "node_modules" / "typescript" / "lib" / "tsserver.js"
    tsserver.write_text("// stub\n", encoding="utf-8")
    binary = ws / "node_modules" / ".bin" / (
        "typescript-language-server.cmd" if os.name == "nt" else "typescript-language-server"
    )
    binary.write_text("", encoding="utf-8")

    spec = resolve_server("typescript", ws)
    assert spec.initialization_options == {"tsserver": {"path": str(tsserver)}}
    assert "--tsserver-path" not in spec.command


def test_typescript_tsserver_falls_back_to_a_global_install(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "node_modules" / ".bin").mkdir(parents=True)
    binary = ws / "node_modules" / ".bin" / (
        "typescript-language-server.cmd" if os.name == "nt" else "typescript-language-server"
    )
    binary.write_text("", encoding="utf-8")
    # A global npm layout under a redirected %APPDATA%, with no workspace typescript.
    global_ts = (
        tmp_path / "appdata" / "npm" / "node_modules" / "typescript" / "lib" / "tsserver.js"
    )
    global_ts.parent.mkdir(parents=True)
    global_ts.write_text("// stub\n", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    spec = resolve_server("typescript", ws)
    assert spec.initialization_options == {"tsserver": {"path": str(global_ts)}}


def test_python_gets_no_initialization_options(tmp_path):
    ws = tmp_path / "ws"
    (ws / "node_modules" / ".bin").mkdir(parents=True)
    binary = ws / "node_modules" / ".bin" / (
        "pyright-langserver.cmd" if os.name == "nt" else "pyright-langserver"
    )
    binary.write_text("", encoding="utf-8")
    spec = resolve_server("python", ws, extension=".py")
    assert spec.initialization_options == {}


def test_workspace_local_binary_is_preferred_over_path(tmp_path):
    ws = tmp_path / "ws"
    (ws / "node_modules" / ".bin").mkdir(parents=True)
    if os.name == "nt":
        binary = ws / "node_modules" / ".bin" / "pyright-langserver.cmd"
    else:
        binary = ws / "node_modules" / ".bin" / "pyright-langserver"
    binary.write_text("@echo off\n", encoding="utf-8")
    if os.name != "nt":
        binary.chmod(0o755)
    spec = resolve_server(
        "python", ws, config=load_config(global_path=tmp_path / "none.toml")
    )
    assert spec.command[0] == str(binary)
    assert spec.source == "workspace"
    assert spec.command[1:] == ["--stdio"]


def test_missing_server_error_names_extension_binary_and_fix(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        "[lsp.servers.python]\n"
        "command = ['definitely-not-a-real-lsp-binary-xyz', '--stdio']\n"
    )
    with pytest.raises(ServerNotFound) as excinfo:
        resolve_server(
            "python", ws, config=load_config(ws, global_path=config), extension=".py"
        )
    message = str(excinfo.value)
    assert "definitely-not-a-real-lsp-binary-xyz" in message
    assert ".py" in message
    assert "[lsp.servers.python]" in message


def test_lsp_enabled_defaults_true_and_is_workspace_overridable(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".reeve").mkdir(parents=True)
    global_config = tmp_path / "global.toml"
    global_config.write_text("[lsp]\nenabled = true\n")
    assert lsp_enabled(load_config(ws, global_path=global_config)) is True

    (ws / ".reeve" / "config.toml").write_text("[lsp]\nenabled = false\n")
    assert lsp_enabled(load_config(ws, global_path=global_config)) is False


# -- the fake-server end to end -----------------------------------------------------


def test_a_configured_language_becomes_routable(tmp_path):
    """A language outside the shipped table is declared in config: a command plus the
    `extensions` it owns (and an optional `language_id`, defaulting to the key). This is
    how Ruby/Lua/C are added without touching the code."""
    ws = tmp_path / "ws"
    ws.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        "[lsp.servers.ruby]\n"
        f"command = ['{sys.executable}', '--stdio']\n"
        "extensions = ['.rb', 'rake']\n"
        "language_id = 'ruby'\n",
        encoding="utf-8",
    )
    cfg = load_config(ws, global_path=config)
    assert language_for(ws / "app.rb", cfg) == ("ruby", "ruby")
    assert language_for(ws / "Rakefile.rake", cfg) == ("ruby", "ruby")
    assert ".rb" in supported_extensions(cfg)
    # The shipped table keeps its own routing: config overrides the COMMAND of a known
    # language, not its extension mapping.
    assert language_for(ws / "a.py", cfg) == ("python", "python")
    assert language_for(ws / "a.unknown", cfg) is None


def test_a_configured_language_runs_end_to_end(tmp_path):
    ws, _source, log = _workspace(tmp_path)
    ruby = ws / "app.rb"
    ruby.write_text("def canned()\n  1\nend\n", encoding="utf-8")
    path = state_dir() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = ", ".join(
        f"'{part}'" for part in [sys.executable, str(FIXTURE), str(log), str(ws)]
    )
    path.write_text(
        "[lsp.servers.ruby]\n"
        f"command = [{rendered}]\n"
        "extensions = ['.rb']\n",
        encoding="utf-8",
    )
    manager = LSPManager()
    try:
        out = manager.request("document_symbol", ruby, workspace=ws)
        assert "error" not in out, out
        assert out["symbols"][0]["name"] == "canned_function"
    finally:
        manager.close()


def _write_server_config(command: list[str]) -> None:
    """Point `[lsp.servers.python]` at the fake server, in the per-test state dir."""
    path = state_dir() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    # TOML literal strings: Windows paths are full of backslashes, which would be
    # escape sequences inside basic strings.
    rendered = ", ".join(f"'{part}'" for part in command)
    path.write_text(f"[lsp]\nenabled = true\n\n[lsp.servers.python]\ncommand = [{rendered}]\n")


def _workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    ws = tmp_path / "ws"
    ws.mkdir()
    source = ws / "demo.py"
    source.write_text("def canned():\n    return 1\n", encoding="utf-8")
    return ws, source, tmp_path / "messages.jsonl"


def _log_entries(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_fake_server_definition_diagnostics_did_change_and_close(tmp_path):
    ws, source, log = _workspace(tmp_path)
    _write_server_config([sys.executable, str(FIXTURE), str(log), str(ws)])
    manager = LSPManager()
    try:
        definition = manager.request(
            "definition", source, workspace=ws, line=1, character=5
        )
        assert "error" not in definition, definition
        location = definition["locations"][0]
        assert (location["line"], location["character"]) == (2, 5)
        assert "return 1" in location["snippet"]

        diagnostics = manager.request("diagnostics", source, workspace=ws)
        assert "error" not in diagnostics, diagnostics
        assert diagnostics["total"] == 1
        row = diagnostics["diagnostics"][0]
        assert row["severity"] == "error"
        assert row["message"] == "canned error"
        assert (row["line"], row["character"]) == (3, 5)

        hover = manager.request("hover", source, workspace=ws, line=1, character=5)
        assert hover["found"] is True
        assert "canned hover" in hover["contents"]

        references = manager.request("references", source, workspace=ws, line=1, character=5)
        assert references["total"] == 2
        assert references["locations"][0]["snippet"] == "def canned():"

        symbols = manager.request("document_symbol", source, workspace=ws)
        assert symbols["total"] == 2  # the parent and its child
        parent = symbols["symbols"][0]
        assert parent["name"] == "canned_function"
        assert parent["children"][0]["name"] == "canned_child"
        assert parent["detail"] == "def canned_function() -> int"

        workspace_symbols = manager.request(
            "workspace_symbol", source, workspace=ws, query="thing"
        )
        assert "error" not in workspace_symbols, workspace_symbols
        match = workspace_symbols["symbols"][0]
        assert match["name"] == "canned_thing"
        assert match["path"] == "canned_symbols.py"
        assert (match["line"], match["character"]) == (3, 1)

        # The file changes on disk; the next call must re-send it as didChange, not a
        # second didOpen.
        source.write_text("def canned():\n    return 2\n", encoding="utf-8")
        again = manager.request("definition", source, workspace=ws, line=1, character=5)
        assert "error" not in again, again
        methods = [entry["method"] for entry in _log_entries(log)]
        assert methods.count("textDocument/didOpen") == 1
        assert methods.count("textDocument/didChange") == 1
        change = next(
            entry
            for entry in _log_entries(log)
            if entry["method"] == "textDocument/didChange"
        )
        assert change["params"]["contentChanges"][0]["text"].endswith("return 2\n")

        client = manager.client_for(ws, "python")
        assert client.alive
        manager.close()
        assert not client.alive
        assert client.returncode is not None
    finally:
        manager.close()


def test_fake_server_capability_gating_refuses_unsupported_operation(tmp_path):
    ws, source, log = _workspace(tmp_path)
    _write_server_config(
        [sys.executable, str(FIXTURE), str(log), str(ws), "--no-references"]
    )
    manager = LSPManager()
    try:
        result = manager.request("references", source, workspace=ws, line=1, character=5)
        assert "error" in result
        assert "does not support" in result["error"]
        # The gate must fire before any document is sent: an unsupported operation is
        # refused whether or not the file is opened with the server.
        methods = [entry["method"] for entry in _log_entries(log)]
        assert "textDocument/didOpen" not in methods
    finally:
        manager.close()


def test_manager_shares_one_client_per_root_and_language(tmp_path):
    ws, source, log = _workspace(tmp_path)
    _write_server_config([sys.executable, str(FIXTURE), str(log), str(ws)])
    manager = LSPManager()
    try:
        first = manager.client_for(ws, "python")
        second = manager.client_for(ws, "python")
        assert first is second
    finally:
        manager.close()


def test_manager_reports_missing_server_as_error_dict(tmp_path):
    ws, source, _ = _workspace(tmp_path)
    _write_server_config(["definitely-not-a-real-lsp-binary-xyz", "--stdio"])
    manager = LSPManager()
    try:
        result = manager.request("definition", source, workspace=ws, line=1, character=1)
        assert "error" in result
        assert "definitely-not-a-real-lsp-binary-xyz" in result["error"]
        # The error must name the extension and the config fix, not just the binary.
        assert ".py" in result["error"]
        assert "[lsp.servers.python]" in result["error"]
    finally:
        manager.close()


# -- the tool ----------------------------------------------------------------------


def test_tool_rejects_a_path_outside_the_session_roots(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    tool = lsp_tools(str(ws))[0]
    result = tool("definition", f"../{outside.name}", 1, 1)
    assert "error" in result
    assert "outside the session's folders" in result["error"]


def test_tool_rejects_an_unknown_operation(tmp_path):
    tool = lsp_tools(str(tmp_path))[0]
    result = tool("teleport", "demo.py")
    assert "error" in result
    assert "unknown" in result["error"]


def test_tool_requires_line_for_position_operations(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "demo.py").write_text("x = 1\n", encoding="utf-8")
    tool = lsp_tools(str(ws))[0]
    result = tool("definition", "demo.py")
    assert "error" in result
    assert "line" in result["error"]


def test_tool_value_render_and_presentation(tmp_path):
    ws, source, log = _workspace(tmp_path)
    _write_server_config([sys.executable, str(FIXTURE), str(log), str(ws)])
    manager = LSPManager()
    try:
        tool = lsp_tools(str(ws), manager=manager)[0]
        value = tool("definition", "demo.py", 1, 5)
        assert "error" not in value, value
        contract = contract_of(tool)
        assert contract is not None
        text = contract.render({"operation": "definition", "file": "demo.py"}, value)
        assert "demo.py:2:5" in text
        shown = contract.present_result({"operation": "definition"}, value)
        assert "definition" in shown.summary

        diagnostics = tool("diagnostics", "demo.py")
        diagnostic_text = contract.render(
            {"operation": "diagnostics", "file": "demo.py"}, diagnostics
        )
        assert "error demo.py:3:5 canned error" in diagnostic_text
    finally:
        manager.close()


def test_tool_metadata_is_a_read(tmp_path):
    tool = lsp_tools(str(tmp_path))[0]
    metadata = tool.__aisuite_tool_metadata__
    assert metadata.category == "lsp"
    assert metadata.requires_approval is False
    assert classify("lsp", metadata) is RiskClass.READ
    # The schema is the explicit one, with the operation enum the model sees.
    schema = tool.__tool_schema__["function"]
    assert schema["name"] == "lsp"
    assert set(schema["parameters"]["properties"]["operation"]["enum"]) == {
        "definition",
        "references",
        "hover",
        "document_symbol",
        "workspace_symbol",
        "diagnostics",
    }


def test_client_start_failure_is_a_typed_error(tmp_path):
    client = LSPClient(["definitely-not-a-real-lsp-binary-xyz"], tmp_path)
    from harness.integrations.lsp.client import LSPError

    with pytest.raises(LSPError):
        client.start()

# -- the Status popover's snapshot ---------------------------------------------------


def test_a_change_notice_reaches_the_server_and_diagnostics_are_not_stale(tmp_path):
    """The bug a live session hit (2026-09-19): after an on-disk change the next
    diagnostics call returned the PREVIOUS snapshot, and a `pyrightconfig.json` written
    mid-session was never noticed at all. Both halves are pinned here: a fresh publish
    wins over the stale one, and the manager forwards change notices to the owning
    server (and only that server)."""
    ws, source, log = _workspace(tmp_path)
    _write_server_config([sys.executable, str(FIXTURE), str(log), str(ws)])
    manager = LSPManager()
    try:
        first = manager.request("diagnostics", source, workspace=ws)
        assert first["diagnostics"][0]["message"] == "canned error"

        # The file changes on disk: the re-ask must land on the NEW publish, not the
        # snapshot taken before the didChange was sent.
        source.write_text("def canned():\n    return 2\n", encoding="utf-8")
        second = manager.request("diagnostics", source, workspace=ws)
        assert second["diagnostics"][0]["message"] == "canned error after change", second

        config = ws / "pyrightconfig.json"
        config.write_text("{}", encoding="utf-8")
        manager.notify_files_changed([config])
        watched = [
            entry
            for entry in _log_entries(log)
            if entry["method"] == "workspace/didChangeWatchedFiles"
        ]
        assert len(watched) == 1
        change = watched[0]["params"]["changes"][0]
        assert change["type"] == 2
        assert change["uri"].endswith("pyrightconfig.json")

        # A file no live server owns is nobody's to correct.
        outside = tmp_path / "outside.py"
        outside.write_text("x = 1\n", encoding="utf-8")
        manager.notify_files_changed([outside])
        watched = [
            entry
            for entry in _log_entries(log)
            if entry["method"] == "workspace/didChangeWatchedFiles"
        ]
        assert len(watched) == 1
    finally:
        manager.close()


def test_a_write_call_tells_the_servers_what_changed(tmp_path):
    """The engine hook: a successful write forwards its paths to the live manager, a
    failed one reports nothing, and a non-write call is not a change."""
    from types import SimpleNamespace

    from harness.loop.tool_batches import ToolBatchMixin as HelpersMixin
    from harness.llm.providers import ToolCall

    seen: list[list] = []
    manager = SimpleNamespace(notify_files_changed=lambda paths: seen.append(list(paths)))
    host = SimpleNamespace(
        lsp_manager=manager,
        permissions=SimpleNamespace(workspace_root=str(tmp_path)),
    )
    write = ToolCall(id="c1", name="write_file", arguments={"path": "demo.py", "content": "x"})

    HelpersMixin._notify_lsp_writes(host, write, "ok")
    assert seen == [[tmp_path / "demo.py"]]

    HelpersMixin._notify_lsp_writes(host, write, "error")
    assert len(seen) == 1, "a failed write has no change to report"

    read = ToolCall(id="c2", name="read_file", arguments={"path": "demo.py"})
    HelpersMixin._notify_lsp_writes(host, read, "ok")
    assert len(seen) == 1, "a read changes nothing"

    HelpersMixin._notify_lsp_writes(SimpleNamespace(lsp_manager=None, permissions=host.permissions), write, "ok")
    assert len(seen) == 1, "no language servers means no notifications"


def test_snapshot_reports_live_pool_config_and_known_languages(tmp_path):
    """GET /v1/lsp's data: rows for the live pool, the config switch, and what COULD
    start. A language server spawns lazily, so "configured but idle" is an empty list,
    not a fabricated row."""
    ws, _source, log = _workspace(tmp_path)
    _write_server_config([sys.executable, str(FIXTURE), str(log), str(ws)])
    manager = LSPManager()
    try:
        before = manager.snapshot()
        assert before["enabled"] is True
        assert before["servers"] == []
        assert "python" in before["known_languages"]

        out = manager.request("document_symbol", ws / "demo.py", workspace=ws)
        assert "error" not in out, out
        after = manager.snapshot()
        assert len(after["servers"]) == 1
        row = after["servers"][0]
        assert row["language"] == "python"
        assert row["language_id"] == "python"
        assert row["workspace"] == str(ws.resolve())
        assert row["alive"] is True
        assert "fake_lsp.py" in row["command"]

        # A server whose process died stays in the pool until the next call replaces it �
        # the one failure the popover's dot is allowed to see.
        for client in manager._clients.values():
            client.close()
        assert manager.snapshot()["servers"][0]["alive"] is False
    finally:
        manager.close()


def test_a_server_that_stops_answering_costs_one_timeout_not_one_per_call(tmp_path, monkeypatch):
    # 2026-09-25 audit (W3): with pyright 1.1.408 every lookup hung for the whole 20 s
    # budget, call after call. After one timeout the next lookups fail at once with the
    # way forward; the cooldown ends, and the server gets another try.
    import time as _time

    from harness.integrations.lsp import client as client_mod
    from harness.integrations.lsp.client import LSPTimeout

    client = LSPClient(["unused"], tmp_path, language="python", request_timeout_ms=50)
    monkeypatch.setattr(client, "_send", lambda message: None)  # a server that never answers
    with pytest.raises(LSPTimeout, match="fail at once"):
        client.request("textDocument/hover", {})
    started = _time.monotonic()
    with pytest.raises(LSPTimeout, match="use grep and read_file"):
        client.request("textDocument/definition", {})
    assert _time.monotonic() - started < 0.02  # did not wait out another budget

    clock = _time.monotonic() + client_mod.STALL_COOLDOWN_S + 1
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: clock)
    with pytest.raises(LSPTimeout, match="timed out"):  # tried again, for real
        client.request("textDocument/hover", {})
