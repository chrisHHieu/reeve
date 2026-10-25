"""The `task` tool — the subagent surface.

These tests pin the CONTRACT the model sees: the description text, the parameters, the
`<task …>` envelope, the last-assistant-text rule, the permission ruleset that decides
which tools a child even has, background notification, and resume. The rails (deadline,
stop) are the harness's and are covered by the runtime tests.
"""

from __future__ import annotations

import threading
import time

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools.task import BACKGROUND_STARTED, DESCRIPTION, render_output, task_tools
from harness.tools.task_agents import ASK, DENY, describe_task, evaluate, resolve_agents, rules_from_config


def _text_turn(text):
    return AssistantTurn(text=text, finish_reason="stop")


def _tool_turn(name, args, call_id="call_1"):
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        finish_reason="tool_calls",
    )


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _tools(tmp_path, provider, **kwargs):
    return task_tools(
        workspace=tmp_path,
        provider=provider,
        model="gpt-5.6",
        **kwargs,
    )


def _task(tools):
    return next(t for t in tools if getattr(t, "__name__", "") == "task")


# -- the surface the model sees ------------------------------------------------------


def test_the_schema_has_the_expected_shape(tmp_path):
    tools = _tools(tmp_path, ScriptedProvider([]))
    schema = _task(tools).__tool_schema__
    assert schema["type"] == "function"
    function = schema["function"]
    assert function["name"] == "task"
    params = function["parameters"]
    assert params["required"] == ["description", "prompt", "subagent_type"]
    assert set(params["properties"]) == {
        "description",
        "prompt",
        "subagent_type",
        "task_id",
        "command",
        "background",
        "inherit_context",
        "output_schema",
        "provider",
        "model",
        "isolation",
        # What a new task is called by, and how hard it thinks. Stopping one moved
        # to `message_task` with everything else said to it after its brief.
        "name",
        "effort",
    }
    assert params["properties"]["isolation"]["enum"] == ["worktree"]
    assert params["properties"]["description"]["description"] == (
        "A short (3-5 words) description of the task"
    )
    assert params["properties"]["task_id"]["description"].startswith(
        "This should only be set if you mean to resume a finished task"
    )


def test_the_description_is_the_contract_plus_the_agent_list(tmp_path):
    tools = _tools(tmp_path, ScriptedProvider([]))
    description = _task(tools).__tool_schema__["function"]["description"]
    assert description.startswith(DESCRIPTION)
    assert "The subagent gets `prompt` and nothing else." in description
    assert "Subagent types:" in description
    assert "- explore: Read-only search of a folder's files" in description
    assert "- general: General-purpose agent" in description


def test_the_description_does_not_say_when_to_delegate_twice():
    """How many subagents and when is the system prompt's Delegation section. The text
    this replaced told the model to launch as many as possible, beside a prompt that says
    one for a focused question."""
    from harness.modes.agents.reeve import DELEGATION_SECTION
    from harness.tools.task_agents import EXPLORE_DESCRIPTION, GENERAL_DESCRIPTION

    for text in (DESCRIPTION, GENERAL_DESCRIPTION, EXPLORE_DESCRIPTION):
        lowered = text.lower()
        assert "whenever possible" not in lowered
        assert "in parallel" not in lowered and "concurrently" not in lowered
    assert "one subagent for a focused question" in DELEGATION_SECTION
    assert len(DESCRIPTION) < 1_700


def test_output_schema_gates_are_loud(tmp_path):
    task = _task(_tools(tmp_path, ScriptedProvider([])))

    out = task(
        description="d",
        prompt="p",
        subagent_type="explore",
        output_schema={"type": "array"},
    )
    assert "object-rooted" in out["error"]

    out = task(
        description="d",
        prompt="p",
        subagent_type="explore",
        output_schema={"type": "object"},
    )
    assert "durable session" in out["error"]

    out = task(
        description="d",
        prompt="p",
        subagent_type="explore",
        task_id="t-1",
        output_schema={"type": "object"},
    )
    assert "new task" in out["error"]


