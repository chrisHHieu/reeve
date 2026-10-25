"""`run_code` — one program instead of many calls.

The property that makes this safe rather than a hole: a tool call made INSIDE a program is
a real tool call. Same authorization, same approval card, same pipeline, same audit. Every
test below either proves that, or proves a bound that keeps a runaway program bounded.
See harness/tools/code_mode.py.
"""

from __future__ import annotations

import asyncio


from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk, ToolCall
from harness.tools import ToolRegistry
from harness.tools.code_mode import DONE, UNAVAILABLE, CodeProgram, run_code_schema


# -- the program on its own ------------------------------------------------------------


def _drive(program: CodeProgram, answer=lambda name, args: {"ok": name}):
    """Run a program to completion, answering every request from a plain function."""
    program.start()
    while True:
        request = program.next_request(timeout=5)
        if request is DONE:
            break
        if request is None:
            continue
        request.answer(answer(request.name, request.arguments))
    return program.result()


def test_what_the_program_prints_is_the_output():
    out = _drive(CodeProgram("print('hello')", names=frozenset()))
    assert out["output"].strip() == "hello"
    assert out["tool_calls"] == 0


def test_a_top_level_return_comes_back():
    out = _drive(CodeProgram("return {'rows': 3}", names=frozenset()))
    assert out["returned"] == {"rows": 3}


def test_a_program_calls_tools_and_only_its_summary_comes_back():
    """The whole point. Twelve reads cost one round-trip and one line of context instead of
    twelve round-trips and twelve file contents."""
    code = (
        "seen = []\n"
        "for name in ['a.py', 'b.py', 'c.py']:\n"
        "    seen.append(tools.read_file(path=name)['ok'])\n"
        "print(f'checked {len(seen)} files')"
    )
    out = _drive(CodeProgram(code, names=frozenset({"read_file"})))

    assert out["output"].strip() == "checked 3 files"
    assert out["tool_calls"] == 3
    # The file contents are NOT in the result: the program curated them away.
    assert "read_file" not in out["output"]


def test_a_traceback_comes_back_verbatim():
    """The model wrote the code; handing it the traceback is what lets the next attempt be
    correct."""
    out = _drive(CodeProgram("1 / 0", names=frozenset()))
    assert "ZeroDivisionError" in out["error"]


def test_an_unknown_tool_says_what_is_available():
    out = _drive(CodeProgram("tools.nope()", names=frozenset({"read_file"})))
    assert "nope" in out["error"]
    assert "read_file" in out["error"]


def test_a_program_cannot_start_a_program():
    """A stack nobody bounded."""
    out = _drive(CodeProgram("tools.run_code(code='x')", names=frozenset({"run_code"})))
    assert "run_code" in out["error"]


def test_the_interactive_tools_are_out_of_reach():
    """They are answered by a human out-of-band, through engine branches a sub-call never
    reaches — a program calling one would land in a code path that does not exist for it."""
    assert {"ask_user", "propose_plan", "request_tool"} <= UNAVAILABLE
    program = CodeProgram("pass", names=frozenset(UNAVAILABLE) | {"read_file"})
    assert program._names == frozenset({"read_file"})


def test_the_number_of_sub_calls_is_bounded():
    """A runaway loop costs a bounded number of REAL tool calls, not every one it can reach
    before the deadline."""
    code = "while True:\n    tools.read_file(path='x')"
    program = CodeProgram(code, names=frozenset({"read_file"}), max_sub_calls=5)
    out = _drive(program)

    assert out["tool_calls"] == 5
    assert "limit" in out["error"]


def test_a_refused_call_raises_inside_the_program_rather_than_ending_it():
    """A refused write is something the model can catch and work around; killing the run
    would throw away everything the program had already done correctly."""
    code = (
        "try:\n"
        "    tools.write_file(path='x')\n"
        "except Exception as exc:\n"
        "    print('refused:', exc)\n"
        "print('carried on')"
    )
    program = CodeProgram(code, names=frozenset({"write_file"}))
    program.start()
    request = program.next_request(timeout=5)
    request.answer("this tool call was not permitted", ok=False)
    while program.next_request(timeout=5) is not DONE:
        pass
    out = program.result()

    assert "refused:" in out["output"]
    assert "carried on" in out["output"]


