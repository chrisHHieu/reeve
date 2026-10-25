"""How a batch of tool calls is ordered and grouped.

Two things are under test here, and they are not equally important.

**Order.** The engine used to dispatch every parallel-safe call to completion and THEN
everything else, whichever order the model wrote them in. A batch is a sequence the model
author wrote down — `[write_file(a), read_file(a)]` is the ordinary "write it and read it
back" pair — and permuting it handed the model the file's old contents, whose only honest
reading is "my write did not land". That is the bug these tests pin first.

**Overlap.** A run closes when one call reserves a path another call in the run touches,
with reads overlapping reads exempt because two reads cannot disturb each other. Today this
rarely fires, because the metadata filter already bars writes from parallel runs; it exists
so the guarantee survives a mutating tool that arrives marked `risk_level="low"`.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import aisuite as ai

from harness.safety.boundary import paths_overlap, read_paths, write_paths
from harness.loop import TurnEngine
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry
from harness.tools.batch import Reservation, plan_batch
from harness.tools.task import _policy as task_policy


# -- the grouping rule, on its own ------------------------------------------------


def _by_name(names):
    """A `reserve` that treats every call as parallel-safe with no paths."""
    return lambda call: Reservation()


def test_consecutive_parallel_safe_calls_form_one_segment():
    calls = ["a", "b", "c"]
    segments = plan_batch(calls, reserve=_by_name(calls))
    assert [len(s) for s in segments] == [3]
    assert segments[0].parallel is True


def test_a_barrier_keeps_its_place_and_splits_the_run():
    """A barrier is a call the caller refuses to overlap. It must not be hoisted."""
    calls = ["read", "shell", "read2"]

    def reserve(call):
        return None if call == "shell" else Reservation()

    segments = plan_batch(calls, reserve=reserve)
    assert [(s.calls, s.parallel) for s in segments] == [
        (["read"], True),
        (["shell"], False),
        (["read2"], True),
    ]


def test_a_leading_barrier_stays_first():
    calls = ["shell", "read"]

    def reserve(call):
        return None if call == "shell" else Reservation()

    segments = plan_batch(calls, reserve=reserve)
    assert [s.calls for s in segments] == [["shell"], ["read"]]


def test_conflicting_reservations_start_a_new_segment():
    """The call that conflicts waits for the previous run rather than racing it — and the
    calls after it still get to overlap with each other."""
    calls = ["read_a", "write_a", "read_b"]

    def reserve(call):
        if call == "write_a":
            return Reservation(("a.txt",), write=True)
        return Reservation(("a.txt" if call == "read_a" else "b.txt",), write=False)

    segments = plan_batch(calls, reserve=reserve)
    assert [s.calls for s in segments] == [["read_a"], ["write_a", "read_b"]]


def test_two_reads_of_the_same_file_still_overlap():
    """Reads cannot disturb each other, however they overlap. Serialising them would cost
    throughput for no correctness at all."""
    calls = ["read_a", "read_a_again"]
    segments = plan_batch(
        calls, reserve=lambda c: Reservation(("a.txt",), write=False)
    )
    assert [len(s) for s in segments] == [2]


def test_two_writes_to_different_files_do_not_conflict():
    """Exact-path semantics: the reason a writer's PATHS are reserved rather than "it
    writes" being the reservation."""
    calls = ["w1", "w2"]
    segments = plan_batch(
        calls, reserve=lambda c: Reservation((f"{c}.txt",), write=True)
    )
    assert [len(s) for s in segments] == [2]


def test_a_writer_whose_reach_is_unknown_conflicts_with_everything():
    """`located=False` is "cannot be proven disjoint", not "touches nothing" — so it ends up
    alone in its own segment, which is the right cost for a mutation nobody can scope."""
    calls = ["opaque_write", "read"]

    def reserve(call):
        if call == "opaque_write":
            return Reservation((), write=True, located=False)
        return Reservation(("a.txt",), write=False)

    segments = plan_batch(calls, reserve=reserve)
    assert [s.calls for s in segments] == [["opaque_write"], ["read"]]


def test_two_reads_cannot_conflict_even_when_neither_is_located():
    calls = ["r1", "r2"]
    segments = plan_batch(
        calls, reserve=lambda c: Reservation((), write=False, located=False)
    )
    assert [len(s) for s in segments] == [2]


def test_an_empty_batch_plans_nothing():
    assert plan_batch([], reserve=lambda c: Reservation()) == []


def test_a_single_call_is_one_segment():
    segments = plan_batch(["only"], reserve=lambda c: Reservation())
    assert [s.calls for s in segments] == [["only"]]


# -- path comparison --------------------------------------------------------------


def test_paths_overlap_counts_nesting_in_both_directions():
    assert paths_overlap("dir/a.txt", "dir/a.txt")
    assert paths_overlap("dir/a.txt", "dir")
    assert paths_overlap("dir", "dir/a.txt")
    assert not paths_overlap("dir/a.txt", "dir/b.txt")
    # A sibling with the same prefix is not inside: `dir2` is not under `dir`.
    assert not paths_overlap("dir", "dir2/a.txt")


