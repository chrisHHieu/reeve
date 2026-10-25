"""Plan mode (`tools/plan`): while a session rehearses, a write is turned back with a
reason and never asked about, and `propose_plan` is how the work is put to the person. An approved
plan leaves the rehearsal in the mode the person chose; a rejected one changes nothing and brings
back what they said. Outside the rehearsal a plan can still be proposed, and the mode stays.
"""

from __future__ import annotations

import asyncio

import aisuite as ai

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.loop import TurnEngine
from harness.safety.permissions import Mode, PermissionEngine
from harness.session.events import EventType
from harness.tools import ToolRegistry
from harness.tools.plan import propose_plan_tool

def _text_turn(text):
    return AssistantTurn(text=text, finish_reason="stop")


def _tool_turn(name, args, call_id="call_1"):
    return AssistantTurn(tool_calls=[ToolCall(id=call_id, name=name, arguments=args)], finish_reason="tool_calls")


class ScriptedProvider(ProviderClient):
    """Answers each request with the next queued turn."""

    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _plan_engine(tmp_path, turns, *, plan_approver=None, plan=True):
    """An engine with the file tools and `propose_plan`, rehearsing unless `plan=False`."""
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    registry.register(propose_plan_tool())
    permissions = PermissionEngine(workspace_root=tmp_path, plan=plan)
    engine = TurnEngine(provider=ScriptedProvider(turns), registry=registry, permissions=permissions, model="gpt-5.5", plan_approver=plan_approver)
    return engine, permissions


def _collect(engine, user_input):
    async def run():
        return [event async for event in engine.run(user_input)]

    return asyncio.run(run())


def _first_result(events):
    return next(event for event in events if event.type == EventType.TOOL_FINISHED).data["status"]


def _a_result_says(engine, words):
    return any(m.get("role") == "tool" and words in m["content"] for m in engine.messages)


def _kinds(events):
    return [event.type for event in events]


def test_plan_overlay_blocks_writes_without_asking(tmp_path):
    engine, _ = _plan_engine(tmp_path, [_tool_turn("write_file", {"path": "x.py", "content": "x"}), _text_turn("understood, planning instead")])
    events = _collect(engine, "fix the bug")
    assert EventType.PERMISSION_REQUIRED not in _kinds(events)  # turned back, not put to the person
    assert not (tmp_path / "x.py").exists()
    assert _first_result(events) == "denied" and _a_result_says(engine, "put this change in the plan")


def test_plan_approval_flips_mode_and_executes(tmp_path):
    proposed = []

    async def approve(args, tool_call_id=None):
        proposed.append(args.get("plan"))
        return {"approved": True, "mode": "auto"}

    turns = [_tool_turn("propose_plan", {"plan": "1. write x.py  2. verify"}), _tool_turn("write_file", {"path": "x.py", "content": "done\n"}, "call_2"), _text_turn("implemented")]
    engine, permissions = _plan_engine(tmp_path, turns, plan_approver=approve)
    kinds = _kinds(_collect(engine, "fix the bug"))
    assert EventType.PLAN_PROPOSED in kinds and proposed == ["1. write x.py  2. verify"]
    # the same session, now in full access and out of the rehearsal, does the write unasked
    assert permissions.mode is Mode.DANGER_FULL_ACCESS and permissions.plan is False
    assert EventType.PERMISSION_REQUIRED not in kinds
    assert (tmp_path / "x.py").read_text() == "done\n"


def test_an_approved_plan_gets_standing_on_the_engine(tmp_path):
    """The plan text rides the assistant tool call, which compaction clips; the engine
    keeps its own copy so the block can carry the plan the session is executing."""
    async def approve(args, tool_call_id=None):
        return {"approved": True, "mode": "auto"}

    plan = "1. migrate the table\n2. backfill\n3. verify counts"
    engine, _ = _plan_engine(
        tmp_path,
        [_tool_turn("propose_plan", {"plan": plan}), _text_turn("starting")],
        plan_approver=approve,
    )
    _collect(engine, "execute it")
    assert engine.approved_plan == plan


def test_the_compacted_block_carries_the_approved_plan():
    from harness.context.compaction import CompactionState, compacted_block

    state = CompactionState(
        boundary_index=1,
        summary_text="summary",
        working_state="",
        approved_plan="1. migrate the table\n2. verify counts",
    )
    block = compacted_block(state)
    assert "Approved plan" in block
    assert "1. migrate the table" in block


