"""`run_code` sub-calls run together under the direct path's rule, and a tool result reads
both ways.

The real session store:
- 438 programs, and not one sub-call ever overlapped another, even in programs of 15 to 41
  calls (37 calls took 22.8s, the sum of their times);
- one program died trying `asyncio.gather`;
- 7 of 17 program errors were a guess about a result's shape, a dict read as text or text
  read as a dict.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from aisuite.agents import ToolMetadata

from harness.tools.batch import Reservation, StreamGate
from harness.tools.code_child_values import ToolDict, ToolList, ToolText, wrap


# -- the gate ------------------------------------------------------------------------


def _drive(gate_calls, cap=0):
    """Run `(reservation, seconds)` calls through one gate as tasks in submission order;
    return `(start order, max concurrent, max concurrent while a barrier ran)`."""
    gate = StreamGate(cap=cap)
    started: list[int] = []
    running: set[int] = set()
    peak = [0, 0]

    async def one(index, reservation, seconds, ticket):
        try:
            await gate.acquire(ticket, reservation)
            started.append(index)
            running.add(index)
            peak[0] = max(peak[0], len(running))
            if reservation is None:
                peak[1] = max(peak[1], len(running))
            await asyncio.sleep(seconds)
            running.discard(index)
        finally:
            await gate.release(ticket)

    async def go():
        tasks = [
            asyncio.create_task(one(i, r, s, gate.ticket()))
            for i, (r, s) in enumerate(gate_calls)
        ]
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

    asyncio.run(go())
    return started, peak[0], peak[1]


READ = Reservation(("a.py",), write=False)
WRITE_A = Reservation(("a.py",), write=True)
WRITE_B = Reservation(("b.py",), write=True)


def test_reads_overlap():
    order, peak, _ = _drive([(READ, 0.05)] * 4)
    assert order == [0, 1, 2, 3] and peak == 4


def test_a_barrier_runs_alone_and_keeps_its_place():
    order, peak, alone = _drive([(READ, 0.05), (None, 0.05), (READ, 0.05), (READ, 0.05)])
    assert order == [0, 1, 2, 3]
    assert alone == 1  # nothing ran beside the barrier
    assert peak == 2  # the two reads after it overlap again


def test_a_write_waits_for_a_read_of_the_same_path_and_keeps_order():
    order, _, _ = _drive([(READ, 0.1), (WRITE_A, 0.01), (Reservation(("c.py",)), 0.01)])
    assert order == [0, 1, 2]  # the read behind the write did not jump the line


def test_disjoint_writes_overlap():
    _, peak, _ = _drive([(WRITE_A, 0.05), (WRITE_B, 0.05)])
    assert peak == 2


def test_the_cap_bounds_how_many_run():
    _, peak, _ = _drive([(READ, 0.05)] * 6, cap=2)
    assert peak == 2


def test_a_call_that_never_starts_does_not_hold_the_line():
    """A refused call is released without acquiring; the calls behind it still run."""
    gate = StreamGate()

    async def go():
        first, second = gate.ticket(), gate.ticket()
        await gate.release(first)  # refused: never started
        await asyncio.wait_for(gate.acquire(second, READ), timeout=1)
        await gate.release(second)

    asyncio.run(go())


# -- the values a program receives ------------------------------------------------------


def test_a_dict_result_is_a_dict_that_also_reads_as_text():
    value = wrap({"content": "line one\nline two", "total": 2})
    assert isinstance(value, dict) and value["total"] == 2
    assert isinstance(value["content"], str)
    assert value["content"].splitlines() == ["line one", "line two"]
    # The shape guessed wrong in the store: a dict read as text.
    assert '"total": 2' in value.text and value.strip().startswith("{")
    assert value.splitlines() == [value.text]
    # `print` is unchanged: Python's own rendering.
    assert str(value) == repr({"content": "line one\nline two", "total": 2})


def test_a_missing_key_names_the_keys_there_are():
    with pytest.raises(KeyError, match="'matches'.*'content'"):
        wrap({"content": "x"})["matches"]


def test_json_text_reads_as_a_dict_and_plain_text_says_it_is_text():
    value = wrap('{"accounts": [{"id": 1}], "count": 1}')
    assert isinstance(value, str)
    assert value.get("count") == 1 and value["accounts"][0]["id"] == 1
    assert value.get("missing", "d") == "d"
    plain = wrap("12 rows\nrow one")
    assert plain[0:2] == "12" and plain.splitlines()[1] == "row one"
    with pytest.raises(KeyError, match="returned text, not a dict"):
        plain.get("rows")
    with pytest.raises(KeyError, match="returned text, not a dict"):
        plain["rows"]


def test_lists_and_scalars():
    value = wrap([{"id": 1}, "x", 3, None])
    assert isinstance(value, ToolList) and isinstance(value[0], ToolDict)
    assert isinstance(value[1], ToolText) and value[2] == 3 and value[3] is None
    assert value.text.startswith("[")


# -- through the engine ----------------------------------------------------------------


def _read_tool(name, barrier, active, peak):
    def tool(tag: str = "") -> str:
        with active[1]:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        try:
            if barrier is not None:
                barrier.wait()
            time.sleep(0.05)
            return f"{name} {tag}"
        finally:
            with active[1]:
                active[0] -= 1

    tool.__name__ = name
    tool.__aisuite_tool_metadata__ = ToolMetadata(name=name, category="read", risk_level="low")
    return tool


def test_awaited_reads_overlap_through_the_engine(tmp_path):
    """Two reads that must MEET: under the old awaitable (a blocking call inside the
    coroutine) the first never returned, because the second was never sent."""
    from test_code_mode import _run

    barrier = threading.Barrier(2, timeout=8)
    active, peak = [0, threading.Lock()], [0]
    code = (
        "a, b = await asyncio.gather(tools.look(tag='a'), tools.look(tag='b'))\n"
        "print(a, b)"
    )
    _, engine = _run(tmp_path, code, extra=[_read_tool("look", barrier, active, peak)])
    content = [m for m in engine.messages if m.get("role") == "tool"][-1]["content"]
    assert "look a" in content and "look b" in content
    assert peak[0] == 2


def test_an_unannotated_tool_still_runs_alone(tmp_path):
    """The rule is the direct path's: a tool that does not declare itself safe to overlap
    runs alone, whatever the program asks for."""
    from test_code_mode import _run

    active, peak = [0, threading.Lock()], [0]
    look = _read_tool("look", None, active, peak)
    del look.__aisuite_tool_metadata__  # undeclared: a barrier
    code = (
        "r = await asyncio.gather(*[tools.look(tag=str(i)) for i in range(4)])\n"
        "print(len(r))"
    )
    _, engine = _run(tmp_path, code, extra=[look])
    content = [m for m in engine.messages if m.get("role") == "tool"][-1]["content"]
    assert "4" in content
    assert peak[0] == 1


def test_a_program_reads_a_dict_result_as_text_and_json_text_as_a_dict(tmp_path):
    """The two guesses from the store, made against real sub-calls."""
    from test_code_mode import _run

    def rows() -> dict:
        return {"content": "a\nb", "total": 2}

    def listing() -> str:
        return '{"matches": ["x.py", "y.py"]}'

    code = (
        "r = tools.rows()\n"
        "print(len(r.splitlines()), r['total'])\n"
        "print(tools.listing().get('matches'))"
    )
    _, engine = _run(tmp_path, code, extra=[rows, listing])
    content = [m for m in engine.messages if m.get("role") == "tool"][-1]["content"]
    assert "1 2" in content and "x.py" in content and "Error" not in content
