"""The Minimal-mode benchmark harness (harness/observability/bench.py), driven by a scripted provider.

The contract under test: a task runs in a throwaway workspace through the REAL TurnEngine
with the minimal toolset, and the verdict comes from the checker — never from the model's
own account of its work. A model that writes the right file passes; one that writes the
wrong thing (or nothing) fails with a detail a human can read. No test here calls a model
API; the real runs are `python -m benchmarks.run` (BENCHMARK.md).
"""

from __future__ import annotations

import json

import pytest

from harness.observability.bench import Task, estimate_cost_usd, load_tasks, run_task
from harness.context.compaction import SUMMARY_SECTIONS
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk
from harness.llm.providers.base import TokenUsage, ToolCall


class Scripted(ProviderClient):
    """One AssistantTurn per request — a second chunk would REPLACE the step's turn."""

    def __init__(self, turns):
        self.turns = list(turns)

    def stream(self, *, model, messages, tools=None, **settings):
        if self.turns:
            yield StreamChunk(turn=self.turns.pop(0))

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("the engine must stream")

    def capabilities(self, model):
        return ModelCapabilities()


def _write_then_done(path: str, content: str):
    return Scripted(
        [
            AssistantTurn(
                tool_calls=[
                    ToolCall(id="c1", name="write_file", arguments={"path": path, "content": content})
                ]
            ),
            AssistantTurn(text="done", finish_reason="stop"),
        ]
    )


def test_a_model_that_writes_the_right_file_passes(tmp_path):
    task = Task(
        id="t",
        prompt="create the file",
        check={"type": "file_equals", "path": "hello.txt", "expected": "hello bench"},
    )
    result, workspace = run_task(task, _write_then_done("hello.txt", "hello bench"), "test-model")
    assert result.passed and result.iterations >= 1
    # A passing run cleans up after itself.
    assert not workspace.exists()


def test_setup_files_are_written_and_a_wrong_answer_fails(tmp_path):
    task = Task(
        id="t",
        prompt="fix it",
        setup={"notes.txt": "context for the model"},
        check={"type": "file_equals", "path": "out.txt", "expected": "right"},
    )
    result, workspace = run_task(task, _write_then_done("out.txt", "wrong"), "test-model")
    assert not result.passed
    assert "expected 'right'" in result.detail
    # A failed run KEEPS its workspace: the evidence is the point.
    assert (workspace / "notes.txt").exists()


def test_a_model_that_never_acts_fails_with_a_readable_reason(tmp_path):
    task = Task(
        id="t",
        prompt="create the file",
        check={"type": "file_equals", "path": "hello.txt", "expected": "hi"},
    )
    result, _ = run_task(task, Scripted([AssistantTurn(text="I refuse", finish_reason="stop")]), "m")
    assert not result.passed
    assert "never written" in result.detail


def test_pytest_checker_verifies_by_running(tmp_path):
    task = Task(
        id="t",
        prompt="make pytest pass",
        setup={
            "total.py": "def total(xs):\n    return sum(xs)\n",
            "test_total.py": "from total import total\n\ndef test_ok():\n    assert total([1]) == 1\n",
        },
        check={"type": "pytest"},
    )
    result, _ = run_task(task, Scripted([AssistantTurn(text="already green", finish_reason="stop")]), "m")
    assert result.passed, result.detail


def test_json_checker_accepts_subset_and_flags_missing(tmp_path):
    task = Task(
        id="t",
        prompt="extract",
        setup={"data.csv": "name,score\nana,7.5\nbill,9.0\n"},
        check={
            "type": "json_matches",
            "path": "result.json",
            "expected": [{"name": "bill", "score": 9.0}],
        },
    )
    good = Task(id="t", prompt="p", check=task.check, setup=task.setup)
    result, _ = run_task(
        good,
        _write_then_done("result.json", json.dumps([{"name": "bill", "score": 9.0}])),
        "m",
    )
    assert result.passed, result.detail

    bad = Task(id="t", prompt="p", check=task.check, setup=task.setup)
    result, _ = run_task(bad, _write_then_done("result.json", "[]"), "m")
    assert not result.passed and "bill" in result.detail


