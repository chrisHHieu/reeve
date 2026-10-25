"""Replay regression: a real transcript through the current pipeline.

The drift this catches is the kind unit tests miss: the projection still runs, it just
disagrees with what the session recorded — a sidecar no longer stripped, a compaction
boundary that moved, an event the log no longer covers. Synthetic fixtures pin the
mechanics; the repo's own sessions are replayed when they are present.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.replay_sessions import main, replay

REPO = Path(__file__).resolve().parents[1]


def _write_session(directory: Path, session: str = "s1") -> tuple[Path, Path]:
    """A transcript and the event log that recorded it, in the real formats."""
    transcript = directory / f"{session}.jsonl"
    events = directory / f"{session}.events.jsonl"
    messages = [
        {"role": "system", "content": "you are Reeve"},
        {"role": "user", "content": "read the notes"},
        {
            "role": "assistant",
            "content": "on it",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": json.dumps({"path": "notes.txt"})},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "hello", "name": "read_file"},
        {"role": "assistant", "content": "the file says hello"},
    ]
    transcript.write_text(
        "\n".join(json.dumps(m) for m in messages) + "\n", encoding="utf-8"
    )
    rows = [
        {"type": "request/header", "reason": "initial", "system": "you are Reeve", "tools": []},
        {"type": "user/message", "turn": 1, "content": "read the notes"},
        {"type": "assistant/message", "turn": 1, "step": 1, "text": "on it"},
        {"type": "tool/call", "turn": 1, "step": 1, "call_id": "c1", "name": "read_file",
         "arguments": json.dumps({"path": "notes.txt"})},
        {"type": "tool/result", "turn": 1, "step": 1, "call_id": "c1", "content": "hello",
         "is_error": False},
        {"type": "assistant/message", "turn": 1, "step": 2, "text": "the file says hello"},
    ]
    events.write_text(
        "\n".join(json.dumps({"seq": i, "t": 1.0, **r}) for i, r in enumerate(rows)) + "\n",
        encoding="utf-8",
    )
    return transcript, events


def test_a_covered_session_replays_clean(tmp_path):
    transcript, _events = _write_session(tmp_path)
    result = replay(transcript)
    assert result.ok, (result.error, result.violations)
    assert result.messages == 5 and result.outbound == 5
    assert result.estimated_tokens > 0 and result.trigger_tokens > 0
    assert result.has_event_log


def test_an_unlogged_message_is_a_violation(tmp_path):
    """The drift case the tool exists for: the transcript grew a message the log never
    recorded (a path that appended to history without logging it)."""
    transcript, events = _write_session(tmp_path)
    with open(transcript, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"role": "assistant", "content": "a message nobody logged"}) + "\n")
    result = replay(transcript)
    assert not result.ok
    assert any("assistant message" in v for v in result.violations), result.violations


def test_a_missing_event_log_is_reported_not_failed(tmp_path):
    """Old state is not broken state: a session from before the log existed replays, says
    what it could not check, and does not fail the run."""
    transcript, events = _write_session(tmp_path)
    events.unlink()
    result = replay(transcript)
    assert result.error == "no event log beside the transcript (older session?)"
    assert result.outbound == 5, "the projection still ran"
    assert main(["--file", str(transcript)]) == 0


def test_the_cli_fails_on_a_violation_and_prints_it(tmp_path, capsys):
    transcript, _events = _write_session(tmp_path)
    with open(transcript, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"role": "user", "content": "also unlogged"}) + "\n")
    assert main(["--file", str(transcript)]) == 1
    out = capsys.readouterr().out
    assert "BAD" in out and "violation" in out


def test_the_cli_json_report_is_machine_readable(tmp_path, capsys):
    transcript, _events = _write_session(tmp_path)
    assert main(["--file", str(transcript), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)  # one document, nothing appended
    assert payload["failed"] == 0
    assert payload["sessions"][0]["session"] == "s1"
    assert payload["sessions"][0]["outbound"] == 5


def test_the_repo_replays_its_own_sessions_when_they_are_present():
    """The real corpus, when this checkout has one. Skipped (not failed) on a fresh clone:
    the point is to run it where real sessions exist, which is where drift shows up."""
    directory = REPO / ".reeve" / "conversations"
    if not directory.is_dir() or not list(directory.glob("*.jsonl")):
        return
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "replay_sessions.py"), "--dir", str(directory), "--limit", "5"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
