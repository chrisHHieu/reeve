"""External subagent providers (codex / claude-code CLIs).

The runner is tested with a fake `subprocess.Popen` so no real CLI is needed: argv
shape, JSON parsing, missing-binary refusal, and Stop. The manager path is tested with
`run_external` stubbed, proving the child session, transcript and result envelope.
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from harness.orchestration import subagent_external as ext
from harness.interface.server.manager import SessionManager


# -- argv / parsing -----------------------------------------------------------------


def test_codex_argv_is_non_interactive_and_workspace_scoped():
    argv = ext.build_argv("codex", "/bin/codex", workspace="/ws")
    assert argv[:3] == ["/bin/codex", "exec", "--json"]
    assert "--cd" in argv and "/ws" in argv
    # Full trust by default: a piped subprocess cannot answer an approval prompt, so the
    # child gets danger-full-access with approvals off (see the builtin's docstring).
    assert argv[argv.index("--sandbox") + 1] == "danger-full-access"
    assert argv[argv.index("-c") + 1] == "approval_policy=never"
    assert "--skip-git-repo-check" in argv
    assert "--model" not in argv

    override = ext.build_argv(
        "codex", "/bin/codex", workspace="/ws", permission_mode="workspace-write"
    )
    assert override[override.index("--sandbox") + 1] == "workspace-write"


def test_claude_argv_uses_print_mode_and_a_permission_preset():
    argv = ext.build_argv(
        "claude-code", "/bin/claude", workspace="/ws", model="claude-sonnet-5"
    )
    assert argv[:3] == ["/bin/claude", "-p", "--output-format"]
    # stream-json is what makes the child's transcript live: events arrive per line
    # instead of one blob at the end.
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--include-partial-messages" in argv and "--verbose" in argv
    # Full bypass by default: headless runs cannot answer an approval prompt, so
    # acceptEdits would block every Bash call (see the builtin's docstring).
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5"

    override = ext.build_argv(
        "claude-code", "/bin/claude", workspace="/ws", permission_mode="plan"
    )
    assert override[override.index("--permission-mode") + 1] == "plan"


def test_parse_report_reads_the_claude_stream_json_shape():
    stream = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "OK"},
                    },
                }
            ),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "OK"}]}}),
            json.dumps(
                {"type": "result", "result": "OK", "usage": {"input_tokens": 2, "output_tokens": 1}}
            ),
        ]
    )
    assert ext.parse_report("claude-code", stream) == ("OK", 3)
    # A run killed before the terminal `result` still reports the last assistant text.
    partial = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "half done"}],
                        "usage": {"input_tokens": 5, "output_tokens": 2},
                    },
                }
            )
        ]
    )
    assert ext.parse_report("claude-code", partial) == ("half done", 7)


def test_progress_text_folds_both_cli_stream_shapes():
    text = ext.progress_text(
        "claude-code",
        {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
        },
        "",
    )
    assert text == "Hi"
    text = ext.progress_text(
        "claude-code",
        {
            "type": "stream_event",
            "event": {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "Bash"}},
        },
        text,
    )
    assert "Bash" in text

    codex = ext.progress_text(
        "codex", {"type": "item.completed", "item": {"type": "command_execution", "command": "pytest -q"}}, ""
    )
    assert codex == "\n\n$ pytest -q\n"
    assert ext.progress_text("codex", {"type": "thread.started"}, codex) == codex


def test_parse_report_reads_both_cli_shapes():
    claude = json.dumps(
        {"type": "result", "result": "hello", "usage": {"input_tokens": 3, "output_tokens": 4}}
    )
    assert ext.parse_report("claude-code", claude) == ("hello", 7)
    codex = "\n".join(
        [
            json.dumps({"type": "thread.started"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 2}}),
        ]
    )
    assert ext.parse_report("codex", codex) == ("done", 3)
    # An unparseable stream degrades to raw text instead of an empty report.
    assert ext.parse_report("claude-code", "plain words")[0] == "plain words"


# -- the runner ---------------------------------------------------------------------


class _FakeProc:
    def __init__(self, *, out: str, returncode: int = 0):
        self._out = out
        self.returncode = returncode
        self.pid = 4242
        self.killed = False
        self.stdin = SimpleNamespace(write=lambda s: None, close=lambda: None)
        self.stdout = SimpleNamespace(read=lambda n: "", readline=lambda: "")
        # Already finished: `run_external`'s poll loop is skipped, so a test cannot
        # wait out the runner's real timeout.
        self._done = True

    def poll(self):
        return self.returncode if self._done else None

    def wait(self, timeout=None):
        self._done = True
        return self.returncode


@pytest.fixture()
def fake_popen(monkeypatch):
    calls: list[dict] = []

    def _popen(argv, **kwargs):
        proc = _FakeProc(out=calls and "" or "")
        calls.append({"argv": argv, **kwargs})
        return proc

    monkeypatch.setattr(ext.subprocess, "Popen", _popen)
    return calls


def test_a_missing_cli_is_a_clear_refusal(monkeypatch):
    monkeypatch.setattr(ext.shutil, "which", lambda name: None)
    out = ext.run_external("claude-code", prompt="hi")
    assert out["status"].startswith("error") and "not on PATH" in out["error"]


def test_the_prompt_rides_stdin_with_the_workspace_as_cwd(monkeypatch, fake_popen):
    monkeypatch.setattr(ext.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(ext, "preflight", lambda provider: None)
    written: list[str] = []

    def _write(text):
        written.append(text)

    def _popen(argv, **kwargs):
        proc = _FakeProc(out="")
        proc.stdin = SimpleNamespace(write=_write, close=lambda: None)
        fake_popen.append({"argv": argv, **kwargs})
        return proc

    monkeypatch.setattr(ext.subprocess, "Popen", _popen)
    monkeypatch.setattr(
        ext,
        "parse_report",
        lambda provider, stdout: ("the answer", 0),
    )
    out = ext.run_external("claude-code", prompt="do the thing", workspace=None)
    assert out["status"] == "completed" and out["report"] == "the answer"
    assert written == ["do the thing"]
    assert fake_popen[-1]["argv"][0].endswith("claude")


def test_streamed_events_reach_on_event(monkeypatch):
    monkeypatch.setattr(ext.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(ext, "preflight", lambda provider: None)
    lines = [
        json.dumps(
            {
                "type": "stream_event",
                "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hel"}},
            }
        )
        + "\n",
        json.dumps(
            {
                "type": "stream_event",
                "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lo"}},
            }
        )
        + "\n",
        json.dumps(
            {"type": "result", "result": "Hello", "usage": {"input_tokens": 1, "output_tokens": 1}}
        )
        + "\n",
    ]
    seen: list[dict] = []

    class _Streaming(_FakeProc):
        def __init__(self):
            super().__init__(out="")
            self._lines = list(lines)
            self.stdout = SimpleNamespace(
                readline=lambda: self._lines.pop(0) if self._lines else ""
            )

    monkeypatch.setattr(ext.subprocess, "Popen", lambda argv, **kwargs: _Streaming())
    out = ext.run_external("claude-code", prompt="hi", on_event=seen.append)

    assert out["status"] == "completed" and out["report"] == "Hello"
    assert [event.get("type") for event in seen] == [
        "stream_event",
        "stream_event",
        "result",
    ]


def test_stop_kills_the_run(monkeypatch):
    monkeypatch.setattr(ext.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(ext, "preflight", lambda provider: None)

    class _Slow(_FakeProc):
        def poll(self):
            return None

    monkeypatch.setattr(ext.subprocess, "Popen", lambda argv, **kwargs: _Slow(out=""))
    monkeypatch.setattr(ext, "_kill_tree", lambda proc, job=None: setattr(proc, "killed", True))
    stop = threading.Event()
    stop.set()
    out = ext.run_external("codex", prompt="hi", stop_event=stop)
    assert out["status"] == "interrupted"


# -- the account's own model list ----------------------------------------------------


class _Resp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


def test_list_models_reads_the_claude_account_with_the_cli_token(monkeypatch, tmp_path):
    creds = tmp_path / "credentials.json"
    creds.write_text(
        json.dumps(
            {"claudeAiOauth": {"accessToken": "oauth-tok", "expiresAt": 4102444800000}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ext, "CLAUDE_CREDENTIALS", str(creds))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    seen: dict = {}

    def _urlopen(request, timeout=0):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["beta"] = request.get_header("Anthropic-beta")
        return _Resp(
            {
                "data": [
                    {"id": "claude-opus-5", "display_name": "Claude Opus 5"},
                    {"id": "claude-sonnet-5", "display_name": "Claude Sonnet 5"},
                ]
            }
        )

    monkeypatch.setattr(ext, "_urlopen", _urlopen)
    out = ext.list_models("claude-code")

    assert out["ok"] is True and out["source"] == "Claude account"
    assert out["models"][0] == {"id": "claude-opus-5", "name": "Claude Opus 5"}
    assert "api.anthropic.com/v1/models" in seen["url"]
    assert seen["auth"] == "Bearer oauth-tok"
    assert seen["beta"] == "oauth-2025-04-20"


def test_list_models_prefers_an_api_key_env(monkeypatch, tmp_path):
    monkeypatch.setattr(ext, "CLAUDE_CREDENTIALS", str(tmp_path / "missing.json"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    seen: dict = {}

    def _urlopen(request, timeout=0):
        seen["key"] = request.get_header("X-api-key")
        return _Resp({"data": [{"id": "claude-haiku-4-5-20251001"}]})

    monkeypatch.setattr(ext, "_urlopen", _urlopen)
    out = ext.list_models("claude-code")

    assert out["ok"] is True and out["source"] == "api key"
    assert seen["key"] == "sk-test"
    assert out["models"] == [
        {"id": "claude-haiku-4-5-20251001", "name": "claude-haiku-4-5-20251001"}
    ]


def test_list_models_flags_an_expired_token(monkeypatch, tmp_path):
    creds = tmp_path / "credentials.json"
    creds.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "t", "expiresAt": 1}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(ext, "CLAUDE_CREDENTIALS", str(creds))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    out = ext.list_models("claude-code")
    assert out["ok"] is False and "expired" in out["error"]


def test_list_models_reports_failures_as_values(monkeypatch, tmp_path):
    monkeypatch.setattr(ext, "CLAUDE_CREDENTIALS", str(tmp_path / "none.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ext.list_models("claude-code")["ok"] is False

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk")
    monkeypatch.setattr(
        ext, "_urlopen", lambda request, timeout=0: (_ for _ in ()).throw(OSError("boom"))
    )
    out = ext.list_models("claude-code")
    assert out["ok"] is False and "boom" in out["error"]


def test_codex_models_come_from_the_cli_catalog_first(monkeypatch):
    monkeypatch.setattr(ext, "available", lambda provider: "/bin/codex")
    payload = {
        "models": [
            {"slug": "gpt-5.6-terra", "display_name": "GPT-5.6-Terra", "visibility": "list"},
            {"slug": "gpt-reserve", "display_name": "GPT-Reserve", "visibility": "hide"},
        ]
    }
    monkeypatch.setattr(
        ext.subprocess,
        "run",
        lambda argv, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload)),
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    out = ext.list_models("codex")
    assert out == {
        "ok": True,
        "source": "Codex CLI catalog",
        "models": [{"id": "gpt-5.6-terra", "name": "GPT-5.6-Terra"}],
    }

    # A broken catalog (or a missing CLI) falls through to the API-key path/refusal.
    monkeypatch.setattr(
        ext.subprocess,
        "run",
        lambda argv, **kwargs: (_ for _ in ()).throw(OSError("boom")),
    )
    out = ext.list_models("codex")
    assert out["ok"] is False and "OPENAI_API_KEY" in out["error"]


def test_codex_models_need_an_api_key_and_filter_non_chat_ids(monkeypatch):
    monkeypatch.setattr(ext, "_codex_cli_models", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    assert ext.list_models("codex")["ok"] is False

    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setattr(
        ext,
        "_urlopen",
        lambda request, timeout=0: _Resp(
            {"data": [{"id": "gpt-5.6-codex"}, {"id": "text-embedding-3-small"}, {"id": "o3"}]}
        ),
    )
    out = ext.list_models("codex")
    assert out["ok"] is True
    assert [m["id"] for m in out["models"]] == ["gpt-5.6-codex", "o3"]

    assert ext.list_models("nope")["ok"] is False


# -- the manager path ----------------------------------------------------------------


class _Provider:
    def complete(self, *, model, messages, tools=None, **settings):
        from harness.llm.providers import AssistantTurn

        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        from harness.llm.providers import ModelCapabilities

        return ModelCapabilities()


# -- install / auth markers (Settings ▸ Subagents) -----------------------------------


def test_auth_status_reads_env_then_credential_files(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ext, "_CRED_FILES", {"claude-code": (str(tmp_path / "creds.json"),)}
    )
    for env in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env, raising=False)

    out = ext.auth_status("claude-code")
    assert out["signed_in"] is False and "Sign in" in out["detail"]

    (tmp_path / "creds.json").write_text("{}", encoding="utf-8")
    out = ext.auth_status("claude-code")
    assert out["signed_in"] is True and "creds.json" in out["detail"]

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    out = ext.auth_status("claude-code")
    assert out["signed_in"] is True and "ANTHROPIC_API_KEY" in out["detail"]


def test_login_and_install_argv_use_the_clis_own_commands(monkeypatch):
    monkeypatch.setattr(ext.shutil, "which", lambda name: f"/bin/{name}")
    assert ext.login_argv("codex") == ["/bin/codex", "login"]
    assert ext.login_argv("claude-code") == ["/bin/claude", "setup-token"]
    assert ext.login_argv("nope") is None
    assert ext.install_argv("codex") == ["/bin/npm", "install", "-g", "@openai/codex"]
    assert ext.install_argv("claude-code")[-1] == "@anthropic-ai/claude-code"

    monkeypatch.setattr(ext.shutil, "which", lambda name: None)
    assert ext.login_argv("codex") is None
    assert ext.install_argv("codex") is None


def test_preflight_requires_a_claude_signin(monkeypatch, tmp_path):
    monkeypatch.setattr(ext, "CLAUDE_CREDENTIALS", str(tmp_path / "missing.json"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    problem = ext.preflight("claude-code")
    assert problem and "sign in" in problem.lower()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert ext.preflight("claude-code") is None

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    creds = tmp_path / "credentials.json"
    creds.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "t",
                    "refreshToken": "r",
                    "refreshTokenExpiresAt": 4102444800000,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ext, "CLAUDE_CREDENTIALS", str(creds))
    assert ext.preflight("claude-code") is None

    creds.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "t",
                    "refreshToken": "r",
                    "refreshTokenExpiresAt": 1,
                }
            }
        ),
        encoding="utf-8",
    )
    problem = ext.preflight("claude-code")
    assert problem and "expired" in problem


def test_preflight_reads_codex_login_status(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setattr(ext, "available", lambda provider: "/bin/codex")

    monkeypatch.setattr(
        ext.subprocess,
        "run",
        lambda argv, **kwargs: SimpleNamespace(
            returncode=0, stdout="Logged in using ChatGPT", stderr=""
        ),
    )
    assert ext.preflight("codex") is None

    monkeypatch.setattr(
        ext.subprocess,
        "run",
        lambda argv, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="Not logged in"
        ),
    )
    problem = ext.preflight("codex")
    assert problem and "codex login" in problem

    monkeypatch.setattr(ext, "available", lambda provider: None)
    problem = ext.preflight("codex")
    assert problem and "not installed" in problem


def test_external_agents_reports_install_and_auth_markers(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        ext, "available", lambda provider: "/bin/claude" if provider == "claude-code" else None
    )
    monkeypatch.setattr(
        ext,
        "auth_status",
        lambda provider: {
            "signed_in": provider == "claude-code",
            "detail": f"marker for {provider}",
        },
    )
    mgr = SessionManager(workspace=tmp_path, provider=_Provider())

    rows = {row["provider"]: row for row in mgr.external_agents()}
    assert rows["codex"] == {
        "provider": "codex",
        "installed": False,
        "path": "",
        "signed_in": False,
        "detail": "marker for codex",
    }
    assert rows["claude-code"]["installed"] is True
    assert rows["claude-code"]["path"] == "/bin/claude"
    assert rows["claude-code"]["signed_in"] is True


def test_external_agent_action_spawns_login_or_install_detached(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(ext, "login_argv", lambda provider: ["/bin/codex", "login"])
    monkeypatch.setattr(
        ext, "install_argv", lambda provider: ["/bin/npm", "install", "-g", "@openai/codex"]
    )
    spawned: list[list[str]] = []
    monkeypatch.setattr(ext.subprocess, "Popen", lambda argv, **kwargs: spawned.append(argv))
    mgr = SessionManager(workspace=tmp_path, provider=_Provider())

    assert mgr.external_agent_action("codex", "login") == {
        "ok": True,
        "started": True,
        "command": "/bin/codex login",
    }
    out = mgr.external_agent_action("codex", "install")
    assert out["ok"] is True and spawned[-1][:2] == ["/bin/npm", "install"]

    bad = mgr.external_agent_action("codex", "dance")
    assert bad["ok"] is False and "unknown action" in bad["error"]

    monkeypatch.setattr(ext, "login_argv", lambda provider: None)
    out = mgr.external_agent_action("codex", "login")
    assert out["ok"] is False and "not available" in out["error"]


def test_list_rows_carry_the_provider_for_external_agents(tmp_path, monkeypatch):
    from harness.tools import task_agents

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        task_agents,
        "external_agent_infos",
        lambda: {
            "codex": task_agents.AgentInfo(
                name="codex",
                description="external codex",
                mode=task_agents.MODE_SUBAGENT,
                provider="codex",
                native=True,
            )
        },
    )
    mgr = SessionManager(workspace=tmp_path, provider=_Provider())

    row = next(r for r in mgr.list_subagents() if r["name"] == "codex")
    assert row["provider"] == "codex"
    assert row["native"] is True


def test_the_manager_spawns_an_external_child_and_returns_its_answer(
    tmp_path, monkeypatch
):
    from harness.session.sessions import SessionRecord
    from harness.tools import task_agents

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        task_agents,
        "external_agent_infos",
        lambda: {
            "codex": task_agents.AgentInfo(
                name="codex",
                description="external codex",
                mode=task_agents.MODE_SUBAGENT,
                provider="codex",
                native=True,
            )
        },
    )
    monkeypatch.setattr(
        ext,
        "run_external",
        lambda provider, **kwargs: {
            "status": "completed",
            "report": "EXTERNAL ANSWER",
            "tokens": 12,
        },
    )
    monkeypatch.setattr(ext, "preflight", lambda provider: None)
    mgr = SessionManager(workspace=tmp_path, provider=_Provider())
    parent = SessionRecord(
        session_id="parent-1",
        workspace=str(mgr.default_workspace or tmp_path),
        model="gpt-5.5",
        mode="interactive",
        messages=[{"role": "user", "content": "hi"}],
        title="p",
        agent="reeve",
    )
    mgr.session_store.save(parent)

    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id="parent-1",
            description="Do the thing",
            prompt="delegate this",
            agent_name="codex",
        )
    )
    assert out["status"] == "completed"
    assert "EXTERNAL ANSWER" in out["report"]
    assert out["tokens"] == 12
    child = mgr.session_store.load(out["task_id"])
    assert child.kind == "subagent"
    assert child.messages[-2] == {"role": "user", "content": "delegate this"}
    assert child.messages[-1]["content"] == "EXTERNAL ANSWER"
    assert not mgr.is_running(out["task_id"])
    assert mgr._external_stops == {}


def test_a_signed_out_external_agent_is_refused_before_a_child_exists(
    tmp_path, monkeypatch
):
    from harness.session.sessions import SessionRecord
    from harness.tools import task_agents

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        task_agents,
        "external_agent_infos",
        lambda: {
            "codex": task_agents.AgentInfo(
                name="codex",
                description="external codex",
                mode=task_agents.MODE_SUBAGENT,
                provider="codex",
                native=True,
            )
        },
    )
    monkeypatch.setattr(ext, "preflight", lambda provider: f"{provider} is not signed in")
    called: list[str] = []
    monkeypatch.setattr(
        ext, "run_external", lambda provider, **kwargs: called.append(provider) or {}
    )
    mgr = SessionManager(workspace=tmp_path, provider=_Provider())
    parent = SessionRecord(
        session_id="parent-1",
        workspace=str(mgr.default_workspace or tmp_path),
        model="gpt-5.5",
        mode="interactive",
        messages=[{"role": "user", "content": "hi"}],
        title="p",
        agent="reeve",
    )
    mgr.session_store.save(parent)

    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id="parent-1",
            description="Do the thing",
            prompt="delegate this",
            agent_name="codex",
        )
    )
    assert out == {"error": "codex is not signed in"}
    assert not called


def test_an_external_child_runs_on_the_requested_bare_model(tmp_path, monkeypatch):
    """A model override for a CLI child is a BARE id from the CLI's own catalog: it is
    stored on the child row and reaches `run_external(model=...)`, whose `build_argv`
    forwards it as `--model`. A provider prefix is refused because the CLI IS the
    provider."""
    from harness.session.sessions import SessionRecord
    from harness.tools import task_agents

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        task_agents,
        "external_agent_infos",
        lambda: {
            "codex": task_agents.AgentInfo(
                name="codex",
                description="external codex",
                mode=task_agents.MODE_SUBAGENT,
                provider="codex",
                native=True,
            )
        },
    )
    monkeypatch.setattr(ext, "preflight", lambda provider: None)
    seen: dict = {}

    def fake_run(provider, **kwargs):
        seen.update(kwargs)
        return {"status": "completed", "report": "OK", "tokens": 0}

    monkeypatch.setattr(ext, "run_external", fake_run)
    mgr = SessionManager(workspace=tmp_path, provider=_Provider())
    parent = SessionRecord(
        session_id="parent-1",
        workspace=str(mgr.default_workspace or tmp_path),
        model="gpt-5.5",
        mode="interactive",
        messages=[{"role": "user", "content": "hi"}],
        title="p",
        agent="reeve",
    )
    mgr.session_store.save(parent)

    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id="parent-1",
            description="Do the thing",
            prompt="delegate this",
            agent_name="codex",
            model="gpt-5.6-luna",
        )
    )
    assert out["status"] == "completed"
    assert seen["model"] == "gpt-5.6-luna"
    assert mgr.session_store.load(out["task_id"]).model == "gpt-5.6-luna"

    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id="parent-1",
            description="Do the thing again",
            prompt="delegate this",
            agent_name="codex",
            model="openai:gpt-5.6-luna",
        )
    )
    assert "bare model id" in out["error"]
