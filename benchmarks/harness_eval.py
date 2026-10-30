"""Harness eval — the REAL harness through a long, compacting session (live).

`benchmarks/run.py` measures a MODEL over a minimal toolset: every one of its tasks passed
on both models the day a compaction regression cost a real session ten minutes of
stalls, because no task ever reached the compaction thresholds (peak ~30k). This drives
the product's own engine (built by `SessionManager`, the agent, every tool, the real prompt)
through ONE multi-turn session that must cross them, and grades the harness, not just
the answers:

    turns 1-3   the three `benchmarks/tasks-long` tasks, each in its own folder
    turns 4-6   questions only the earlier turns answer, after the compactions: the new name
                task one asked for, how many tests its last pytest run passed (only a
                summary holds that), and the files task two changed
    gate        every task's hidden check passes, the answers are right, at least one
                compaction ran, and the session's own event log shows no UNEXPLAINED
                pre-step stall and ~no UNATTRIBUTED spend (`scripts/harness_kpis.py`,
                run on the log this session wrote)

A small working window (`--window`, default 60k) makes the thresholds reachable in four
turns; the mechanisms are the same ones a 400k window runs. Spends real tokens:

    .venv\\Scripts\\python.exe -m benchmarks.harness_eval --model opencode-go:deepseek-v4.1-flash
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness.observability.bench import _run_checker, load_tasks  # noqa: E402

from harness.loop import ApprovalOutcome  # noqa: E402
from harness.compose.features import FEATURE_IDS  # noqa: E402
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient  # noqa: E402


TASK_ORDER = ("api-migration", "bugfix-chain", "settings-audit")
RECALL_PROMPT = (
    "Quick question about the first task (folder t1): what exact new function name did "
    "fetch_record become? Answer with just the name."
)
RECALL_ANSWER = "load_record"
#: The detail probe. The recall's answer is in the person's own request, which the
#: block keeps word for word by code, so no summary can lose it. This one is not: the number of
#: tests is in task one's pytest output and in what the model said about it, so after the cuts it
#: is wherever the block's summaries kept it. A summary written over the one before dropped the
#: concrete details of the last block more often than it kept them (51% of 4,351 on the store).
DETAIL_PROMPT = (
    "One more, from what you remember and without running any tool: when you last ran pytest "
    "in the first task (folder t1), how many tests passed? Answer with just the number."
)
DETAIL_ANSWER = "22"
#: The artifact probe (Factory's "which files did we change and how", 2025-12): after the
#: compactions, which source files of task two the session edited. The answer is graded against
#: the files that really changed on disk, so it needs no judge; compaction's commonest failure is
#: an omission (Slipstream, arXiv 2605.08580: about 90% of failures), and a forgotten edit is one.
ARTIFACT_PROMPT = (
    "Another question, answered from what you remember, without running any tool: which "
    "files under folder t2 did you change while fixing it? List each path relative to t2, "
    "one per line, nothing else."
)


def changed_files(workspace: Path, setup: dict[str, str]) -> list[str]:
    """The source files a task's folder holds differently from its setup: edited or new, caches
    and test output left out."""
    changed = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or any(part in ("__pycache__", ".pytest_cache", ".reeve") for part in path.parts):
            continue
        rel = path.relative_to(workspace).as_posix()
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if setup.get(rel) != body:
            changed.append(rel)
    return changed
#: The share of `turn/end` tokens no logged call may leave unexplained.
MAX_UNATTRIBUTED_PCT = 2.0


def _kpis() -> Any:
    spec = importlib.util.spec_from_file_location(
        "harness_kpis", REPO / "scripts" / "harness_kpis.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


async def _approve(_request: Any) -> ApprovalOutcome:
    return ApprovalOutcome.ONCE


def _prompt_for(folder: str, prompt: str) -> str:
    return (
        f"Work ONLY inside the folder `{folder}/` of the workspace: every path in this "
        f"task is relative to it, and run pytest from inside it (`cd {folder}` first). "
        + prompt.replace("the workspace root", f"the root of `{folder}/`")
    )


#: Mechanisms `benchmarks/ablation.py` can switch off, one at a time. The first four are
#: engine-level (`engine.ablated`), the rest are the session settings they stand for.
ENGINE_ABLATABLE = ("verify_nudge", "check_failed_nudge", "check_loop", "todo_nudge", "repair_recall")
ABLATABLE = (
    *ENGINE_ABLATABLE,
    "todo_list",
    "context_budget_line",
    "tool_discovery",
    "delegation_prompt",
    "model_fold",
    "prune_write_arguments",
)
#: Mechanisms that are OFF in the product and that `benchmarks/ablation.py --enable` switches
#: ON, one at a time: the baseline is the product as it ships, the variant adds the mechanism.
#: `model_fold` was measured this way and is now on by default, so it is ablated
#: like the rest; so is `prune_write_arguments`. The next mechanism under trial
#: goes here.
OPT_IN: tuple[str, ...] = ("context_ledger",)
#: Every feature of a session, switched off the way a person does it: `[features]
#: remove` in the eval workspace's `.reeve/config.toml`, so the variant is a configuration the
#: product really runs. Named one by one (`--only feature:web`); they are not in the default set,
#: which would run fifteen more variants.
FEATURE_PREFIX = "feature:"
FEATURE_ABLATABLE: tuple[str, ...] = tuple(FEATURE_PREFIX + feature for feature in FEATURE_IDS)


def _remove_features(workspace: Path, ablate: frozenset[str]) -> None:
    """Write the `[features] remove` the ablated features ask for into the workspace config."""
    removed = sorted(name[len(FEATURE_PREFIX):] for name in ablate if name.startswith(FEATURE_PREFIX))
    if not removed:
        return
    config = workspace / ".reeve" / "config.toml"
    existing = config.read_text(encoding="utf-8") if config.is_file() else ""
    if "[features]" in existing:
        raise ValueError(f"{config} already has a [features] table; the eval would not be the one it names")
    config.parent.mkdir(parents=True, exist_ok=True)
    head = existing if not existing or existing.endswith("\n") else existing + "\n"
    config.write_text(f"{head}[features]\nremove = {json.dumps(removed)}\n", encoding="utf-8")


class _Silent(ProviderClient):
    """A provider no build calls: what an offline engine is built with."""

    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


@functools.lru_cache(maxsize=None)
def feature_tools(mechanism: str) -> frozenset[str]:
    """The tools `feature:<id>` brings to the product's session: the eval engine built offline
    with it and without it, and the difference. A baseline acted on the feature when the model
    called one of them."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        names: dict[str, set[str]] = {}
        for side, ablate in (("on", frozenset()), ("off", frozenset({mechanism}))):
            workspace = Path(tmp) / side / "ws"
            workspace.mkdir(parents=True)
            engine = product_engine("fake:model", workspace, Path(tmp) / side / "state", side, ablate, provider=_Silent())
            names[side] = set(engine.registry.names())
            release(engine)
    return frozenset(names["on"] - names["off"])


