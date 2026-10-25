"""Language-server diagnostics appended after an edit.

The half the post-edit diagnostics deferred: a successful, parse-clean write can still be wrong at the
type level, and the server that already knows should say so in the same step. Pinned
here: the end-to-end note against the fake LSP child (real wire protocol), errors only,
the item cap plus its "more" tail, the fail-open paths (no server at all, exception,
kill switch), the call-shape gates (failed write, read), the dict-result key, and the
forwarding of the tight settle/timeout budget.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from harness.loop import TurnEngine
from harness.integrations.lsp.manager import LSPManager
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import ModelCapabilities, ProviderClient, ToolCall
from harness.llm.providers.base import AssistantTurn
from harness.safety.secrets import state_dir
from harness.tools import ToolRegistry

FIXTURE = Path(__file__).parent / "fixtures" / "fake_lsp.py"


class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(ws: Path) -> TurnEngine:
    return TurnEngine(
        provider=Provider(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=ws),
        model="gpt-5.5",
        messages=[{"role": "system", "content": "s"}],
    )


def _write_call(path: str) -> ToolCall:
    return ToolCall(id="c1", name="write_file", arguments={"path": path, "content": "..."})


def _write_server_config(command: list[str], *, extra: str = "") -> None:
    """Point `[lsp.servers.python]` at the fake server, in the per-test state dir."""
    path = state_dir() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = ", ".join(f"'{part}'" for part in command)
    path.write_text(
        f"[lsp]\nenabled = true\n{extra}\n[lsp.servers.python]\ncommand = [{rendered}]\n"
    )


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("x = 1\n", encoding="utf-8")
    return ws, tmp_path / "messages.jsonl"


class FakeLSP:
    """A stand-in manager: records calls, returns a canned shaped result or raises."""

    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, operation, path, **kwargs):
        self.calls.append((operation, str(path), kwargs))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _diagnostics(*rows) -> dict:
    return {
        "operation": "diagnostics",
        "diagnostics": list(rows),
        "total": len(rows),
        "truncated": False,
    }


def _row(severity: str, message: str, line: int = 2) -> dict:
    return {
        "severity": severity,
        "line": line,
        "character": 4,
        "message": message,
        "source": "fake-lsp",
    }


# -- the real path -----------------------------------------------------------------


def test_a_write_gets_the_servers_errors_appended(tmp_path):
    ws, log = _workspace(tmp_path)
    _write_server_config([sys.executable, str(FIXTURE), str(log), str(ws)])
    manager = LSPManager()
    engine = _engine(ws)
    engine.lsp_manager = manager
    try:
        engine._record_result(
            _write_call(str(ws / "app.py")), {"ok": True}, "ok"
        )
    finally:
        manager.close()

    content = engine.messages[-1]["content"]
    assert "[lsp]" in content
    assert "canned error" in content  # the fake server's own message
    assert "app.py" in content


def test_a_clean_edit_with_no_server_adds_nothing(tmp_path):
    ws, _log = _workspace(tmp_path)
    engine = _engine(ws)
    engine.lsp_manager = LSPManager()  # nothing configured: resolve_server fails
    try:
        engine._record_result(_write_call(str(ws / "app.py")), {"ok": True}, "ok")
    finally:
        engine.lsp_manager.close()
    assert "[lsp]" not in engine.messages[-1]["content"]


# -- shape and bounds (a fake manager, so the assertions are exact) ------------------


def test_errors_only_and_the_cap_with_its_more_tail(tmp_path):
    ws, _log = _workspace(tmp_path)
    rows = [_row("warning", "noise")]
    rows += [_row("error", f"bad{i}") for i in range(8)]
    engine = _engine(ws)
    engine.lsp_manager = FakeLSP(_diagnostics(*rows))

    engine._record_result(_write_call(str(ws / "app.py")), {"ok": True}, "ok")

    note = json.loads(engine.messages[-1]["content"])["diagnostics"]
    assert "bad0" in note and "bad4" in note
    assert "bad5" not in note  # the cap
    assert "and 3 more error(s)" in note  # 8 errors, 5 shown
    assert "noise" not in note  # warnings are not the edit loop's business


def test_an_error_is_reported_once_not_after_every_edit(tmp_path):
    # Session b3a8491e-9db (2026-09-28): eleven edits to one file, each carrying the same
    # `Import "sqlalchemy" could not be resolved` from line 6 — the server's environment,
    # not anything the edit wrote.
    ws, _log = _workspace(tmp_path)
    engine = _engine(ws)
    env_error = _row("error", 'Import "sqlalchemy" could not be resolved', line=6)
    engine.lsp_manager = FakeLSP(_diagnostics(env_error))
    call = _write_call(str(ws / "app.py"))

    def last_note():
        return json.loads(engine.messages[-1]["content"]).get("diagnostics")

    engine._record_result(call, {"ok": True}, "ok")
    assert "sqlalchemy" in last_note()  # the first time, in full
    engine._record_result(call, {"ok": True}, "ok")
    assert last_note() is None  # the same error again says nothing

    # A new error is reported, and the old one is counted, not repeated — even though
    # the edit moved it to another line.
    moved = _row("error", 'Import "sqlalchemy" could not be resolved', line=9)
    engine.lsp_manager = FakeLSP(_diagnostics(moved, _row("error", "undefined name 'payoff'")))
    engine._record_result(call, {"ok": True}, "ok")
    note = last_note()
    assert "payoff" in note and "sqlalchemy" not in note
    assert "1 error(s) reported for" in note and "still there" in note

    # Fixed, then back: that is new again.
    engine.lsp_manager = FakeLSP(_diagnostics(moved))
    engine._record_result(call, {"ok": True}, "ok")
    engine.lsp_manager = FakeLSP(_diagnostics(moved, _row("error", "undefined name 'payoff'")))
    engine._record_result(call, {"ok": True}, "ok")
    assert "payoff" in last_note()


def test_a_dict_result_keeps_its_shape_and_gains_the_key(tmp_path):
    ws, _log = _workspace(tmp_path)
    engine = _engine(ws)
    engine.lsp_manager = FakeLSP(_diagnostics(_row("error", "bad")))

    engine._record_result(
        _write_call(str(ws / "app.py")), {"ok": True, "path": "app.py"}, "ok"
    )

    payload = json.loads(engine.messages[-1]["content"])
    assert payload["ok"] is True and payload["path"] == "app.py"
    assert payload["diagnostics"].startswith("[lsp]")


def test_the_tight_budget_is_forwarded(tmp_path):
    ws, _log = _workspace(tmp_path)
    fake = FakeLSP(_diagnostics())
    engine = _engine(ws)
    engine.lsp_manager = fake

    engine._record_result(_write_call(str(ws / "app.py")), {"ok": True}, "ok")

    assert fake.calls and fake.calls[0][0] == "diagnostics"
    _, _, kwargs = fake.calls[0]
    assert kwargs["settle_ms"] == 300 and kwargs["timeout_ms"] == 3_000


# -- fail-open ----------------------------------------------------------------------


def test_a_manager_error_dict_is_silent(tmp_path):
    ws, _log = _workspace(tmp_path)
    engine = _engine(ws)
    engine.lsp_manager = FakeLSP({"error": "no language server for .py"})
    engine._record_result(_write_call(str(ws / "app.py")), {"ok": True}, "ok")
    assert "[lsp]" not in engine.messages[-1]["content"]


def test_a_raising_manager_is_silent(tmp_path):
    ws, _log = _workspace(tmp_path)
    engine = _engine(ws)
    engine.lsp_manager = FakeLSP(RuntimeError("server crashed"))
    engine._record_result(_write_call(str(ws / "app.py")), {"ok": True}, "ok")
    assert "[lsp]" not in engine.messages[-1]["content"]


def test_the_kill_switch_silences_the_note(tmp_path):
    ws, log = _workspace(tmp_path)
    _write_server_config(
        [sys.executable, str(FIXTURE), str(log), str(ws)],
        extra="diagnostics_after_edit = false",
    )
    manager = LSPManager()
    engine = _engine(ws)
    engine.lsp_manager = manager
    try:
        engine._record_result(_write_call(str(ws / "app.py")), {"ok": True}, "ok")
    finally:
        manager.close()
    assert "[lsp]" not in engine.messages[-1]["content"]


# -- call-shape gates ---------------------------------------------------------------


def test_a_failed_write_and_a_read_are_not_asked_about(tmp_path):
    ws, _log = _workspace(tmp_path)
    fake = FakeLSP(_diagnostics(_row("error", "bad")))
    engine = _engine(ws)
    engine.lsp_manager = fake

    engine._record_result(_write_call(str(ws / "app.py")), {"error": "denied"}, "error")
    engine._record_result(
        ToolCall(id="c2", name="read_file", arguments={"path": str(ws / "app.py")}),
        {"content": "x = 1"},
        "ok",
    )

    assert fake.calls == []
    assert all("[lsp]" not in str(m.get("content")) for m in engine.messages)