def test_load_tasks_reads_the_bundled_set():
    tasks = load_tasks(__import__("pathlib").Path("benchmarks/tasks"))
    ids = {t.id for t in tasks}
    assert {"hello-file", "fix-off-by-one", "extract-top-scores"} <= ids
    for task in tasks:
        assert task.prompt and task.check


def test_the_bench_records_the_full_token_split():
    """Input, both cache buckets and output are tallied per run — the numbers a cost
    regression hides in. An unpriced model reports 0.0 USD: unknown, not free."""
    turns = [
        AssistantTurn(
            tool_calls=[
                ToolCall(id="c1", name="write_file", arguments={"path": "f.txt", "content": "x"})
            ],
            usage=TokenUsage(input=100, output=10, cache_read=900, cache_write=50),
        ),
        AssistantTurn(
            text="done",
            finish_reason="stop",
            usage=TokenUsage(input=20, output=5, cache_read=1_000),
        ),
    ]
    task = Task(
        id="t",
        prompt="create the file",
        check={"type": "file_equals", "path": "f.txt", "expected": "x"},
    )
    result, _ = run_task(task, Scripted(turns), "unpriced-model")
    assert result.passed
    assert result.input_tokens == 120
    assert result.cache_read_tokens == 1_900
    assert result.cache_write_tokens == 50
    assert result.output_tokens == 15
    assert result.cost_usd == 0.0


def test_estimate_cost_usd_prices_the_split(monkeypatch):
    """Per-class rates, with cache writes billed at the input rate when the sheet has no
    write-side rate (implicit caching)."""

    class Stub:
        def get(self, model):
            return {"cost": {"input": 3.0, "output": 15.0, "cache_read": 0.3}}

    monkeypatch.setattr("harness.llm.providers.catalog.models_dev", lambda: Stub())
    cost = estimate_cost_usd(
        "p:m",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    assert cost == pytest.approx(3.0 + 15.0 + 0.3 + 3.0)


def test_an_unpriced_model_is_unknown_not_free(monkeypatch):
    class Stub:
        def get(self, model):
            return None

    monkeypatch.setattr("harness.llm.providers.catalog.models_dev", lambda: Stub())
    assert estimate_cost_usd("x", input_tokens=1_000_000) is None


# -- the context side (a capped window must actually reach the engine) -----------------


_SUMMARY = "\n".join(
    f"## {heading}\nsummarized."
    for heading in SUMMARY_SECTIONS
)


class _Reader(Scripted):
    """Reads four big files, reporting a growing prompt; answers the summarizer too."""

    def __init__(self):
        turns = [
            AssistantTurn(
                tool_calls=[ToolCall(id=f"r{i}", name="read_file", arguments={"path": f"f{i}.txt"})],
                usage=TokenUsage(input=6_000 * (i + 1), output=50),
            )
            for i in range(4)
        ]
        turns.append(AssistantTurn(text="done", finish_reason="stop", usage=TokenUsage(input=30_000, output=5)))
        super().__init__(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text=_SUMMARY, finish_reason="stop", usage=TokenUsage(input=1_000, output=200))

    def stream(self, *, model, messages, tools=None, **settings):
        # The summarizer streams too (a non-streamed call let the read timeout cap the whole summary), so it
        # is told apart by its instruction rather than by which method it calls.
        if any("Produce ALL of the following sections" in str(m.get("content", "")) for m in messages):
            yield StreamChunk(turn=self.complete(model=model, messages=messages))
            return
        yield from super().stream(model=model, messages=messages, tools=tools, **settings)


def _reading_task():
    return Task(
        id="reader",
        prompt="read the files",
        setup={f"f{i}.txt": ("line of text that fills the file\n" * 400) for i in range(4)},
        check={"type": "file_equals", "path": "never.txt", "expected": "x"},
    )


def test_a_capped_window_compacts_and_the_run_reports_it():
    result, _ = run_task(
        _reading_task(), _Reader(), "test-model", compaction={"max_context_tokens": 30_000}
    )
    assert result.peak_context == 30_000
    assert result.compactions >= 1


def test_without_a_cap_nothing_compacts():
    result, _ = run_task(_reading_task(), _Reader(), "test-model")
    assert result.peak_context == 30_000
    assert result.compactions == 0 and result.summarizer_tokens == 0