def product_engine(
    model: str,
    workspace: Path,
    state: Path,
    tag: str,
    ablate: frozenset[str] = frozenset(),
    provider: Any = None,
    max_iterations: int = 80,
    enable: frozenset[str] = frozenset(),
    extra_tools: Optional[list[Any]] = None,
) -> Any:
    """The engine the product runs, built the way the product builds it: through
    `SessionManager.get_engine`, with the session tools (memory, scheduling, history,
    goal), the session log under `state/conversations/`, and the manager's own prefs —
    kept in `state`, never the user's.

    A bare `build_engine` offered 32 tools where the product offers 51, so the deferrable
    payload never reached the discovery threshold and `tool_discovery` was ablated from a
    configuration no user runs (measured 2026-09-25: 8.3k < 16k chars).
    """
    from harness.llm.providers.router import ProviderRouter
    from harness.safety.secrets import SecretStore
    from harness.interface.server.manager import SessionManager

    _remove_features(workspace, ablate)
    manager = SessionManager(
        workspace=workspace,
        data_dir=state,
        model=model,
        provider=provider if provider is not None else ProviderRouter(SecretStore()),
    )
    manager.set_tool_discovery("tool_discovery" not in ablate)
    # Each conversation's own folder (`<scratch base>/<session id>`) inside this run's state too:
    # it was the user's ~/Reeve, keyed by the run's tag, so the third run of a probe found the
    # files the first left there (2026-10-09: deepseek reopened an earlier run's HTML calculator
    # and kept building it, 0 of 9 two-loans runs calling show_visual) and the user's home filled
    # with probe folders.
    manager.set_scratch_base(str(state / "scratch"))
    # `extra_tools` is how the product hands a session its MCP callables (`arm_mcp` then
    # `get_engine`): a probe's own MCP server arrives the same way.
    engine = manager.get_engine(tag, workspace=str(workspace), approver=_approve, extra_tools=extra_tools or None)
    # Kept so a caller can shut down what the manager started (its language servers keep
    # the workspace open, and Windows then refuses to delete it).
    engine.eval_manager = manager
    # The product sets no step cap (config `max_iterations` unset, `token_budget` 0): this is
    # the eval's safety net against a runaway, not the product's rule. A caller that grades
    # completion passes a cap well above a real run's length and reports hitting it apart.
    engine.max_iterations = max_iterations
    # Nobody answers an eval. The manager wires its Inbox asker, which waits for the owner;
    # an `ask_user` then parked a probe run forever (2026-09-29: retry-the-flake, 20 min
    # idle in the Inbox await, found by a stack dump). Without an asker the tool returns
    # "asking isn't available here", as it does on a headless surface, and the model goes on.
    engine.question_asker = None
    engine.ablated =frozenset(name for name in ablate if name in ENGINE_ABLATABLE)
    if "context_budget_line" in ablate:
        engine.context_budget_line = False
    if "model_fold" in ablate:
        engine.disable_model_fold()  # No `fold_context`, no fold hints
    if "prune_write_arguments" in ablate:
        engine.prune_write_arguments = False
    if "context_ledger" in enable:
        engine.context_ledger = True
    if "todo_list" in ablate:
        # No task list at all: the tool, and the prompt's words asking for it. The nudge
        # goes with the tool (it fires only where `todo_write` is offered).
        from harness.modes.agents.reeve import PLANNING_SECTION

        engine.registry.remove("todo_write")
        system = engine.messages[0] if engine.messages else {}
        if system.get("role") == "system" and isinstance(system.get("content"), str):
            system["content"] = system["content"].replace(PLANNING_SECTION, "")
    if "delegation_prompt" in ablate:
        # The agent prompt's delegation section, taken out of the system message the
        # engine sends (orchestration audit O10) — everything else in the prompt stays.
        from harness.modes.agents.reeve import DELEGATION_SECTION

        system = engine.messages[0] if engine.messages else {}
        if system.get("role") == "system" and isinstance(system.get("content"), str):
            system["content"] = system["content"].replace(DELEGATION_SECTION, "")
    return engine


