"""A tool result says what happened in data, and is bounded once.

Three counts in `harness_review` were read from sentences written for the model, and within an
hour of going live two of them were wrong: `exit 7` counted as a lost status, and a `wc` the
model ran on a spill file counted as that file's second owner. The same turn showed a page
cut twice by two layers that did not know each other: two files, and a footer that counted
only the first cut. Scripted providers and fake responses only.
"""

from __future__ import annotations

from aisuite.agents import ToolMetadata

import asyncio
import importlib.util
import json
from pathlib import Path

from harness.loop import ApprovalOutcome, TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.tools.contract import FACT_CHARS, FACT_KEYS, FULL_CHARS, LOCATOR, SHOWN_CHARS, ToolContract, safe_facts
from harness.tools.retention import BODY_MAX_CHARS, RESULT_CEILING_CHARS
from harness.tools.spill import SpillStore
from harness.integrations.web import fetch as fetch_mod

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("harness_review_facts", REPO / "scripts" / "harness_review.py")
harness_review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness_review)  # type: ignore[union-attr]


# -- the contract --------------------------------------------------------------------------


def test_facts_are_scalars_short_and_few():
    contract = ToolContract(
        facts=lambda arguments, value: {
            "exit_code": 3, "ended": "reported", "cut": True, "ratio": 0.5,
            "nothing": None, "page": "p" * 5_000, "rows": [1, 2, 3], "nested": {"a": 1},
            **{f"k{i}": i for i in range(40)},
        }
    )
    facts = safe_facts(contract, "t", {}, {})
    assert facts["exit_code"] == 3 and facts["ended"] == "reported" and facts["cut"] is True
    assert "nothing" not in facts and "rows" not in facts and "nested" not in facts
    assert len(facts["page"]) == FACT_CHARS
    assert len(facts) == FACT_KEYS


def test_a_facts_declaration_that_raises_or_returns_junk_costs_nothing():
    def boom(arguments, value):
        raise RuntimeError("bad presenter")

    assert safe_facts(ToolContract(facts=boom), "t", {}, {}) == {}
    assert safe_facts(ToolContract(facts=lambda a, v: "not a dict"), "t", {}, {}) == {}
    assert safe_facts(ToolContract(), "t", {}, {}) == {}
    assert safe_facts(None, "t", {}, {}) == {}


# -- the engine ----------------------------------------------------------------------------


class Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


class _Root:
    def __init__(self, path):
        self.path, self.label, self.writable = str(path), "scratch", True