def test_paths_overlap_folds_separators_and_case():
    """Windows: `dir\\a.txt`, `dir/a.txt` and `DIR/A.TXT` are one file. Comparing them as
    three would let a write and a read of the same file share a parallel run."""
    assert paths_overlap("dir\\a.txt", "dir/a.txt")
    assert paths_overlap("DIR/A.TXT", "dir/a.txt")
    assert paths_overlap("./dir/a.txt", "dir/a.txt")


def test_paths_overlap_refuses_to_compare_absolute_with_relative():
    """Not comparable, so not provably disjoint — and the caller serialises on that. Only
    the paths themselves are checked here; the consequence is `plan_batch`'s."""
    assert paths_overlap("/abs/a.txt", "a.txt")


# -- read/write extraction --------------------------------------------------------


def test_read_paths_knows_the_read_tools_and_ignores_the_rest():
    assert read_paths("read_file", {"path": "a.txt"}) == (["a.txt"], True)
    assert read_paths("grep", {"path": "src", "pattern": "x"}) == (["src"], True)
    # A tool with no path argument touches nothing a write could race.
    assert read_paths("todo_write", {"todos": []}) == ([], True)
    # A read tool given no path is malformed, and an unknown reach is not a disjoint one.
    assert read_paths("read_file", {}) == ([], False)


def test_write_paths_still_reports_an_unlocatable_patch():
    """The floor under all of this: a patch blob with no headers cannot say what it
    touches, and `located=False` is what carries that to the planner."""
    assert write_paths("apply_patch", {"patch": "not a patch"}) == ([], False)


