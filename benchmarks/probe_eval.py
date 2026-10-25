"""Probe eval — one single-turn session per `benchmarks/tasks-probe` task, on the
product's engine, graded on what the task's mechanism protects (live).

`benchmarks/make_probe_tasks.py` says why each probe exists. A probe passes when all of:

    check       the task's deterministic check, run AFTER its `hidden` files land
    unchanged   every file the task lists as off-limits is byte-identical to its setup
    answer      the final answer contains each `answer_mentions` string (any case)
    tools       for a probe that names `tools`: the calls the session made, as the engine
                reported them, meet each expectation (a tool called or not; for `show_visual`,
                the block types shown and whether a block's numbers came from a file)

Spends real tokens:

    .venv\\Scripts\\python.exe -m benchmarks.probe_eval --model opencode-go:deepseek-v4.1-flash [--only forget-the-caller]
"""

from __future__ import annotations

import argparse
import asyncio
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

from benchmarks import runs  # noqa: E402
from benchmarks.harness_eval import _kpis, fresh_tokens, product_engine, release  # noqa: E402
from harness.observability.bench import _run_checker  # noqa: E402


PROBES = REPO / "benchmarks" / "tasks-probe"


def load_probes(only: Optional[list[str]] = None) -> list[dict[str, Any]]:
    probes = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(PROBES.glob("*/task.json"))
    ]
    return [probe for probe in probes if not only or probe["id"] in only]


def _tools_verdict(expected: dict[str, Any], calls: list[dict[str, Any]], answer: str = "") -> Any:
    """True when the session's calls meet `expected`, else the first reason they do not.

    A visual counts only once the harness showed it (its result carries the visual card): a call
    the catalog refused showed the person nothing, however close it came."""
    for name, want in expected.items():
        made = [call for call in calls if call.get("name") == name]
        if not want.get("called", True):
            if made:
                return f"called {name} where text would do"
            continue
        if name != "show_visual":
            if not made:
                return f"never called {name}"
            continue
        shown = [
            block
            for call in made
            if isinstance(call.get("card"), dict) and call["card"].get("card") == "visual"
            for block in call["card"].get("blocks") or []
        ]
        if not shown:
            return f"{len(made)} show_visual call(s), none shown" if made else "never showed a visual"
        kinds = {block.get("type") for block in shown}
        wanted = want.get("blocks_any") or []
        if wanted and not kinds & set(wanted):
            return f"showed {sorted(kinds)}, not any of {wanted}"
        if want.get("source") and not any(
            "source" in block or any("source" in item for item in block.get("items") or [])
            for block in shown
        ):
            return "typed the numbers instead of naming the file"
        # The visual is part of the answer: an answer that restates it as a table says the same
        # thing twice (the owner's report, 2026-10-09; 8 of 10 answers with a visual did).
        if want.get("no_restate") and _TABLE_RULE.search(answer or ""):
            return "restated the visual as a table in the answer"
    return True


#: A Markdown table's header rule: `| --- | :---: |`.
_TABLE_RULE = re.compile(r"^\s*\|[\s:|-]+\|\s*$", re.M)