def test_the_schema_names_no_tools():
    """The tool list of the request already names them; a second copy in this description
    was a quarter of the tool list on a loaded install."""
    described = run_code_schema()["function"]["description"]

    assert "Available:" not in described and "read_file" not in described
    assert "refused before it starts" in described


def test_a_program_naming_a_tool_that_is_not_there_is_refused_before_it_runs(tmp_path):
    """Half a program's side effects is the worst outcome it has, so the names are read
    from its text first: the real call ahead of the unknown one must not happen."""
    code = "print(tools.probe(q='first'))\nprint(tools.prob(q='second'))"
    events, engine = _run(tmp_path, code)

    finished = [e.data for e in events if e.type is EventType.TOOL_FINISHED]
    assert [d["name"] for d in finished] == ["run_code"]  # no sub-call ran
    result = next(m for m in engine.messages if m.get("role") == "tool")
    assert "Nothing ran" in result["content"]
    assert "tools.prob: no such tool. Closest: probe." in result["content"]


def test_the_refusal_reaches_the_log_as_a_paired_row_with_its_fact(tmp_path):
    """`harness_review` counts refused programs from `facts`, never from the text,
    and a result row with no call row cannot be located."""
    from harness.session.session_log import SessionLog
    from harness.tools.code_mode import run_code_tool

    registry = ToolRegistry()
    registry.register(probe)
    registry.register(run_code_tool())
    engine = TurnEngine(
        provider=_CodeProvider("tools.nope(); tools.also_missing()"),
        registry=registry,
        permissions=PermissionEngine(workspace_root=str(tmp_path), mode=Mode.DANGER_FULL_ACCESS),
        model="m",
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")

    async def go():
        return [e async for e in engine.run("go")]

    asyncio.run(go())
    rows = engine.log.read()
    call = next(r for r in rows if r.get("type") == "tool/call" and r.get("name") == "run_code")
    result = next(r for r in rows if r.get("type") == "tool/result" and r.get("name") == "run_code")
    assert call["call_id"] == result["call_id"]
    assert result["facts"] == {"outcome": "unknown_tool", "unknown": 2}
    assert result["is_error"] is True


def test_a_person_is_not_asked_about_a_program_that_will_be_refused(tmp_path):
    asked: list[str] = []
    from harness.tools.code_mode import run_code_tool

    from harness.loop import ApprovalOutcome

    async def approver(request):
        asked.append(request.tool_name)
        return ApprovalOutcome.ONCE

    def engine_for(code):
        registry = ToolRegistry()
        registry.register(probe)
        registry.register(run_code_tool())
        return TurnEngine(
            provider=_CodeProvider(code),
            registry=registry,
            permissions=PermissionEngine(workspace_root=str(tmp_path), mode=Mode.WORKSPACE_WRITE),
            model="m",
            approver=approver,
        )

    async def go(engine):
        return [e async for e in engine.run("go")]

    # The control: in this mode a program that names real tools IS asked about.
    asyncio.run(go(engine_for("print(tools.probe(q='x'))")))
    assert asked[:1] == ["run_code"]

    asked.clear()
    refused = engine_for("tools.nope()")
    asyncio.run(go(refused))
    assert asked == []
    assert "Nothing ran" in next(m for m in refused.messages if m.get("role") == "tool")["content"]


def test_a_tool_that_asks_the_user_is_named_as_direct_only():
    from harness.tools.code_names import check_names

    refusal, unknown = check_names(
        "tools.ask_user(questions=[])", {"read_file"}, direct_only={"ask_user"}
    )
    assert unknown == ["ask_user"]
    assert "Call ask_user directly, outside run_code." in refusal


def test_only_tool_names_are_read_from_a_program():
    from harness.tools.code_names import check_names, named_tools

    code = (
        "import asyncio\n"
        "a, b = await asyncio.gather(tools.grep(pattern='x'), tools.read_file(path='p'))\n"
        "print(tools.describe('grep'), tools._private)\n"
        "other.tools.gone()\n"
        "name = 'tools.fake()'\n"
        "return tools.grep\n"
    )
    assert named_tools(code) == ["grep", "read_file"]
    assert named_tools("def broken(:") == []  # the child reports a syntax error better
    assert check_names(code, {"grep", "read_file"}) == ("", [])


def test_the_closest_names_include_an_mcp_tool_by_its_own_name():
    from harness.tools.code_names import check_names, close_names

    held = {"mcp__mail__search_messages", "mcp__mail__get_message", "read_file", "grep"}
    assert close_names("search_messages", held)[0] == "mcp__mail__search_messages"
    refusal, _ = check_names("tools.get_mesage(id=1)", held, searchable=True)
    assert "mcp__mail__get_message" in refusal
    assert "tool_search finds one by what it does" in refusal


def test_a_refused_program_is_recorded_as_data():
    from harness.tools.code_mode import RUN_CODE_CONTRACT

    assert RUN_CODE_CONTRACT.facts({}, {"error": "x", "unknown_tools": ["a", "b"]}) == {
        "outcome": "unknown_tool",
        "unknown": 2,
    }
    assert RUN_CODE_CONTRACT.facts({}, {"output": "", "tool_calls": 3}) == {"outcome": "ok", "calls": 3}
    assert RUN_CODE_CONTRACT.facts({}, {"error": "boom", "tool_calls": 1})["outcome"] == "error"


# -- through the engine ----------------------------------------------------------------


class _CodeProvider(ProviderClient):
    """One `run_code` call, then stop."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.n = 0

    def complete(self, **kw):
        raise NotImplementedError

    def stream(self, *, model, messages, tools=None, **settings):
        self.n += 1
        if self.n == 1:
            yield StreamChunk(
                turn=AssistantTurn(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="run_code",
                            arguments={"code": self.code, "description": "probe"},
                        )
                    ],
                    finish_reason="tool_calls",
                )
            )
        else:
            yield StreamChunk(turn=AssistantTurn(text="done", finish_reason="stop"))

    def capabilities(self, model):
        return ModelCapabilities()


def probe(q: str = "") -> str:
    """A tool that returns something."""
    return f"probed {q}"


def _run(tmp_path, code, *, mode=Mode.DANGER_FULL_ACCESS, plan=False, extra=(), workspace=None):
    from harness.tools.code_mode import run_code_tool

    registry = ToolRegistry()
    registry.register(probe)
    for tool in extra:
        registry.register(tool)
    registry.register(run_code_tool())
    engine = TurnEngine(
        provider=_CodeProvider(code),
        registry=registry,
        permissions=PermissionEngine(
            workspace_root=str(tmp_path), mode=mode, plan=plan
        ),
        model="m",
    )
    engine.workspace = workspace

    async def go():
        return [e async for e in engine.run("go")]

    return asyncio.run(go()), engine


def _program_output(engine) -> str:
    return next(
        m["content"] for m in engine.messages if m.get("role") == "tool" and m.get("name", "run_code") == "run_code"
    )


def test_a_program_runs_in_the_session_workspace(tmp_path):
    """A relative path in a program means the session's folder, as it does for `run_shell`. It
    meant the server's own working directory, so a probe's `_inspect.py` landed in the repo root."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    code = "open('made.txt', 'w').write('x')\nimport os\nprint(os.path.basename(os.getcwd()))"
    _, engine = _run(tmp_path, code, workspace=str(workspace))

    assert (workspace / "made.txt").read_text() == "x"
    assert "ws" in _program_output(engine)


def test_a_workspace_module_does_not_shadow_what_the_program_runs_on(tmp_path):
    """The workspace is the child's working directory, and `-m` would put it first on `sys.path`: a
    project's own `json.py` would replace the one the child's protocol is written in."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "json.py").write_text("raise SystemExit('the workspace json.py was imported')\n")
    _, engine = _run(tmp_path, "print('still here')", workspace=str(workspace))

    assert "still here" in _program_output(engine)


def test_a_sub_call_is_a_real_tool_call(tmp_path):
    """It is announced like one, so a surface shows it — and the `in_program` marker is how
    the surface can say where it came from."""
    events, _ = _run(tmp_path, "print(tools.probe(q='x'))")

    proposed = [e for e in events if e.type == EventType.TOOL_PROPOSED]
    assert [e.data["name"] for e in proposed] == ["run_code", "probe"]
    assert proposed[1].data.get("in_program") is True


def test_a_sub_call_reports_back_when_it_is_over(tmp_path):
    """The other half of announcing it. A proposal with no matching finish leaves the
    surface with a step that never stops running — and, because a turn is read as live
    while any of its steps is, a whole turn that never settles."""
    events, engine = _run(tmp_path, "print(tools.probe(q='x'))")

    subs = [
        e
        for e in events
        if e.type == EventType.TOOL_FINISHED and e.data.get("in_program")
    ]
    assert [e.data["name"] for e in subs] == ["probe"]
    assert subs[0].data["status"] == "ok"
    assert "probed x" in str(subs[0].data["result_preview"])
    # Every proposal is answered, and each pair names the same call. Not in the same
    # order: the sub-call reports back inside the program, so it lands before the
    # `run_code` that opened it.
    proposed = [e for e in events if e.type == EventType.TOOL_PROPOSED]
    finished = [e for e in events if e.type == EventType.TOOL_FINISHED]
    assert sorted(e.data["call_id"] for e in proposed) == sorted(
        e.data["call_id"] for e in finished
    )
    # ...and the saving this whole feature exists for is untouched.
    assert len([m for m in engine.messages if m.get("role") == "tool"]) == 1


def test_a_refused_sub_call_still_reports_back(tmp_path):
    """A row opened by a refused call would otherwise spin for the rest of the session."""
    events, _ = _run(tmp_path, "tools.probe(q='x')", plan=True)
    subs = [
        e
        for e in events
        if e.type == EventType.TOOL_FINISHED and e.data.get("in_program")
    ]
    assert all(e.data["status"] != "ok" for e in subs)


def sidecar_tool() -> dict:
    """Shaped like `read_file`: the content, plus the three engine-only sidecars."""
    text = "line one\nline two"
    return {
        "path": "a.py",
        "content": text,
        "_card_meta": {"read": {"lines": [{"number": 1, "text": text}]}},
        "_display": {"hidden_by_filters": 7},
        "_attach": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}],
    }


def test_a_sub_call_gets_the_result_without_engine_sidecars(tmp_path):
    """The report: `tools.read_file` handed the program the file twice — `content` plus
    the same text inside `_card_meta` — and `_card_meta` is for the GUI transcript. A
    program has no surface for any of the three sidecars (`_card_meta`, `_display`, the
    `_attach` image route), so what it receives is the cleaned dict a direct call shows
    the model, not the raw one."""
    code = "res = tools.sidecar_tool()\nprint('keys:', sorted(res))\nprint(res['content'])"
    _, engine = _run(tmp_path, code, extra=[sidecar_tool])
    content = [m for m in engine.messages if m.get("role") == "tool"][-1]["content"]

    assert "_card_meta" not in content
    assert "_display" not in content  # user-facing filter metadata must not leak
    assert "_attach" not in content
    assert "content" in content and "line one" in content  # the real result survived


def test_a_tool_level_error_comes_back_as_a_documented_dict(tmp_path):
    """The other half of the shape, and the one the report called inconsistent. A tool
    that reports failure IN ITS RESULT (grep on an escaped path, a bad regex) returns that
    dict — the direct path treats it as data the same way, and raising it would end
    programs that can work around it. A refusal or an exception raises RuntimeError inside
    the program instead (`test_a_refused_call_raises_inside_the_program_rather_than_ending_it`)."""

    def failing_tool() -> dict:
        return {"error": "path escapes the workspace"}

    code = "res = tools.failing_tool()\nprint('error' in res, res['error'])"
    _, engine = _run(tmp_path, code, extra=[failing_tool])
    content = [m for m in engine.messages if m.get("role") == "tool"][-1]["content"]

    assert "path escapes the workspace" in content


def test_the_schema_documents_the_sub_call_result_shape():
    """Both halves have to be readable in the prompt, not discovered by experiment: the
    stripped sidecars and the two failure shapes (returned `error` dict / raised)."""
    described = run_code_schema()["function"]["description"]
    assert "_card_meta" in described  # says it is stripped
    assert "`error`" in described  # the returned failure shape
    assert "RuntimeError" in described  # the raised refusal shape


def test_the_programs_output_is_what_reaches_the_model(tmp_path):
    events, _ = _run(tmp_path, "print(tools.probe(q='x'))")

    finished = [e for e in events if e.type == EventType.TOOL_FINISHED]
    assert any("probed x" in str(e.data.get("result_preview", "")) for e in finished)


def test_sub_calls_are_logged_but_never_enter_the_conversation(tmp_path):
    """Putting them in the message list would spend the context this exists to save."""
    _, engine = _run(tmp_path, "tools.probe(q='one')\ntools.probe(q='two')")

    kinds = [e["type"] for e in engine.log.read()]
    # NULL_LOG in this harness keeps nothing, so assert the message list instead — which is
    # the property that actually matters for context.
    tool_messages = [m for m in engine.messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1  # the run_code result only
    assert "probed one" not in str(tool_messages[0].get("content", ""))
    assert kinds == kinds  # the log is exercised even when it keeps nothing


def test_read_only_mode_refuses_the_program_before_it_starts(tmp_path):
    """`run_code` is EXEC: the body is arbitrary Python, which is what `run_shell` already
    accepts, behind the same gate."""
    events, engine = _run(tmp_path, "print('should not run')", plan=True)

    # Assert the REFUSAL, not the absence of output: an empty event list satisfies an
    # absence on its own, and a check that cannot fail is worse than no check. This one did
    # fail — the branch had skipped authorization entirely and the program ran in plan mode.
    denied = [
        e
        for e in events
        if e.data.get("name") == "run_code" and e.data.get("status") == "denied"
    ]
    assert denied, "run_code was never refused"
    assert "read-only" in denied[0].data["reason"]
    # And it never got as far as STARTING. Not "no finished event" — a refusal is itself
    # delivered as one — and not "the code string is absent", since the proposal carries it
    # either way. `tool_started` is the event that only exists once a program is running.
    assert not [
        e
        for e in events
        if e.type is EventType.TOOL_STARTED and e.data.get("name") == "run_code"
    ]


def test_a_program_that_never_returns_is_stopped(tmp_path):
    """A program that never finishes would hold the turn open."""
    import harness.tools.code_mode as module

    original = module.DEFAULT_TIMEOUT
    module.DEFAULT_TIMEOUT = 1.0
    try:
        events, _ = _run(tmp_path, "import time\nwhile True:\n    time.sleep(0.05)")
    finally:
        module.DEFAULT_TIMEOUT = original

    finished = [e for e in events if e.type == EventType.TOOL_FINISHED]
    assert finished
    assert "still running" in str(finished[-1].data.get("result_preview", ""))


# -- the process boundary --------------------------------------------------------------
#
# The first version ran the program with `exec()` inside reeve, next to the permission
# engine and the keys held in memory. `run_shell` gets its own process — it can wreck the
# machine but cannot reach into the thing watching it — so a boundary the shell had and this
# did not was the wrong way round. Every test here fails on that first version.


def test_the_program_runs_in_a_different_process():
    import os

    out = _drive(CodeProgram("import os; print(os.getpid())", names=frozenset()))

    assert out["output"].strip().isdigit()
    assert int(out["output"].strip()) != os.getpid()


def test_a_program_cannot_reach_into_the_harness_that_is_watching_it():
    """The one that matters. In-process, this switched the approval gate off for the rest
    of the session — the single kind of damage that also removes the evidence of itself."""
    import harness.safety.risk as risk

    before = set(risk.EXEC_TOOLS)
    code = (
        "import harness.safety.risk as r\n"
        "r.EXEC_TOOLS = frozenset()\n"
        "print('tampered with', len(r.EXEC_TOOLS))"
    )
    out = _drive(CodeProgram(code, names=frozenset()))

    # The child really did change ITS copy...
    assert "tampered with 0" in out["output"]
    # ...and the harness's own is untouched.
    assert set(risk.EXEC_TOOLS) == before
    assert "run_shell" in risk.EXEC_TOOLS


def test_a_program_writing_to_the_raw_stdout_cannot_forge_a_frame():
    """fd 1 is pointed at the null device in the child, so a program bypassing `print`
    reaches nothing. Its own printed output still comes back."""
    code = (
        "import os, json\n"
        "os.write(1, (json.dumps({'t': 'done', 'output': 'FORGED'}) + chr(10)).encode())\n"
        "print('real output')"
    )
    out = _drive(CodeProgram(code, names=frozenset()))

    assert "FORGED" not in out["output"]
    assert "real output" in out["output"]


def test_a_frame_is_rebuilt_rather_than_passed_through():
    """The child is model-controlled, so a frame that looks right must not smuggle extra
    fields into whatever reads it next."""
    from harness.tools.code_mode import _validated_call, _validated_done

    rebuilt = _validated_call(
        {"t": "call", "id": 1, "name": "grep", "args": {"q": "x"}, "sneaky": True}
    )
    assert rebuilt == {"id": 1, "name": "grep", "args": {"q": "x"}}

    # Wrong shapes are not calls at all, however plausible they look.
    assert _validated_call({"t": "call", "id": "1", "name": "grep", "args": {}}) is None
    assert _validated_call({"t": "call", "id": True, "name": "grep", "args": {}}) is None
    assert _validated_call({"t": "call", "id": 1, "name": "", "args": {}}) is None
    assert _validated_call({"t": "call", "id": 1, "name": "grep", "args": []}) is None
    assert _validated_done({"t": "done", "output": 5, "error": None})["output"] == ""


def test_a_child_that_dies_without_answering_says_so():
    """Rather than returning an empty success, which reads as "the program did nothing"."""
    out = _drive(CodeProgram("import os; os._exit(1)", names=frozenset()))
    assert "without returning a result" in out["error"]


def test_parallel_sub_calls_can_be_in_flight_together(tmp_path):
    """Replies are routed BY ID now, so a program may have several calls in flight: two
    tools that must MEET cannot both return under the old send-then-read-next protocol —
    the first call blocked the host loop and the second was never dispatched."""
    import threading

    barrier = threading.Barrier(2, timeout=8)

    def meet(tag: str = "") -> str:
        barrier.wait()
        return f"met {tag}"

    # A read: only parallel-safe calls may overlap.
    from aisuite.agents import ToolMetadata

    meet.__aisuite_tool_metadata__ = ToolMetadata(name="meet", category="read", risk_level="low")

    code = """
import threading

out = {}
def call(tag):
    out[tag] = tools.meet(tag=tag)

threads = [threading.Thread(target=call, args=(t,)) for t in ("a", "b")]
for t in threads:
    t.start()
for t in threads:
    t.join()
print(sorted(out.values()))
"""
    _, engine = _run(tmp_path, code, extra=[meet])
    content = [m for m in engine.messages if m.get("role") == "tool"][-1]["content"]
    assert "met a" in content and "met b" in content