def test_a_caller_that_may_not_use_an_agent_does_not_see_it(tmp_path):
    """Denied agents are hidden from the listing: the list is a
    permission decision, not documentation."""
    from harness.tools.task_agents import AgentInfo, resolve_agents

    agents = resolve_agents(None)
    caller = AgentInfo(name="plan", permission=[("task", "general", "deny")])
    listing = describe_task(agents, caller)
    assert "- general:" not in listing
    assert "- explore:" in listing


def test_inherit_context_is_refused_on_a_resume(tmp_path):
    """`inherit_context` seeds a NEW child; a resumed one already carries its context,
    and silently ignoring the flag would read as a fork that never happened."""
    tools = _tools(tmp_path, ScriptedProvider([]))
    out = _task(tools)(
        description="d",
        prompt="p",
        subagent_type="explore",
        task_id="explore-1",
        inherit_context=True,
    )
    assert "inherit_context only applies to a new task" in out["error"]


def test_inherit_context_needs_a_durable_session(tmp_path):
    """The thread harness has no session to copy from: the refusal names the missing
    surface instead of quietly spawning a blank child."""
    tools = _tools(tmp_path, ScriptedProvider([]))
    out = _task(tools)(
        description="d", prompt="p", subagent_type="explore", inherit_context=True
    )
    assert "durable session" in out["error"]


# -- the result ---------------------------------------------------------------------


def test_a_foreground_task_returns_the_envelope_with_the_last_message(tmp_path):
    provider = ScriptedProvider([_text_turn("the findings")])
    tools = _tools(tmp_path, provider)
    result = _task(tools)(
        description="map the auth flow", prompt="find the auth flow", subagent_type="explore"
    )
    assert result["report"] == render_output(
        session_id=result["task_id"], state="completed", text="the findings"
    )
    assert "<task_result>" in result["report"]
    assert result["status"] == "completed"
    assert result["task_id"].startswith("explore-")


def test_the_report_is_the_childs_final_message(tmp_path):
    provider = ScriptedProvider(
        [
            _tool_turn("grep", {"pattern": "x"}),
            _text_turn("final answer"),
        ]
    )
    tools = _tools(tmp_path, provider)
    result = _task(tools)(description="d", prompt="p", subagent_type="explore")
    assert "final answer" in result["report"]
    # The work that produced it is a COUNT on the result (`ChildResult.steps` counts the
    # finished calls): the parent reads the answer, and the child's own transcript — a real
    # session on the manager path — is where the steps themselves live.
    assert result["steps"] >= 1


def test_an_unknown_agent_is_refused_with_the_standard_words(tmp_path):
    tools = _tools(tmp_path, ScriptedProvider([]))
    result = _task(tools)(description="d", prompt="p", subagent_type="nope")
    assert result["error"] == "Unknown agent type: nope is not a valid agent type"


