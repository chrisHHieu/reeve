"""Read-before-edit and the version guard.

Two silent destructions this refuses, both of which succeed today: rewriting a file the
agent never read, and editing one that changed since it looked. See
harness/tools/observed.py.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from harness.safety.provenance import resolve
from harness.tools.observed import ObservedFiles, UNVERIFIED
from harness.tools.observed_seed import seed_from_messages
from harness.tools.observed_gate import observation_gate, observation_recorder
from harness.tools.pipeline import BLOCKED, OK, ToolExecution, ToolPipeline, ToolResult


@pytest.fixture
def observed(tmp_path):
    return ObservedFiles(resolve=lambda raw: resolve(raw, tmp_path))


def _locate(name, arguments):
    """The `write_paths` shape, narrowed to what these tests exercise."""
    if name in ("write_file", "replace_in_file"):
        path = arguments.get("path")
        return ([str(path)], True) if path else ([], False)
    if name == "apply_patch":
        paths = [
            line.split(":", 1)[1].strip()
            for line in str(arguments.get("patch") or "").splitlines()
            if line.startswith(("*** Update File:", "*** Add File:", "*** Delete File:"))
        ]
        return (paths, True) if paths else ([], False)
    return ([], False)


# -- the rule ------------------------------------------------------------------------


def test_creating_a_file_is_never_refused(observed, tmp_path):
    """There was nothing to have read. A file that does not exist is not a file the agent
    failed to look at."""
    assert observed.verdict(str(tmp_path / "new.txt")) is None


def test_editing_a_file_it_never_read_is_refused(observed, tmp_path):
    (tmp_path / "config.py").write_text("PORT = 8080\n", encoding="utf-8")

    reason = observed.verdict("config.py")

    assert reason is not None
    # The refusal reaches the model verbatim, so it names the path the MODEL named and
    # says what to do next.
    assert "config.py" in reason
    assert "read it" in reason.lower()


def test_a_file_it_read_may_be_edited(observed, tmp_path):
    (tmp_path / "config.py").write_text("PORT = 8080\n", encoding="utf-8")
    observed.observe("config.py")

    assert observed.verdict("config.py") is None


def test_a_file_that_changed_since_the_read_is_refused(observed, tmp_path):
    target = tmp_path / "config.py"
    target.write_text("PORT = 8080\n", encoding="utf-8")
    observed.observe("config.py")

    target.write_text("PORT = 9090\n", encoding="utf-8")  # a build, a command, the user

    reason = observed.verdict("config.py")
    assert reason is not None
    assert "changed" in reason.lower()


def test_writing_the_file_counts_as_seeing_it(observed, tmp_path):
    """A file the agent just wrote is one whose contents it knows exactly; making it read
    back its own write would be a round-trip to learn what it already said."""
    target = tmp_path / "new.txt"
    target.write_text("first\n", encoding="utf-8")
    observed.observe("new.txt")

    target.write_text("second\n", encoding="utf-8")
    observed.observe("new.txt")

    assert observed.verdict("new.txt") is None


def test_the_same_file_named_three_ways_is_one_entry(observed, tmp_path):
    """`./a.py`, `a.py` and the absolute form must not be three files that disagree —
    which is why the resolver is injected rather than re-spelled here."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    observed.observe("./a.py")

    assert observed.verdict("a.py") is None
    assert observed.verdict(str(tmp_path / "a.py")) is None


# -- what it refuses to judge --------------------------------------------------------


def test_a_file_too_large_to_hash_still_needs_a_read_but_skips_the_comparison(
    observed, tmp_path, monkeypatch
):
    """Fails open on its own ignorance, closed only on knowledge."""
    import harness.tools.observed as module

    monkeypatch.setattr(module, "MAX_DIGEST_BYTES", 4)
    big = tmp_path / "big.bin"
    big.write_bytes(b"0123456789")

    # Never read: still refused — the size says nothing about whether it was looked at.
    assert observed.verdict("big.bin") is not None

    # Observed (with no digest recordable), then changed: no comparison is possible, so
    # blocking would be asserting a fact the check never established.
    observed.observe("big.bin")
    big.write_bytes(b"9876543210")
    assert observed.verdict("big.bin") is None