def run_tool(tmp_path, func, contract=None, *, calls=1):
    registry = ToolRegistry()
    registry.register(func, contract=contract, metadata=ToolMetadata())
    name = func.__name__
    turns = [
        AssistantTurn(
            text="", finish_reason="tool_calls",
            tool_calls=[ToolCall(id=f"c{i}", name=name, arguments={}) for i in range(calls)],
        ),
        AssistantTurn(text="done", finish_reason="stop"),
    ]
    engine = TurnEngine(
        provider=Scripted(turns),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        spill=SpillStore.under([_Root(tmp_path)]),
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")

    async def _run():
        return [e async for e in engine.run("go")]

    asyncio.run(_run())
    return engine


def rows(engine, kind):
    return [e for e in engine.log.read() if e.get("type") == kind]


def spilled_files(tmp_path):
    return sorted(p.name for p in tmp_path.rglob("*.log"))


def test_a_result_that_reports_its_own_failure_is_an_error_on_the_row(tmp_path):
    """The dispatch returned, so `status` is ok; the call did not do what was asked, so
    `is_error` is true. Counted 2026-10-06: 44 such rows were logged as successes."""
    def refuses() -> dict:
        return {"error": "MCP error: invalid arguments"}

    (row,) = rows(run_tool(tmp_path / "a", refuses), "tool/result")
    assert row["status"] == "ok" and row["is_error"] is True

    def works() -> dict:
        return {"error": "", "rows": 3}

    (row,) = rows(run_tool(tmp_path / "b", works), "tool/result")
    assert row["is_error"] is False

    def fails_by_exit() -> dict:
        # A non-zero exit is data, not a failed call: the tool ran what it was asked to.
        return {"exit_code": 1, "output": "error: 2 tests failed"}

    (row,) = rows(run_tool(tmp_path / "c", fails_by_exit), "tool/result")
    assert row["is_error"] is False

    def text() -> str:
        return "Error: looks like one, and is a string a renderer wrote"

    (row,) = rows(run_tool(tmp_path / "d", text), "tool/result")
    assert row["is_error"] is False


def test_the_screen_is_told_what_the_log_is_told(tmp_path):
    """A refusal the tool returned read "ok" on screen, live and on reload, while the log
    counted it a failure (2026-10-13: a message to a workflow run). The live frame carries
    `is_error`, the stored message `_failed`, and no provider is sent the mark."""
    from harness.llm.wire import wire_message

    def refuses() -> dict:
        return {"error": "6ab9 is a workflow run: nothing was sent"}

    def works() -> dict:
        return {"rows": 3}

    for func, failed in ((refuses, True), (works, False)):
        registry = ToolRegistry()
        registry.register(func, metadata=ToolMetadata())
        engine = TurnEngine(
            provider=Scripted([
                AssistantTurn(text="", finish_reason="tool_calls",
                              tool_calls=[ToolCall(id="c0", name=func.__name__, arguments={})]),
                AssistantTurn(text="done", finish_reason="stop"),
            ]),
            registry=registry,
            permissions=PermissionEngine(workspace_root=tmp_path),
            model="gpt-5.5",
            spill=SpillStore.under([_Root(tmp_path)]),
        )
        engine.log = SessionLog(tmp_path / f"{func.__name__}.jsonl")

        async def _run():
            return [e async for e in engine.run("go")]

        events = asyncio.run(_run())
        (finished,) = [e.data for e in events if e.type.value == "tool_finished"]
        assert finished["status"] == "ok"
        assert finished.get("is_error", False) is failed
        (message,) = [m for m in engine.messages if m.get("role") == "tool"]
        assert message.get("_failed", False) is failed
        assert "_failed" not in (wire_message(message) or {})


def test_a_failure_with_no_renderer_reads_as_text_like_every_other(tmp_path):
    """It reached the model as `{"error": …}` while rendered tools said `Error: …` (82 such
    results since 2026-09-25). A field that only repeats the error is not said twice."""
    def goal_like() -> dict:
        return {"error": "done refused: the check has not passed", "next": "run pytest", "unmet": ["tests"]}

    engine = run_tool(tmp_path / "a", goal_like)
    content = next(m for m in engine.messages if m.get("role") == "tool")["content"]
    assert content == 'Error: done refused: the check has not passed\nnext: run pytest\nunmet: ["tests"]'

    def blocked_like() -> dict:
        return {"error": "tool call blocked by read-before-edit: a.py was never read", "reason": "a.py was never read", "rule": "read-before-edit"}

    engine = run_tool(tmp_path / "b", blocked_like)
    content = next(m for m in engine.messages if m.get("role") == "tool")["content"]
    assert content == "Error: tool call blocked by read-before-edit: a.py was never read"

    def fine() -> dict:
        return {"rows": 3}

    engine = run_tool(tmp_path / "c", fine)
    assert next(m for m in engine.messages if m.get("role") == "tool")["content"] == '{"rows": 3}'


def test_a_call_that_did_not_run_says_so_as_text():
    from harness.loop.message_shapes import tool_error_message as _tool_error_message
    from harness.llm.providers import ToolCall

    message = _tool_error_message(ToolCall(id="c", name="write_file", arguments={}), "denied by the user")
    assert message["content"] == "Error: tool call not executed: denied by the user"


def test_what_a_tool_declares_is_on_its_result_row(tmp_path):
    def probe() -> dict:
        return {"exit_code": 4, "ended": "reported", "output": "boom"}

    contract = ToolContract(
        render=lambda arguments, value: f"{value['output']}\n[exit {value['exit_code']}]",
        facts=lambda arguments, value: {"exit_code": value["exit_code"], "ended": value["ended"]},
    )
    engine = run_tool(tmp_path, probe, contract)
    (row,) = rows(engine, "tool/result")
    assert row["facts"] == {"exit_code": 4, "ended": "reported"}


def test_a_tool_that_declares_nothing_has_no_facts_until_a_bound_makes_some(tmp_path):
    def small() -> str:
        return "fine"

    (row,) = rows(run_tool(tmp_path, small), "tool/result")
    assert "facts" not in row


def test_a_second_cut_names_the_file_the_tool_already_wrote(tmp_path):
    """One file per result. The tool cut its page and saved the whole of it; the ceiling
    then has to cut what was rendered, and must point at that file, not write the cut text
    to another."""
    own = tmp_path / "page-1.log"
    own.write_text("the whole page", encoding="utf-8")
    body = "word " * 30_000  # 150k characters: over the per-result ceiling on its own

    def fetch() -> dict:
        return {"text": body, "full_output": str(own), "page_chars": 400_000}

    contract = ToolContract(
        render=lambda arguments, value: value["text"],
        facts=lambda arguments, value: {LOCATOR: value["full_output"], FULL_CHARS: value["page_chars"]},
    )
    engine = run_tool(tmp_path, fetch, contract)

    assert spilled_files(tmp_path) == ["page-1.log"]  # nothing new was written
    (budget,) = rows(engine, "tool/result/budget")
    assert budget["spilled"] == str(own) and budget["reused"] is True
    (row,) = rows(engine, "tool/result")
    assert row["facts"][LOCATOR] == str(own)
    assert row["facts"][FULL_CHARS] == 400_000
    shown = next(m for m in engine.messages if m.get("role") == "tool")["content"]
    assert row["facts"][SHOWN_CHARS] == len(shown) <= RESULT_CEILING_CHARS
    # One statement of the size, and it is the size of the whole thing.
    assert "it is 400,000 characters in all" in shown
    assert f"Full output saved to: {own}" in shown


def test_a_tool_that_kept_its_output_inside_both_bounds_is_not_cut_again(tmp_path):
    """3,000 short lines through a tool that bounds itself: the engine's line ceiling has
    nothing left to do, so the model reads one statement about the cut, not three."""
    from harness.tools.retention import retain

    def listing() -> dict:
        kept = retain("".join(f"LINE-{i}" + chr(10) for i in range(1, 3001)), budget=20_000)
        return {"output": kept.text, "omitted_lines": kept.omitted_lines}

    contract = ToolContract(
        render=lambda arguments, value: value["output"] + chr(10) + "[exit 0; truncated, "
        + str(value["omitted_lines"]) + " lines omitted]",
    )
    engine = run_tool(tmp_path, listing, contract)
    assert rows(engine, "tool/result/budget") == []
    shown = next(m for m in engine.messages if m.get("role") == "tool")["content"]
    assert shown.count("omitted from the middle") == 1 and "The tool call succeeded but" not in shown


def test_a_result_with_no_file_of_its_own_gets_exactly_one(tmp_path):
    def dump() -> str:
        return "x" * 130_000

    engine = run_tool(tmp_path, dump)
    assert len(spilled_files(tmp_path)) == 1
    (budget,) = rows(engine, "tool/result/budget")
    assert "reused" not in budget
    (row,) = rows(engine, "tool/result")
    assert row["facts"][LOCATOR] == budget["spilled"]
    assert row["facts"][FULL_CHARS] >= 130_000
    shown = next(m for m in engine.messages if m.get("role") == "tool")["content"]
    assert f"it is {row['facts'][FULL_CHARS]:,} characters in all" in shown


# -- web_fetch: one bound ------------------------------------------------------------------


class _Resp:
    def __init__(self, body: bytes):
        self.status_code = 200
        self.content = body
        self.headers = {"content-type": "text/plain; charset=utf-8"}
        self.encoding = "utf-8"
        self.extensions: dict = {}

    def iter_bytes(self):
        yield self.content

    def close(self) -> None:
        pass


LONG_PAGE = ("A paragraph of a long paper, with its numbers and its caveats.\n\n" * 1_800).encode()


def test_the_most_a_fetch_may_return_fits_under_the_ceiling(monkeypatch, tmp_path):
    """Asked for 100,000 characters the tool cut the page to that, and the engine cut it
    again to 50,000: two files, and a footer saying 10,124 characters were missing from a
    page the model had been shown less than half of."""
    monkeypatch.setattr(fetch_mod, "get_checked", lambda client, u, **kw: _Resp(LONG_PAGE))
    tool = fetch_mod.make_web_fetch_tool(SpillStore(tmp_path))

    out = tool("https://papers.example.com/long", max_chars=100_000)
    rendered = fetch_mod.WEB_FETCH_CONTRACT.render({}, out)

    assert len(out["text"]) <= BODY_MAX_CHARS < RESULT_CEILING_CHARS
    assert len(rendered) <= RESULT_CEILING_CHARS  # the engine's ceiling has nothing to cut
    assert out["truncated"] and len(spilled_files(tmp_path)) == 1
    facts = fetch_mod.WEB_FETCH_CONTRACT.facts({}, out)
    assert facts[LOCATOR] == out["full_output"]
    assert facts[FULL_CHARS] == len(LONG_PAGE.decode()) and facts[SHOWN_CHARS] == len(out["text"])
    # One statement of where this text sits in the page (a position to read
    # on from, where it used to be a count of what the two ends left out).
    assert f"characters 0–{out['end']:,} of {facts[FULL_CHARS]:,}" in rendered
    assert f"next: start={out['end']}" in rendered


def test_the_schema_promises_what_is_delivered():
    text = fetch_mod._SCHEMA["function"]["parameters"]["properties"]["max_chars"]["description"]
    assert f"max {BODY_MAX_CHARS}" in text and "100000" not in text


async def _approve(request):
    return ApprovalOutcome.ONCE


def test_a_fetch_through_the_engine_is_cut_once_and_keeps_one_file(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_mod, "get_checked", lambda client, u, **kw: _Resp(LONG_PAGE))
    spill = SpillStore.under([_Root(tmp_path)])
    tool = fetch_mod.make_web_fetch_tool(spill)
    registry = ToolRegistry()
    registry.register(tool, metadata=ToolMetadata())
    engine = TurnEngine(
        provider=Scripted([
            AssistantTurn(
                text="", finish_reason="tool_calls",
                tool_calls=[ToolCall(id="c1", name="web_fetch", arguments={
                    "url": "https://papers.example.com/long", "format": "full", "max_chars": 100_000,
                })],
            ),
            AssistantTurn(text="done", finish_reason="stop"),
        ]),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        spill=spill,
        approver=_approve,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")

    async def _run():
        return [e async for e in engine.run("read it")]

    asyncio.run(_run())

    (result,) = rows(engine, "tool/result")
    assert result["status"] == "ok", result["content"][:200]
    assert rows(engine, "tool/result/budget") == []
    assert len(spilled_files(tmp_path)) == 1
    (row,) = rows(engine, "tool/result")
    assert row["facts"][FULL_CHARS] == len(LONG_PAGE.decode())
    assert Path(row["facts"][LOCATOR]).name == spilled_files(tmp_path)[0]


def test_an_answer_names_its_reader_and_how_much_of_the_page_went_unread():
    whole = {"format": "answer", "status_code": 200, "text": "42", "answered_by": "m", "page_chars": 90_000,
             "page_parts": 2, "full_output": "/w/.reeve/output/page-3.log"}
    facts = fetch_mod.WEB_FETCH_CONTRACT.facts({}, whole)
    assert facts["reader"] == "m" and facts["page_unread"] == 0 and facts["page_parts"] == 2
    # The saved PAGE is not the answer's complete output: a bound must not point there.
    assert LOCATOR not in facts and facts["page_file"].endswith("page-3.log")

    holed = {**whole, "page_chars": 1_900_000, "page_cut_to": 1_400_000}
    assert fetch_mod.WEB_FETCH_CONTRACT.facts({}, holed)["page_unread"] == 500_000


# -- the review reads facts ----------------------------------------------------------------


def _log(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "s.events.jsonl"
    path.write_text("\n".join(json.dumps({"seq": i, "t": 1.0, **e}) for i, e in enumerate(events)), encoding="utf-8")
    return path


OLD_SENTENCE = "[the command's shell exited before it reported a status — the output above is everything it printed.]"


def test_a_lost_status_is_a_status_nobody_has_not_a_sentence(tmp_path):
    def shell(content, **facts):
        row = {"type": "tool/result", "name": "run_shell", "call_id": content[:6], "content": content}
        return {**row, "facts": facts} if facts else row

    log = _log(tmp_path, [
        # `exit 7`: no trailer, the process's own code. Nothing is lost.
        shell("(no output)\n[exit 7; cwd /w]", exit_code=7, ended="ended"),
        # The model printing the old sentence itself (a grep over this repo) is not a loss.
        shell(f"docs/audit.md: {OLD_SENTENCE}\n[exit 0; cwd /w]", exit_code=0, ended="reported"),
        # Killed from outside: no trailer and no code.
        shell("partial\n[exit unknown; cwd /w]", ended="ended"),
        shell("partial\n[exit unknown; cwd /w]", ended="held"),
        # A row from before facts existed is read the only way it can be.
        shell(f"(no output)\n[exit 0; cwd /w]\n{OLD_SENTENCE}"),
    ])
    counts, refs = harness_review.review([log])
    assert counts["shell-results"] == 5 and counts["lost-exit-status"] == 3
    assert [ref[1] for ref in refs["lost-exit-status"]] == [2, 3, 4]


def test_a_spill_file_has_two_owners_only_when_two_results_were_told_it_is_theirs(tmp_path):
    where = "C:\\w\\.reeve\\output\\"
    log = _log(tmp_path, [
        {"type": "tool/result", "name": "run_shell", "call_id": "a", "content": "…", "facts": {"locator": where + "shell-6.log"}},
        {"type": "tool/call", "name": "web_fetch", "call_id": "b", "arguments": json.dumps({"url": "https://x.example/one"})},
        {"type": "tool/result", "name": "web_fetch", "call_id": "b", "content": "…", "facts": {"page_file": where + "page-7.log"}},
        # The model runs `wc` on the spill file: the path is at the end of its output.
        {"type": "tool/result", "name": "run_shell", "call_id": "c", "content": "…", "facts": {"exit_code": 0, "ended": "reported"}},
        # Another page told that the first page's file is its own: the defect this counts.
        {"type": "tool/call", "name": "web_fetch", "call_id": "d", "arguments": json.dumps({"url": "https://x.example/two"})},
        {"type": "tool/result", "name": "web_fetch", "call_id": "d", "content": "…", "facts": {"locator": where + "page-7.log"}},
    ])
    transcript = [
        {"role": "tool", "tool_call_id": "a", "ts": 1.0, "content": f"rows [full output: {where}shell-6.log]"},
        {"role": "tool", "tool_call_id": "c", "ts": 1.0, "content": f"20001 {where}shell-6.log\n[exit 0; cwd /w]"},
    ]
    (tmp_path / "s.jsonl").write_text("\n".join(json.dumps(m) for m in transcript), encoding="utf-8")

    counts, refs = harness_review.review([log])

    assert counts["spill-locators"] == 3 and counts["locator-reuse"] == 1
    assert refs["locator-reuse"][0][2] == "page-7.log"


def test_a_page_with_a_part_unread_is_counted_from_what_the_tool_declared(tmp_path):
    log = _log(tmp_path, [
        {"type": "tool/result", "name": "web_fetch", "call_id": "a", "content": "answer", "facts": {"reader": "m", "page_unread": 0}},
        {"type": "tool/result", "name": "web_fetch", "call_id": "b", "content": "answer", "facts": {"reader": "m", "page_unread": 500_000}},
        {"type": "tool/result", "name": "web_fetch", "call_id": "c", "content": "the page", "facts": {"format": "markdown"}},
    ])
    counts, _ = harness_review.review([log])
    assert counts["reader-pages"] == 2 and counts["reader-cut-pages"] == 1