def test_a_child_with_no_report_fails_the_task(tmp_path):
    class _Empty(ScriptedProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            return _text_turn("")

    tools = _tools(tmp_path, _Empty([]))
    result = _task(tools)(description="d", prompt="p", subagent_type="explore")
    # The task fails with the engine's reason, and the
    # envelope never pretends a silent child produced an answer.
    assert result["error"].startswith("Subagent failed (task_id: explore-1): ")
    assert "empty response" in result["error"]


# -- the child's tools are its ruleset ------------------------------------------------


def test_explore_is_denial_first_and_keeps_only_read_tools(tmp_path):
    tools = _tools(tmp_path, ScriptedProvider([]))
    _task(tools)
    # The child engine is built inside the tool; its registry is what the rules decide.
    from harness.tools.task_runtime import build_child_engine
    from harness.tools.task_agents import resolve_agents

    agents = resolve_agents(None)
    engine = build_child_engine(
        workspace=tmp_path,
        provider=ScriptedProvider([]),
        model="gpt-5.6",
        agent=agents["explore"],
    )
    try:
        names = set(engine.registry.names())
    finally:
        engine.executor.close()
    assert {"grep", "list_files", "read_file", "run_shell", "web_fetch"} <= names
    assert not {"write_file", "replace_in_file", "apply_patch", "task"} & names


def test_general_keeps_the_write_tools(tmp_path):
    from harness.tools.task_agents import resolve_agents
    from harness.tools.task_runtime import build_child_engine

    agents = resolve_agents(None)
    engine = build_child_engine(
        workspace=tmp_path,
        provider=ScriptedProvider([]),
        model="gpt-5.6",
        agent=agents["general"],
    )
    try:
        names = set(engine.registry.names())
    finally:
        engine.executor.close()
    assert {"write_file", "replace_in_file", "read_file", "run_shell"} <= names
    # These are denied in EVERY child unless the agent asks for them.
    assert "todo_write" not in names and "ask_user" not in names and "task" not in names


def test_a_config_agent_is_added_and_can_disable_a_builtin(tmp_path):
    config = {
        "agents": {
            "reviewer": {
                "description": "Reviews a diff",
                "mode": "subagent",
                "permission": {"*": "deny", "read": "allow", "grep": "allow"},
            },
            "explore": {"disable": True},
        }
    }
    agents = resolve_agents(config)
    # External CLI agents appear wherever their binary is installed on the machine
    # running the tests (claude-code here); the config's own additions still hold.
    from harness.tools.task_agents import external_agent_infos

    assert set(agents) == {"general", "verify", "reviewer"} | set(external_agent_infos())
    assert "reviewer" in agents and "explore" not in agents
    task = _tools(tmp_path, ScriptedProvider([]), config=config)
    description = _task(task).__tool_schema__["function"]["description"]
    assert "- reviewer: Reviews a diff" in description
    assert "- explore:" not in description


def test_a_bare_action_rule_means_every_pattern(tmp_path):
    # Last match wins, exactly as in TOML: the broad allow, then the narrower ask.
    rules = rules_from_config({"edit": "deny", "read": {"*": "allow", "*.env": "ask"}})
    assert ("edit", "*", DENY) in rules
    assert evaluate("write_file", "anything", rules) == DENY
    assert evaluate("read_file", "/s/.env", rules) == ASK
    assert evaluate("read_file", "/s/main.py", rules) == "allow"


def test_the_child_inherits_the_parents_denies_but_not_its_allows(tmp_path):
    from harness.tools.task_agents import child_permission, resolve_agents

    agents = resolve_agents(None)
    parent_rules = [("run_shell", "*", DENY), ("read_file", "*", "allow")]
    inherited = child_permission(parent_rules, agents["general"])
    assert ("run_shell", "*", DENY) in inherited
    assert ("read_file", "*", "allow") not in inherited
    # `task` is always denied unless the agent's own ruleset mentions it — which is what
    # keeps nesting from being free.
    assert ("task", "*", DENY) in inherited
    # An agent that already speaks about `todo_write` (general denies it) gets no second
    # rule; one that does not (explore) is denied it here.
    assert ("todo_write", "*", DENY) in child_permission(parent_rules, agents["explore"])
    assert ("todo_write", "*", DENY) not in inherited


def test_a_denied_spawn_is_refused_with_the_ruleset_text(tmp_path):
    tools = _tools(
        tmp_path,
        ScriptedProvider([]),
        session_rules=[("task", "explore", DENY)],
    )
    result = _task(tools)(description="d", prompt="p", subagent_type="explore")
    assert "prevents you from using this specific tool call" in result["error"]
    assert "task:explore" in result["error"]


# -- background ---------------------------------------------------------------------


class _WakeSpy:
    def __init__(self):
        self.calls = []

    def add_completion(self, session_id, job_id, *, note="", depth=0):
        self.calls.append({"session_id": session_id, "job_id": job_id, "note": note, "depth": depth})


def test_background_starts_immediately_and_returns_the_running_envelope(tmp_path):
    release = threading.Event()

    class _Blocking(ScriptedProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            if self._turns:
                return self._turns.pop(0)
            release.wait(10)
            return _text_turn("late findings")

    spy = _WakeSpy()
    tools = _tools(
        tmp_path,
        _Blocking([]),
        wake_store=spy,
        session_id="sess-1",
    )
    started = time.monotonic()
    result = _task(tools)(
        description="research", prompt="look it up", subagent_type="explore", background=True
    )
    assert time.monotonic() - started < 3  # a task_id, not the child's completion
    assert result["status"] == "running"
    assert BACKGROUND_STARTED in result["report"]
    assert 'state="running"' in result["report"]
    release.set()
    # The finished envelope is injected into the session when the child ends — the note IS
    # the result text, so nothing has to poll for it.
    deadline = time.monotonic() + 10
    while not spy.calls and time.monotonic() < deadline:
        time.sleep(0.02)
    assert spy.calls[0]["job_id"] == result["task_id"]
    assert "<task_result>" in spy.calls[0]["note"]
    assert "late findings" in spy.calls[0]["note"]


def test_background_needs_a_durable_session(tmp_path):
    tools = _tools(tmp_path, ScriptedProvider([]))
    result = _task(tools)(description="d", prompt="p", subagent_type="explore", background=True)
    assert "background tasks need a durable session" in result["error"]


# -- resume -------------------------------------------------------------------------


def test_task_id_resumes_the_same_child(tmp_path):
    provider = ScriptedProvider([_text_turn("first pass"), _text_turn("second pass")])
    tools = _tools(tmp_path, provider)
    task = _task(tools)
    first = task(description="d", prompt="p", subagent_type="explore")
    second = task(description="d", prompt="now check X", subagent_type="explore", task_id=first["task_id"])
    assert second["task_id"] == first["task_id"]
    assert "second pass" in second["report"]


def test_resuming_an_unknown_task_says_the_context_is_gone(tmp_path):
    tools = _tools(tmp_path, ScriptedProvider([]))
    result = _task(tools)(description="d", prompt="p", subagent_type="explore", task_id="explore-99")
    assert "Unknown task_id: explore-99" in result["error"]


# -- the presentation ---------------------------------------------------------------


def test_the_presentation_carries_the_run_reference(tmp_path):
    from harness.tools.task import _present_result

    tools = _tools(tmp_path, ScriptedProvider([_text_turn("r")]))
    result = _task(tools)(description="d", prompt="p", subagent_type="explore")
    shown = _present_result({}, result)
    assert shown.run_id == result["task_id"]
    assert shown.run_steps == 0


# -- parity: an allowed call never asks the person ------------------------------------


def test_a_ruleset_allow_runs_without_asking(tmp_path):
    """The gap the owner caught: `explore` is allowed `web_search`/`web_fetch`/`run_shell`
    by its ruleset, so a child doing research must not raise an approval prompt — that is
    the intended behaviour (the default ruleset is `"*": "allow"`, and `explore` re-allows
    the read tools after its wildcard deny)."""
    from harness.safety.permissions import PermissionEngine
    from harness.tools.task_agents import resolve_agents, rule_decider

    agents = resolve_agents(None)
    rules = list(agents["explore"].permission)

    # The one decider both child paths use (folding the test's private copy of `_rule_value` into
    # `task_agents.rule_decider` once left this import dangling).
    engine = PermissionEngine(workspace_root=tmp_path, rule_decider=rule_decider(rules))
    allowed = engine.evaluate("web_search", {"query": "openai flagship models"})
    assert allowed.allowed and not allowed.needs_user

    # And what the ruleset does NOT allow is a refusal, not a prompt: the child reads the
    # rule that stopped it instead of interrupting the person.
    refused = engine.evaluate("write_file", {"path": "a.py", "content": "x"})
    assert not refused.allowed and not refused.needs_user


# -- P4: the @-mention bypass --------------------------


def test_a_mention_turn_skips_the_spawn_ask(tmp_path):
    """: when the last user message carries an agent part,
    `bypassAgentCheck` is true and `task.ts` never calls `ctx.ask`. The tool's asker must
    not see the call at all."""
    asked: list[dict] = []

    def asker(**kwargs):
        asked.append(kwargs)
        return "reject"  # a reject must not matter when the mention skipped the ask

    tools = _tools(
        tmp_path,
        ScriptedProvider([_text_turn("the findings")]),
        session_rules=[("task", "*", ASK)],
        permission_asker=asker,
        mention_bypass=lambda: True,
    )
    result = _task(tools)(
        description="map the auth flow", prompt="find it", subagent_type="explore"
    )
    assert asked == []
    assert result["status"] == "completed"
    assert "the findings" in result["report"]


def test_a_mention_turn_overrides_a_caller_deny(tmp_path):
    """The mention path never evaluates the caller's `task` ruleset either: the
    person's explicit invocation IS the consent — which is exactly why the synthetic
    instruction says "Invoked by user; guaranteed to exist." when the caller is denied."""
    tools = _tools(
        tmp_path,
        ScriptedProvider([_text_turn("the findings")]),
        session_rules=[("task", "explore", DENY)],
        mention_bypass=lambda: True,
    )
    result = _task(tools)(
        description="map the auth flow", prompt="find it", subagent_type="explore"
    )
    assert "error" not in result
    assert result["status"] == "completed"


def test_without_a_mention_the_ask_still_parks(tmp_path):
    """The baseline, so the bypass tests cannot pass vacuously: the same asker setup with
    an empty mention turn refuses with the reject string."""
    asked: list[dict] = []

    def asker(**kwargs):
        asked.append(kwargs)
        return "reject"

    tools = _tools(
        tmp_path,
        ScriptedProvider([]),
        session_rules=[("task", "*", ASK)],
        permission_asker=asker,
        mention_bypass=lambda: False,
    )
    result = _task(tools)(
        description="map the auth flow", prompt="find it", subagent_type="explore"
    )
    assert asked and asked[0]["permission"] == "task"
    assert result == {
        "error": "The user rejected permission to use this specific tool call."
    }


# -- P5: the depth limit and `default_agent` -----------------------------------------


def test_the_depth_limit_refuses_with_the_exact_text(tmp_path):
    """: the ancestry depth is checked BEFORE the ask, and the
    refusal is the standard sentence, character for character."""
    from harness.config.config import Config
    from harness.tools.task_agents import depth_limit_message

    tools = _tools(
        tmp_path,
        ScriptedProvider([]),
        config=Config(subagent_depth=2),
        session_depth=lambda: 2,
    )
    result = _task(tools)(description="d", prompt="p", subagent_type="explore")
    assert result == {
        "error": (
            'Subagent depth limit reached (2). Increase "subagent_depth" to allow '
            "nested subagents."
        )
    }
    assert depth_limit_message(2) == result["error"]


def test_depth_zero_refuses_a_registered_by_hand_tool(tmp_path):
    """`0` disables subagents. The session registry never gets the tool (pinned against
    the manager in `test_subagent_sessions.py`); this pins the direct-call refusal for a
    host that registered it anyway."""
    from harness.config.config import Config
    from harness.tools.task_agents import depth_limit_message

    tools = _tools(tmp_path, ScriptedProvider([]), config=Config(subagent_depth=0))
    result = _task(tools)(description="d", prompt="p", subagent_type="explore")
    assert result == {"error": depth_limit_message(0)}
    assert "(0)" in result["error"]


def test_default_agent_validation(tmp_path):
    """ — the three guards and their exact messages, raised when the
    agent map resolves."""
    import pytest

    from harness.config.config import Config
    from harness.tools.task_agents import default_agent, resolve_agents

    with pytest.raises(ValueError) as missing:
        resolve_agents(Config(default_agent="ghost"))
    assert str(missing.value) == 'default agent "ghost" not found'

    with pytest.raises(ValueError) as sub:
        resolve_agents(Config(default_agent="explore"))
    assert str(sub.value) == 'default agent "explore" is a subagent'

    with pytest.raises(ValueError) as hidden:
        resolve_agents(
            Config(
                default_agent="undercover",
                agents={"undercover": {"mode": "all", "hidden": True}},
            )
        )
    assert str(hidden.value) == 'default agent "undercover" is hidden'

    config = Config(
        default_agent="reviewer",
        agents={"reviewer": {"mode": "all", "description": "Reviews diffs"}},
    )
    assert default_agent(config).name == "reviewer"
    assert resolve_agents(config)["reviewer"].mode == "all"


def test_the_mention_instruction_text():
    """, spacing included: the synthetic text appended after
    the person's message, with the guarantee clause for a denied caller."""
    from harness.tools.task_agents import mention_instruction

    assert mention_instruction("explore") == (
        " Use the above message and context to generate a prompt and call the task tool "
        "with subagent: explore"
    )
    denied = mention_instruction("explore", denied=True)
    assert denied.endswith(" . Invoked by user; guaranteed to exist.")
    assert denied.startswith(" Use the above message")



def test_the_verify_agent_reads_and_runs_but_never_writes():
    """The checker of a maker/checker pair (lecture 13): a fresh context that inspects the
    change and runs the checks itself. A verifier that could edit would be a second maker."""
    from harness.tools.task_agents import _builtin_agents, evaluate

    verify = _builtin_agents()["verify"]
    assert verify.native and "VERDICT: PASS | FAIL | UNVERIFIED" in verify.prompt
    for tool in ("read_file", "grep", "list_files", "run_shell"):
        assert evaluate(tool, "", verify.permission) == "allow", tool
    for tool in ("write_file", "replace_in_file", "apply_patch", "task", "web_fetch", "ask_user"):
        assert evaluate(tool, "", verify.permission) == "deny", tool


# -- a failed child's partial work, and how many children stay resumable --------------


def test_a_timed_out_child_keeps_its_partial_report(tmp_path):
    """Orchestration audit O5 (probe P2): `drive` keeps the child's last words when the
    deadline stops it, and the failure used to drop them."""
    import time as _time

    class _Stalls(ProviderClient):
        def __init__(self):
            self.n = 0

        def complete(self, *, model, messages, tools=None, **settings):
            self.n += 1
            if self.n == 1:
                return AssistantTurn(
                    text="PARTIAL: found 3 modules so far",
                    tool_calls=[ToolCall(id="c1", name="list_files", arguments={"path": "."})],
                    finish_reason="tool_calls",
                )
            _time.sleep(3)
            return _text_turn("too late")

        def capabilities(self, model):
            return ModelCapabilities()

    tools = _tools(tmp_path, _Stalls(), timeout_seconds=1)
    result = _task(tools)(description="d", prompt="survey", subagent_type="explore")

    assert "timed out after 1s" in result["error"]
    assert "Partial report:" in result["error"]
    assert "found 3 modules so far" in result["error"]


def test_a_manager_child_that_failed_keeps_its_partial_report(tmp_path):
    """O5, the manager path: a child that ended in error with text behind it."""

    def spawner(**kwargs):
        return {"task_id": "general-1", "status": "error: provider went away", "report": "found 2 of 5"}

    tools = _tools(tmp_path, ScriptedProvider([]), spawner=spawner, session_id="s1")
    result = _task(tools)(description="d", prompt="p", subagent_type="general")

    assert result["error"].startswith("Subagent failed (task_id: general-1): provider went away")
    assert "Partial report:\nfound 2 of 5" in result["error"]


def test_only_the_last_three_finished_children_stay_resumable(tmp_path):
    """Orchestration audit O6: `_MAX_RESUMABLE` was declared and never used, so every
    finished child's engine and history stayed in memory for the session's life."""
    provider = ScriptedProvider([_text_turn(f"pass {i}") for i in range(6)])
    task = _task(_tools(tmp_path, provider))
    ids = [task(description="d", prompt=f"p{i}", subagent_type="explore")["task_id"] for i in range(5)]

    gone = task(description="d", prompt="again", subagent_type="explore", task_id=ids[0])
    kept = task(description="d", prompt="again", subagent_type="explore", task_id=ids[-1])

    assert "its context is gone" in gone["error"]
    assert kept["task_id"] == ids[-1] and "pass 5" in kept["report"]


def _message_task(tools):
    return next(t for t in tools if getattr(t, "__name__", "") == "message_task")


def test_stop_reaches_the_manager_with_the_task_and_nothing_else(tmp_path):
    """A stop on `message_task` is a stop, not a resume: no spawn
    ask, no new turn, and the model reads the manager's stopped envelope."""
    seen: list = []

    def spawner(**kwargs):
        seen.append(kwargs)
        return {"task_id": "general-1", "status": "stopped", "report": '<task id="general-1" state="stopped">'}

    message = _message_task(_tools(tmp_path, ScriptedProvider([]), spawner=spawner, session_id="s1"))
    out = message(to="survey", stop=True)
    assert seen == [{"parent_id": "s1", "to": "survey", "stop": True}]
    assert out["status"] == "stopped" and 'state="stopped"' in out["report"]

    assert "not both" in message(to="survey", message="also this", stop=True)["error"]
    assert "empty" in message(to="survey")["error"]
    # Without a manager there is no child that outlives its call, so no tool to reach one.
    assert not any(getattr(t, "__name__", "") == "message_task" for t in _tools(tmp_path, ScriptedProvider([])))


def test_a_message_reaches_the_manager_and_its_answer_reads_like_a_task_result(tmp_path):
    """The manager decides what a message does; a finished child's answer comes back
    in the same envelope a `task` call's does, and a delivered one passes through as running."""
    replies = [
        {"task_id": "abc123", "status": "completed", "report": "found it", "tokens": 5},
        {"task_id": "abc123", "status": "running", "report": '<task id="abc123" state="running">Delivered</task>'},
    ]
    seen: list = []

    def spawner(**kwargs):
        seen.append(kwargs)
        return replies.pop(0)

    message = _message_task(_tools(tmp_path, ScriptedProvider([]), spawner=spawner, session_id="s1"))
    answered = message(to="survey", message="  look at the cache too ")
    assert seen[0]["to"] == "survey" and seen[0]["prompt"] == "look at the cache too"
    assert answered["status"] == "completed" and "found it" in answered["report"]
    delivered = message(to="abc123", message="stop at the first hit")
    assert delivered["status"] == "running" and "Delivered" in delivered["report"]


def test_name_and_effort_are_for_a_new_task_only(tmp_path):
    """A name must be usable; neither applies to a resume; the thread path names its
    run with it and has no thinking level to set."""
    seen: list = []

    def spawner(**kwargs):
        seen.append(kwargs)
        return {"task_id": "c1", "status": "completed", "report": "ok", "tokens": 1}

    task = _task(_tools(tmp_path, ScriptedProvider([]), spawner=spawner, session_id="s1"))
    assert "not usable" in task(description="d", prompt="p", subagent_type="explore", name="Survey!")["error"]
    assert "new task" in task(description="d", prompt="p", subagent_type="explore", task_id="c1", name="x")["error"]
    assert "new task" in task(description="d", prompt="p", subagent_type="explore", task_id="c1", effort="high")["error"]
    task(description="d", prompt="p", subagent_type="explore", name="survey", effort="High")
    assert seen[-1]["name"] == "survey" and seen[-1]["effort"] == "high"
    task(description="d", prompt="p", subagent_type="explore")
    assert "name" not in seen[-1] and "effort" not in seen[-1]

    plain = _task(_tools(tmp_path, ScriptedProvider([_text_turn("done"), _text_turn("again")])))
    assert "durable session" in plain(description="d", prompt="p", subagent_type="explore", effort="high")["error"]
    first = plain(description="d", prompt="p", subagent_type="explore", name="survey")
    assert first["task_id"] == "survey"
    assert "already has a task" in plain(description="d", prompt="p", subagent_type="explore", name="survey")["error"]