def test_a_directory_is_not_this_module_s_question(observed, tmp_path):
    (tmp_path / "sub").mkdir()
    assert observed.verdict("sub") is None


# -- resume ---------------------------------------------------------------------------


def _read_call(path):
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "c1",
                "function": {"name": "read_file", "arguments": json.dumps({"path": path})},
            }
        ],
    }


def test_a_resumed_session_does_not_refuse_the_read_it_already_did(observed, tmp_path):
    """The read is in the model's context; the store that witnessed it is not. Refusing
    would be true about this process and false about the conversation."""
    (tmp_path / "config.py").write_text("PORT = 8080\n", encoding="utf-8")

    assert seed_from_messages(observed, [_read_call("config.py")]) == 1
    assert observed.verdict("config.py") is None


def test_a_seeded_read_is_not_re_hashed(observed, tmp_path):
    """Hashing at seed time would record what the file looks like TODAY as though the
    agent had seen it — a file changed while the process was down would then PASS the
    guard it exists to fail. Unverified is the honest entry."""
    (tmp_path / "config.py").write_text("PORT = 8080\n", encoding="utf-8")
    seed_from_messages(observed, [_read_call("config.py")])

    assert observed._seen[resolve("config.py", tmp_path)] == UNVERIFIED


def test_one_real_read_upgrades_a_seeded_entry(observed, tmp_path):
    """So the version guard comes back on for that file rather than staying off forever."""
    target = tmp_path / "config.py"
    target.write_text("PORT = 8080\n", encoding="utf-8")
    seed_from_messages(observed, [_read_call("config.py")])

    observed.observe("config.py")
    target.write_text("PORT = 9090\n", encoding="utf-8")

    assert observed.verdict("config.py") is not None


def test_seeding_ignores_calls_that_are_not_reads(observed, tmp_path):
    written = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "c2",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps({"path": "config.py"}),
                },
            }
        ],
    }
    # A grep hit shows the agent a NAME, not the contents; recording it as a read is the
    # guess this module exists to refuse.
    assert seed_from_messages(observed, [written, {"role": "user", "content": "hi"}]) == 0


# -- through the pipeline -------------------------------------------------------------


def _run(pipeline, execution, value="written", status=OK):
    async def run():
        return ToolResult(value=value, status=status)

    return asyncio.run(pipeline.dispatch(execution, run))


def test_the_gate_blocks_the_call_and_the_reason_reaches_the_model(observed, tmp_path):
    (tmp_path / "config.py").write_text("PORT = 8080\n", encoding="utf-8")
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))

    out = _run(
        pipeline,
        ToolExecution(name="write_file", arguments={"path": "config.py"}, call_id="c1"),
    )

    assert out.status == BLOCKED
    assert "config.py" in out.value["reason"]
    # `reason` is the human sentence; `error` is what every renderer prints. The rule
    # name rides both halves so a surface that only knows `error` can still say which
    # policy spoke (the bare "Error: tool call blocked" of session 435e0be4).
    assert out.value["rule"] == "read-before-edit"
    assert out.value["error"].startswith("tool call blocked by read-before-edit:")


def test_the_gate_names_the_version_rule_when_the_file_changed(observed, tmp_path):
    target = tmp_path / "config.py"
    target.write_text("PORT = 8080\n", encoding="utf-8")
    observed.observe("config.py")
    target.write_text("PORT = 9090\n", encoding="utf-8")  # a build, a command, the user
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))

    out = _run(
        pipeline,
        ToolExecution(name="write_file", arguments={"path": "config.py"}, call_id="c1"),
    )

    assert out.status == BLOCKED
    assert out.value["rule"] == "stale-file"
    assert "changed" in out.value["reason"]