def test_approved_plan_survives_a_state_round_trip():
    from harness.context.compaction import CompactionState

    state = CompactionState(
        boundary_index=1, summary_text="", working_state="", approved_plan="PLAN X"
    )
    restored = CompactionState.from_dict(state.as_dict())
    assert restored is not None
    assert restored.approved_plan == "PLAN X"


def test_update_plan_writes_the_session_plan_file(tmp_path):
    """`update_plan` is engine-owned: it writes the session's `.reeve/plans/` file
    while the plan overlay's read-only boundary stays untouched (no write-tool exception)."""
    plan = "1. explore the schema\n2. migrate\n3. verify counts"
    engine, _ = _plan_engine(
        tmp_path,
        [_tool_turn("update_plan", {"content": plan}), _text_turn("noted")],
    )
    engine.plan_path = tmp_path / ".reeve" / "plans" / "sess1.md"

    events = _collect(engine, "plan the migration")
    assert EventType.PLAN_UPDATED in [e.type for e in events]
    assert engine.plan_path.read_text(encoding="utf-8") == plan
    assert any(
        m.get("role") == "tool" and '"updated": true' in m["content"]
        for m in engine.messages
    )
    # The managed directory hides itself from the user's `git status`.
    assert (engine.plan_path.parent / ".gitignore").read_text() == "*\n"


def test_update_plan_replaces_rather_than_appends(tmp_path):
    engine, _ = _plan_engine(
        tmp_path,
        [
            _tool_turn("update_plan", {"content": "first draft"}, "c1"),
            _tool_turn("update_plan", {"content": "second draft"}, "c2"),
            _text_turn("done"),
        ],
    )
    engine.plan_path = tmp_path / ".reeve" / "plans" / "sess2.md"
    _collect(engine, "plan it")
    assert engine.plan_path.read_text(encoding="utf-8") == "second draft"


def test_update_plan_is_refused_in_read_only_mode(tmp_path):
    engine, permissions = _plan_engine(
        tmp_path,
        [_tool_turn("update_plan", {"content": "plan"}), _text_turn("describing")],
        plan=False,
    )
    engine.plan_path = tmp_path / ".reeve" / "plans" / "sess3.md"
    permissions.mode = Mode.READ_ONLY
    _collect(engine, "go")
    assert not engine.plan_path.exists()
    assert any(
        m.get("role") == "tool" and "read-only" in m["content"]
        for m in engine.messages
    )


def test_update_plan_without_a_session_file_says_so(tmp_path):
    engine, _ = _plan_engine(
        tmp_path,
        [_tool_turn("update_plan", {"content": "plan"}), _text_turn("in the answer")],
    )
    assert engine.plan_path is None  # CLI / rootless: no plan file
    _collect(engine, "go")
    assert any(
        m.get("role") == "tool" and "aren't available" in m["content"]
        for m in engine.messages
    )


def test_build_engine_binds_the_plan_path_and_registers_the_tool(tmp_path):
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent

    engine = build_engine(
        agent=reeve_agent(), workspace=tmp_path, provider=_Stub(), session_id="sess9"
    )
    try:
        assert "update_plan" in engine.registry.names()
        assert engine.plan_path is not None
        assert engine.plan_path.name == "sess9.md"
        assert str(engine.plan_path).startswith(str(tmp_path))
    finally:
        engine.executor.close()


def test_without_a_session_id_update_plan_is_not_advertised(tmp_path):
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent

    engine = build_engine(agent=reeve_agent(), workspace=tmp_path, provider=_Stub())
    try:
        assert engine.plan_path is None
        assert "update_plan" not in engine.registry.names()
    finally:
        engine.executor.close()


def test_the_compacted_block_points_at_the_working_plan_file():
    from harness.context.compaction import CompactionState, compacted_block

    state = CompactionState(
        boundary_index=1,
        summary_text="summary",
        working_state="",
        plan_path="/w/.reeve/plans/sess.md",
    )
    block = compacted_block(state)
    assert "Working plan file" in block
    assert "/w/.reeve/plans/sess.md" in block
    assert "read_file" in block


def test_an_approved_plan_names_its_file_copy():
    from harness.context.compaction import CompactionState, compacted_block

    state = CompactionState(
        boundary_index=1,
        summary_text="summary",
        working_state="",
        approved_plan="1. migrate\n2. verify",
        plan_path="/w/.reeve/plans/sess.md",
    )
    block = compacted_block(state)
    assert "Approved plan" in block
    assert "/w/.reeve/plans/sess.md" in block