def release(engine: Any) -> None:
    """Stop what a run left holding its workspace open: the shell and its background
    tasks, and the manager's language servers (a pyright with its cwd in the workspace made
    Windows refuse the delete and ended the first isolated history pilot)."""
    executor = getattr(engine, "executor", None)
    for task in list(getattr(executor, "_bg_tasks", {}).values()):
        try:
            task.kill()
        except Exception:  # noqa: BLE001
            pass
    for close in (getattr(executor, "close", None),
                  getattr(getattr(getattr(engine, "eval_manager", None), "lsp", None), "close", None)):
        try:
            if close is not None:
                close()
        except Exception:  # noqa: BLE001
            pass


def fired(state: Path, mechanism: str) -> int:
    """How many times `mechanism` acted in one session's log (its own events).

    The first ablation's lesson: three of seven mechanisms never acted in the baseline,
    and one variant had the very same configuration as the baseline, yet the token
    column moved up to 26% — that was run-to-run noise. A mechanism that did not act
    cannot explain a difference, so the table says whether it did.
    """
    count = 0
    for path in (state / "conversations").glob("*.events.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind, reason = event.get("type"), event.get("reason")
            if mechanism.startswith(FEATURE_PREFIX):
                count += kind == "tool/call" and event.get("name") in feature_tools(mechanism)
            elif mechanism == "model_fold":
                count += kind == "compaction/end" and reason == "folded"
            elif mechanism == "prune_write_arguments":
                count += int(event.get("arguments_folded") or 0) if str(kind).startswith("compaction/") else 0
            elif mechanism == "todo_list":
                count += kind == "todo/write"
            elif mechanism == "context_ledger":
                count += kind == "context/budget" and event.get("hint") == "ledger"
            elif mechanism == "delegation_prompt":
                count += kind == "tool/call" and event.get("name") in ("task", "workflow")
            elif mechanism == "repair_recall":
                count += kind == "repair/recalled"
            elif mechanism == "tool_discovery":
                if kind == "request/header":
                    names = {(t.get("function") or {}).get("name") for t in event.get("tools") or [] if isinstance(t, dict)}
                    count += "tool_search" in names
            elif kind == "verify/nudge":
                count += {"verify_nudge": "no_run", "check_failed_nudge": "check_failed", "check_loop": "check_loop"}.get(mechanism) == reason
            elif kind == "context/budget" and event.get("hint"):
                continue  # the fold hint shares the row type; it is not the budget line
            else:
                count += {"todo_nudge": "todo/nudge", "context_budget_line": "context/budget"}.get(mechanism) == kind
    return count


def fresh_tokens(state: Path) -> int:
    """Input tokens the provider priced in full (not read from its cache) across a
    session: what a mechanism that rewrites the middle of the prompt can make dearer
    while the total token count goes down."""
    total = 0
    for path in (state / "conversations").glob("*.events.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            usage = event.get("usage") if event.get("type") == "assistant/message" else None
            if isinstance(usage, dict):
                total += int(usage.get("input") or 0) + int(usage.get("cache_write") or 0)
    return total


def token_split(state: Path) -> dict[str, int]:
    """A session's tokens by what they cost: priced in full, read from the provider's cache,
    and written by the model. A mechanism that shrinks the context moves tokens between the
    first two, so one total hides exactly what it changes."""
    split = {"fresh": 0, "cached": 0, "output": 0}
    for path in (state / "conversations").glob("*.events.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            usage = event.get("usage") if event.get("type") == "assistant/message" else None
            if isinstance(usage, dict):
                split["fresh"] += int(usage.get("input") or 0) + int(usage.get("cache_write") or 0)
                split["cached"] += int(usage.get("cache_read") or 0)
                split["output"] += int(usage.get("output") or 0)
    return split


def compaction_ends(state: Path) -> dict[str, int]:
    """How each compaction attempt of a session ended, by reason (`summarized`, `pruned`,
    `folded`, `failed`, …)."""
    ends: dict[str, int] = {}
    for path in (state / "conversations").glob("*.events.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "compaction/end":
                reason = str(event.get("reason") or "?")
                ends[reason] = ends.get(reason, 0) + 1
    return ends


async def run_session(
    model: str,
    window: int,
    state: Path,
    tag: str,
    ablate: frozenset[str] = frozenset(),
    enable: frozenset[str] = frozenset(),
    compaction: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    tasks = {t.id: t for t in load_tasks(REPO / "benchmarks" / "tasks-long")}
    workspace = Path(tempfile.mkdtemp(prefix="harness-eval-"))
    for index, task_id in enumerate(TASK_ORDER, 1):
        for rel, body in tasks[task_id].setup.items():
            path = workspace / f"t{index}" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")

    engine = product_engine(model, workspace, state, tag, ablate, enable=enable)
    # `window` 0 is the product's own setting ("auto": the model's window under the 400k
    # ceiling), where a threshold compaction is far away and only a mechanism that acts
    # earlier does anything.
    settings: dict[str, Any] = {"max_context_tokens": window or "auto", **(compaction or {})}
    engine.compaction_settings = lambda: settings

    prompts = [
        _prompt_for(f"t{i}", tasks[task_id].prompt) for i, task_id in enumerate(TASK_ORDER, 1)
    ] + [RECALL_PROMPT, DETAIL_PROMPT, ARTIFACT_PROMPT]
    answers: list[str] = []
    compactions = 0
    started = time.time()
    try:
        for prompt in prompts:
            # Everything the turn said, not its last message: a finish gate can make the model
            # speak again after it answered (2026-10-10: an answer naming all four files, then a
            # second message about an unfinished check, graded as naming none).
            said: list[str] = []
            async for event in engine.run(prompt):
                kind = event.type.value
                if kind == "assistant_message" and event.data.get("text"):
                    said.append(event.data["text"])
                if kind == "compacted":
                    compactions += 1
            answers.append("\n".join(said))
    finally:
        try:
            engine.executor.close()
        except Exception:
            pass

    checks = {}
    for index, task_id in enumerate(TASK_ORDER, 1):
        ok, detail = _run_checker(tasks[task_id].check, workspace / f"t{index}")
        checks[task_id] = ok if ok else detail[-300:]
    edited = changed_files(workspace / "t2", tasks[TASK_ORDER[1]].setup)
    recall_answer, detail_answer, artifact_answer = answers[-3], answers[-2], answers[-1]
    named = artifact_answer.replace("\\", "/")
    shutil.rmtree(workspace, ignore_errors=True)
    return {
        "tag": tag,
        "model": model,
        "checks": checks,
        "recall": RECALL_ANSWER in (recall_answer or ""),
        "recall_answer": (recall_answer or "")[:160],
        "detail": re.search(rf"(?<!\d){DETAIL_ANSWER}(?!\d)", detail_answer or "") is not None,
        "detail_answer": (detail_answer or "")[:160],
        # Every file the session really changed in t2 is named; with none changed, nothing is.
        "artifact": bool(edited) and all(path in named for path in edited),
        "artifact_missing": [path for path in edited if path not in named],
        "compactions": compactions,
        "wall": round(time.time() - started, 1),
    }


def session_passed(result: dict[str, Any]) -> bool:
    """Whether the session did its work: every task's check, and what it still knew after the
    compactions (the recall of task one, a detail of its work, the files it changed in task two).
    The eval ledger's `passed` for this suite; `gate` adds the KPIs on top. A row written before a
    probe existed is read without it."""
    checks = result.get("checks") or {}
    return (
        bool(checks)
        and all(ok is True for ok in checks.values())
        and bool(result.get("recall"))
        and bool(result.get("detail", True))
        and bool(result.get("artifact", True))
    )


def session_spec(window: int, compaction: Optional[dict[str, Any]] = None) -> str:
    """What this suite IS, for pairing ledger rows: the three tasks, the probes, the window."""
    from benchmarks import runs

    return runs.spec(
        REPO / "benchmarks" / "tasks-long", TASK_ORDER, RECALL_PROMPT, DETAIL_PROMPT, ARTIFACT_PROMPT, window,
        compaction or {},
    )


def gate(row: dict[str, Any], report: dict[str, Any]) -> list[str]:
    """What this run got wrong, as sentences; empty = the harness passed."""
    problems = [f"{task} failed its check" for task, ok in row["checks"].items() if ok is not True]
    if not row["recall"]:
        problems.append(f"recall after compaction was wrong: {row['recall_answer']!r}")
    if not row.get("detail", True):
        problems.append(f"a detail of task one was lost across the cuts: {row.get('detail_answer')!r}")
    if not row.get("artifact", True):
        problems.append(f"files changed in t2 not named after compaction: {row.get('artifact_missing')}")
    if row["compactions"] < 1:
        problems.append("no compaction ran — the thresholds were never reached")
    unexplained = report["latency"]["stalls"].get("unexplained", 0)
    if unexplained:
        problems.append(f"{unexplained} unexplained pre-step stall(s)")
    if report["spend"]["unattributed_pct"] > MAX_UNATTRIBUTED_PCT:
        problems.append(f"{report['spend']['unattributed_pct']}% of tokens unattributed")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", action="append", required=True)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--window", type=int, default=60_000)
    parser.add_argument("--out", default="", help="keep the event logs + results here")
    args = parser.parse_args(argv)

    from benchmarks import runs

    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="harness-eval-out-"))
    kpis = _kpis()
    failures = 0
    rows = []
    entries: list[dict[str, Any]] = []
    for model in args.model:
        for n in range(args.runs):
            tag = f"{model.replace(':', '_').replace('/', '_')}-{n}"
            state = out / tag
            row = asyncio.run(run_session(model, args.window, state, tag))
            report = kpis.compute(state)
            problems = gate(row, report)
            row.update(
                unattributed_pct=report["spend"]["unattributed_pct"],
                prepare_pct=report["spend"]["prepare_pct"],
                stalls=report["latency"]["stalls"],
                problems=problems,
            )
            rows.append(row)
            entry = runs.row(
                suite="long", task="session", variant="baseline", model=model,
                passed=session_passed(row), state=state, tag=tag,
                steps=int(report.get("steps") or 0), tokens=int(report.get("tokens") or 0),
                fresh=fresh_tokens(state), wall=row["wall"], task_spec=session_spec(args.window),
                flags=["kpi-gate"] if problems and session_passed(row) else [],
            )
            runs.record([entry])
            entries.append(entry)
            failures += bool(problems)
            verdict = "PASS" if not problems else "FAIL"
            print(
                f"[{verdict}] {tag}: checks={row['checks']} recall={row['recall']} detail={row.get('detail')} artifact={row.get('artifact')} "
                f"compactions={row['compactions']} unattributed={row['unattributed_pct']}% "
                f"prepare={row['prepare_pct']}% stalls={row['stalls']} wall={row['wall']}s",
                flush=True,
            )
            for problem in problems:
                print(f"    - {problem}", flush=True)
    (out / "results.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"logs and results: {out}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