def test_an_allowed_no_op_replace_succeeds(observed, tmp_path):
    """The control half of the report's A/B: the same no-op edit passes on a file the
    agent read, so the refusal is about the file's observation state, not the edit."""
    (tmp_path / "config.py").write_text("PORT = 8080\n", encoding="utf-8")
    observed.observe("config.py")
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))

    out = _run(
        pipeline,
        ToolExecution(
            name="replace_in_file",
            arguments={"path": "config.py", "old": "PORT = 8080\n", "new": "PORT = 8080\n"},
            call_id="c1",
        ),
        value={"path": "config.py", "replacements": 1},
    )

    assert out.status == OK


def test_the_gate_lets_a_read_file_through_untouched(observed, tmp_path):
    """It fires only on calls whose paths `locate` can name — which is the write tools."""
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))

    out = _run(
        pipeline,
        ToolExecution(name="read_file", arguments={"path": "config.py"}, call_id="c1"),
        value="contents",
    )

    assert out.status == OK


def test_a_read_then_an_edit_passes_end_to_end(observed, tmp_path):
    (tmp_path / "config.py").write_text("PORT = 8080\n", encoding="utf-8")
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))
    pipeline.on_post_execute(observation_recorder(observed, _locate))

    read = _run(
        pipeline,
        ToolExecution(name="read_file", arguments={"path": "config.py"}, call_id="c1"),
        value="PORT = 8080",
    )
    edit = _run(
        pipeline,
        ToolExecution(name="write_file", arguments={"path": "config.py"}, call_id="c2"),
    )

    assert read.status == OK
    assert edit.status == OK


def test_a_failed_read_shows_the_agent_nothing(observed, tmp_path):
    (tmp_path / "config.py").write_text("PORT = 8080\n", encoding="utf-8")
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))
    pipeline.on_post_execute(observation_recorder(observed, _locate))

    _run(
        pipeline,
        ToolExecution(name="read_file", arguments={"path": "config.py"}, call_id="c1"),
        value={"error": "no such file"},
        status="error",
    )
    out = _run(
        pipeline,
        ToolExecution(name="write_file", arguments={"path": "config.py"}, call_id="c2"),
    )

    assert out.status == BLOCKED


def test_a_blocked_write_never_records_the_observation_that_would_let_it_through(
    observed, tmp_path
):
    """`dispatch` returns before the post waterfall, so the refusal cannot teach itself
    away — a second identical call is refused identically."""
    (tmp_path / "config.py").write_text("PORT = 8080\n", encoding="utf-8")
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))
    pipeline.on_post_execute(observation_recorder(observed, _locate))
    execution = ToolExecution(
        name="write_file", arguments={"path": "config.py"}, call_id="c1"
    )

    first = _run(pipeline, execution)
    second = _run(pipeline, execution)

    assert first.status == BLOCKED and second.status == BLOCKED


def test_the_gate_is_outermost_so_a_refusal_lands_before_the_snapshot(observed, tmp_path):
    """Registered first in agent.py for this reason: copying a file aside for a write that
    is not going to happen is work spent on nothing."""
    (tmp_path / "config.py").write_text("PORT = 8080\n", encoding="utf-8")
    snapshots: list[str] = []
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))

    async def snapshot(execution, nxt):
        snapshots.append(execution.call_id)
        return await nxt()

    pipeline.on_pre_execute(snapshot)

    out = _run(
        pipeline,
        ToolExecution(name="write_file", arguments={"path": "config.py"}, call_id="c1"),
    )

    assert out.status == BLOCKED
    assert snapshots == []

# -- partial reads ------------------------------------------


def _read(pipeline, name, arguments, value):
    return _run(pipeline, ToolExecution(name=name, arguments=arguments, call_id="r"), value=value)


def test_a_windowed_read_of_a_small_file_does_not_authorise_an_edit(observed, tmp_path):
    """The hole this closes: the guard recorded the WHOLE file's digest after a windowed
    read, so an edit to text the model never saw passed the gate. The reader reports its
    own window, so the recorder knows."""
    (tmp_path / "config.py").write_text("".join(f"L{i}\n" for i in range(50)), encoding="utf-8")
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))
    pipeline.on_post_execute(observation_recorder(observed, _locate))

    _read(
        pipeline,
        "read_file",
        {"path": "config.py", "start_line": 1, "max_lines": 10},
        {"path": "config.py", "start_line": 1, "end_line": 10, "total_lines": 50},
    )
    blocked = _run(
        pipeline,
        ToolExecution(name="write_file", arguments={"path": "config.py"}, call_id="e"),
    )
    assert blocked.status == BLOCKED
    assert "read only part of" in blocked.value.get("error", "") or blocked.rule == "partial-read"