# -- the engine: order ------------------------------------------------------------


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _tool_call_turn(*calls):
    return AssistantTurn(
        tool_calls=[
            ToolCall(id=f"call_{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ],
        finish_reason="tool_calls",
    )


async def _collect(engine, text="go"):
    return [event async for event in engine.run(text)]


def test_a_read_after_a_write_sees_the_write(tmp_path):
    """The bug this phase exists for.

    `[write_file(a), read_file(a)]` is how a model checks its own edit. The old engine ran
    the low-risk read FIRST and handed back the pre-write contents, so the model was told
    its write had not landed and would write again.
    """
    order: list[str] = []
    target = tmp_path / "notes.txt"
    low = ai.ToolMetadata(category="search", risk_level="low", requires_approval=False)
    medium = ai.ToolMetadata(
        category="filesystem", risk_level="medium", requires_approval=False
    )

    def write_file(path: str, content: str = "") -> dict:
        """Write it."""
        order.append("write")
        Path(path).write_text(content, encoding="utf-8")
        return {"path": path}

    def read_file(path: str) -> dict:
        """Read it."""
        order.append("read")
        return {"text": Path(path).read_text(encoding="utf-8")}

    registry = ToolRegistry()
    registry.register(write_file, metadata=medium)
    registry.register(read_file, metadata=low)
    engine = TurnEngine(
        provider=_Scripted(
            [
                _tool_call_turn(
                    ("write_file", {"path": str(target), "content": "after"}),
                    ("read_file", {"path": str(target)}),
                ),
                AssistantTurn(text="done", finish_reason="stop"),
            ]
        ),
        registry=registry,
        # Approvals are not what this test is about; the write tool would otherwise be
        # parked on an approval card there is no approver for, and never run.
        permissions=PermissionEngine(
            workspace_root=tmp_path, mode=Mode.DANGER_FULL_ACCESS
        ),
        model="gpt-5.5",
    )

    asyncio.run(_collect(engine))
    assert order == ["write", "read"]
    results = [m for m in engine.messages if m.get("role") == "tool"]
    assert "after" in results[1]["content"], "the read must see the write it followed"


def test_a_barrier_between_two_reads_keeps_the_reads_apart(tmp_path):
    """A shell call in the middle: the reads either side of it must not join into one run
    that jumps over it."""
    order: list[str] = []
    low = ai.ToolMetadata(category="search", risk_level="low", requires_approval=False)
    high = ai.ToolMetadata(
        category="shell", risk_level="high", requires_approval=False
    )

    def probe(name):
        def tool() -> dict:
            """Probe."""
            order.append(name)
            return {"name": name}

        # The registry keys tools by `__name__`, so three closures sharing one would be one
        # entry and two of the names would resolve to nothing.
        tool.__name__ = name
        return tool

    registry = ToolRegistry()
    registry.register(probe("r1"), metadata=low)
    registry.register(probe("shell"), metadata=high)
    registry.register(probe("r2"), metadata=low)
    engine = TurnEngine(
        provider=_Scripted(
            [
                _tool_call_turn(("r1", {}), ("shell", {}), ("r2", {})),
                AssistantTurn(text="done", finish_reason="stop"),
            ]
        ),
        registry=registry,
        # Approvals are not what this test is about; the write tool would otherwise be
        # parked on a card there is no approver for.
        permissions=PermissionEngine(
            workspace_root=tmp_path, mode=Mode.DANGER_FULL_ACCESS
        ),
        model="gpt-5.5",
    )

    asyncio.run(_collect(engine))
    assert order == ["r1", "shell", "r2"]


def test_low_risk_calls_that_do_not_conflict_still_run_at_the_same_time(tmp_path):
    """The throughput the parallel path exists for, proved by a two-party barrier: the turn
    only finishes if both calls are really in flight together."""
    barrier = threading.Barrier(2, timeout=5)
    low = ai.ToolMetadata(category="search", risk_level="low", requires_approval=False)

    def side_a(path: str) -> dict:
        """Wait for side_b."""
        barrier.wait()
        return {"side": "a"}

    def side_b(path: str) -> dict:
        """Wait for side_a."""
        barrier.wait()
        return {"side": "b"}

    registry = ToolRegistry()
    registry.register(side_a, metadata=low)
    registry.register(side_b, metadata=low)
    engine = TurnEngine(
        provider=_Scripted(
            [
                _tool_call_turn(
                    ("side_a", {"path": "a.txt"}), ("side_b", {"path": "b.txt"})
                ),
                AssistantTurn(text="done", finish_reason="stop"),
            ]
        ),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )

    events = asyncio.run(_collect(engine))
    finished = [e for e in events if e.type.value == "tool_finished"]
    assert [e.data["status"] for e in finished] == ["ok", "ok"]


def test_a_mutating_tool_carrying_low_metadata_is_still_reserved_as_a_writer(tmp_path):
    """The hole, closed.

    `_reserve` asks `risk.classify` — the classifier the permission engine and the change
    receipt use — rather than checking the built-in `WRITE_TOOLS` name set. `apply_patch`
    is a built-in whose class the risk floor pins at `WRITE_LOCAL`, so this registration
    cannot actually relax it; what the test proves is that the WRITER decision comes from
    the classifier and the PATH comes from the patch body, which is what would catch a
    mutating tool this codebase did not author.
    """
    engine = TurnEngine(
        provider=_Scripted([]),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )
    low = ai.ToolMetadata(
        category="filesystem", risk_level="low", requires_approval=False
    )

    def apply_patch(patch: str = "") -> dict:
        """Patch."""
        return {"ok": True}

    engine.registry.register(apply_patch, metadata=low)

    reservation = engine._reserve(
        ToolCall(
            id="c1",
            name="apply_patch",
            arguments={"patch": "*** Update File: a.txt\n@@\n-a\n+b\n"},
        )
    )
    assert reservation is not None, "low metadata admits it to the parallel set"
    assert reservation.write is True, "but the classifier says it writes"
    assert reservation.paths == ("a.txt",), "and the patch body says where"


def test_a_read_tool_reserves_the_file_it_reads(tmp_path):
    """The other half: a read's paths are what let the planner see it overlap a writer."""
    engine = TurnEngine(
        provider=_Scripted([]),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )

    def read_file(path: str = "") -> dict:
        """Read."""
        return {"text": ""}

    engine.registry.register(
        read_file,
        metadata=ai.ToolMetadata(
            category="search", risk_level="low", requires_approval=False
        ),
    )
    reservation = engine._reserve(
        ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"})
    )
    assert reservation == Reservation(("a.txt",), write=False, located=True)


def test_two_delegated_spawns_run_their_children_in_parallel(tmp_path):
    """Two `task` calls in one message are two children that MUST overlap.

    `task` declares `__tool_policy__` as a plain dict of metadata fields, and the
    engine's readers read attributes — so until `_metadata_for` normalised the policy's
    answer, every spawn looked unannotated, `_reserve` returned a barrier, and the two
    children ran one after the other. The owner saw it live (2026-09-20): the second
    node's session did not exist while the first worked. Parallel subagents
    are the reference behaviour.
    """
    starts: list[float] = []

    def task(description: str = "", subagent_type: str = "", prompt: str = "") -> dict:
        """Spawn a child (the sleep stands in for its turn)."""
        starts.append(time.monotonic())
        time.sleep(0.4)
        return {"task_id": subagent_type, "status": "completed", "report": "ok"}

    task.__tool_policy__ = task_policy
    registry = ToolRegistry()
    registry.register(task)
    engine = TurnEngine(
        provider=_Scripted(
            [
                _tool_call_turn(
                    ("task", {"description": "A", "subagent_type": "explore", "prompt": "a"}),
                    ("task", {"description": "B", "subagent_type": "explore", "prompt": "b"}),
                ),
                AssistantTurn(text="done", finish_reason="stop"),
            ]
        ),
        registry=registry,
        permissions=PermissionEngine(
            workspace_root=tmp_path, mode=Mode.DANGER_FULL_ACCESS
        ),
        model="gpt-5.5",
    )

    started = time.monotonic()
    asyncio.run(_collect(engine))
    elapsed = time.monotonic() - started

    assert len(starts) == 2
    assert abs(starts[1] - starts[0]) < 0.15, "the second child must not wait for the first"
    assert elapsed < 0.75, f"sequential children would take >= 0.8s, took {elapsed:.2f}s"
