"""`workflow` — a model-written Python script that orchestrates subagents.

One `task` call delegates ONE piece of work; a fan-out (audit over many files, a
migration, multi-angle research) otherwise costs the parent a model round-trip per step
and lands every intermediate result in its context. A workflow moves the loop, the
branching and the intermediate results into a SCRIPT: the engine runs it in the same
child-process runtime `run_code` uses (concurrent sub-calls included), and the parent
sees only the script's return value.

The script body is plain Python with top-level `return`; the helpers are injected by
`WORKFLOW_PRELUDE` under `__program__(tools)` so they can close over the tool bridge:

- `agent(prompt, schema=None, label=None, subagent_type=None, phase=None,
  provider=None, model=None, isolation=None, effort=None)` — one
  subagent to completion. With `schema` (the same object-rooted subset `task` accepts)
  it returns the VALIDATED object; without it, the child's final text (the `<task…>`
  envelope the tool returns is unwrapped here). A child that fails resolves to `None`
  and logs why; filter with a comprehension. `subagent_type` defaults to `general`.
  `provider` / `model` pick that child's LLM route (the delegation options; either may be
  given alone — `list_subagent_models` lists the ids). `effort` is how hard that child
  thinks, one of the levels its model offers: light for a mechanical stage, deep
  for a review, in the same script.
- `parallel(thunks)` — run zero-argument callables concurrently, await all (a barrier).
- `start(prompt, schema=None, label=None, subagent_type=None, phase=None,
  provider=None, model=None, isolation=None, effort=None)` /
  `collect(handle)` — launch a child without waiting and gather it later; the script keeps
  working while it runs, and any child left uncollected still finishes before the run
  returns. `start` claims its agent() slot immediately, so the cap bounds work launched.
- `pipeline(items, *stages)` — each item through the stages independently, no barrier;
  an ordinary stage throw drops that item to `None` and skips its remaining stages.
- `phase(title)` / `log(message)` — narration lines in the script's output.
- `args` — the tool call's `args` parameter, verbatim (already a Python value).
- `json` — the stdlib module, so a script can `json.dumps`/`loads` without importing.

Misuse (a bad option, the agent cap, the token budget) raises inside the script and kills
the run — it never dissolves into the same `None` a failed child produces.

**Journaling.** Every completed `agent()` call is recorded beside the session's event log,
and a later call of the SAME script + args + meta.name in the same session with
`resume=True` replays those results instead of buying them again — position and arguments
must both match, so a changed script or a clock-dependent prompt re-runs. That is what
makes a timed-out or stopped fan-out resumable rather than wasted.

**Trust model.** The script runs in the same child-process runtime as `run_code`: the
prelude is ours and runs with full builtins, the model-written BODY runs with a
restricted builtin table (no `open`/`eval`/`exec`/…; `import` reaches only the pure
std-lib modules in `code_child_sandbox.WORKFLOW_IMPORTS`) and the host refuses any other
import and dunder attribute access before anything starts. That raises the bar a long
way, and it is deliberately NOT a security boundary — the boundary is the child process,
and the `workflow` call sits behind the same EXEC gate `run_code` does. Anything that
needs to touch the machine belongs in an `agent()`, under the normal tool gates.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from . import cards
from .contract import ToolContract, ToolPresentation, attach_contract, clip
from .workflow_checks import validate_meta, validate_script  # noqa: F401 — re-exported

#: Whole-script bound: a script larger than this is not an orchestration any more.
MAX_SCRIPT_CHARS = 20_000
#: `agent()` calls one run may make. A workflow is a bounded fan-out, not a fleet.
MAX_TOTAL_AGENTS = 24
#: Concurrent `agent()` calls a script may have in flight (the host's per-session
#: `subagent_max_parallel` still applies on top and wins when it is smaller).
DEFAULT_CONCURRENCY = 4
#: What `agent()` uses when the script names no agent type.
DEFAULT_SUBAGENT = "general"
#: The agent type of the child session a background run is (`server/manager/workflow_runs.py`). Never registered: no `task` call can name it, and it does not count as a
#: level of nesting (`session_depth`) — its agents are the parent's delegation, one level down.
WORKFLOW_RUNNER = "workflow-runner"
#: What a background run's call answers at once: the run is a task, and its result is a message.
WORKFLOW_STARTED = (
    "The workflow is running in the background. Its return value arrives as a message "
    "(a <task> envelope with this id) when the script finishes.\n"
    "DO NOT sleep, poll, or call this workflow again meanwhile: silence means it is "
    "working. Its agents and their progress show beside the conversation. End your "
    "response here if your next step needs its result; otherwise continue with work "
    "that does not depend on it."
)

#: What a message to a workflow run answers: the run executes its script and reads
#: nothing, so a message could only be swallowed. Stopping it is still the one thing to say.
WORKFLOW_TAKES_NO_MESSAGES = (
    "{run} is a workflow run: it runs its script and reads no messages, so nothing was sent. "
    "To stop it, call message_task with to=\"{run}\" and stop=true. To change what it does, "
    "stop it and run the workflow again with the changed script; resume=true reuses the "
    "agents it already finished."
)

#: The injected helper block. Text, not a module: it must run inside `__program__` with
#: `tools` in scope, and the two `_WF_` placeholders are substituted host-side (the
#: JSON-encoded `args`, the caps) before the child ever sees it.
WORKFLOW_PRELUDE = '''\
# -- workflow helpers (injected) --------------------------------------------
import json as _wf_json
import threading as _wf_threading
from concurrent.futures import ThreadPoolExecutor as _WF_Pool

_wf_lock = _wf_threading.Lock()
_wf_agents = [0]
_wf_tokens = [0]
_wf_gate = _wf_threading.Semaphore(_WF_CONCURRENCY)
args = _wf_json.loads(_WF_ARGS)
# The one stdlib module a script needs to glue JSON around: agents emit JSON, results
# are dicts/lists, and `json` is what turns one into the other — in scope without an
# import; `_wf_json` is the same module, under the friendly name.
json = _wf_json


def _wf_emit(kind, text):
    note = getattr(tools, "_wf_note", None)
    if note is not None:
        note(kind or "log", text)
        return
    print(f"[workflow] {kind}: {text}" if kind else f"[workflow] {text}")


def _wf_unwrap(text):
    """The child's own words from a task result.

    The `task` tool answers the PARENT MODEL with an envelope
    (`<task id="…" state="…"><task_result>…</task_result></task>`); a script wants the
    text inside it. A result that is not an envelope (a refusal, an already-raw report)
    comes back stripped, untouched.
    """
    if not isinstance(text, str):
        return ""
    for tag in ("task_result", "task_error"):
        open_tag, close_tag = f"<{tag}>", f"</{tag}>"
        if open_tag in text:
            inner = text.split(open_tag, 1)[1]
            if close_tag in inner:
                inner = inner.split(close_tag, 1)[0]
            return inner.strip()
    return text.strip()


def _wf_from_json(text):
    """The object a validated child reply holds, or None when there is none.

    The host already validated the reply against the schema before answering, so the
    first `loads` is the normal case. The brace-slice fallback is for a host/wire change
    that lets prose through — it recovers instead of failing the whole run.
    """
    try:
        return _wf_json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return _wf_json.loads(text[start : end + 1])
        except ValueError:
            return None
    return None


def phase(title):
    _wf_emit("phase", title)


def _wf_fail_text(result):
    """The sentence for a failed task result: the host answers `{"error": "…"}`."""
    if isinstance(result, dict):
        text = result.get("error")
        if isinstance(text, str) and text:
            return text
    return "it produced no result"


def log(message):
    _wf_emit("", message)


class _WorkflowFatal(Exception):
    """Misuse/caps kill the SCRIPT; child failures are the per-item `None`."""


def _wf_claim():
    with _wf_lock:
        _wf_agents[0] += 1
        used = _wf_agents[0]
    if used > _WF_MAX_AGENTS:
        raise _WorkflowFatal(
            f"workflow agent cap reached ({_WF_MAX_AGENTS}) - no further agent()/start() calls"
        )


def _wf_finish(result, schema, description):
    """The one tail every child result goes through — `agent()` and `collect()` alike:
    fold its tokens into the run's budget, a failure becomes `None` WITH a note, and a
    structured result is the parsed object."""
    if isinstance(result, dict):
        # The run's token spend, as the child reported it: over budget kills the script
        # (fatal), the same rule the agent cap follows.
        spent = result.get("tokens")
        if isinstance(spent, int) and spent > 0:
            with _wf_lock:
                _wf_tokens[0] += spent
                total = _wf_tokens[0]
            if _WF_MAX_TOKENS and total > _WF_MAX_TOKENS:
                raise _WorkflowFatal(
                    f"workflow token budget reached ({_WF_MAX_TOKENS})"
                )
    if not isinstance(result, dict) or result.get("error"):
        _wf_emit("", f"agent failed: {description}: {_wf_fail_text(result)}")
        return None
    if str(result.get("status") or "").startswith("error"):
        _wf_emit("", f"agent failed: {description}: {result.get('status')}")
        return None
    if result.get("status") == "running":
        # Its report is the "running in the background" notice, not an answer (O1).
        _wf_emit(
            "",
            f"agent failed: {description}: it moved to the background, and a workflow "
            "needs its result now",
        )
        return None
    report = _wf_unwrap(result.get("report"))
    if not report:
        _wf_emit("", f"agent failed: {description}: it produced no report")
        return None
    if schema is not None:
        value = _wf_from_json(report)
        if value is None:
            _wf_emit(
                "",
                f"agent returned no valid JSON: {description}: {report[:200]}",
            )
        return value
    return report


def _wf_arguments(prompt, schema, label, subagent_type, provider=None, model=None, isolation=None, effort=None):
    arguments = {
        "description": str(label or " ".join(str(prompt).split())[:60] or "workflow task"),
        "prompt": str(prompt),
        "subagent_type": str(subagent_type or _WF_DEFAULT_AGENT),
    }
    if schema is not None:
        arguments["output_schema"] = schema
    if provider:
        arguments["provider"] = str(provider)
    if model:
        arguments["model"] = str(model)
    if isolation:
        arguments["isolation"] = str(isolation)
    if effort:
        arguments["effort"] = str(effort)
    return arguments


def agent(
    prompt,
    schema=None,
    label=None,
    subagent_type=None,
    phase=None,
    provider=None,
    model=None,
    isolation=None,
    effort=None,
):
    """One child, to completion, then its result."""
    _wf_claim()
    if phase is not None:
        _wf_emit("phase", phase)
    arguments = _wf_arguments(prompt, schema, label, subagent_type, provider, model, isolation, effort)
    with _wf_gate:
        result = tools.task(**arguments)
    return _wf_finish(result, schema, arguments["description"])


def start(
    prompt,
    schema=None,
    label=None,
    subagent_type=None,
    phase=None,
    provider=None,
    model=None,
    isolation=None,
    effort=None,
):
    """Launch a child NOW and hand back a handle; `collect(handle)` gathers it later.

    The child runs on its own thread over the same id-keyed channel `parallel()` uses, so
    the script keeps working while it thinks: start a sweep, `log()` progress, collect the
    ones that matter first, and let the run end without collecting the rest — the host
    still waits for every started child before the script's result is returned. Claims an
    agent() slot at START, so the cap bounds work launched, not just work gathered.
    """
    _wf_claim()
    if phase is not None:
        _wf_emit("phase", phase)
    arguments = _wf_arguments(prompt, schema, label, subagent_type, provider, model, isolation, effort)
    holder = {"result": None, "error": None}

    def _call():
        try:
            with _wf_gate:
                holder["result"] = tools.task(**arguments)
        except BaseException as exc:  # the channel died: surface it at collect()
            holder["error"] = exc

    thread = _wf_threading.Thread(target=_call, name="workflow-agent", daemon=True)
    thread.start()
    return {
        "thread": thread,
        "holder": holder,
        "schema": schema,
        "description": arguments["description"],
    }


def collect(handle):
    """Wait for a `start()`ed child and return what `agent()` would have returned."""
    if (
        not isinstance(handle, dict)
        or "thread" not in handle
        or "holder" not in handle
    ):
        _wf_emit("", "collect() needs the handle start() returned")
        return None
    handle["thread"].join()
    error = handle["holder"].get("error")
    if error is not None:
        raise error
    return _wf_finish(
        handle["holder"].get("result"),
        handle.get("schema"),
        str(handle.get("description") or "workflow task"),
    )


def parallel(thunks):
    items = list(thunks)
    if not items:
        return []

    def run(thunk):
        try:
            return thunk()
        except _WorkflowFatal:
            raise
        except Exception:
            return None

    workers = max(1, min(len(items), _WF_CONCURRENCY))
    with _WF_Pool(max_workers=workers) as pool:
        return list(pool.map(run, items))


def pipeline(items, *stages):
    values = list(items)
    if not values:
        return []
    if not stages:
        return values

    def run(index, item):
        value = item
        for stage in stages:
            try:
                value = stage(value, item, index)
            except _WorkflowFatal:
                raise
            except Exception:
                return None
        return value

    workers = max(1, min(len(values), _WF_CONCURRENCY))
    with _WF_Pool(max_workers=workers) as pool:
        return list(pool.map(lambda pair: run(*pair), enumerate(values)))
'''


def compose_script(
    script: str,
    *,
    args: Any = None,
    max_agents: int = MAX_TOTAL_AGENTS,
    max_tokens: int = 0,
    concurrency: int = DEFAULT_CONCURRENCY,
    subagent: str = DEFAULT_SUBAGENT,
) -> tuple[str, str]:
    """`(prelude, body)`, with the caps substituted as literals.

    They stay separate because the child executes them in different namespaces: the
    prelude is ours (full builtins, its imports are the helpers'), the body is the
    model's (restricted builtins, the allowed std-lib imports only).
    """
    # `repr` of the JSON text: the substituted value must be a valid PYTHON literal in the
    # source, not a bare JSON object (json.dumps alone yields `{"files": [...]}`, which is
    # a dict expression — `loads` then received a dict and the prelude died).
    args_json = repr(json.dumps(args if args is not None else {}))
    prelude = (
        WORKFLOW_PRELUDE.replace("_WF_ARGS", args_json)
        .replace("_WF_MAX_AGENTS", str(int(max_agents)))
        .replace("_WF_MAX_TOKENS", str(max(0, int(max_tokens))))
        .replace("_WF_CONCURRENCY", str(max(1, int(concurrency))))
        .replace("_WF_DEFAULT_AGENT", json.dumps(str(subagent)))
    )
    return prelude, script


def workflow_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "workflow",
            "description": (
                "Run a Python workflow script that orchestrates subagents at scale. Use it "
                "for work that fans out across many independent pieces — an audit over many "
                "files, a migration, multi-angle research, adversarial verification — where "
                "you write the orchestration as a script instead of delegating turn by turn. "
                "The script runs in its own process and the parent conversation sees only "
                "its return value. Helpers: agent(prompt, schema=None, label=None, "
                "subagent_type=None, phase=None, provider=None, model=None, isolation=None, effort=None) runs one "
                "subagent (with `schema` it returns the "
                "validated JSON object, otherwise the final text; a failed child is None "
                "and the reason is logged); `provider`/`model` pick that child's LLM route "
                "(either may be given alone; see list_subagent_models — the CLI agents "
                "codex / claude-code are selected with `subagent_type`, and only `model` "
                "applies to them); isolation=\"worktree\" gives that child its own copy of "
                "the repository — use it when agents WRITE in parallel; effort is how hard "
                "that child thinks, a level its model offers (low for a mechanical stage, high "
                "for a review); "
                "parallel(thunks) runs zero-argument callables "
                "concurrently; start(prompt, ...) launches a child without waiting and "
                "collect(handle) gathers it later while the script keeps working; "
                "pipeline(items, *stages) runs each item through the stages "
                "with no barrier (a stage throw drops that item to None); phase(title) and "
                "log(message) narrate; `args` holds the call's args parameter; `json` is in "
                "scope. Scripts may import only pure std-lib modules (json, re, math, "
                "statistics, collections, itertools, functools, textwrap, heapq, bisect, "
                "difflib, string, copy) — no time/random/datetime: pass such values in "
                "`args` — and builtins are limited: coordinate, don't compute. Each agent() is a real "
                "`task` call with the same approval and audit. Keep runs bounded: at most "
                f"{MAX_TOTAL_AGENTS} agent() calls and {DEFAULT_CONCURRENCY} in flight. "
                "With `resume=true` the completed agent() calls of the previous run of this "
                "same script + args are replayed (position and arguments must match) — the "
                "way a timed-out or stopped run is continued instead of bought again."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": (
                            "The Python workflow body (top-level `return <json-value>` "
                            "allowed). The helpers above are already in scope — do not "
                            "import or define them."
                        ),
                    },
                    "meta": {
                        "type": "object",
                        "description": (
                            "The workflow identity as plain JSON: required `name` (short "
                            "kebab-case) and `description`; optional `whenToUse` and "
                            "`phases` ([{title, detail?, provider?, model?}])."
                        ),
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "whenToUse": {"type": "string"},
                            "phases": {"type": "array", "items": {"type": "object"}},
                        },
                        "required": ["name", "description"],
                    },
                    "args": {
                        "type": "object",
                        "description": "Optional input exposed to the script as `args`.",
                    },
                    "resume": {
                        "type": "boolean",
                        "description": (
                            "Reuse the completed agent() calls of the previous run of this "
                            "same script + args in this session instead of running them "
                            "again (matched by position and exact arguments); the rest "
                            "runs live. Use it after a timed-out or stopped run."
                        ),
                    },
                    "background": {
                        "type": "boolean",
                        "description": (
                            "Default true: the run goes to the background and this call "
                            "returns at once with its task id; the script's return value "
                            "arrives as a message when it finishes, and the conversation "
                            "stays free meanwhile. false waits for the return value in this "
                            "call, bounded by the foreground time limit — for a short script "
                            "whose result the very next step needs. Where no background run "
                            "is possible, the run waits either way."
                        ),
                    },
                },
                "required": ["script", "meta"],
            },
        },
    }


def workflow_tool() -> object:
    """The schema carrier. Like `run_code`, the ENGINE intercepts the call (the sub-call
    round-trip needs the turn's event stream); this body runs only where no engine does,
    and says so instead of pretending."""
    from aisuite.agents import ToolMetadata, tool

    def workflow(
        script: str, meta: dict, args: dict | None = None, resume: bool = False
    ) -> dict:
        return {"error": "workflow needs the turn engine; it is not available in this surface"}

    wrapped = tool(
        workflow,
        metadata=ToolMetadata(
            category="execute",
            # High keeps the call strictly ordered: it never overlaps another call in its
            # step (`_parallel_safe`). It is NOT an ask. The permission engine classes a
            # workflow as a read (`risk.py`: not in `EXEC_TOOLS`, unlike `run_code`), so
            # it runs in every mode: the script cannot reach the machine, and each
            # `agent()` is a `task` whose child is judged by its own mode and ruleset
            # (decided 2026-10-05).
            risk_level="high",
            capabilities=["workflow"],
            description="Run a Python workflow script that orchestrates subagents.",
        ),
    )
    wrapped.__tool_schema__ = workflow_schema()
    attach_contract(wrapped, WORKFLOW_CONTRACT)
    return wrapped


def _wf_present_call(arguments: dict[str, Any]) -> ToolPresentation:
    meta = arguments.get("meta") if isinstance(arguments.get("meta"), dict) else {}
    name = str(meta.get("name") or "workflow")
    description = str(meta.get("description") or "").strip()
    script = arguments.get("script")
    text = script if isinstance(script, str) else ""
    lines = len(text.splitlines())
    detail = f"{lines} line{'' if lines == 1 else 's'}"
    if description:
        detail = f"{description} · {detail}"
    verb = "Resume" if arguments.get("resume") else "Run"
    return ToolPresentation(
        summary=f"{verb} the {name} workflow",
        detail=detail,
        kind="execute",
        # The script itself is what the person is being asked to consent to, exactly as
        # the `run_code` card shows the program.
        card=cards.generic_call(f"{verb} the {name} workflow", kind="execute", raw_input=text or None),
    )


def _wf_render(arguments: dict[str, Any], value: Any) -> str:
    """What the model reads: the script's return value as JSON, or why the run failed."""
    if not isinstance(value, dict):
        return json.dumps(value, default=str, ensure_ascii=False)
    error = value.get("error")
    if isinstance(error, str) and error:
        return error
    if value.get("status") == "running":
        # A background run: the `<task>` envelope that names it; its result is
        # injected later under the same id.
        return str(value.get("report") or WORKFLOW_STARTED)
    returned = value.get("returned")
    try:
        text = json.dumps(returned, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        text = str(returned)
    calls = value.get("tool_calls")
    if isinstance(calls, int):
        text = f"{text}\n[{calls} agent call{'' if calls == 1 else 's'}]"
    output = value.get("output")
    if isinstance(output, str) and output.strip():
        text = f"{text}\n\n{clip(output, 4000)}"
    return text or "(no return value)"


def _wf_facts(arguments: dict[str, Any], value: Any) -> dict[str, Any]:
    """What the log records about a run: how it ended — in the background,
    refused before anything ran (and by which check), failed, or returned — and how many
    agent() calls it made. `harness_review` counts refused runs from this."""
    if not isinstance(value, dict):
        return {}
    if value.get("status") == "running":
        return {"outcome": "background", "task_id": value.get("task_id")}
    if value.get("refused"):
        return {"outcome": "refused", "rule": value.get("refused")}
    agents = value.get("tool_calls") if isinstance(value.get("tool_calls"), int) else None
    return {"outcome": "error" if value.get("error") else "ok", "agents": agents}


def _wf_present_result(arguments: dict[str, Any], value: Any) -> Optional[ToolPresentation]:
    """A background run names its runner (`run_id`), the way a `task` names its child: the
    stored `_presentation` carries it, so a reopened session still knows the call is a run in
    the subagents roster and which session holds its agents. Before this a reload saw the
    envelope's text only, and the run was gone from the roster (owner, 2026-10-13)."""
    if not isinstance(value, dict) or value.get("status") != "running" or not value.get("task_id"):
        return None
    meta = arguments.get("meta") if isinstance(arguments.get("meta"), dict) else {}
    name = str(meta.get("name") or "workflow")
    return ToolPresentation(
        summary=f"The {name} workflow is running in the background",
        kind="execute",
        run_id=str(value["task_id"]),
    )


WORKFLOW_CONTRACT = ToolContract(
    render=_wf_render, present_call=_wf_present_call, present_result=_wf_present_result, facts=_wf_facts
)
