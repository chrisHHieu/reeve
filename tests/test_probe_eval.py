"""The ablation's measuring instruments, offline: the eval engine is the product's, each
probe discriminates, each probe makes its mechanism act, and the reading refuses to call
noise a result."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import re
from pathlib import Path

import pytest

from benchmarks.ablation import reading, suites
from benchmarks.harness_eval import ABLATABLE, ENGINE_ABLATABLE, fired, product_engine
from benchmarks.probe_eval import grade, load_probes, run_probe
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        if not self._turns:
            return AssistantTurn(text="Done.", finish_reason="stop")
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _call(name, **arguments):
    return AssistantTurn(
        text="",
        tool_calls=[ToolCall(id=f"c-{name}", name=name, arguments=arguments)],
        finish_reason="tool_calls",
    )


def _probe(probe_id):
    (probe,) = load_probes([probe_id])
    return probe


def test_the_eval_engine_defers_tools_like_the_product(tmp_path):
    """A bare `build_engine` carried 32 tools, 8.3k chars of deferrable schema, so the eval
    judged discovery on a configuration no user runs. Built through the manager it carries
    the product's own tools, engages exactly when the product would (its deferrable
    payload at the threshold — the memory tools leaving took a default install
    below it), and the ablation switch still turns it off."""
    from harness.tools.discovery import DISCOVERY_MIN_CHARS, deferred_payload_chars

    (tmp_path / "probe-ws").mkdir()
    probe = product_engine("fake:model", tmp_path / "probe-ws", tmp_path / "probe", "p", provider=ScriptedProvider([]))
    engages = deferred_payload_chars(probe.registry) >= DISCOVERY_MIN_CHARS
    probe.executor.close()
    for ablate, expected in ((frozenset(), engages), (frozenset({"tool_discovery"}), False)):
        state = tmp_path / ("off" if ablate else "on")
        workspace = tmp_path / f"ws-{bool(ablate)}"
        workspace.mkdir()
        engine = product_engine("fake:model", workspace, state, "s", ablate, provider=ScriptedProvider([]))

        async def go():
            async for _ in engine.run("hello"):
                pass

        asyncio.run(go())
        engine.executor.close()
        assert engine.registry.progressive_discovery is expected
        assert (fired(state, "tool_discovery") > 0) is expected


def test_a_question_in_an_eval_is_answered_as_unavailable(tmp_path):
    """Nobody answers an eval: an `ask_user` parked a probe run in the Inbox for 20 minutes.
    The question comes back unavailable and the run goes on to its answer."""
    (tmp_path / "ws").mkdir()
    provider = ScriptedProvider([_call("ask_user", question="Leave the red test alone?")])
    engine = product_engine("fake:model", tmp_path / "ws", tmp_path / "state", "q", provider=provider)

    async def go():
        events = []
        async for event in engine.run("fix it"):
            events.append(event)
        return events

    events = asyncio.run(asyncio.wait_for(go(), timeout=30))
    engine.executor.close()
    assert events[-1].type.value == "turn_end"
    results = [m for m in engine.messages if m.get("role") == "tool"]
    assert "asking isn't available here" in results[-1]["content"]


def _solve(probe_id, ws):
    """A reference solution for each probe — what a careful agent leaves behind."""
    if probe_id == "forget-the-caller":
        stock = ws / "inventory/stock.py"
        stock.write_text(stock.read_text().replace("def restock(item, qty)", "def restock(item, *, qty)"))
        receiving = ws / "inventory/receiving.py"
        receiving.write_text(receiving.read_text().replace("restock(item, qty)", "restock(item, qty=qty)"))
        return "Done."
    if probe_id == "red-and-unrelated":
        basic = ws / "stats/basic.py"
        basic.write_text(basic.read_text() + (
            "\n\ndef median(values):\n    values = sorted(values)\n    if not values:\n"
            "        raise ValueError('empty')\n    mid = len(values) // 2\n"
            "    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2\n"
        ))
        return "Added median. tests/test_rates.py::test_rates_2027 still fails: the 2027 export is missing."
    if probe_id == "retry-the-flake":
        return "Not green: test_sync_remote fails every run (connection reset)."
    if probe_id == "rename-everywhere":
        for path in (ws / "orders").glob("*.py"):
            path.write_text(re.sub(r"\bqty\b", "quantity", path.read_text()))
        return "Renamed."
    if probe_id == "three-modules":
        tax = ws / "billing/tax.py"
        tax.write_text(tax.read_text().replace(
            "return int(amount_cents * rate_percent / 100)",
            "return (amount_cents * rate_percent + 50) // 100",
        ))
        tiers = ws / "shipping/tiers.py"
        tiers.write_text(tiers.read_text().replace("weight_grams < bound", "weight_grams <= bound"))
        slug = ws / "catalog/slug.py"
        slug.write_text(slug.read_text().replace(
            're.sub(r"[^a-z0-9]", "-", name.lower())',
            're.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")',
        ))
        return "Fixed billing rounding, the shipping tier bound, and slug hyphens."
    if probe_id == "seen-before":
        table = ws / "shop/data/tables/t017.py"
        table.write_text(table.read_text().replace("'EU-North'", "'eu-north'"))
        return "The regional rates table spelled a key in mixed case; lookups are lower-cased."
    if probe_id == "figures-in-the-paper":
        notes = ws / "reading-notes.md"
        notes.write_text(notes.read_text() + "- Context as a Tool, https://arxiv.org/abs/2512.22087: 53.8%\n")
        return "Nemotron-3 30B without planning: 13.6%; Threshold-Compression: 53.8%; GPT-4.1 at 16 updates: 93.04."
    if probe_id == "chart-from-the-file":
        return "Nhà ở is the largest: rent every month plus the air conditioner."
    if probe_id == "two-loans":
        return "A is cheaper at a 10.5% float; try the sliders."
    if probe_id == "just-a-fact":
        return "Canberra."
    if probe_id == "decide-in-a-sentence":
        return "Gói A: lãi thả nổi trên 10% thì gói B đắt hơn từ năm thứ ba trở đi."
    if probe_id == "chart-from-the-workbook":
        return "Doanh thu đi lên đều; Miền Bắc kéo tăng trưởng nhiều nhất.\n\n:visual[v1]"
    if probe_id == "plan-on-a-timeline":
        return "Thiết kế chồng lên khảo sát, phát triển chồng lên kiểm thử.\n\n:visual[v1]"
    if probe_id == "sales-by-country":
        return "Hoa Kỳ là thị trường lớn nhất, rồi Trung Quốc.\n\n:visual[v1]"
    if probe_id == "which-model-after-a-switch":
        return "Giờ là DeepSeek V4.1 Flash, model id `opencode-go:deepseek-v4.1-flash`; câu ở trên là của Gemini 3.8 Flash."
    if probe_id == "ledger-over-mcp":
        (ws / "tong-chi.txt").write_text("17350000\n", encoding="utf-8")
        return "Tổng chi 17.350.000 VND; Ăn uống chi nhiều nhất (6.400.000 VND)."
    raise AssertionError(f"no reference solution for {probe_id}")


def _visual(*blocks):
    return {"name": "show_visual", "status": "ok", "card": {"card": "visual", "blocks": list(blocks)}}


#: The calls a careful session makes for each probe that is graded on its calls.
_CALLS = {
    "chart-from-the-file": [_visual({"type": "chart", "labels": ["Nhà ở"], "series": [], "source": {"file": "chi-tieu-q3.csv"}})],
    "two-loans": [_visual({"type": "what_if", "inputs": [], "outputs": []})],
    "just-a-fact": [],
    "decide-in-a-sentence": [],
    "chart-from-the-workbook": [_visual({"type": "chart", "labels": ["2026-01-01"], "series": [],
                                         "source": {"file": "doanh-thu-2026.xlsx"}})],
    "plan-on-a-timeline": [_visual({"type": "timeline", "items": []})],
    "sales-by-country": [_visual({"type": "map", "regions": [], "points": [], "source": {"file": "xuat-khau.csv"}})],
}


def _setup(probe, ws):
    from benchmarks.probe_eval import write_setup

    write_setup(probe, ws)


@pytest.mark.parametrize("probe_id", [probe["id"] for probe in load_probes()])
def test_each_probe_fails_untouched_and_passes_solved(tmp_path, probe_id):
    """A probe that passes without work, or fails a correct solution, measures nothing."""
    from benchmarks.probe_eval import with_model

    probe = with_model(_probe(probe_id), "opencode-go:deepseek-v4.1-flash")
    _setup(probe, tmp_path / "untouched")
    assert grade(probe, tmp_path / "untouched", "All good, ready to go.")["passed"] is False
    _setup(probe, tmp_path / "solved")
    answer = _solve(probe_id, tmp_path / "solved")
    expected = {"passed": True, "check": True, "touched": [], "missing": []}
    if probe.get("tools"):
        expected["tools"] = True
    assert grade(probe, tmp_path / "solved", answer, _CALLS.get(probe_id)) == expected


def test_a_visual_is_graded_on_what_was_shown_and_where_its_numbers_came_from(tmp_path):
    """Typed numbers, a refused call and the wrong block each fail the visual probes, and a
    visual where text would do fails the one that asks a plain question."""
    chart = _probe("chart-from-the-file")
    _setup(chart, tmp_path)
    answer = _solve("chart-from-the-file", tmp_path)
    typed = [_visual({"type": "chart", "labels": ["Nhà ở"], "series": []})]
    assert grade(chart, tmp_path, answer, typed)["tools"] == "typed the numbers instead of naming the file"
    refused = [{"name": "show_visual", "status": "ok", "card": None}]
    assert grade(chart, tmp_path, answer, refused)["tools"] == "1 show_visual call(s), none shown"
    loans = _probe("two-loans")
    assert "not any of ['what_if']" in grade(loans, tmp_path, "x", _CALLS["chart-from-the-file"])["tools"]
    fact = _probe("just-a-fact")
    assert grade(fact, tmp_path, "Canberra.", _CALLS["two-loans"])["tools"] == "called show_visual where text would do"
    restated = answer + "\n\n| Danh mục | Số tiền |\n| --- | ---: |\n| Nhà ở | 19.800.000 |\n"
    assert grade(chart, tmp_path, restated, _CALLS["chart-from-the-file"])["tools"] == "restated the visual as a table in the answer"


def test_the_probe_reads_a_visual_off_the_product_engine(tmp_path):
    """Offline, through the engine a user runs: the tool is there, the harness reads the file,
    and the grade is made from the events the session emitted."""
    probe = _probe("chart-from-the-file")
    provider = ScriptedProvider([
        _call("show_visual", blocks=[{"type": "chart", "kind": "bar", "title": "Q3",
                                      "source": {"file": "chi-tieu-q3.csv", "x": "danh_muc", "y": "so_tien"}}]),
        AssistantTurn(text="Nhà ở là lớn nhất.", finish_reason="stop"),
    ])
    row = asyncio.run(run_probe("fake:model", probe, tmp_path / "state", "t", provider=provider))
    assert row["tools"] is True and row["passed"] is True
    assert row["calls"][0]["name"] == "show_visual" and row["calls"][0]["status"] == "ok"


def test_forget_the_caller_catches_the_edit_that_stops_early(tmp_path):
    """The failure the verification gate exists for: change the signature, never run
    anything, declare victory. The probe grades it FAIL."""
    probe = _probe("forget-the-caller")
    _setup(probe, tmp_path)
    stock = tmp_path / "inventory/stock.py"
    stock.write_text(stock.read_text().replace("def restock(item, qty)", "def restock(item, *, qty)"))
    verdict = grade(probe, tmp_path, "Done - qty is keyword-only now.")
    assert verdict["passed"] is False and verdict["check"] is not True


def test_touching_an_off_limits_file_fails_the_probe(tmp_path):
    probe = _probe("red-and-unrelated")
    _setup(probe, tmp_path)
    answer = _solve("red-and-unrelated", tmp_path)
    (tmp_path / "tests/test_rates.py").write_text("def test_nothing():\n    pass\n")
    assert grade(probe, tmp_path, answer)["touched"] == ["tests/test_rates.py"]


def _edit_stock_and_stop(probe):
    body = probe["setup"]["inventory/stock.py"].replace("def restock(item, qty)", "def restock(item, *, qty)")
    return [_call("read_file", path="inventory/stock.py"), _call("write_file", path="inventory/stock.py", content=body)]


@pytest.mark.parametrize("ablate, acted", [(frozenset(), 1), (frozenset({"verify_nudge"}), 0)])
def test_the_probe_makes_its_mechanism_act(tmp_path, ablate, acted):
    """The first ablation's engine nudges never acted: every long task told the model to
    run pytest. An agent that edits and stops on this probe draws the gate, and the
    switch silences it."""
    probe = _probe("forget-the-caller")
    provider = ScriptedProvider(_edit_stock_and_stop(probe))
    row = asyncio.run(run_probe("fake:model", probe, tmp_path / "state", "t", ablate, provider=provider))
    assert fired(tmp_path / "state", "verify_nudge") == acted
    assert row["passed"] is False


class _Watching(ScriptedProvider):
    """A scripted provider that keeps every request it was sent."""

    def __init__(self, turns):
        super().__init__(turns)
        self.requests = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.requests.append([dict(message) for message in messages])
        return super().complete(model=model, messages=messages, tools=tools, **settings)


def _pytest_here():
    """The suite, run by this interpreter through the product's own shell: PowerShell wants
    the call operator before a quoted path, a POSIX shell does not."""
    quoted = f'"{sys.executable}"'
    return f"{'& ' if os.name == 'nt' else ''}{quoted} -m pytest -q -p no:cacheprovider"


def _said_of_repairs(request):
    return [str(m.get("content")) for m in request if "repaired this failure before" in str(m.get("content"))]


@pytest.mark.parametrize("ablate, acted", [(frozenset(), 1), (frozenset({"repair_recall"}), 0)])
def test_seen_before_makes_the_recall_act(tmp_path, ablate, acted):
    """The probe's project already holds a repair of the test that fails. An agent that only
    runs the suite is told where that repair was, in the very next request; the switch
    silences it. The real shell and the real pytest: the failure's name is read off output
    the harness did not write."""
    probe = _probe("seen-before")
    provider = _Watching([_call("run_shell", command=_pytest_here())])
    row = asyncio.run(run_probe("fake:model", probe, tmp_path / "state", "t", ablate, provider=provider))
    assert row["passed"] is False  # nothing was fixed
    assert fired(tmp_path / "state", "repair_recall") == acted
    assert _said_of_repairs(provider.requests[0]) == []  # nothing is said before the failure is seen
    said = _said_of_repairs(provider.requests[-1])
    assert len(said) == acted
    if acted:
        assert "`tests/test_report.py::test_total_includes_regional_tax` stopped failing" in said[0]
        assert "`shop/data/tables/t017.py`" in said[0] and "(14 steps then)" in said[0]
        assert "Back as before that repair: `shop/data/tables/t017.py`." in said[0]
        assert "-    'EU-North': 0.25," in said[0] and "+    'eu-north': 0.25," in said[0]  # what it changed


def test_a_repair_made_in_one_session_is_recalled_in_the_next(tmp_path):
    """Both halves through the product's engine, with nothing seeded: one session repairs a
    failing test, the bug comes back, and a second session of the same project is told."""
    workspace = tmp_path / "ws"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "conftest.py").write_text("import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n")
    (workspace / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (workspace / "tests" / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")

    def session(tag, turns):
        provider = _Watching(turns)
        engine = product_engine("fake:model", workspace, tmp_path / "state", tag, provider=provider)
        assert engine.repairs is not None  # the product builds the store with the project's memory

        async def go():
            async for _ in engine.run("make the suite green"):
                pass

        try:
            asyncio.run(asyncio.wait_for(go(), timeout=120))
        finally:
            engine.executor.close()
        log = tmp_path / "state" / "conversations" / f"{tag}.events.jsonl"
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return provider, [r for r in rows if str(r.get("type", "")).startswith(("repair/", "invariant/"))]

    _, rows = session("first", [
        _call("run_shell", command=_pytest_here()),
        _call("read_file", path="calc.py"),
        _call("write_file", path="calc.py", content="def add(a, b):\n    return a + b\n"),
        _call("run_shell", command=_pytest_here()),
    ])
    # The product's undo store took the file's before-image at the edit: the record knows
    # what the repair started from.
    assert [(r["type"], r["marks"], r["files"], r["before"], r["steps"]) for r in rows] == [("repair/fixed", 1, 1, 1, 3)]
    (kept,) = json.loads(next((tmp_path / "state").rglob(".repairs.json")).read_text(encoding="utf-8"))["repairs"]
    assert (kept["session"], kept["turn"], kept["marks"]) == ("first", 1, ["test:tests/test_calc.py::test_add"])

    (workspace / "calc.py").write_text("def add(a, b):\n    return a - b  # the merge brought it back\n")
    provider, rows = session("second", [_call("run_shell", command=_pytest_here())])
    assert [(r["type"], r["marks"], r["repairs"], r["undone"], r["changed"]) for r in rows] == [
        ("repair/recalled", 1, 1, 0, 1)
    ]
    (said,) = _said_of_repairs(provider.requests[-1])
    assert "`tests/test_calc.py::test_add` stopped failing" in said and "`calc.py` (3 steps then)" in said
    assert "Changed since: `calc.py`." in said and "Back as before" not in said

    # The repair undone to the byte: said as that.
    (workspace / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    provider, rows = session("third", [_call("run_shell", command=_pytest_here())])
    assert [(r["type"], r["undone"], r["diffs"], r["changed"]) for r in rows] == [("repair/recalled", 1, 1, 0)]
    (said,) = _said_of_repairs(provider.requests[-1])
    assert "Back as before that repair: `calc.py`." in said and "Changed since" not in said
    # What the first session's repair changed, as the product's undo store saw it.
    assert "```diff\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n```" in said


def test_a_costly_repair_is_asked_for_its_cause_and_the_lesson_comes_back_with_the_recall(tmp_path):
    """Step three through the product's engine and its memory door: a repair that took many
    steps is asked about, the lesson the agent writes is admitted, and the next session that
    meets the failure is told of the repair AND shown the lesson."""
    workspace = tmp_path / "ws"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "conftest.py").write_text("import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n")
    (workspace / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (workspace / "tests" / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")

    def session(tag, turns):
        provider = _Watching([])
        engine = product_engine("fake:model", workspace, tmp_path / "state", tag, provider=provider)
        provider._turns = list(turns(engine))

        async def go():
            async for _ in engine.run("make the suite green"):
                pass

        try:
            asyncio.run(asyncio.wait_for(go(), timeout=180))
        finally:
            engine.executor.close()
        log = tmp_path / "state" / "conversations" / f"{tag}.events.jsonl"
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return provider, [r for r in rows if str(r.get("type", "")).startswith(("repair/", "invariant/"))]

    lesson = (
        "---\nname: lesson-add-sign\ndescription: add() subtracted; the sign in calc.py was flipped by a merge\n"
        "metadata:\n  type: lesson\n---\n`tests/test_calc.py::test_add` failed with `assert -1 == 5`. "
        "`calc.py` returned `a - b`. Fixed by restoring `a + b`.\n"
    )

    def first(engine):
        yield _call("run_shell", command=_pytest_here())
        for _ in range(7):  # a long look around before the fix: what makes the repair costly
            yield _call("read_file", path="calc.py")
        yield _call("write_file", path="calc.py", content="def add(a, b):\n    return a + b\n")
        yield _call("run_shell", command=_pytest_here())
        yield _call("write_file", path=str(engine.repairs.root / "lesson_add_sign.md"), content=lesson)

    provider, rows = session("first", first)
    assert [(r["type"], r["steps"], r.get("asked")) for r in rows] == [("repair/fixed", 9, True)]
    asked = [r for r in provider.requests if any("took 9 steps to repair" in str(m.get("content")) for m in r)]
    assert asked and "save it now as a `lesson` memory" in str(asked[0][-1]["content"])
    saved = next((tmp_path / "state").rglob("lesson_add_sign.md"))
    assert saved.read_text(encoding="utf-8").startswith("---\nname: lesson-add-sign")  # the door admitted it

    (workspace / "calc.py").write_text("def add(a, b):\n    return a - b  # back again\n")
    provider, rows = session("second", lambda engine: [_call("run_shell", command=_pytest_here())])
    assert [r["type"] for r in rows] == ["repair/recalled"]
    (said,) = _said_of_repairs(provider.requests[-1])
    assert "A note was saved with that repair: `lesson_add_sign.md` (add() subtracted; the sign in calc.py was flipped by a merge)." in said


def test_every_engine_mechanism_has_a_probe():
    """A nudge measured on the long suite never acts there (its prompts say "run pytest")."""
    measured = suites(list(ABLATABLE))
    assert {name for name in ENGINE_ABLATABLE if measured[name] == "long"} == set()
    # The prompt section has its own probe (O10); the other non-engine mechanisms act only
    # in a long, compacting session.
    assert measured["delegation_prompt"] == "three-modules"
    # Taking the task list away is measured where the todo nudge is: a many-step task.
    assert measured["todo_list"] == measured["todo_nudge"] == "rename-everywhere"
    # Discovery acts where an MCP server's deferred tools are reached for.
    assert measured["tool_discovery"] == "ledger-over-mcp"
    assert {
        measured[name]
        for name in ABLATABLE
        if name not in ENGINE_ABLATABLE and name not in ("delegation_prompt", "todo_list", "tool_discovery")
    } == {"long"}


def test_the_task_list_and_the_argument_fold_switches_take(tmp_path):
    """A switch that did not take makes a variant identical to its baseline and reports
    "worth nothing". Offline: the engines are built, no request is made."""
    from harness.modes.agents.reeve import PLANNING_SECTION

    built = {}
    for tag, ablate, enable in (
        ("base", frozenset(), frozenset()),
        ("off", frozenset({"todo_list", "prune_write_arguments"}), frozenset()),
    ):
        (tmp_path / f"ws-{tag}").mkdir()
        engine = product_engine(
            "fake:model", tmp_path / f"ws-{tag}", tmp_path / tag, tag, ablate,
            provider=ScriptedProvider([]), enable=enable,
        )
        engine.executor.close()
        built[tag] = engine
    base, off = built["base"], built["off"]
    assert base.registry.get("todo_write") is not None and PLANNING_SECTION in base.messages[0]["content"]
    assert off.registry.get("todo_write") is None and PLANNING_SECTION not in off.messages[0]["content"]
    assert base._prunes_arguments() is True and off._prunes_arguments() is False


def _rows(passed, tokens, acted):
    return [{"passed": p, "tokens": t, "fired": {"m": acted}} for p, t in zip(passed, tokens)]


def test_the_reading_calls_only_what_clears_the_noise():
    base = _rows([True] * 3, [100, 130, 120], acted=1)
    assert reading("m", _rows([True] * 3, [100, 130, 120], acted=0), _rows([True] * 3, [90, 125, 140], 0)) == "never acted in the baseline: no reading"
    assert reading("m", base, _rows([True] * 3, [90, 125, 140], 0)) == "within noise"
    assert reading("m", base, _rows([True] * 3, [150, 160, 170], 0)) == "cost: dearer without it"
    assert reading("m", base, _rows([False, False, False], [100, 110, 120], 0)) == "quality: worse without it"
    # The same three runs, read as the third of five planned rounds, are not yet a result.
    assert reading("m", base, _rows([False, False, False], [100, 110, 120], 0), looks=5) == "within noise"
    # One pass in three against three in three is inside the unchanged harness's own spread;
    # the rule before it called this "worse".
    assert reading("m", base, _rows([False, False, True], [100, 110, 120], 0)) == "within noise"
    assert reading("m", base, _rows([True, True, False], [100, 110, 120], 0)) == "within noise"
    # A run that never reached a model is on neither side: three of them are not three failures.
    void = [{**row, "outcome": "void"} for row in _rows([False] * 3, [0, 0, 0], 0)]
    assert reading("m", base, void) == "no runs"


def test_a_runs_conversation_folder_is_its_own(tmp_path, monkeypatch):
    """Each eval run's conversation folder lives in that run's state, not a shared base: keyed by
    the run's tag in the user's ~/Reeve, a probe's third run found the files its first left
    (2026-10-09, 0 of 9 deepseek two-loans runs calling show_visual)."""
    monkeypatch.delenv("REEVE_SCRATCH_BASE", raising=False)
    scratch = {}
    for name in ("first", "second"):
        (tmp_path / f"ws-{name}").mkdir()
        engine = product_engine("fake:model", tmp_path / f"ws-{name}", tmp_path / name, "two-loans-0",
                                provider=ScriptedProvider([]))
        engine.executor.close()
        scratch[name] = [r.path for r in engine.permissions.roots if r.label == "scratch"]
    # Named at build, made by the run's first turn (the engine's `_make_scratch`).
    assert scratch["first"] == [(tmp_path / "first" / "scratch" / "two-loans-0").resolve()]
    assert scratch["second"] == [(tmp_path / "second" / "scratch" / "two-loans-0").resolve()]
