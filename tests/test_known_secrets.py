"""A secret the process holds is never written into a log or shown to a model (2026-10-15: an agent
read the server token from its file and pasted it into curl commands, so it sat in the session log
and the transcript; 64 hex characters, which no shape-based redaction can tell from a git sha)."""

from __future__ import annotations

import json

from harness.config import known_secrets
from harness.llm.providers import ToolCall
from harness.loop.message_shapes import tool_result_message
from harness.safety.secrets.store import SecretStore
from harness.session.session_log import SessionLog

TOKEN = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"  # invented: 64 hex characters, the server token's shape
KEY = "test-key-0123456789abcdef"


def test_the_server_token_leaves_the_model_view_and_the_log(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_API_TOKEN", TOKEN)
    call = ToolCall(id="c1", name="run_shell", arguments={"command": "cat sidecar-8765.token"})
    message = tool_result_message(call, {"output": TOKEN + "\n"}, rendered=f"{TOKEN}\n[exit 0]")
    assert TOKEN not in message["content"]
    assert "$REEVE_API_TOKEN" in message["content"]  # how a command can still use it

    log = SessionLog(tmp_path / "events.jsonl")
    log.append("tool/call", call_id="c1", name="run_shell", arguments=f'curl -H "x-reeve-token: {TOKEN}" …')
    log.append("tool/result", call_id="c1", name="run_shell", facts={"note": TOKEN})
    text = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert TOKEN not in text
    assert json.loads(text.splitlines()[0])["arguments"].startswith('curl -H "x-reeve-token: [REDACTED')


def test_a_stored_credential_is_redacted_but_its_url_is_not(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.put("provider:example", {"api_key": KEY, "base_url": "https://api.example.invalid/v1"})
    log = SessionLog(tmp_path / "events.jsonl")
    log.append("tool/result", call_id="c2", name="run_shell", content=f"using {KEY} at https://api.example.invalid/v1")
    row = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8"))
    assert KEY not in row["content"]
    assert "https://api.example.invalid/v1" in row["content"]


def test_a_credential_read_back_in_a_later_process_is_held_too(tmp_path):
    SecretStore(tmp_path / "secrets.json").put("p", {"token": KEY})
    known_secrets.forget_all()  # a fresh process has held nothing yet
    assert known_secrets.redact(KEY) == KEY
    SecretStore(tmp_path / "secrets.json").get("p")
    assert known_secrets.redact(f"x {KEY} y") == "x [REDACTED] y"


def test_short_values_and_ordinary_text_are_left_alone():
    known_secrets.remember({"password": "hunter2"})  # too short to replace safely
    known_secrets.remember({"region": "eu-west-1-long-name"})  # not kept under a credential's name
    assert known_secrets.redact("hunter2 in eu-west-1-long-name") == "hunter2 in eu-west-1-long-name"