def test_a_full_read_authorises_the_edit(observed, tmp_path):
    (tmp_path / "config.py").write_text("".join(f"L{i}\n" for i in range(50)), encoding="utf-8")
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))
    pipeline.on_post_execute(observation_recorder(observed, _locate))
    _read(
        pipeline,
        "read_file",
        {"path": "config.py"},
        {"path": "config.py", "start_line": 1, "end_line": 50, "total_lines": 50},
    )
    assert _run(
        pipeline,
        ToolExecution(name="replace_in_file", arguments={"path": "config.py"}, call_id="e"),
    ).status == OK


def test_a_partial_read_of_a_file_too_large_to_read_whole_still_edits(observed, tmp_path):
    """Past the reader's window ceiling, refusing would make the file uneditable by any
    gated tool — so the rule yields to the constraint and says nothing."""
    target = tmp_path / "huge.py"
    target.write_text("".join(f"L{i}\n" for i in range(3_000)), encoding="utf-8")
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))
    pipeline.on_post_execute(observation_recorder(observed, _locate))
    _read(
        pipeline,
        "read_file",
        {"path": "huge.py", "start_line": 1, "max_lines": 2000},
        {"path": "huge.py", "start_line": 1, "end_line": 2000, "total_lines": 3000},
    )
    assert _run(
        pipeline,
        ToolExecution(name="replace_in_file", arguments={"path": "huge.py"}, call_id="e"),
    ).status == OK


def test_a_write_clears_the_partial_mark(observed, tmp_path):
    (tmp_path / "config.py").write_text("old\n", encoding="utf-8")
    observed.observe("config.py", covered=(1, 1), total=10)
    observed.observe("config.py")  # a successful write means the whole content is known
    assert observed.verdict(str(tmp_path / "config.py")) is None


def test_a_stale_partial_read_is_reported_as_stale_not_partial(observed, tmp_path):
    """Order matters: "the file changed" is the more urgent fact and the one the model can
    act on first."""
    target = tmp_path / "config.py"
    target.write_text("one\n", encoding="utf-8")
    observed.observe(str(target), covered=(1, 1), total=3)
    target.write_text("one\ntwo\n", encoding="utf-8")
    rule, reason = observed.blame(str(target))
    assert rule == "stale-file" and "changed on disk" in reason


# -- coverage is per LINE, not per call (session 1030c218-40a) -------------------------


def _windowed_read(pipeline, path, start, end, total):
    return _read(
        pipeline,
        "read_file",
        {"path": path, "start_line": start, "max_lines": end - start + 1},
        {"path": path, "start_line": start, "end_line": end, "total_lines": total},
    )


def test_two_contiguous_windows_add_up_to_a_full_read(observed, tmp_path):
    """The false refusal that started this: `CLAUDE.md` read as 1-120 then 121-287 is the
    whole file, and neither call alone covered it."""
    (tmp_path / "CLAUDE.md").write_text("".join(f"line {i}\n" for i in range(1, 288)), encoding="utf-8")
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))
    pipeline.on_post_execute(observation_recorder(observed, _locate))

    _windowed_read(pipeline, "CLAUDE.md", 1, 120, 287)
    _windowed_read(pipeline, "CLAUDE.md", 121, 287, 287)

    assert _run(
        pipeline,
        ToolExecution(name="write_file", arguments={"path": "CLAUDE.md"}, call_id="w"),
    ).status == OK