def test_manager_session_plan_serves_the_working_file(tmp_path):
    """The HTTP read side: one session's plan file, path included, empty-not-error when
    the tool was never called."""
    from harness.llm.providers import ModelCapabilities, ProviderClient
    from harness.interface.server import SessionManager

    class _Provider(ProviderClient):
        def complete(self, *, model, messages, tools=None, **s):
            return AssistantTurn(text="", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    mgr = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    mgr._prefs["scratch_base"] = str(tmp_path / "scratchbase")
    sid = "sessP"
    engine = mgr.get_engine(sid, agent="reeve")
    assert engine is not None
    try:
        assert engine.plan_path is not None
        empty = mgr.session_plan(sid)
        assert empty["content"] == "" and empty["exists"] is False

        engine.plan_path.parent.mkdir(parents=True, exist_ok=True)
        engine.plan_path.write_text("1. do the thing", encoding="utf-8")
        served = mgr.session_plan(sid)
        assert served["exists"] is True
        assert served["content"] == "1. do the thing"
        assert served["path"] == str(engine.plan_path)
    finally:
        engine.executor.close()


def test_plan_rejection_keeps_plan_overlay_and_returns_feedback(tmp_path):
    async def reject(args, tool_call_id=None):
        return {"approved": False, "feedback": "don't touch x.py, fix y.py instead"}

    engine, permissions = _plan_engine(tmp_path, [_tool_turn("propose_plan", {"plan": "edit x.py"}), _text_turn("revising the plan")], plan_approver=reject)
    events = _collect(engine, "fix the bug")
    assert permissions.plan is True and permissions.mode is Mode.WORKSPACE_WRITE  # nothing changed
    assert _first_result(events) == "denied" and _a_result_says(engine, "fix y.py instead")


def test_propose_plan_without_approver_noops(tmp_path):
    engine, permissions = _plan_engine(tmp_path, [_tool_turn("propose_plan", {"plan": "p"}), _text_turn("ok")])
    assert EventType.PLAN_PROPOSED not in _kinds(_collect(engine, "go"))
    assert permissions.plan is True and _a_result_says(engine, "isn't available")


# -- what a built engine has -------------------------------------------------------------


class _Stub:
    """A provider for engines that are built and never run."""

    def complete(self, **request):  # pragma: no cover
        raise AssertionError("no turn is run")

    def capabilities(self, model):
        return ModelCapabilities()


def test_build_engine_plan_overlay_wiring(tmp_path):
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent

    engine = build_engine(agent=reeve_agent(), workspace=tmp_path, provider=_Stub(), plan=True)
    try:
        assert "propose_plan" in engine.registry.names()
        assert "Plan mode is active" in engine.context_provider()  # said each turn while rehearsing
        engine.set_plan(False)
        assert "Plan mode is active" not in engine.context_provider()
    finally:
        engine.executor.close()


def test_build_engine_workspace_write_registers_tool_without_reminder(tmp_path):
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent

    # The GUI can put a live session into the rehearsal (set_plan), so the tool is always there;
    # the reminder is only said while rehearsing.
    engine = build_engine(agent=reeve_agent(), workspace=tmp_path, provider=_Stub())
    try:
        assert "propose_plan" in engine.registry.names() and "Plan mode is active" not in engine.context_provider()
    finally:
        engine.executor.close()


def _read_only(tmp_path, turns):
    engine, permissions = _plan_engine(tmp_path, turns, plan=False)
    permissions.mode = Mode.READ_ONLY
    return engine


def test_read_only_mode_blocks_writes_without_plan_pressure(tmp_path):
    engine = _read_only(tmp_path, [_tool_turn("write_file", {"path": "x.py", "content": "x"}), _text_turn("here's what I'd change instead")])
    _collect(engine, "tweak x.py")
    assert not (tmp_path / "x.py").exists() and _a_result_says(engine, "say what you would change instead")


def test_propose_plan_in_read_only_mode_says_describe_instead(tmp_path):
    engine = _read_only(tmp_path, [_tool_turn("propose_plan", {"plan": "p"}), _text_turn("ok, describing")])
    assert EventType.PLAN_PROPOSED not in _kinds(_collect(engine, "go"))
    assert _a_result_says(engine, "describe the proposed changes")


def test_build_engine_read_only_reminder_not_plan_contract(tmp_path):
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent

    engine = build_engine(agent=reeve_agent(), workspace=tmp_path, provider=_Stub(), mode=Mode.READ_ONLY)
    try:
        said = engine.context_provider()
        assert "Permissions: read only." in said and "propose_plan" not in said  # nothing pushes it to plan
    finally:
        engine.executor.close()


def test_non_code_personas_get_the_plan_door(tmp_path):
    """Plans are not a code feature. The harness serves MyHelper sessions and the agent's (no repo,
    no files) as first-class users, so every non-lead persona must be able to propose one —
    the registration rule is `agent.team != "lead"`, not "is an agent that edits files"."""
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent, myhelper_agent

    for agent in (reeve_agent(), myhelper_agent()):
        engine = build_engine(agent=agent, workspace=tmp_path, provider=_Stub())
        try:
            assert "propose_plan" in engine.registry.names(), agent.name
        finally:
            engine.executor.close()


def test_plan_copy_is_not_code_only(tmp_path):
    """The model decides whether a plan "fits" from this copy — and it read the old text
    ("summarize what you'll change, in which files") as "code only", then told a user that
    propose_plan does not suit a financial plan. Both places the model reads the framing
    from (the tool's own text and the per-turn plan-mode context) now name non-code work."""
    import inspect

    from harness.compose import prompts as agent_module
    from harness.tools import plan as plan_module

    tool_text = inspect.getsource(plan_module).lower()
    ctx_text = agent_module.PLAN_MODE_CONTEXT.replace("\n", " ").lower()
    for text in (tool_text, ctx_text):
        assert "in which files" not in text
        assert "implementation plan" not in text
    non_code = [w for w in ("documents", "research", "money", "apps") if w in tool_text]
    assert len(non_code) >= 2, tool_text


# -- a plan in any mode -------------------------------------------------------
#
# The approval is the consent, so the tool works in every preset. The rehearsal is a read-only
# run-through, not a step a plan needs: a session in full access can lay one out before touching
# anything, and outside the rehearsal there is nothing to leave.


def test_propose_plan_works_in_workspace_write_and_keeps_the_mode(tmp_path):
    proposed = []

    async def approve(args, tool_call_id=None):
        proposed.append(args.get("plan"))
        return {"approved": True}  # no mode chosen: the session keeps its own

    engine, permissions = _plan_engine(tmp_path, [_tool_turn("propose_plan", {"plan": "1. write x.py  2. verify"}), _text_turn("implementing")], plan_approver=approve, plan=False)
    permissions.mode = Mode.WORKSPACE_WRITE
    events = _collect(engine, "fix the bug")
    assert EventType.PLAN_PROPOSED in _kinds(events) and proposed == ["1. write x.py  2. verify"]
    assert permissions.mode is Mode.WORKSPACE_WRITE and permissions.plan is False  # neither down nor up
    assert _first_result(events) == "ok" and _a_result_says(engine, "implement it now")


def test_propose_plan_in_full_access_waits_on_nobody_and_is_not_approved(tmp_path):
    """Full access asks nobody: a card here stopped a session the owner had left
    running (2026-10-15). But the person has not seen the plan, so it is kept as the working plan
    and not approved, and the model is told to do what the person asked (2026-10-18: asked "what
    next?", a model laid out a plan, was told "approved, carry it out now", and started it)."""
    asked = []

    async def approve(args, tool_call_id=None):
        asked.append(args)
        return {"approved": False, "feedback": "never reached"}

    engine, permissions = _plan_engine(
        tmp_path,
        [
            _tool_turn("propose_plan", {"plan": "1. write x.py  2. verify"}),
            _tool_turn("write_file", {"path": "x.py", "content": "done\n"}, "call_2"),
            _text_turn("done"),
        ],
        plan_approver=approve,
        plan=False,
    )
    permissions.mode = Mode.DANGER_FULL_ACCESS
    events = _collect(engine, "do it")
    types = [e.type for e in events]
    assert asked == []
    assert EventType.PLAN_PROPOSED not in types
    assert EventType.PERMISSION_REQUIRED not in types
    assert permissions.mode is Mode.DANGER_FULL_ACCESS
    # Not an approved plan: nothing carries it into compaction as the person's.
    assert not engine.approved_plan
    # The person asked for the work ("do it"), so the model went on and did it.
    assert (tmp_path / "x.py").read_text() == "done\n"
    finished = next(e for e in events if e.type == EventType.TOOL_FINISHED)
    assert finished.data["status"] == "ok"
    (told,) = [m["content"] for m in engine.messages if m.get("role") == "tool" and "not_asked" in m["content"]]
    assert '"approved": true' not in told and "carry it out now" not in told
    assert "has not seen or approved this one" in told and "Do what the person asked" in told


def test_the_plan_overlay_still_asks_under_full_access(tmp_path):
    """The overlay is the person asking to review a plan, so it outranks the preset."""
    asked = []

    async def approve(args, tool_call_id=None):
        asked.append(args.get("plan"))
        return {"approved": True}

    engine, permissions = _plan_engine(
        tmp_path,
        [_tool_turn("propose_plan", {"plan": "p"}), _text_turn("go")],
        plan_approver=approve,
        plan=True,
    )
    permissions.mode = Mode.DANGER_FULL_ACCESS
    events = _collect(engine, "do it")
    assert EventType.PLAN_PROPOSED in [e.type for e in events]
    assert asked == ["p"]
    assert permissions.mode is Mode.DANGER_FULL_ACCESS
    assert permissions.plan is False


def test_approving_out_of_read_only_lands_somewhere_executable(tmp_path):
    """A rehearsal under the read-only preset must end in a preset that can act, even if
    the approver names no mode."""

    async def approve(args, tool_call_id=None):
        return {"approved": True}

    engine, permissions = _plan_engine(
        tmp_path,
        [_tool_turn("propose_plan", {"plan": "p"}), _text_turn("go")],
        plan_approver=approve,
        plan=True,
    )
    permissions.mode = Mode.READ_ONLY
    _collect(engine, "go")
    assert permissions.mode is Mode.WORKSPACE_WRITE
    assert permissions.plan is False


def test_rejection_in_a_write_mode_keeps_that_mode(tmp_path):
    async def reject(args, tool_call_id=None):
        return {"approved": False, "feedback": "not like that"}

    engine, permissions = _plan_engine(
        tmp_path,
        [_tool_turn("propose_plan", {"plan": "p"}), _text_turn("revising")],
        plan_approver=reject,
        plan=True,
    )
    permissions.mode = Mode.WORKSPACE_WRITE
    events = _collect(engine, "go")
    assert permissions.mode is Mode.WORKSPACE_WRITE
    assert permissions.plan is True
    assert next(e for e in events if e.type == EventType.TOOL_FINISHED).data["status"] == "denied"
    assert any(
        m.get("role") == "tool" and "not like that" in m["content"] for m in engine.messages
    )


# -- tool copy is domain-neutral (2026-09-11, follow-up to the plan-copy fix) -------
#
# A MyHelper session asked for a financial plan and the agent declined: propose_plan's
# copy said "in which files", so the model read the tool as code-only. The same class of
# leak exists in other shared tools — these tests pin the ones we fixed so the wording
# cannot quietly narrow again.


def test_shared_tool_copy_is_not_code_only():
    """grep / list_files / the task tool's explorer serve every persona (the agent,
    MyHelper), so their model-facing copy must say what the mechanics allow — searching
    and exploring FILES, not 'code'. Asserts on the wording the model reads, in the
    places the explore agent describes itself (description, prompt)."""
    import inspect

    from harness.tools import search as search_module
    from harness.tools import task as task_module
    from harness.tools import task_agents

    grep_copy = str(search_module.SCHEMA).lower()
    assert "locating code" not in grep_copy
    assert "any text file" in grep_copy

    # The copy now lives across the task modules (the tool description and schema in
    # `task`, the agent definitions and their prompts in `task_agents`), so grep all of
    # them.
    subagent_copy = (
        inspect.getsource(task_module) + inspect.getsource(task_agents)
    ).lower()
    assert "codebase sweep" not in subagent_copy
    assert "coding worker" not in subagent_copy
    # The explorer's copy is file-first and read-only: a search specialist that names
    # patterns and keywords, and promises it creates nothing.
    assert "file search specialist" in subagent_copy
    assert "do not create any files" in subagent_copy
    assert "finds files by pattern" in subagent_copy
    # What the caller reads about the explorer and about the call says files, not code
    # (both rewritten: the persona that delegates may not be a developer).
    for copy in (task_agents.EXPLORE_DESCRIPTION, task_module.DESCRIPTION):
        assert "codebase" not in copy.lower() and "symbol" not in copy.lower()
    # The general agent is described by the work it does (research, multistep tasks),
    # not by one domain's source.
    assert "general-purpose agent for researching" in subagent_copy
