"""A call that sends another harness's parameter names runs under this one's.

Models trained on other coding agents send `old_string`/`new_string`, `offset`/`limit`/`end_line`
and `head_limit`; each used to be refused and retried, re-reading the whole context. The names are
taken before the call enters the transcript, so history, the log and what runs agree, and the log
keeps what was sent under `renamed`.
"""

from __future__ import annotations

import asyncio
import json

from harness.loop import TurnEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.safety.permissions import PermissionEngine
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.tools.argument_aliases import take_aliases

RAN: list[tuple[str, dict]] = []


def read_file(path: str, start_line: int = 1, max_lines: int = 200) -> str:
    """Read a file."""
    RAN.append(("read_file", {"path": path, "start_line": start_line, "max_lines": max_lines}))
    return "text"


def replace_in_file(path: str, old: str, new: str, expected_replacements: int = 1) -> str:
    """Replace a fragment."""
    RAN.append(("replace_in_file", {"path": path, "old": old, "new": new}))
    return "ok"


def grep(pattern: str, path: str = ".", max_results: int = 50, offset: int = 0) -> str:
    """Search."""
    RAN.append(("grep", {"pattern": pattern, "max_results": max_results, "offset": offset}))
    return "hits"


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)
        self.seen: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.seen.append(messages)
        return self._turns.pop(0) if self._turns else AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _run(tmp_path, calls):
    RAN.clear()
    registry = ToolRegistry()
    for tool in (read_file, replace_in_file, grep):
        registry.register(tool)

    async def approver(request):
        from harness.loop import ApprovalOutcome

        return ApprovalOutcome.ONCE

    provider = ScriptedProvider([AssistantTurn(tool_calls=calls, finish_reason="tool_calls")])
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="x",
        approver=approver,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl", trace="t1")

    async def go():
        return [ev async for ev in engine.run("go")]

    asyncio.run(go())
    return engine, provider


def test_the_other_harnesses_names_run_and_the_log_keeps_what_was_sent(tmp_path):
    engine, provider = _run(
        tmp_path,
        [
            ToolCall(id="c1", name="replace_in_file", arguments={"file_path": "a.py", "old_string": "x", "new_string": "y"}),
            ToolCall(id="c2", name="read_file", arguments={"file_path": "a.py", "offset": 10, "limit": 5}),
            ToolCall(id="c3", name="grep", arguments={"pattern": "todo", "head_limit": 7}),
        ],
    )
    assert ("replace_in_file", {"path": "a.py", "old": "x", "new": "y"}) in RAN
    assert ("read_file", {"path": "a.py", "start_line": 10, "max_lines": 5}) in RAN
    assert ("grep", {"pattern": "todo", "max_results": 7, "offset": 0}) in RAN
    results = [m for m in engine.messages if m.get("role") == "tool"]
    assert results and not any("Invalid arguments" in str(m.get("content")) for m in results)
    # The next request replays the call under this harness's names: history and what ran agree.
    replayed = next(m for m in provider.seen[-1] if m.get("role") == "assistant" and m.get("tool_calls"))
    first = json.loads(replayed["tool_calls"][0]["function"]["arguments"])
    assert first == {"path": "a.py", "old": "x", "new": "y"}
    logged = {e["call_id"]: e for e in engine.log.read() if e["type"] == "tool/call"}
    assert json.loads(logged["c1"]["renamed"]) == {"file_path": "a.py", "old_string": "x", "new_string": "y"}
    assert json.loads(logged["c3"]["renamed"]) == {"head_limit": 7}


def test_end_line_is_counted_from_start_line(tmp_path):
    _run(tmp_path, [ToolCall(id="c1", name="read_file", arguments={"path": "a.py", "start_line": 20, "end_line": 29})])
    assert RAN == [("read_file", {"path": "a.py", "start_line": 20, "max_lines": 10})]


def test_a_name_the_tool_takes_itself_or_a_call_that_has_ours_is_left_alone(tmp_path):
    # grep has an `offset` of its own; a call carrying `old` keeps it and its `old_string` is refused.
    args = {"pattern": "x", "offset": 3}
    assert take_aliases("grep", args, {"pattern", "path", "max_results", "offset"}) == {}
    assert args == {"pattern": "x", "offset": 3}
    both = {"path": "a.py", "old": "x", "old_string": "z", "new": "y"}
    assert take_aliases("replace_in_file", both, {"path", "old", "new", "expected_replacements"}) == {}
    _run(tmp_path, [ToolCall(id="c1", name="replace_in_file", arguments=dict(both))])
    assert RAN == []  # refused, as before


def test_a_tool_outside_the_table_is_never_touched():
    args = {"file_path": "a.py"}
    assert take_aliases("web_fetch", args, None) == {} and args == {"file_path": "a.py"}
