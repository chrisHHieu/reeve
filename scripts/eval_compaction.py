"""Offline evaluation harness for context compaction: what it LOSES, not what it saves.

Every existing compaction test pins tokens, order and boundaries. None of them can answer
the question a change to the policy actually turns on: **can the session still finish its
job afterwards?** A compaction that halves the window and drops the file path the work is
about looks identical to one that halves the window and keeps everything, in every number
the engine reports.

So this harness plants facts, compacts, and then asks a FRESH model — one that never saw the
conversation — questions whose answers only exist in the region that was summarized away.
Recall is the metric; tokens retained is the cost; the pair is the trade-off.

    python -m scripts.eval_compaction --model anthropic:claude-opus-5
    python -m scripts.eval_compaction --model ... --policies current,prune-only
    python -m scripts.eval_compaction --model ... --stub    # no network; plumbing check

This is NOT a pytest: it needs a live model and money, is run on demand, and its output is
committed as a dated report under `reports/`. A cheap sanity test
(`tests/test_compaction_eval.py`) exercises the plumbing with a stub so the scoring stays
covered — same split `scripts/eval_reviewer.py` uses.

**Why the facts are planted rather than mined.** Deriving a question bank from arbitrary
history needs a model to decide what mattered, which makes the metric measure that model.
Generating both the fact and its question from one template means the answer key is exact
and the only model under test is the compaction policy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Allow `python scripts/eval_compaction.py` as well as `-m scripts.eval_compaction`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.context import compaction as C  # noqa: E402
from harness.context.compaction.extraction import text_of  # noqa: E402


#: The policies compared. Each is a way of spending less context, and the point of the
#: matrix is that they are not interchangeable — `none` is the ceiling on recall and the
#: floor on cost, `prune-only` is free and lossy in one specific way, and the tail policies
#: trade recall for a fixed retained budget instead of a summarized one.
POLICIES: dict[str, dict[str, Any]] = {
    "none": {"kind": "verbatim", "keep_tokens": 10**9},
    "tail25k": {"kind": "verbatim", "keep_tokens": 25_000},
    "tail10k": {"kind": "verbatim", "keep_tokens": 10_000},
    "prune-only": {"kind": "prune", "keep_tokens": 25_000},
    "current": {"kind": "summarize", "keep_tokens": 25_000},
}

#: Facts are planted at these depths. `old` is deep inside the summarized region, `middle`
#: is at its edge, and `recent` is inside the verbatim tail — so the per-depth recall column
#: shows WHERE a policy loses things, which is the actionable part of the result.
DEPTHS = ("old", "middle", "recent")


@dataclass
class Fact:
    """One thing the session must still know after compaction."""

    depth: str
    token: str  # the unique string the answer must contain
    question: str
    what: str  # for the report: what kind of fact this is


@dataclass
class Scenario:
    """A generated session plus the questions its compaction must survive."""

    name: str
    messages: list[dict[str, Any]]
    facts: list[Fact]
    boundary: int


@dataclass
class PolicyResult:
    policy: str
    recall: dict[str, float] = field(default_factory=dict)
    answered: int = 0
    correct: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    summary_calls: int = 0

    @property
    def overall(self) -> float:
        return (self.correct / self.answered) if self.answered else 0.0

    @property
    def saved_pct(self) -> float:
        if not self.tokens_before:
            return 0.0
        return max(0.0, (self.tokens_before - self.tokens_after) / self.tokens_before)


# -- scenario generation ----------------------------------------------------------


def default_facts() -> list[Fact]:
    """The planted facts, in one place so a caller (and the plumbing test) can reference the
    same list the scenario uses rather than re-deriving it."""
    return [
        Fact(
            depth="old",
            token="src/ledger.py",
            question="Which file holds the posting logic that was being fixed?",
            what="a file path",
        ),
        Fact(
            depth="old",
            token="NULLABLE_SETTLE_DATE",
            question="What is the name of the feature flag that was enabled?",
            what="a constant",
        ),
        Fact(
            depth="middle",
            token="never send without my approval",
            question="What standing constraint did the user state about sending?",
            what="a user constraint",
        ),
        Fact(
            depth="recent",
            token="INV-4471",
            question="What is the newest open incident the user mentioned?",
            what="a recent instruction",
        ),
    ]


def build_scenario(name: str, *, turns: int = 24) -> Scenario:
    """A long working session with facts planted at known depths.

    The shape is the one compaction is FOR: many turns of tool work, most of it no longer
    load-bearing, and a handful of specifics the session must not lose.

    Every turn carries a USER message, which is not decoration. `pick_boundary` chooses
    among user turns, so a transcript whose only user messages are the first and the last
    has exactly one legal tail for every keep budget — the tail policies then produce
    identical views and the comparison between them measures nothing. The generator was
    wrong in exactly that way, and `tests/test_compaction_eval.py` now pins the property.
    """
    facts = default_facts()
    by_depth: dict[str, list[Fact]] = {}
    for fact in facts:
        by_depth.setdefault(fact.depth, []).append(fact)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "You are a careful coding agent."},
        {"role": "user", "content": "Audit the posting path and fix the null settle date."},
    ]
    planted: dict[str, int] = {depth: 0 for depth in DEPTHS}
    for i in range(turns):
        depth = "old" if i < turns // 3 else "middle" if i < 2 * turns // 3 else "recent"
        messages.append(
            {"role": "user", "content": f"keep going — step {i} of the audit"}
        )
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "function": {
                            "name": "run_shell",
                            "arguments": json.dumps({"command": f"pytest tests/test_{i}.py -q"}),
                        },
                    }
                ],
            }
        )
        # Plant this depth's next unplanted fact into this turn's tool result, then filler —
        # so the fact sits inside the region a policy may summarize away rather than in a
        # message the harness happens to keep.
        #
        # `recent` facts are deliberately NOT planted here: their whole purpose is to be in
        # the tail, and planting them twice would hide a policy that dropped the tail.
        body = ""
        pending = by_depth.get(depth, [])
        if depth != "recent" and planted[depth] < len(pending):
            body = f"SEEN-{depth}: noted {pending[planted[depth]].token}. "
            planted[depth] += 1
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "content": body + ("diagnostic output " * 300),
            }
        )
        messages.append({"role": "assistant", "content": f"step {i} done " + ("y" * 400)})

    recent = by_depth.get("recent", [])
    tail = "Latest: " + (
        f"{recent[0].token} landed, fix it next." if recent else "keep going."
    )
    messages.append({"role": "user", "content": tail})
    messages.append({"role": "assistant", "content": "on it"})

    boundary = C.pick_boundary(messages, keep_tokens=8_000) or len(messages) // 2
    return Scenario(name=name, messages=messages, facts=facts, boundary=boundary)


# -- applying a policy ------------------------------------------------------------


class _StubSummarizer:
    """Deterministic stand-in so the plumbing can be tested without a network.

    It must satisfy the SAME contract a real summarizer does — every required section —
    or `build_state`'s structure check (and its one corrective re-ask) turns every policy
    run into a failure that measures the stub, not the policy."""

    def complete(self, *, model, messages, tools=None, **settings):
        from harness.llm.providers import AssistantTurn

        # Every required heading, from the contract itself: a hand-typed list here went stale
        # the moment a section was added and turned every policy run into a stub failure.
        body = {
            "Primary request and intent": "audit the posting path",
            "Key concepts and decisions": "The ledger is append-only.",
            "Artifacts and files": "src/posting.py",
            "All user messages": "- audit the posting path",
            "Pending tasks": "- finish the audit",
            "Current work": "reviewing the posting path",
            "Next step": "report findings",
        }
        text = "\n\n".join(f"## {name}\n{body.get(name, '(none)')}" for name in C.SUMMARY_SECTIONS)
        return AssistantTurn(text=text, finish_reason="stop")

    def capabilities(self, model):
        from harness.llm.providers import ModelCapabilities

        return ModelCapabilities()


def apply_policy(
    scenario: Scenario, policy: str, provider: Any, model: str
) -> tuple[list[dict[str, Any]], int]:
    """The outbound view this policy produces, plus the summarizer calls it cost.

    Built from `harness.context.compaction` directly rather than through `TurnEngine`: the engine's
    loop, retries and thresholds are not what is under test here, and driving them would
    couple the measurement to the machinery.
    """
    spec = POLICIES[policy]
    messages = scenario.messages
    calls = 0

    if spec["kind"] == "verbatim":
        keep = spec["keep_tokens"]
        boundary = C.pick_boundary(messages, keep_tokens=keep)
        if boundary is None:
            return messages, 0
        head = [messages[0]] if messages[0].get("role") == "system" else []
        return head + messages[boundary:], 0

    if spec["kind"] == "prune":
        keep = spec["keep_tokens"]
        boundary = C.pick_boundary(messages, keep_tokens=keep) or len(messages)
        state = C.CompactionState(
            boundary_index=0,
            summary_text="",
            working_state="",
            pruned_upto=C.prune_boundary(messages, keep_tokens=int(0.1 * keep)),
        )
        return C.apply_to_outbound(messages, state), 0

    # summarize: the shipped policy — prune the cheap lever, then summarize the span.
    keep = spec["keep_tokens"]
    state = C.build_state(
        messages,
        provider=provider,
        model=model,
        keep_tokens=keep,
        prior=None,
        replay=None,
        tools=None,
        trigger=int(0.8 * 100_000),
        standing_plan=None,
    )
    if state is None:
        return messages, 0
    calls = 1
    return C.apply_to_outbound(messages, state), calls


# -- asking -----------------------------------------------------------------------


def _context_text(messages: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"[{m.get('role')}] {text_of(m.get('content'))}" for m in messages
    )


ANSWER_SYSTEM = (
    "You are resuming a session whose earlier history was compacted. Answer the question "
    "using ONLY the context provided. If the context does not contain the answer, reply "
    "exactly NOT_IN_CONTEXT. Otherwise reply with the answer, short."
)


async def ask(provider: Any, model: str, context: str, question: str) -> str:
    """One question, asked with the compacted view and nothing else — the position a
    resumed session is actually in."""
    answer = await asyncio.to_thread(
        lambda: provider.complete(
            model=model,
            messages=[
                {"role": "system", "content": ANSWER_SYSTEM},
                {"role": "user", "content": f"CONTEXT:\n{context}\n\nQUESTION: {question}"},
            ],
            max_tokens=200,
        )
    )
    return str(getattr(answer, "text", "") or "")


def score(fact: Fact, answer: str) -> bool:
    """Whether the answer carries the fact. Normalised because a model may case-fold or
    re-quote; the token itself is unique and long enough that a substring test is exact."""
    return fact.token.lower() in answer.lower()


# -- the run ----------------------------------------------------------------------


async def run_policy(
    scenario: Scenario, policy: str, provider: Any, model: str
) -> PolicyResult:
    result = PolicyResult(policy=policy)
    view, calls = apply_policy(scenario, policy, provider, model)
    result.summary_calls = calls
    result.tokens_before = C.estimate_tokens(scenario.messages)
    result.tokens_after = C.estimate_tokens(view)
    context = _context_text(view)

    for fact in scenario.facts:
        result.answered += 1
        if score(fact, await ask(provider, model, context, fact.question)):
            result.correct += 1
            result.recall[fact.depth] = result.recall.get(fact.depth, 0.0) + 1
    # Turn counts into rates: recall is per depth, so a reader can see WHERE it lost things.
    for depth in DEPTHS:
        hits = result.recall.get(depth, 0.0)
        total = sum(1 for f in scenario.facts if f.depth == depth)
        result.recall[depth] = (hits / total) if total else 0.0
    return result


def format_report(results: list[PolicyResult], model: str, stamp: str) -> str:
    lines = [
        f"# Compaction recall eval — {stamp}",
        "",
        f"Model: `{model}`. {len(results)} policies over a generated long session.",
        "",
        "**Recall** is the fraction of planted facts a FRESH model could still answer from "
        "the post-compaction context alone. **Tokens** is what that policy retained. A "
        "policy that wins on tokens and loses on recall has not made progress; that pairing "
        "is the whole point of the table.",
        "",
        "| policy | recall (all) | old | middle | recent | tokens kept | saved | summary calls |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| `{r.policy}` | {r.overall:.0%} | "
            f"{r.recall.get('old', 0):.0%} | {r.recall.get('middle', 0):.0%} | "
            f"{r.recall.get('recent', 0):.0%} | {r.tokens_after:,} | "
            f"{r.saved_pct:.0%} | {r.summary_calls} |"
        )
    lines.append("")
    lines.append(
        "Reading it: `none` is the ceiling — recall with the whole conversation still "
        "present, and the cost that made compaction necessary. `recent` recall near 100% "
        "for every policy is the verbatim tail doing its job; a policy that loses `old` "
        "recall has summarized away a load-bearing specific, which is what this measures "
        "and what the token counts cannot see."
    )
    return "\n".join(lines)


def build_provider(model: str, *, stub: bool) -> Any:
    if stub:
        return _StubSummarizer()
    from harness.llm.providers import ProviderRouter
    from harness.safety.secrets import SecretStore

    return ProviderRouter(SecretStore(), default_provider=model.split(":", 1)[0])


async def _amain(args: argparse.Namespace) -> int:
    provider = build_provider(args.model, stub=args.stub)
    names = args.policies.split(",") if args.policies else list(POLICIES)
    for name in names:
        if name not in POLICIES:
            print(f"unknown policy: {name} (have: {', '.join(POLICIES)})", file=sys.stderr)
            return 2
    scenario = build_scenario("generated")
    results = [
        await run_policy(scenario, name, provider, args.model) for name in names
    ]
    report = format_report(results, args.model, args.stamp or "unstamped")
    if args.stub:
        report += "\n\n**STUB RUN** — plumbing check only; not evidence about any model."
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
        print(f"\n(written to {args.out})", file=sys.stderr)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Measure what compaction loses in recall.")
    p.add_argument("--model", required=True, help="e.g. anthropic:claude-opus-5")
    p.add_argument("--policies", help=f"comma-separated (default: all). Have: {', '.join(POLICIES)}")
    p.add_argument("--stub", action="store_true", help="no network; canned answers (plumbing check)")
    p.add_argument("--out", help="also write the report to this path")
    p.add_argument("--stamp", help="date stamp for the report header, e.g. 2026-09-10")
    args = p.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