def grade(probe: dict[str, Any], workspace: Path, answer: str, calls: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """The verdicts of one finished probe, each with the reason it failed."""
    for rel, body in (probe.get("hidden") or {}).items():
        path = workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    ok, detail = _run_checker(probe["check"], workspace)
    touched = [
        rel
        for rel in probe.get("unchanged") or []
        if not (workspace / rel).is_file()
        or _unchanged_bytes((workspace / rel).read_bytes()) != _unchanged_bytes(setup_bytes(probe["setup"][rel]))
    ]
    missing = [
        needle
        for needle in probe.get("answer_mentions") or []
        if needle.lower() not in (answer or "").lower()
    ]
    verdict = {
        "passed": ok and not touched and not missing,
        "check": True if ok else detail[-300:],
        "touched": touched,
        "missing": missing,
    }
    if probe.get("tools"):
        tools = _tools_verdict(probe["tools"], calls or [], answer)
        verdict["tools"] = tools
        verdict["passed"] = verdict["passed"] and tools is True
    return verdict


def mcp_tools(spec: Optional[dict[str, Any]], loop: Any) -> list[Any]:
    """The probe's MCP server as the session's registry receives one (`build_callables`: the
    `mcp__<server>__<tool>` names, deferred, in the MCP group), answered from the task's own
    data instead of a process: what is under test is how the tools reach the model, not the
    transport. A call is answered by the entry whose arguments it matches, else the tool's
    `otherwise` text as the server's error."""
    if not spec:
        return []
    from types import SimpleNamespace

    from harness.extensions.mcp import MCPServerDef, build_callables

    by_tool = {tool["name"]: tool for tool in spec["tools"]}

    def canonical(arguments: Any) -> str:
        return json.dumps({k: str(v).strip() for k, v in (arguments or {}).items()}, sort_keys=True)

    async def call(tool: str, arguments: dict[str, Any]) -> Any:
        entry = by_tool[tool]
        for answer in entry["answers"]:
            if canonical(answer["args"]) == canonical(arguments):
                return json.dumps(answer["result"], ensure_ascii=False)
        return {"error": entry["otherwise"] or "no data"}

    server = MCPServerDef(name=spec["server"], transport="stdio", command="probe", requires_approval=False)
    listed = [
        SimpleNamespace(name=tool["name"], description=tool["description"], inputSchema=tool["inputSchema"], annotations=None)
        for tool in spec["tools"]
    ]
    return build_callables(server, listed, call, loop)


def seed_repairs(engine: Any, repairs: list[dict[str, Any]]) -> None:
    """Write what an EARLIER session of this project repaired into its repairs store (ADR
    0132), as that session would have: the probe's situation is a failure that is back."""
    store = getattr(engine, "repairs", None)
    if not repairs or store is None:
        return
    from harness.context.memory.repairs import Repairs

    from harness.context.memory.anchors import digest
    from harness.context.memory.repairs import change_between

    earlier = Repairs(store.root, resolve=store._resolve, stamp=lambda: ("an-earlier-session", 1))
    for repair in repairs:
        # `left`: what that repair left in a file. The file as it is now (the bug, back) is
        # what it was before the repair; the record is written with the repair's work in place,
        # then the bug is put back, as the earlier session and whatever undid it would have.
        found = {raw: store.resolve(raw) for raw in repair.get("left") or {}}
        now = {raw: at.read_bytes() for raw, at in found.items() if at is not None and at.is_file()}
        before = {raw: digest(found[raw]) for raw in now}
        left = {raw: text.encode("utf-8") for raw, text in (repair.get("left") or {}).items() if raw in now}
        changes = {raw: change_between(now[raw], left[raw]) for raw in left}
        try:
            for raw, body in left.items():
                found[raw].write_bytes(body)
            earlier.record(
                repair["marks"], repair.get("command", ""), repair.get("files") or [], repair.get("steps", 0),
                before=before, changes=changes,
            )
        finally:
            for raw, body in now.items():
                found[raw].write_bytes(body)


def setup_bytes(body: str) -> bytes:
    """A setup file's bytes: text as UTF-8, or a binary file (a workbook) written `base64:…`."""
    if body.startswith("base64:"):
        import base64

        return base64.b64decode(body[len("base64:"):])
    return body.encode("utf-8")


def _unchanged_bytes(data: bytes) -> bytes:
    """A file's bytes as compared for `unchanged`: line endings alike, as reading it as text did."""
    return data.replace(b"\r\n", b"\n")


def write_setup(probe: dict[str, Any], workspace: Path) -> None:
    """The probe's files as the task starts, into `workspace`."""
    for rel, body in probe["setup"].items():
        path = workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(setup_bytes(body))


def seed_earlier(engine: Any, earlier: dict[str, Any]) -> None:
    """Turns another model answered before the probe's, then the switch to the model under test,
    the way the person switches in the app (`switch_model`): what a model knows of which model it
    is and which wrote the replies above it."""
    for turn in earlier.get("turns") or []:
        engine.messages.append({"role": "user", "content": turn["user"], "ts": time.time()})
        engine.messages.append({"role": "assistant", "content": turn["assistant"], "ts": time.time()})
    current = engine.model
    engine.model = earlier["model"]
    engine.switch_model(current)


def with_model(probe: dict[str, Any], model: str) -> dict[str, Any]:
    """The probe with `{model}` in what its answer must mention read as the model under test's id
    (its name without the provider): a probe of which model answers is graded per model."""
    mentions = [m.replace("{model}", model.split(":", 1)[-1]) for m in probe.get("answer_mentions") or []]
    return {**probe, "answer_mentions": mentions} if probe.get("answer_mentions") else probe


async def run_in(
    model: str,
    probe: dict[str, Any],
    workspace: Path,
    state: Path,
    tag: str,
    ablate: frozenset[str] = frozenset(),
    provider: Any = None,
    seed: bool = True,
) -> dict[str, Any]:
    """One session of the probe's prompt in a workspace the caller prepared and keeps.
    `seed=False` leaves the project's repairs store as the caller made it (a store an
    earlier real session wrote, `benchmarks/recur_eval.py`)."""
    engine = product_engine(
        model, workspace, state, tag, ablate, provider=provider,
        extra_tools=mcp_tools(probe.get("mcp"), asyncio.get_running_loop()),
    )
    if seed:
        seed_repairs(engine, probe.get("repairs") or [])
    if probe.get("earlier"):
        seed_earlier(engine, probe["earlier"])
    answer = ""
    end: dict[str, Any] = {}
    # The session's calls as the engine reported them: what was asked, how it ended, and the
    # card the person was shown. A probe that names `tools` is graded on these.
    calls: dict[str, dict[str, Any]] = {}
    started = time.time()
    try:
        async for event in engine.run(probe["prompt"]):
            kind = event.type.value
            if kind == "assistant_message":
                answer = event.data.get("text") or answer
            elif kind == "tool_proposed" and not event.data.get("in_program"):
                calls[str(event.data.get("call_id"))] = {
                    "name": event.data.get("name"), "arguments": event.data.get("arguments"),
                }
            elif kind == "tool_finished" and str(event.data.get("call_id")) in calls:
                shown = event.data.get("presentation") or {}
                calls[str(event.data.get("call_id"))].update(
                    status=event.data.get("status"), card=shown.get("card"), summary=shown.get("summary"),
                )
            elif kind == "turn_end":
                end = dict(event.data)
    finally:
        release(engine)
    return {
        "answer": answer,
        "calls": list(calls.values()),
        "unverified": bool(end.get("unverified")),
        "check_failed": bool(end.get("check_failed")),
        "wall": round(time.time() - started, 1),
    }


async def run_probe(
    model: str,
    probe: dict[str, Any],
    state: Path,
    tag: str,
    ablate: frozenset[str] = frozenset(),
    provider: Any = None,
) -> dict[str, Any]:
    probe = with_model(probe, model)
    workspace = Path(tempfile.mkdtemp(prefix=f"probe-{probe['id']}-"))
    write_setup(probe, workspace)
    ran = await run_in(model, probe, workspace, state, tag, ablate, provider=provider)
    try:
        verdict = grade(probe, workspace, ran["answer"], ran["calls"])
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return {
        "tag": tag,
        "probe": probe["id"],
        "mechanism": probe["mechanism"],
        **verdict,
        **ran,
        "answer": ran["answer"][:400],
        "answer_chars": len(ran["answer"]),
        # What was called and how it ended, without the payloads (a card holds a whole table).
        "calls": [{"name": c.get("name"), "status": c.get("status"), "summary": c.get("summary")} for c in ran["calls"]],
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="probe-eval-"))
    kpis = _kpis()
    rows = []
    entries = []
    for probe in load_probes(args.only):
        for run in range(max(1, args.runs)):
            tag = f"{probe['id']}-{run}"
            row = asyncio.run(run_probe(args.model, probe, out / tag, tag))
            rows.append(row)
            report = kpis.compute(out / tag)
            entry = runs.row(
                suite="probe", task=probe["id"], variant="baseline", model=args.model,
                passed=row["passed"], state=out / tag, tag=tag,
                steps=int(report.get("steps") or 0), tokens=int(report.get("tokens") or 0),
                fresh=fresh_tokens(out / tag), wall=row["wall"], task_spec=runs.spec(probe),
            )
            runs.record([entry])
            entries.append(entry)
            print(
                f"[{entry['outcome'].upper()}] {tag}: check={row['check'] is True} "
                f"touched={row['touched']} missing={row['missing']} wall={row['wall']}s"
                + (f" tools={row['tools']}" if "tools" in row else "")
                + (f" ({entry['why']})" if entry["why"] else ""),
                flush=True,
            )
    (out / "results.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"logs and results: {out}")
    # A run that was not a run is not a failed probe: it is reported, and the exit code
    # speaks only for the runs that happened.
    return 0 if all(entry["outcome"] != runs.FAILED for entry in entries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
