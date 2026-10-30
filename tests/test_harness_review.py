"""`scripts/harness_review.py`: each detector counts its shape and nothing else."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("harness_review", REPO / "scripts" / "harness_review.py")
harness_review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness_review)  # type: ignore[union-attr]


def _log(tmp_path: Path, events: list[dict], name: str = "s") -> Path:
    path = tmp_path / f"{name}.events.jsonl"
    path.write_text("\n".join(json.dumps({"seq": i, **e}) for i, e in enumerate(events)), encoding="utf-8")
    return path


def test_each_shape_is_counted_once_and_passing_work_is_not(tmp_path):
    def call(cid, name, args):
        return {"type": "tool/call", "call_id": cid, "name": name, "arguments": json.dumps(args)}

    def result(cid, name, content):
        return {"type": "tool/result", "call_id": cid, "name": name, "content": content}

    log = _log(tmp_path, [
        call("a", "run_shell", {"command": "pytest -q | tail -3"}),
        result("a", "run_shell", "1 failed, 2 passed in 0.1s\n[exit 0; cwd /w]"),
        call("b", "run_shell", {"command": "pytest -q"}),
        result("b", "run_shell", "3 passed in 0.1s\n[exit 0; cwd /w]"),
        call("c", "replace_in_file", {"path": "a.py"}),
        result("c", "replace_in_file", "Error: Expected 1 replacement(s), found 0: a.py"),
        call("d", "replace_in_file", {"path": "a.py"}),
        result("d", "replace_in_file", '{"error": "tool call blocked", "rule": "read-before-edit"}'),
        {"type": "turn/end", "reason": "completed_unverified"},
        {"type": "assistant/message", "t": 1, "usage": {"cache_read": 50_000, "input": 10}},
        {"type": "assistant/message", "t": 2, "usage": {"cache_read": 1_000, "input": 49_000}},
        {"type": "assistant/message", "t": 3, "usage": {"cache_read": 60_000, "input": 10}},
        {"type": "assistant/message", "t": 900, "usage": {"cache_read": 100, "input": 60_000}},
    ])
    counts, refs = harness_review.review([log])
    assert counts["checks"] == 2
    assert counts["masked-checks"] == 1 and counts["piped-checks"] == 1
    assert counts["edit-misses"] == 1 and counts["edit-misses retried at once"] == 1
    assert counts["gate-blocks"] == 1 and refs["gate-blocks"][0][2] == "replace_in_file:read-before-edit"
    assert counts["raw-json-errors"] == 1
    assert counts["unverified-ends"] == 1
    assert counts["cache-drops unexplained"] == 1
    assert counts["cache-drops idle"] == 1  # the last one followed a 15-minute gap


def test_a_cache_drop_is_explained_by_another_model_and_by_its_routes_own_lifetime(tmp_path):
    def step(t, model, read, fresh=10):
        return {"type": "assistant/message", "t": t, "model": model, "usage": {"cache_read": read, "input": fresh}}

    log = _log(tmp_path, [
        step(1, "opencode-go:deepseek-v4.1-flash", 50_000),
        step(5, "zai-coding-plan:glm-5.3", 0, 52_000),           # another model: its own cache
        step(9, "zai-coding-plan:glm-5.3", 55_000),
        step(190, "zai-coding-plan:glm-5.3", 0, 56_000),          # 3 minutes on a 5-minute route
        step(200, "vertex:gemini-3.8-flash", 0, 57_000),          # switch again (model, not idle)
        step(205, "vertex:gemini-3.8-flash", 58_000),
        step(385, "vertex:gemini-3.8-flash", 0, 59_000),          # 3 minutes on Gemini: past its life
    ])
    counts, _ = harness_review.review([log])
    assert counts["cache-drops"] == 4
    assert counts["cache-drops model"] == 2
    assert counts["cache-drops idle"] == 1
    assert counts["cache-drops unexplained"] == 1


def test_the_gateway_wires_that_shared_one_routing_session_are_priced_by_what_missed_the_cache(tmp_path):
    """OpenCode Go's Messages, Responses and Gemini wires sent one routing session for every
    conversation until 2026-10-21. The store keeps the catalogue that names each model's
    wire beside its logs; only steps on those wires are counted, chat and other routes are not."""
    (tmp_path / "model-facts.json").write_text(json.dumps({"version": 9, "entries": {
        "opencode-go:qwen3.8-flash": {"wire": "messages"},
        "opencode-go:gemini-4-argon-contributor": {"wire": "gemini"},
        "opencode-go:deepseek-v4.1-flash": {},
    }}), encoding="utf-8")
    conversations = tmp_path / "conversations"
    conversations.mkdir()

    def step(t, model, read, fresh):
        return {"type": "assistant/message", "t": t, "model": model, "usage": {"cache_read": read, "input": fresh}}

    log = _log(conversations, [
        step(1, "opencode-go:qwen3.8-flash", 0, 10_000),
        step(2, "opencode-go:qwen3.8-flash", 9_000, 1_000),
        step(3, "opencode-go:gemini-4-argon-contributor", 6_000, 2_000),
        step(4, "opencode-go:deepseek-v4.1-flash", 0, 50_000),      # the chat wire
        step(5, "anthropic:claude-haiku-5-5", 0, 50_000),           # another route
    ])
    counts, _ = harness_review.review([log])
    assert counts["gateway-wire prompt tokens"] == 28_000
    assert counts["gateway-wire uncached tokens"] == 13_000


def test_a_task_that_ended_on_the_subagent_cap_is_counted_and_a_queued_one_is_not(tmp_path):
    """A call past the cap is queued; one that still ends on the cap (a
    background call, a program's) is a slot refusal. Read from the fact, and from the text on
    rows written before the fact existed."""

    def result(cid, content, facts=None):
        return {"type": "tool/result", "call_id": cid, "name": "task", "content": content, **({"facts": facts} if facts else {})}

    log = _log(tmp_path, [
        result("a", '<task id="" state="queued">in line</task>', {"outcome": "queued"}),
        result("b", '4 subagents are already running for this session and "subagent_max_parallel" is 4.', {"outcome": "cap"}),
        # Before facts: the refusal's own words.
        result("c", '4 subagents are already running for this session and "subagent_max_parallel" is 4 and none finished in 840 s.'),
        result("d", "the report", {}),
    ])
    counts, refs = harness_review.review([log])
    assert counts["slot-refusals"] == 2
    assert [ref[1] for ref in refs["slot-refusals"]] == [1, 2]


def test_a_step_that_went_without_its_own_thinking_is_counted_by_cause_and_a_model_switch_is_not(tmp_path):
    # A block the harness or the API left out is reasoning the model no longer has.
    log = _log(tmp_path, [
        {"type": "assistant/message", "t": 1},
        {"type": "thinking/dropped", "stale": 2},
        {"type": "assistant/message", "t": 2},
        {"type": "thinking/dropped", "api": 1, "retried": 3},
        {"type": "assistant/message", "t": 3},
        {"type": "thinking/dropped", "other_model": 4},
    ])
    counts, refs = harness_review.review([log])
    assert counts["thinking-drops"] == 2 and counts["steps"] == 3
    assert [ref[2] for ref in refs["thinking-drops"]] == ["stale", "api+retried"]
    assert counts["thinking blocks dropped stale"] == 2 and counts["thinking blocks dropped retried"] == 3


def test_a_stale_thought_left_out_of_every_later_request_is_one_loss_not_one_a_step(tmp_path):
    # The store's first reading: one session's 3-13 stale blocks, re-sent without on every step,
    # counted as 314 steps that lost thinking. A row that says `stale_new` is read by it; an older
    # row by how far `stale` rose in its log.
    older = _log(tmp_path, [
        {"type": "assistant/message", "t": 1},
        {"type": "thinking/dropped", "stale": 3},
        {"type": "assistant/message", "t": 2},
        {"type": "thinking/dropped", "stale": 3},
        {"type": "assistant/message", "t": 3},
        {"type": "thinking/dropped", "stale": 5},
    ], "older")
    newer = _log(tmp_path, [
        {"type": "assistant/message", "t": 1},
        {"type": "thinking/dropped", "stale": 2, "stale_new": 2},
        {"type": "assistant/message", "t": 2},
        {"type": "thinking/dropped", "stale": 2, "stale_new": 0},
        {"type": "assistant/message", "t": 3},
        {"type": "thinking/dropped", "stale": 1, "stale_new": 1},
    ], "newer")
    counts, refs = harness_review.review([older, newer])
    assert counts["thinking-drops"] == 4
    assert counts["thinking blocks dropped stale"] == 3 + 2 + 2 + 1
    assert counts["thinking blocks re-dropped stale"] == (3 + 3) + 2


def test_a_cache_drop_after_a_prune_is_the_harness_own(tmp_path):
    # A prune rewrites tool results the cached prefix holds: the drop after it is not the provider's.
    def step(t, read, fresh=10):
        return {"type": "assistant/message", "t": t, "model": "opencode-go:deepseek-v4.1-flash", "usage": {"cache_read": read, "input": fresh}}

    log = _log(tmp_path, [
        step(1, 50_000),
        {"type": "compaction/prune", "reason": "rederivable", "saved_tokens": 9_000},
        step(5, 0, 42_000),
        step(9, 44_000),
        step(13, 0, 45_000),  # nothing between: the provider's own miss
    ])
    counts, _ = harness_review.review([log])
    assert counts["cache-drops compaction/prune"] == 1
    assert counts["cache-drops unexplained"] == 1


# -- the shapes added from the harness-optimisation papers (2026-10-04) ------------------


def _step(turn, names, *, t=10.0, tokens=1_000):
    return {
        "type": "assistant/message", "turn": turn, "t": t,
        "tool_calls": [{"name": n} for n in names],
        "usage": {"input": tokens, "cache_read": 0},
    }


def _named(tmp_path: Path, name: str, events: list[dict]) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(json.dumps({"seq": i, **e}) for i, e in enumerate(events)), encoding="utf-8")
    return path


def test_a_bookkeeping_step_is_counted_by_where_it_sat_and_work_is_not(tmp_path):
    log = _log(tmp_path, [
        {"type": "turn/start", "turn": 1, "t": 1.0},
        _step(1, ["todo_write"]),                    # opens the turn with the plan
        _step(1, ["todo_write", "read_file"]),       # the list beside work: not bookkeeping
        _step(1, ["grep"]),
        {"type": "todo/nudge", "turn": 1, "t": 11.0},
        _step(1, ["todo_write"]),                    # the nudge answered by a step of its own
        _step(1, ["read_file"]),
        _step(1, ["todo_write", "todo_write"]),      # on its own, mid-work
        _step(1, ["read_file"]),
        _step(1, ["todo_write"]),                    # closes the list…
        _step(1, []),                                # …then the answer
        {"type": "turn/end", "turn": 1, "reason": "completed", "t": 12.0},
    ])
    counts, refs = harness_review.review([log])
    assert counts["steps"] == 9
    assert counts["bookkeeping-steps"] == 4
    assert counts["bookkeeping-steps opening the turn"] == 1
    assert counts["bookkeeping-steps after a nudge"] == 1
    assert counts["bookkeeping-steps on their own, mid-work"] == 1
    assert counts["bookkeeping-steps closing the list before the answer"] == 1
    assert counts["nudge-alone"] == 1 and counts["todo-nudges"] == 1
    assert counts["tokens re-read by bookkeeping-steps"] == 4_000
    assert counts["tokens re-read by steps"] == 9_000


def test_a_nudge_is_only_called_toolless_when_the_header_says_so(tmp_path):
    def header(names):
        return {"type": "request/header", "t": 1.0, "tools": [{"function": {"name": n}} for n in names]}

    nudge = {"type": "todo/nudge", "t": 2.0}
    counts, _ = harness_review.review([_named(tmp_path, "a.events.jsonl", [header(["todo_write", "grep"]), nudge])])
    assert counts["todo-nudges"] == 1 and counts["nudge-no-tool"] == 0

    counts, _ = harness_review.review([_named(tmp_path, "b.events.jsonl", [header(["grep"]), nudge])])
    assert counts["nudge-no-tool"] == 1

    # No header at all is "unknown", which is not the same as "not offered".
    counts, _ = harness_review.review([_named(tmp_path, "c.events.jsonl", [nudge])])
    assert counts["todo-nudges"] == 1 and counts["nudge-no-tool"] == 0


def test_a_wake_that_did_nothing_is_idle_and_one_that_worked_is_not(tmp_path):
    def wake(turn):
        return {"type": "user/message", "turn": turn, "t": 5.0, "source": {"synthetic": "background_task"}}

    log = _log(tmp_path, [
        {"type": "turn/start", "turn": 1, "t": 5.0}, wake(1), _step(1, []),
        {"type": "turn/start", "turn": 2, "t": 6.0}, wake(2), _step(2, ["read_file"]), _step(2, []),
        {"type": "turn/start", "turn": 3, "t": 7.0},
        {"type": "user/message", "turn": 3, "t": 7.0}, _step(3, []),  # a person: not a wake
    ])
    counts, _ = harness_review.review([log])
    assert counts["wake-turns"] == 2 and counts["idle-wakes"] == 1


def test_a_task_that_went_to_the_background_unasked_and_one_that_asked(tmp_path):
    def task(cid, args, status):
        return [
            {"type": "tool/call", "t": 1.0, "call_id": cid, "name": "task", "arguments": json.dumps(args)},
            {"type": "tool/result", "t": 2.0, "call_id": cid, "name": "task", "content": "…",
             "delegation": {"run_id": cid, "status": status}},
        ]

    log = _log(tmp_path, [
        *task("a", {"prompt": "read"}, "running"),                       # moved without asking
        *task("b", {"prompt": "read", "background": True}, "running"),   # asked
        *task("c", {"prompt": "read"}, "completed"),                     # waited for
    ])
    counts, _ = harness_review.review([log])
    assert counts["task-calls"] == 3 and counts["unasked-background"] == 1


def test_an_argument_refusal_names_the_tool_and_the_parameter(tmp_path):
    log = _log(tmp_path, [
        {"type": "tool/result", "t": 1.0, "name": "web_fetch", "content":
            "Error: Invalid arguments for `web_fetch`: unexpected 'max_results'. Accepted parameters: url"},
        {"type": "tool/result", "t": 1.0, "name": "grep", "content": "3 matches for 'Invalid arguments'"},
    ])
    counts, refs = harness_review.review([log])
    assert counts["invalid-arguments"] == 1
    assert refs["invalid-arguments"][0][2] == "web_fetch:max_results"


def test_a_goal_set_by_the_model_is_counted_and_a_status_read_is_not(tmp_path):
    log = _log(tmp_path, [
        {"type": "tool/call", "t": 1.0, "name": "goal", "arguments": json.dumps({"action": "set", "objective": "x"})},
        {"type": "tool/call", "t": 1.0, "name": "goal", "arguments": json.dumps({"action": "status"})},
    ])
    counts, _ = harness_review.review([log])
    assert counts["goal-set"] == 1


def test_a_turn_number_the_log_already_used_is_counted_once_per_reuse(tmp_path):
    log = _log(tmp_path, [
        {"type": "turn/start", "turn": 1, "t": 1.0},
        {"type": "turn/start", "turn": 2, "t": 2.0},
        {"type": "turn/start", "turn": 1, "t": 3.0},  # an engine was rebuilt and counted again
        {"type": "turn/start", "turn": 2, "t": 4.0},
        {"type": "turn/start", "turn": 3, "t": 5.0},
    ])
    counts, _ = harness_review.review([log])
    assert counts["turn-starts"] == 5 and counts["turn-reuse"] == 2


def test_a_spill_path_given_to_two_results_is_a_reuse_and_one_owner_is_not(tmp_path):
    log = _log(tmp_path, [{"type": "turn/start", "turn": 1, "t": 1.0}])
    where = str(Path("w") / ".reeve" / "output")

    def cut(name):
        return f"text … [full output: {where}{chr(92)}{name}]"

    transcript = [
        {"role": "tool", "tool_call_id": "a", "ts": 1.0, "content": cut("page-1.log")},
        {"role": "tool", "tool_call_id": "b", "ts": 2.0, "content": cut("page-2.log")},
        {"role": "tool", "tool_call_id": "c", "ts": 3.0, "content": cut("page-1.log")},
        # A path in the MIDDLE of output the model asked for is not a locator.
        {"role": "tool", "tool_call_id": "d", "ts": 4.0,
         "content": ".reeve/output/page-2.log" + " listing" * 200},
    ]
    (tmp_path / "s.jsonl").write_text("\n".join(json.dumps(m) for m in transcript), encoding="utf-8")
    counts, refs = harness_review.review([log])
    assert counts["spill-locators"] == 3 and counts["locator-reuse"] == 1
    assert refs["locator-reuse"][0][2] == "page-1.log"


def test_a_shell_result_that_lost_its_status_is_counted_and_a_reported_one_is_not(tmp_path):
    log = _log(tmp_path, [
        {"type": "tool/result", "t": 1.0, "name": "run_shell", "content":
            "(no output)\n[exit 0; cwd /w]\n[the command's shell exited before it reported a status — the output above is everything it printed.]"},
        {"type": "tool/result", "t": 1.0, "name": "run_shell", "content": "done2\n[exit 0; cwd /w]"},
        {"type": "tool/result", "t": 1.0, "name": "read_file", "content": "exited before it reported a status"},
    ])
    counts, _ = harness_review.review([log])
    assert counts["shell-results"] == 2 and counts["lost-exit-status"] == 1


def test_a_page_the_reader_saw_whole_and_one_it_saw_with_a_hole(tmp_path):
    log = _log(tmp_path, [{"type": "turn/start", "turn": 1, "t": 1.0}])
    tail = "[https://x.example/p · HTTP 200 · answered from the page by opencode-go:deepseek-v4.1-flash ({size}); you were not shown the page itself]"
    transcript = [
        {"role": "tool", "tool_call_id": "a", "ts": 1.0, "content": "answer " + tail.format(size="63,531 characters")},
        {"role": "tool", "tool_call_id": "b", "ts": 2.0,
         "content": "answer " + tail.format(size="103,440 characters, the middle left out past 79,999")},
        # Since 2026-10-04 a long page is read in parts: whole is whole, however many reads.
        {"role": "tool", "tool_call_id": "p", "ts": 2.5, "content": "answer " + tail.format(size="103,440 characters, read in 2 parts")},
        {"role": "tool", "tool_call_id": "q", "ts": 2.6,
         "content": "answer " + tail.format(size="1,900,000 characters, read in 6 parts, only 1,400,000 of them read")},
        # The model quoting that sentence back in a file it read is not a fetch.
        {"role": "tool", "tool_call_id": "c", "ts": 3.0,
         "content": "answered from the page by x (9 characters, the middle left out past 1)" + " filler" * 400},
    ]
    (tmp_path / "s.jsonl").write_text("\n".join(json.dumps(m) for m in transcript), encoding="utf-8")
    counts, _ = harness_review.review([log])
    assert counts["reader-pages"] == 4 and counts["reader-cut-pages"] == 2


def test_a_fold_made_mid_edit_and_a_write_refused_after_a_cut_are_counted(tmp_path):
    """Both read the engine's own account: the state on `compaction/start`, and the
    rule a refusal names."""
    def step(tokens):
        return {"type": "assistant/message", "t": 5.0, "tool_calls": [{"name": "fold_context"}, {"name": "run_shell"}],
                "usage": {"input": tokens, "cache_read": 0, "output": 100}}

    def fold(**state):
        return [
            {"type": "compaction/start", "t": 5.0, "author": "model", **state},
            {"type": "compaction/summary", "t": 5.0, "reason": "folded"},
        ]

    log = _log(tmp_path, [
        step(60_000), *fold(unsettled="unverified"), step(20_000), step(20_000),
        step(60_000), *fold(), step(20_000), step(20_000),
        step(60_000), *fold(unsettled="check_failed"), step(20_000), step(20_000),
        {"type": "tool/call", "call_id": "w", "name": "write_file", "arguments": json.dumps({"path": "a.py"})},
        {"type": "tool/result", "call_id": "w", "name": "write_file", "t": 5.0,
         "content": '{"error": "tool call blocked", "rule": "out-of-view"}'},
    ])
    counts, refs = harness_review.review([log], keep_all="fold-unsettled")
    assert counts["folds"] == 3 and counts["fold-unsettled"] == 2
    assert sorted(ref[2] for ref in refs["fold-unsettled"]) == ["check_failed", "unverified"]
    assert counts["gate-blocks"] == 1 and refs["gate-blocks"][0][2] == "write_file:out-of-view"


def test_what_the_memory_gate_refused_and_what_was_saved_are_counted(tmp_path):
    """The rule a refusal names, through either door, beside the writes that stood."""
    where = "C:/state/projects/D-Project-x/memory/"

    def write(cid, name, target, content):
        return [
            {"type": "tool/call", "call_id": cid, "name": name, "arguments": json.dumps({"path": where + target})},
            {"type": "tool/result", "call_id": cid, "name": name, "t": 5.0, "content": content},
        ]

    log = _log(tmp_path, [
        *write("a", "write_file", "vendor.md", "tool call blocked by memory-anchor: vendor.md was not saved: a `decision` names the code it is about"),
        *write("b", "write_file", "status.md", '{"error": "tool call blocked by memory-kind: …", "rule": "memory-kind"}'),
        *write("c", "write_file", "deploys.md", "Wrote deploys.md"),
        *write("d", "replace_in_file", "MEMORY.md", "Replaced 1 occurrence"),
        # A write somewhere else is not a memory, and a failed one saved nothing.
        {"type": "tool/call", "call_id": "e", "name": "write_file", "arguments": json.dumps({"path": "D:/ws/a.py"})},
        {"type": "tool/result", "call_id": "e", "name": "write_file", "t": 5.0, "content": "Wrote a.py"},
        *write("f", "write_file", "x.md", "Error: disk full"),
        # A shell command's write, put back.
        {"type": "tool/call", "call_id": "g", "name": "run_shell", "arguments": json.dumps({"command": "cat > x.md"})},
        {"type": "tool/result", "call_id": "g", "name": "run_shell", "t": 5.0,
         "content": '{"error": "tool call blocked by memory-anchor: the call ran, but …", "rule": "memory-anchor"}'},
        # Read back: a note counts, an index does not.
        *write("h", "read_file", "deploys.md", "1  ---"),
        *write("i", "read_file", "MEMORY.md", "1  - [Deploys](deploys.md)"),
        *write("j", "read_file", "MEMORY.legacy.md", "1  - [Old](old.md)"),
    ])
    counts, refs = harness_review.review([log], keep_all="memory-refused")
    assert counts["memory-refused"] == 3 and counts["memory writes saved"] == 2
    assert sorted(ref[2] for ref in refs["memory-refused"]) == ["memory-anchor", "memory-anchor", "memory-kind"]
    assert counts["memory notes read"] == 1


def test_a_fold_is_judged_by_the_steps_that_ran_on_it(tmp_path):
    """Provider counts only. A fold spares `(before - after)` on every step that follows and
    costs its note, plus the whole request when the step did nothing but fold."""
    def fold(seq_t):
        return {"type": "compaction/summary", "t": seq_t, "reason": "folded"}

    def step(names, tokens, *, out=0):
        return {
            "type": "assistant/message", "t": 5.0, "tool_calls": [{"name": n} for n in names],
            "usage": {"input": tokens, "cache_read": 0, "output": out},
        }

    paid = _named(tmp_path, "paid.events.jsonl", [
        step(["fold_context", "todo_write"], 90_000, out=3_000), fold(5.0),
        step(["run_shell"], 25_000), step(["run_shell"], 27_000), step([], 30_000),
    ])
    # The same fold with one step after it: it spared 25k and cost a 50k request.
    unpaid = _named(tmp_path, "unpaid.events.jsonl", [
        step(["fold_context"], 50_000, out=1_600), fold(5.0), step([], 25_000),
    ])
    # Made beside real work, the step would have happened anyway: only the note is spent.
    beside = _named(tmp_path, "beside.events.jsonl", [
        step(["fold_context", "run_shell"], 50_000, out=1_600), fold(5.0), step([], 25_000),
    ])
    last = _named(tmp_path, "last.events.jsonl", [step(["fold_context"], 50_000), fold(5.0)])
    # A compaction the harness ran is not a fold, and ends the stretch a fold is counted over.
    other = _named(tmp_path, "other.events.jsonl", [
        step(["fold_context"], 50_000, out=1_600), fold(5.0), step([], 25_000),
        {"type": "compaction/summary", "t": 6.0, "reason": "summarized"},
        step([], 9_000), step([], 9_000), step([], 9_000), step([], 9_000),
    ])

    counts, refs = harness_review.review([paid, unpaid, beside, last, other], keep_all="fold-unpaid")

    assert counts["folds"] == 5 and counts["fold-unpaid"] == 3
    assert sorted(ref[0] for ref in refs["fold-unpaid"]) == ["last.events.jsonl", "other.events.jsonl", "unpaid.events.jsonl"]
    assert counts["tokens spared by folds"] == 65_000 * 3 + 25_000 * 3
    assert counts["tokens spent on folds"] == (90_000 + 3_000) + (50_000 + 1_600) + 1_600 + (50_000 + 1_600)


def test_a_fold_hint_for_an_item_is_counted_when_no_item_was_left_to_start(tmp_path):
    def todos(*statuses):
        return {"type": "todo/write", "t": 1.0, "todos": [{"content": "x", "status": s} for s in statuses]}

    def hint(moment):
        return {"type": "context/budget", "t": 2.0, "hint": "fold", "moment": moment, "used": 50_000, "foldable": 30_000}

    log = _log(tmp_path, [
        todos("done", "in_progress", "pending"), hint("item"),  # work not yet started remains
        todos("done", "done", "in_progress"), hint("item"),     # only the item under way: the report
        todos("done", "done", "done"), hint("item"),            # nothing left but the answer
        hint("turn"), hint("late"),                             # other moments are not about the list
        {"type": "context/budget", "t": 2.0, "used": 50_000, "band": 1},  # a budget line, no hint
    ])
    counts, refs = harness_review.review([log], keep_all="fold-hint-nothing-open")
    assert counts["fold-item-hints"] == 3 and counts["fold-hint-nothing-open"] == 2
    assert [ref[1] for ref in refs["fold-hint-nothing-open"]] == [3, 5]


def test_a_fold_hint_is_counted_when_a_fold_could_not_halve_the_request(tmp_path):
    def hint(used, foldable, moment="turn"):
        return {"type": "context/budget", "t": 2.0, "hint": "fold", "moment": moment, "used": used, "foldable": foldable}

    log = _log(tmp_path, [
        hint(46_313, 15_467),            # the real one: a third of the request
        hint(80_997, 45_183),            # more than half
        hint(50_011, 30_961, "item"),
        hint(60_000, 29_999, "late"),    # just under half, whatever the moment
    ])
    counts, refs = harness_review.review([log], keep_all="fold-hint-small")
    assert counts["fold-hints"] == 4 and counts["fold-hint-small"] == 2
    assert [ref[2] for ref in refs["fold-hint-small"]] == ["turn", "late"]


def test_a_fold_that_took_a_step_of_its_own_and_notes_past_their_budget_are_counted(tmp_path):
    def step(names, tokens):
        return {"type": "assistant/message", "t": 5.0, "tool_calls": [{"name": n} for n in names],
                "usage": {"input": tokens, "cache_read": 0, "output": 1_500}}

    def fold(chars):
        return {"type": "compaction/summary", "t": 5.0, "reason": "folded", "summary_chars": chars}

    log = _log(tmp_path, [
        step(["fold_context"], 80_000), fold(6_724), step(["run_shell"], 25_000),                  # a step of its own
        step(["fold_context", "todo_write"], 60_000), fold(13_051), step(["run_shell"], 30_000),  # the list is not work
        step(["fold_context", "web_search"], 70_000), fold(18_467), step([], 28_000),             # beside work; notes too large
    ])
    counts, refs = harness_review.review([log])
    assert counts["folds"] == 3 and counts["fold-own-step"] == 2
    assert counts["notes-over-budget"] == 1 and refs["notes-over-budget"][0][2] == "18,467 characters"


def test_a_cut_is_judged_by_what_the_block_before_it_held(tmp_path):
    """The transcript's `compacted` notices carry each block's memory: what the
    earlier one held and the later one does not is lost. Since parts, a row is held to their
    budget and not to the notes' one."""
    def notice(summary, ts):
        return {"role": "notice", "kind": "compacted", "summary": summary, "ts": ts}

    first = "Renamed `fetch_record` in t1/store.py; flag --max-retries is 7; ticket 48213."
    kept = first + "\n\n### Part 2\nWrote t1/CHANGELOG.md."
    rewritten = "Renamed it in the store. Wrote t1/CHANGELOG.md."
    log = _log(tmp_path, [
        {"type": "compaction/summary", "t": 5.0, "reason": "folded", "summary_chars": 40_000,
         "parts": 4, "parts_tokens": 9_000, "parts_budget": 16_384, "merged": 1},
        {"type": "compaction/summary", "t": 6.0, "reason": "summarized", "summary_chars": 9_000,
         "parts": 5, "parts_tokens": 17_000, "parts_budget": 16_384},
    ])
    transcript = log.with_name(log.name.replace(".events.jsonl", ".jsonl"))
    transcript.write_text("\n".join(json.dumps(m) for m in [
        notice(first, 1.0), notice(kept, 2.0), notice(rewritten, 3.0),
    ]), encoding="utf-8")

    counts, refs = harness_review.review([log], keep_all="cut-details-lost")
    # first -> kept loses nothing; kept -> rewritten loses fetch_record, the flag, the ticket, store.py.
    assert counts["cuts after a cut"] == 2 and counts["cut details"] == 4 + 5
    assert counts["cut-details-lost"] == 4 and [r[2] for r in refs["cut-details-lost"]] == ["4 of 5"]
    assert counts["notes-over-budget"] == 0  # a row with parts is not judged by the notes' budget
    assert counts["parts-over-budget"] == 1 and counts["merges of parts"] == 1


def test_a_cut_that_paid_again_for_the_earlier_parts_is_counted(tmp_path):
    """The floor is what the request after the first cut read; a later cut with
    earlier parts that reads no more than that paid for them again. Cold, merged, a model
    switch, or no earlier parts: not judged."""
    def step(read, model="anthropic:claude-sonnet-5-5"):
        return {"type": "assistant/message", "t": 5.0, "model": model, "usage": {"cache_read": read, "input": 3_000}}

    def cut(parts_tokens, merged=0):
        return {"type": "compaction/summary", "t": 5.0, "reason": "folded", "parts_tokens": parts_tokens, "merged": merged}

    log = _log(tmp_path, [
        step(60_000), cut(4_000), step(20_000),         # first cut: the floor is 20k
        step(50_000), cut(9_000), step(24_500),         # 4k of earlier parts read: kept
        step(55_000), cut(13_000), step(21_000),        # 9k earlier, 1k over the floor: paid again
        step(52_000), cut(10_000, merged=1), step(20_000),  # merged: its parts changed
        step(0), cut(14_000), step(20_000),             # cold before: says nothing
        step(40_000), cut(18_000), step(0, model="opencode-go:deepseek-v4.1-flash"),  # a switch
    ])
    counts, refs = harness_review.review([log], keep_all="parts-repriced")
    assert counts["cuts keeping parts"] == 2 and counts["parts-repriced"] == 1
    assert refs["parts-repriced"][0][2] == "read 21,000, floor 20,000, earlier parts ~9,000"


def test_a_compaction_whose_own_price_disagrees_with_the_signal_is_counted(tmp_path):
    def start(before, signal):
        return {"type": "compaction/start", "t": 1.0, "before_tokens": before, "signal": signal}

    log = _log(tmp_path, [
        start(126_669, 90_129),   # 1.41x: priced on the stored rows
        start(17_427, 50_402),    # 0.35x: the ratio at its floor
        start(88_000, 90_000),    # one unit
        start(20_696, 20_696),    # no provider count yet: the signal IS the price
        {"type": "compaction/start", "t": 1.0, "trigger": 380_000},  # a row from before both fields
    ])
    counts, refs = harness_review.review([log])
    assert counts["compaction-starts"] == 4 and counts["price-off"] == 2
    assert [ref[2] for ref in refs["price-off"]] == ["1.41", "0.35"]


def test_a_page_read_from_the_sessions_copy_is_not_a_second_download(tmp_path):
    def call(cid, t, **args):
        return {"type": "tool/call", "t": t, "call_id": cid, "name": "web_fetch",
                "arguments": json.dumps({"url": "https://x.example/p", **args})}

    def result(cid, t, **facts):
        row = {"type": "tool/result", "t": t, "call_id": cid, "name": "web_fetch", "content": "page"}
        return {**row, "facts": facts} if facts else row

    log = _log(tmp_path, [
        call("a", 10.0), result("a", 10.0, format="markdown"),
        # Read on, searched and asked within the quarter hour: the session's copy each time.
        call("b", 60.0, start=20_000), result("b", 60.0, format="markdown", cached=True, start=20_000, shown_chars=9),
        call("c", 70.0, find="method"), result("c", 70.0, format="find", cached=True),
        # A second DOWNLOAD of it inside the quarter hour is the thing counted…
        call("d", 80.0), result("d", 80.0, format="markdown"),
        # …unless a fresh copy was asked for, or it is another page, or the copy had aged out.
        call("e", 90.0, refresh=True), result("e", 90.0, format="markdown"),
        call("f", 95.0, url="https://x.example/other"), result("f", 95.0, format="markdown"),
        call("g", 5_000.0), result("g", 5_000.0, format="markdown"),
        # A fetch that failed downloaded no page.
        call("h", 5_010.0), {"type": "tool/result", "t": 5_010.0, "call_id": "h", "name": "web_fetch",
                             "content": "Error: fetch failed", "is_error": True},
    ])
    counts, refs = harness_review.review([log])
    assert counts["page-fetches"] == 7 and counts["page-refetch"] == 1
    assert refs["page-refetch"][0][1] == 7


def test_a_pages_file_read_by_several_calls_has_one_owner(tmp_path):
    """Within an hour of the page store going live the ledger read REFUTED, 3 hits: one page
    read on three more times, each result naming the page's file, as designed."""
    where = "C:\\w\\.reeve\\output\\"

    def call(cid, **args):
        return {"type": "tool/call", "t": 1.0, "call_id": cid, "name": "web_fetch",
                "arguments": json.dumps({"url": "https://x.example/p", **args})}

    def result(cid, name="web_fetch", **facts):
        return {"type": "tool/result", "t": 1.0, "call_id": cid, "name": name, "content": "…", "facts": facts}

    log = _log(tmp_path, [
        call("a", format="text"), result("a", locator=where + "page-23.log", start=0),
        call("b", format="text", start=47_000), result("b", locator=where + "page-23.log", cached=True, start=47_000),
        call("c", format="text", find="method"), result("c", page_file=where + "page-23.log", cached=True),
        # Another format of the same address is another text, with a file of its own.
        call("d"), result("d", locator=where + "page-24.log", start=0),
        # The real defect: a file of one page handed to a result about something else.
        call("e", url="https://x.example/other", format="text"), result("e", locator=where + "page-23.log", start=0),
        {"type": "tool/call", "t": 1.0, "call_id": "f", "name": "run_shell", "arguments": "{}"},
        result("f", name="run_shell", locator=where + "page-24.log"),
    ])
    counts, refs = harness_review.review([log], keep_all="locator-reuse")
    assert counts["locator-reuse"] == 2
    assert sorted(ref[2] for ref in refs["locator-reuse"]) == ["page-23.log", "page-24.log"]


def test_a_page_returned_as_its_two_ends_is_counted_and_one_read_from_a_position_is_not(tmp_path):
    def result(cid, **facts):
        row = {"type": "tool/result", "t": 1.0, "call_id": cid, "name": "web_fetch", "content": "page"}
        return {**row, "facts": facts} if facts else row

    log = _log(tmp_path, [
        result("old"),                                                     # before facts: read from its text
        result("mid", format="full", shown_chars=47_999, full_chars=109_983),  # facts, cut, no position
        result("new", format="full", shown_chars=19_874, full_chars=109_983, start=0),
        result("ans", format="answer", reader="m", page_unread=0),
        result("whole", format="markdown", full_chars=900),
    ])
    transcript = [
        {"role": "tool", "tool_call_id": "old", "ts": 1.0,
         "content": "text … [https://x.example/p · HTTP 200 · truncated, 10124 chars omitted · full output: /w/.reeve/output/page-8.log]"},
        # The same words in a result that has facts are not read: its facts are.
        {"role": "tool", "tool_call_id": "new", "ts": 1.0, "content": "quoting: · truncated, 5 chars omitted ·"},
    ]
    (tmp_path / "s.jsonl").write_text("\n".join(json.dumps(m) for m in transcript), encoding="utf-8")
    counts, _ = harness_review.review([log])
    assert counts["page-fetches"] == 5 and counts["page-middle-dropped"] == 2


def test_the_window_counts_only_its_rows_but_reads_what_came_before(tmp_path):
    log = _log(tmp_path, [
        {"type": "request/header", "t": 1.0, "tools": [{"function": {"name": "grep"}}]},
        {"type": "tool/call", "t": 2.0, "call_id": "a", "name": "task", "arguments": "{}"},
        {"type": "todo/nudge", "t": 50.0},  # inside: judged by the header from before it
        {"type": "tool/result", "t": 60.0, "call_id": "a", "content": "…",
         "delegation": {"status": "running"}},  # inside: its name comes from the call before
        {"type": "todo/nudge", "t": 500.0},  # after the window
    ])
    counts, _ = harness_review.review([log], since=10.0, until=100.0)
    assert counts["todo-nudges"] == 1 and counts["nudge-no-tool"] == 1
    assert counts["unasked-background"] == 1 and counts["task-calls"] == 0


def test_refs_for_one_detector_are_kept_whole(tmp_path):
    log = _log(tmp_path, [{"type": "turn/start", "turn": 1, "t": float(i)} for i in range(1, 8)])
    counts, refs = harness_review.review([log], keep_all="turn-reuse")
    assert counts["turn-reuse"] == 6 and len(refs["turn-reuse"]) == 6
    _, short = harness_review.review([log])
    assert len(short["turn-reuse"]) == 3


def test_every_detector_names_its_owner():
    assert set(harness_review.OWNER) == set(harness_review.DETECTORS)
    assert all(harness_review.OWNER[name] for name in harness_review.DETECTORS)


def test_a_refused_workflow_is_counted_by_its_facts_and_an_older_one_by_its_sentence(tmp_path):
    def result(cid, content, facts=None):
        row = {"type": "tool/result", "t": 1.0, "call_id": cid, "name": "workflow", "content": content}
        return {**row, "facts": facts} if facts is not None else row

    log = _log(tmp_path, [
        {"type": "turn/start", "turn": 1, "t": 1.0},
        result("a", "import os is not available in a workflow script", {"outcome": "refused", "rule": "script"}),
        result("b", '{"n": 1}', {"outcome": "ok", "agents": 2}),
        result("c", "running", {"outcome": "background", "task_id": "x"}),
        # Before facts (2026-10-05): the refusal's own sentence.
        result("d", "workflow scripts must not import anything (saw import json)"),
        result("e", "Traceback ... NameError: name 'type' is not defined"),
    ])
    counts, refs = harness_review.review([log])
    # "c" is a background run's "running" answer: its runner's own row counts it.
    assert counts["workflow-runs"] == 4 and counts["workflow-refused"] == 2


def test_a_program_that_named_a_missing_tool_is_counted_by_where_it_was_stopped(tmp_path):
    def call(cid):
        return {"type": "tool/call", "t": 1.0, "call_id": cid, "name": "run_code", "arguments": "{}"}

    def result(cid, content, facts=None):
        row = {"type": "tool/result", "t": 1.0, "call_id": cid, "name": "run_code", "content": content}
        return {**row, "facts": facts} if facts is not None else row

    log = _log(tmp_path, [
        {"type": "turn/start", "turn": 1, "t": 1.0},
        call("a"), result("a", "Nothing ran: ...", {"outcome": "unknown_tool", "unknown": 1}),
        call("b"), result("b", "AttributeError: no tool named 'x'. Available: y", {"outcome": "error", "calls": 2}),
        call("c"), result("c", "AttributeError: no tool named 'x'. Available: y"),  # before facts
        call("d"), result("d", "3 files", {"outcome": "ok", "calls": 3}),
        # Another tool quoting the phrase is not a program.
        {"type": "tool/result", "t": 1.0, "call_id": "e", "name": "grep", "content": "no tool named 'x'"},
    ])
    counts, refs = harness_review.review([log])
    assert counts["programs"] == 4 and counts["unknown-tool-programs"] == 3
    assert sorted(ref[2] for ref in refs["unknown-tool-programs"]) == ["at the call", "at the call", "before it ran"]


def test_the_size_of_the_tool_list_is_summed_per_header(tmp_path):
    tools = [{"name": "read_file", "description": "x" * 100}]
    log = _log(tmp_path, [
        {"type": "request/header", "t": 1.0, "tools": tools},
        {"type": "request/header", "t": 2.0, "tools": tools + [{"name": "grep", "description": "y" * 50}],
         "system": "z" * 70},
    ])
    counts, _ = harness_review.review([log])
    assert counts["tool-lists"] == 2 and counts["system chars"] == 70
    assert counts["tool-list chars"] == sum(
        len(json.dumps(sent, ensure_ascii=False)) for sent in (tools, tools + [{"name": "grep", "description": "y" * 50}])
    )


def test_searches_and_describes_of_deferred_tools_are_counted(tmp_path):
    log = _log(tmp_path, [
        {"type": "turn/start", "turn": 1, "t": 1.0},
        {"type": "tool/call", "t": 1.0, "turn": 1, "step": 1, "call_id": "a", "name": "tool_search", "arguments": {}},
        {"type": "tool/call", "t": 1.0, "turn": 1, "step": 2, "call_id": "b", "name": "tool_describe", "arguments": {}},
        {"type": "tool/call", "t": 1.0, "turn": 1, "step": 2, "call_id": "c", "name": "tool_describe", "arguments": {}},
    ])
    counts, _ = harness_review.review([log])
    assert counts["tool-searches"] == 1 and counts["tool-describes"] == 2


def test_a_prune_that_folded_write_payloads_is_counted_with_how_many(tmp_path):
    log = _log(tmp_path, [
        {"type": "turn/start", "turn": 1, "t": 1.0},
        {"type": "compaction/prune", "t": 1.0, "reason": "cache_cold", "arguments_folded": 3},
        {"type": "compaction/end", "t": 1.0, "reason": "pruned", "arguments_folded": 1},
        {"type": "compaction/end", "t": 1.0, "reason": "summarized"},
        {"type": "compaction/prune", "t": 1.0, "reason": "cache_cold"},
    ])
    counts, refs = harness_review.review([log])
    assert counts["argument-folds"] == 2
    assert sorted(ref[2] for ref in refs["argument-folds"]) == ["1", "3"]


def test_a_step_that_only_asks_a_background_task_again_is_counted(tmp_path):
    def step(turn, *names):
        return {"type": "assistant/message", "t": 1.0, "turn": turn,
                "tool_calls": [{"name": name} for name in names]}

    log = _log(tmp_path, [
        step(1, "run_shell"),
        step(1, "shell_task_output"),          # the first look is fine
        step(1, "shell_task_output"),          # asking again: counted
        step(1, "shell_task_output"),          # and again
        step(1, "shell_task_output", "read_file"),  # did other work too: not a poll step
        step(1, "shell_task_output"),
        step(2, "shell_task_output"),          # a new turn starts over
    ])
    counts, _ = harness_review.review([log])
    assert counts["poll-steps"] == 2


def test_an_error_ending_is_counted_by_its_class_and_an_old_one_as_unclassified(tmp_path):
    log = _log(tmp_path, [
        {"type": "turn/end", "t": 1.0, "reason": "error", "error": "bad_request"},
        {"type": "turn/end", "t": 1.0, "reason": "error"},
        {"type": "turn/end", "t": 1.0, "reason": "completed"},
    ])
    counts, refs = harness_review.review([log])
    assert counts["error-ends"] == 2 and counts["turn-ends"] == 3
    assert counts["error-ends bad_request"] == 1 and counts["error-ends unclassified"] == 1
    # A class is a name; no class is not.
    assert counts["unnamed-error-ends"] == 1


def test_a_search_written_as_a_shell_command_and_a_cut_grep_page_are_counted(tmp_path):
    """File searches the shell ran in `grep`'s place, per `grep` call, and `grep`
    pages that stopped short, read from the result's facts. A pipe that filters a command's
    own output is not a file search."""
    log = _log(
        tmp_path,
        [
            {"type": "tool/call", "name": "run_shell", "arguments": {"command": "rg -n needle D:/other"}},
            {"type": "tool/call", "name": "run_shell", "arguments": {"command": 'cd x && Select-String -Path *.py -Pattern a'}},
            {"type": "tool/call", "name": "run_shell", "arguments": {"command": "git log --oneline | grep fix"}},
            {"type": "tool/call", "name": "grep", "arguments": {"pattern": "a"}},
            {"type": "tool/result", "name": "grep", "facts": {"capped": True, "offset": 0}},
            {"type": "tool/call", "name": "grep", "arguments": {"pattern": "b"}},
            {"type": "tool/result", "name": "grep", "facts": {"capped": False, "offset": 0}},
        ],
    )
    counts, _refs = harness_review.review([log])
    assert counts["shell-searches"] == 2 and counts["grep calls"] == 2
    assert counts["grep-capped"] == 1 and counts["grep results"] == 2


def test_a_turn_a_dead_process_left_is_lost_unless_the_next_one_picked_it_up(tmp_path):
    """The synthetic end an adopting engine writes marks a turn the process died in;
    a `turn/resumed` for that turn means it went on."""
    def end(turn, **extra):
        return {"type": "turn/end", "turn": turn, "reason": "interrupted", "t": 10.0, **extra}

    log = _log(tmp_path, [
        {"type": "turn/start", "turn": 1, "t": 1.0},
        end(1, synthetic=True),
        {"type": "turn/resumed", "turn": 1, "attempt": 1, "path": "continue", "t": 11.0},
        {"type": "turn/start", "turn": 2, "t": 12.0},
        end(2, synthetic=True),
        {"type": "turn/start", "turn": 3, "t": 13.0},
        end(3),  # the person pressed Stop: not a process that died
    ])
    counts, refs = harness_review.review([log])
    assert counts["lost-turns"] == 1 and refs["lost-turns"][0][1] == 4
    assert counts["picked-up turns"] == 1


def test_a_design_page_left_with_something_to_fix_is_counted_per_page(tmp_path):
    def check(turn, path, issues, phone=0):
        facts = {"design_checked": True, "design_issues": issues, "design_overflow_phone": phone, "design_behaviour": phone}
        return {"type": "design/check", "turn": turn, "path": path, "facts": facts}

    log = _log(tmp_path, [
        check(1, "design/a.html", 2, phone=1),
        check(1, "design/a.html", 0),          # fixed in the same turn
        check(1, "design/b.html", 1),          # left with one thing to fix
        check(2, "design/a.html", 0),
        {"type": "design/check", "turn": 2, "path": "design/c.html", "facts": {"design_checked": False}},
    ])
    counts, refs = harness_review.review([log])
    assert counts["design pages"] == 3 and counts["design checks"] == 4
    assert counts["design-unfixed"] == 1 and refs["design-unfixed"][0][1] == 2
    assert counts["design first drafts with issues"] == 2
    assert counts["design first drafts off a phone"] == 1
    assert counts["design checks not run"] == 1
    assert counts["design first drafts that misbehave when used"] == 1


def test_a_design_page_whose_last_version_still_has_still_controls_is_counted(tmp_path):
    """Per page and turn, by the last check; a page declared static is not counted."""
    def check(turn, path, dead, static=False):
        facts = {"design_checked": True, "design_issues": 0, "design_dead_clicks": dead, "design_static": static}
        return {"type": "design/check", "turn": turn, "path": path, "facts": facts}

    log = _log(tmp_path, [
        check(1, "design/a.html", 3), check(1, "design/a.html", 0),   # wired before the answer
        check(1, "design/b.html", 1), check(1, "design/b.html", 2),   # still dead at the end
        check(1, "design/c.html", 4, static=True),                    # a picture of a screen
    ])
    counts, _ = harness_review.review([log])
    assert counts["design pages"] == 3 and counts["design pages left with dead clicks"] == 1


def test_a_design_page_drawn_before_the_guide_was_read_is_counted_once(tmp_path):
    def check(turn, path, unguided):
        facts = {"design_checked": True, "design_issues": 0, "design_unguided": unguided}
        return {"type": "design/check", "turn": turn, "path": path, "facts": facts}

    log = _log(tmp_path, [
        check(1, "design/a.html", 1),          # drawn before the guide
        check(1, "design/a.html", 1),          # the same page again, same turn: one page
        check(1, "design/b.html", 0),          # the guide was read by then
    ])
    counts, refs = harness_review.review([log])
    assert counts["design pages"] == 2
    assert counts["design-unguided"] == 1 and refs["design-unguided"][0][1] == 0


def test_an_app_left_unlike_its_design_and_a_reviewed_design_moved_are_counted(tmp_path):
    def compare(turn, page, issues, seq_note=""):
        return {"type": "tool/result", "turn": turn, "name": "compare_design", "call_id": f"c{turn}{page}{issues}",
                "facts": {"compare_checked": True, "compare_issues": issues, "compare_page": page}}

    log = _log(tmp_path, [
        compare(1, "design/a.html", 3),
        compare(1, "design/a.html", 0),   # fixed in the same turn
        compare(1, "design/b.html", 2),   # left unlike its design
        compare(2, "design/a.html", 1),
        {"type": "tool/result", "turn": 2, "name": "compare_design", "facts": {"compare_checked": False, "compare_page": "design/c.html"}},
        {"type": "design/drift", "turn": 3, "name": "edit_file", "pages": ["design/a.html"], "tokens": 1},
    ])
    counts, refs = harness_review.review([log])
    assert counts["design compare runs"] == 4 and counts["design compares"] == 3
    assert counts["compare-unfixed"] == 2
    assert sorted(seq for _, seq in refs["compare-unfixed"]) == [2, 3]
    assert counts["design-drift"] == 1 and refs["design-drift"][0][1] == 5


def test_a_drift_repeated_after_its_note_is_counted_apart_from_one_written_beside_it(tmp_path):
    # The note reaches the model with the step's results: a second drift in the same step was
    # written before it was read; one in a later step of the turn was written after.
    def drift(cid, turn, step):
        return [
            {"type": "tool/call", "call_id": cid, "name": "run_shell", "turn": turn, "step": step,
             "arguments": json.dumps({"command": "python x.py"})},
            {"type": "tool/result", "call_id": cid, "name": "run_shell", "turn": turn, "step": step,
             "content": "No such file or directory", "facts": {"cwd_drift": True}},
        ]

    log = _log(tmp_path, drift("a", 1, 1) + drift("b", 1, 1) + drift("c", 1, 2) + drift("d", 2, 1))
    counts, _ = harness_review.review([log])
    assert counts["cwd-drift"] == 4
    assert counts["cwd-drift-repeats"] == 1  # only `c`: same turn, a later step


def test_the_headers_that_carry_a_progress_log_are_counted_apart(tmp_path):
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    log = _log(tmp_path, [
        {"type": "request/header", "system": "rules\n\nThe progress log of work that spans sessions is p.md (9,000 characters).", "tools": tools},
        {"type": "request/header", "system": "rules and nothing else", "tools": tools},
        {"type": "request/header", "system": "rules\n\nContents of p.md (the progress log of work that spans sessions; resume from it):\n\n- a", "tools": tools},
    ])
    counts, _ = harness_review.review([log])
    assert counts["tool-lists"] == 3
    assert counts["progress headers"] == 2
    assert counts["progress header chars"] == len(
        "rules\n\nThe progress log of work that spans sessions is p.md (9,000 characters)."
    ) + len("rules\n\nContents of p.md (the progress log of work that spans sessions; resume from it):\n\n- a")


def test_a_steer_held_by_the_steps_own_wait_is_counted_and_one_held_by_the_model_is_not(tmp_path):
    """`steer-waits`: a person's message that a shell command of its step held 10 s
    past its sending. One that waited as long on the model's own answer is not this shape."""
    log = _log(tmp_path, [
        {"type": "tool/result", "t": 100, "turn": 1, "step": 1, "name": "run_shell", "content": "ok"},
        {"type": "steering/message", "t": 101, "turn": 1, "step": 1, "text": "x", "waited_ms": 60_000},
        {"type": "tool/result", "t": 200, "turn": 1, "step": 2, "name": "read_file", "content": "ok"},
        {"type": "steering/message", "t": 230, "turn": 1, "step": 2, "text": "y", "waited_ms": 30_000},
        {"type": "steering/message", "t": 300, "turn": 1, "step": 3, "text": "z", "source": {"kind": "hook"}},
    ])
    counts, refs = harness_review.review([log])
    assert counts["person-steers"] == 2
    assert counts["steer-waits"] == 1 and refs["steer-waits"][0][2] == "run_shell 60s"


def test_a_read_of_a_file_the_read_guard_just_refused_is_counted_once(tmp_path):
    """The read the refusal stands for when it shows the file. A refusal from before
    refused calls had a `tool/call` row names its file only in its sentence."""

    def result(cid, content, turn=1):
        return {"type": "tool/result", "call_id": cid, "name": "write_file", "turn": turn, "content": content}

    def read(cid, path, turn=1):
        return {"type": "tool/call", "call_id": cid, "name": "read_file", "turn": turn,
                "arguments": json.dumps({"path": path})}

    log = _log(tmp_path, [
        # the old shape: no tool/call for the refused write
        result("w1", "Error: tool call blocked by stale-file: web/src/Map.tsx has changed on disk"),
        read("r1", "D:/p/web/src/Map.tsx"),
        read("r2", "web/src/Map.tsx"),  # a second read of it is not a second count
        # the new shape: the refused call is logged
        {"type": "tool/call", "call_id": "w2", "name": "write_file", "turn": 1,
         "arguments": json.dumps({"path": "notes.md"})},
        result("w2", "Error: tool call blocked by read-before-edit: notes.md already exists"),
        read("r3", "notes.md", turn=2),  # another turn: not the refusal's read
        result("w3", "Error: tool call blocked by out-of-view: what you read of a.py is no longer"),
        read("r4", "other.py"),
    ])
    counts, refs = harness_review.review([log])
    assert counts["gate-blocks"] == 3
    assert counts["gate-block-rereads"] == 1 and refs["gate-block-rereads"][0][1] == 1


def test_a_cut_priced_far_past_the_request_last_sent_is_drift_and_a_distrusted_route_is_counted(tmp_path):
    """The request as sent is the provider's count plus what it said it cleared."""

    def step(prompt, cleared=None):
        row = {"type": "assistant/message", "t": 1, "usage": {"input": 2, "cache_read": prompt, "cache_write": 0}}
        if cleared:
            row["context_cleared"] = {"input_tokens": cleared}
        return row

    def prune(before):
        return {"type": "compaction/prune", "reason": "cache_cold", "before_tokens": before, "after_tokens": before // 2}

    log = _log(tmp_path, [
        step(160_000), prune(320_000),              # twice what was sent: drift
        step(100_000, cleared=100_000), prune(210_000),  # 200k sent, 34k of it cleared first: not drift
        prune(900_000),                              # nothing sent since the last cut: not priced
        {"type": "notice", "kind": "usage_partial", "model": "anthropic-subscription:claude-opus-5-5"},
    ])
    counts, refs = harness_review.review([log])
    assert counts["priced cuts"] == 2
    assert counts["estimate-drift"] == 1 and refs["estimate-drift"][0][2] == "2.00x"
    assert counts["usage-distrust"] == 1