def test_an_edit_inside_a_read_window_is_allowed(observed, tmp_path):
    """The other false refusal: four windows of `docs/MCP.md`, including the one being
    edited, refused because the file as a whole was not covered."""
    target = tmp_path / "MCP.md"
    target.write_text("".join(f"line {i}\n" for i in range(1, 306)), encoding="utf-8")
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))
    pipeline.on_post_execute(observation_recorder(observed, _locate))

    _windowed_read(pipeline, "MCP.md", 20, 59, 305)
    _windowed_read(pipeline, "MCP.md", 245, 305, 305)

    edit = _run(
        pipeline,
        ToolExecution(
            name="replace_in_file",
            arguments={"path": "MCP.md", "old": "line 250\n", "new": "line 250 fixed\n"},
            call_id="e",
        ),
    )
    assert edit.status == OK


def test_an_edit_that_quotes_its_text_runs_outside_every_read_window(observed, tmp_path):
    """`replace_in_file` and a patch's Update
    hunk change only text they match exactly, so an edit written from memory fails to
    match on its own. The gate refused them anyway — 12 times in 36 sessions, each after
    the model had seen the file some other way — and added a read, never safety."""
    target = tmp_path / "MCP.md"
    target.write_text("".join(f"line {i}\n" for i in range(1, 306)), encoding="utf-8")
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))
    pipeline.on_post_execute(observation_recorder(observed, _locate))

    _windowed_read(pipeline, "MCP.md", 20, 59, 305)

    edit = ToolExecution(
        name="replace_in_file",
        arguments={"path": "MCP.md", "old": "line 250\n", "new": "x\n"},
        call_id="e",
    )
    assert _run(pipeline, edit).status == OK
    update = "*** Begin Patch\n*** Update File: MCP.md\n@@\n-line 251\n+y\n*** End Patch"
    patch = ToolExecution(name="apply_patch", arguments={"patch": update}, call_id="p")
    assert _run(pipeline, patch).status == OK


def test_a_patch_that_adds_over_an_unread_file_is_still_refused(observed, tmp_path):
    (tmp_path / "old.py").write_text("x = 1\n", encoding="utf-8")
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))
    envelope = "*** Begin Patch\n*** Add File: old.py\n+y = 2\n*** End Patch"
    blocked = _run(
        pipeline, ToolExecution(name="apply_patch", arguments={"patch": envelope}, call_id="a")
    )
    assert blocked.status == BLOCKED


def test_a_whole_file_write_still_needs_the_whole_file_read(observed, tmp_path):
    """`write_file` replaces everything, so a covered region is not enough — the whole
    file has to have been seen."""
    target = tmp_path / "a.py"
    target.write_text("".join(f"line {i}\n" for i in range(1, 50)), encoding="utf-8")
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))
    pipeline.on_post_execute(observation_recorder(observed, _locate))
    _windowed_read(pipeline, "a.py", 1, 10, 49)

    blocked = _run(
        pipeline,
        ToolExecution(name="write_file", arguments={"path": "a.py"}, call_id="w"),
    )
    assert blocked.status == BLOCKED
    assert "read only part of it" in blocked.value.get("error", "")


def test_a_multi_file_edit_is_refused_once_naming_every_unread_file(observed, tmp_path):
    """Live eval (deepseek-v4.1-flash, 2026-09-24): a 21-file apply_patch was refused
    naming only the FIRST unread file of 14. That model guessed the rest; one that did not
    would be refused 14 times, one read per round-trip. Every blocked path is named."""
    for name in ("a.py", "b.py", "c.py", "seen.py"):
        (tmp_path / name).write_text("X = 1\n", encoding="utf-8")
    observed.observe("seen.py")

    def locate(name, arguments):
        return (list(arguments.get("paths") or []), True)

    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, locate))
    out = _run(
        pipeline,
        ToolExecution(
            name="apply_patch",
            arguments={"paths": ["a.py", "seen.py", "b.py", "c.py", "new.py"]},
            call_id="c1",
        ),
    )

    assert out.status == BLOCKED
    reason = out.value["reason"]
    assert all(name in reason for name in ("a.py", "b.py", "c.py"))
    assert "seen.py" not in reason and "new.py" not in reason
    assert out.value["rule"] == "read-before-edit"
