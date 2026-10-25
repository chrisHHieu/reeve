"""Asking a model to summarize the part of the history a compaction removes.

The prompt texts below are the contract with the summarizer. Their wording is functional: the nine
required sections are checked after the reply (`summary_missing_sections`), and a change to either
side is caught by a test that compares the two.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from .boundary import pick_boundary
from .extraction import cap_user_messages, extract_user_messages, extract_working_state, text_of
from .asking import ask_text, settle_parts
from .parts import part, parts_of, render_parts
from .plans import clip_approved_plan, normalize_plan
from .record import CompactionState
from .tokens import (
    _SPAN_BUDGET_CHARS,
    _SPAN_TOOL_RESULT_CLIP,
    _USER_MESSAGES_MAX,
    SUMMARY_MAX_TOKENS,
    block_limits,
    estimate_tokens,
    redact_secrets,
    summary_call_tokens,
)

# First line of every summarizer instruction. The replay call keeps the conversation's own tool list
# in front of the model, which is what makes it a cache prefix, and a model that sees tools will
# sometimes answer with a call. That is a reply with no text, which surfaces as "summarizer returned
# an empty summary (tool_calls=1)" and costs the compaction (session 3d9041d1-0e6). The call's tools
# are never executed, so the only thing a call can do here is cost the summary.
#
# SUMMARY_SYSTEM_PROMPT is the instruction when the span is rendered into one user message.
# COMPACTION_INSTRUCTION is the same nine sections for the replay shape, where the conversation is
# sent in front of it and so is above, not below. It is delivered as the final user message with the
# conversation's own system prompt, tools and messages left in front of it, which makes the call a
# real prefix of the request that just ran, so the provider reads its cache again and does not pay for
# a prompt that shares no bytes with anything. SUMMARY_SECTIONS lists the nine sections the prompt
# requires, in order. A resumed session relies on each one, and the prose itself cannot be pinned, so
# the deterministic half of "does the summary carry what matters" is checking they are there. Tests
# pin it against the prompt, so a section dropped from either side is noticed. SUMMARY_CORRECTION is
# the one corrective re-ask: a second miss is the caller's failure policy and not a third call.
NO_TOOLS_NOTICE = (
    "CRITICAL: respond with TEXT ONLY. Do NOT call any tool. Any tools listed in this "
    "request are only there so it shares its prefix with the conversation; a tool call "
    "here is a lost summary.\n\n"
)

SUMMARY_SYSTEM_PROMPT = NO_TOOLS_NOTICE + """You are compacting an AI coding agent's session history so the agent can continue working in a smaller context. Write a structured summary of the conversation below. It is the agent's ONLY memory of these turns, so preserve everything load-bearing.

Produce ALL of the following sections, in this order, each as a markdown heading:

1. **Primary request and intent** — what the user is trying to get done, in their terms, including standing constraints stated at any point (e.g. "never send without my approval"). Constraints outlive the turns they were stated in.
2. **Key concepts and decisions** — domain facts, technical choices, and rationale established so far. Include the WHY, not just the what — a decision without its reason gets relitigated.
3. **Artifacts and files** — every file/deliverable created, modified, or read that still matters: path, its role, and a short excerpt of load-bearing content only.
4. **Errors and fixes** — problems hit and how they were resolved, including user corrections ("no, do it this way") — those are feedback with lasting force.
5. **Problem solving** — what is being worked out right now and has no answer yet: the symptom, hypotheses already ruled out (and how), what was tried, what is still unexplained. Errors and fixes holds what is SETTLED; this holds what is open, so an investigation resumes instead of restarting.
6. **All user messages** — a chronological list of every user message (trimmed of pasted bulk). This is the intent audit-trail.
7. **Pending tasks** — explicitly incomplete items, promised follow-ups, things the user said "later" about.
8. **Current work** — precisely what was in progress at this point: which step, which file, what state.
9. **Next step** — the immediate next action, justified by the user's request, with a short VERBATIM quote of the user's latest words it follows from, so the work does not drift from what was asked. If no next step is implied, say so instead of inventing one.

Rules:
- Do NOT carry full file contents as truth. Note THAT a file was read/edited; the agent re-reads it if it needs the content again. Stale memory of a file is worse than no memory.
- Be concrete: paths, names, commands, ids — not vague references.
- Finished work keeps its outcome: what was done, the files it changed, and the result line of the check that proved it (e.g. `22 passed`). A later question about finished work can only be answered from here; only the steps that led there may be dropped.
- NEVER quote a secret — API keys, tokens, passwords, private keys — even if the conversation contains them verbatim. Write [REDACTED] where one appeared; the shape of the credential may be noted ("an OpenAI key"), never its value.
- Output only the summary sections, no preamble."""

COMPACTION_INSTRUCTION = SUMMARY_SYSTEM_PROMPT.replace(
    "Write a structured summary of the conversation below.",
    "Write a structured summary of the conversation ABOVE.",
).replace(
    "You are compacting an AI coding agent's session history",
    "You are now acting as a compaction engine for this session's history",
)


CONTINUATION_CONTRACT = (
    "Continue where you left off: pick up the current work and next step exactly as "
    "described. Do not re-ask answered questions, do not recap, do not mention that the "
    "context was compacted. If you need the contents of a file noted above, re-read it."
)

SUMMARY_SECTIONS = (
    "Primary request and intent",
    "Key concepts and decisions",
    "Artifacts and files",
    "Errors and fixes",
    "Problem solving",
    "All user messages",
    "Pending tasks",
    "Current work",
    "Next step",
)

SUMMARY_CORRECTION = (
    "Your previous reply did not contain all required sections (missing: {missing}). "
    "Produce the summary again: exactly the nine sections, in order, each as a markdown "
    "heading, and nothing else."
)


def summary_missing_sections(text: str) -> list[str]:
    """The required headings absent from a summarizer reply (case-insensitive substring).

    Formatting is forgiven, whether numbering, bold or heading level, because none of it changes what
    the model remembers. A missing section is not forgiven: each is a class of fact that silently
    stops surviving compaction."""
    lowered = (text or "").lower()
    return [name for name in SUMMARY_SECTIONS if name.lower() not in lowered]


def render_span(span: list[dict[str, Any]], *, budget_chars: int = _SPAN_BUDGET_CHARS) -> str:
    """The summarized span as compact text for the summarizer. Tool results are clipped hard, being the
    first casualty, and if the whole render still exceeds the budget the oldest lines are dropped,
    since the newest context is the most load-bearing."""
    lines: list[str] = []
    for message in span:
        role = message.get("role")
        if role in ("system", "notice"):
            continue
        if role == "tool":
            text = " ".join(text_of(message.get("content")).split())
            if len(text) > _SPAN_TOOL_RESULT_CLIP:
                text = text[: _SPAN_TOOL_RESULT_CLIP - 1] + "…"
            lines.append(f"[tool result] {text}")
            continue
        text = text_of(message.get("content"))
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                arguments = " ".join(str(function.get("arguments", "")).split())
                if len(arguments) > 200:
                    arguments = arguments[:199] + "…"
                lines.append(f"[assistant → {function.get('name')}] {arguments}")
            if text:
                lines.append(f"[assistant] {text}")
        elif role == "user" and text:
            # A message the engine attached on a tool's behalf carries no text, and rendering it
            # would put an empty "[user]" line in front of the summarizer.
            lines.append(f"[user] {text}")
    rendered = "\n".join(lines)
    if len(rendered) > budget_chars:
        rendered = "(…oldest turns elided…)\n" + rendered[-budget_chars:]
    return rendered


def compaction_instruction(instructions: str = "") -> str:
    """The instruction for the replay shape, appended after the conversation.

    Custom `instructions` replace the default prompt, a rule taken because whoever writes the
    compaction prompt owns what survives (docs/compaction-best-practices.md). The default's nine
    sections are a good contract and not a requirement, and the code-level net (redaction, the
    mechanical working state, the verbatim user messages) holds either way. The one thing a
    replacement cannot change is the direction: the conversation is above the instruction here and not
    below it, and the note says so."""
    base = (instructions or "").strip()
    if not base:
        return COMPACTION_INSTRUCTION
    return NO_TOOLS_NOTICE + base + "\n\n(The conversation to summarize is ABOVE this message.)"


# What a cut after the first one writes. The earlier summary stays in the block as it was
# written, so this one covers only the conversation since: asked to combine the two, as it was, the
# summarizer rewrote what the first cut kept at every cut after it, and the median cut lost 65% of
# the concrete details the block before it held. The earlier parts are shown for what they settle
# (the request, the names), and the replay path, where they are the `<compacted-history>` block
# inside the conversation, needs the same words.
_PART_RULES = (
    "The earlier summary stays in the session word for word: you are writing ONLY the part "
    "for the conversation after it. Do not repeat what it already says. Say what changed "
    "since: a decision reversed, a task finished, a fact corrected. A section with nothing "
    "new says \"Nothing new.\""
)


def summarizer_messages(
    span: list[dict[str, Any]], *, prior_summary: str = "", instructions: str = ""
) -> list[dict[str, Any]]:
    """The provider-ready messages for the summarizer call. On a later cut the earlier parts of the
    block come first, for context, and the turns since are what is summarized. Custom
    `instructions` replace the default system prompt."""
    body = render_span(span)
    if prior_summary:
        body = (
            "[earlier summary of this session — kept as it is. "
            + _PART_RULES
            + "]\n"
            + prior_summary
            + "\n\n[conversation since — summarize this]\n"
            + body
        )
    return [
        {"role": "system", "content": (instructions or "").strip() or SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": body},
    ]


def summarize_span(
    provider: Any,
    model: str,
    span: list[dict[str, Any]],
    *,
    prior_summary: str = "",
    max_tokens: int = SUMMARY_MAX_TOKENS,
    replay: Optional[list[dict[str, Any]]] = None,
    tools: Optional[list[dict[str, Any]]] = None,
    usage_sink: Optional[Callable[[Any], None]] = None,
    instructions: str = "",
    correction: str = "",
    target_tokens: Optional[int] = None,
    output_limit: Optional[int] = None,
) -> str:
    """One summarizer round trip. It blocks, so the engine runs it off the loop, and it raises on a
    provider failure or an empty summary, leaving the retry and trim policy to the caller.

    `correction` rides as one more user message after the instruction (or after the rendered span):
    the corrective re-ask `build_state` sends when a reply was missing required sections. It is a new
    tail on the identical prefix, so a replayed call still reads its prompt cache.

    `replay` is the conversation exactly as the provider last saw it. With one, the call becomes that
    conversation plus the instruction, so the prefix is byte-identical to the request that just ran and
    the provider reads its cache again and does not pay full price for a prompt that shares no bytes
    with it. Compaction fires when the context is fullest, which is exactly when that difference is
    largest. `tools` rides along for the same reason, since providers cache tools with the system
    prompt and leaving them out would move the boundary and lose the prefix. A tool call in the reply
    is harmless: this is a bare provider round trip and not the agent loop, so nothing dispatches it
    and only `.text` is read. Without `replay` the call has its original shape: a dedicated system
    prompt, and the span rendered into one user message."""
    if replay is not None:
        instruction = compaction_instruction(instructions)
        if prior_summary:
            # The replayed conversation carries the earlier parts as its `<compacted-history>`
            # block. Without the rules the summarizer reads it as part of what to summarize.
            instruction = instruction + "\n\n" + _PART_RULES
        messages = [*replay, {"role": "user", "content": instruction}]
    else:
        messages = summarizer_messages(span, prior_summary=prior_summary, instructions=instructions)
        tools = None
    if target_tokens:
        # The block's share of the window is a length the model is told and not a cap that cuts it
        # off. Reasoning spends the call's max_tokens first (SUMMARY_REASONING_HEADROOM), so a cap at
        # the target returned empty or section-less summaries.
        words = max(300, int(target_tokens * 0.75))
        length = (
            f"Length: the whole summary must stay under about {words} words — it replaces "
            "the history in a small context. Keep every section; be terse inside them."
        )
        last = messages[-1]
        messages = [*messages[:-1], {**last, "content": f"{last['content']}\n\n{length}"}]
        max_tokens = summary_call_tokens(target_tokens)
    if output_limit and output_limit > 0:
        # Never past what the model declares it can emit. A provider refuses a request whose
        # max_tokens exceeds the model's output limit, so an 8k-output model would reject the
        # reasoning headroom outright and compaction would fail every time.
        max_tokens = min(max_tokens, int(output_limit))
    if correction:
        messages = [*messages, {"role": "user", "content": correction}]
    return ask_text(provider, model, messages, tools=tools, max_tokens=max_tokens, usage_sink=usage_sink)


def build_state(
    messages: list[dict[str, Any]],
    *,
    provider: Any,
    model: str,
    keep_tokens: int,
    prior: Optional[CompactionState] = None,
    replay: Optional[list[dict[str, Any]]] = None,
    tools: Optional[list[dict[str, Any]]] = None,
    usage_sink: Optional[Callable[[Any], None]] = None,
    trigger: Optional[int] = None,
    standing_plan: Optional[list[dict[str, str]]] = None,
    approved_plan: Optional[str] = None,
    plan_path: Optional[str] = None,
    instructions: str = "",
    output_limit: Optional[int] = None,
) -> Optional[CompactionState]:
    """Summarize the turns since the last cut into a new part of the block: the earlier
    parts are kept as they were written, and two of them are combined only when the parts outgrow
    their budget. It returns None when there is nothing to compact and raises when the summarizer
    fails, so the caller applies the policy; a merge that fails costs nothing but the budget.

    `approved_plan` is the last plan the person approved (the engine's `propose_plan`). Omitted or
    None, the prior state's plan carries forward, because a compaction must not be the moment the
    session forgets what it was told to carry out.

    `trigger` sizes the block against the window it is freeing (`block_limits`). Omitted, the
    historical fixed limits apply, which is what every existing caller wants."""
    boundary = pick_boundary(messages, keep_tokens=keep_tokens)
    if boundary is None or (prior is not None and boundary <= prior.boundary_index):
        return None
    summary_tokens, user_limit = (
        block_limits(trigger) if trigger is not None else (SUMMARY_MAX_TOKENS, _USER_MESSAGES_MAX)
    )
    span = messages[(prior.boundary_index if prior is not None else 0) : boundary]
    prior_users = list(prior.user_messages) if prior is not None else []
    asked = dict(
        prior_summary=prior.summary_text if prior is not None else "",
        target_tokens=summary_tokens,
        output_limit=output_limit,
        replay=replay,
        tools=tools,
        usage_sink=usage_sink,
        instructions=instructions,
    )
    summary = summarize_span(provider, model, span, **asked)
    missing = summary_missing_sections(summary)
    if missing:
        # One corrective re-ask: formatting drift is worth a second call, and a second miss raises
        # into the caller's failure policy (retry, trim) and does not adopt a summary missing whole
        # classes of fact. The correction is a new tail on the same prefix, so the replayed call still
        # reads its cache.
        summary = summarize_span(
            provider, model, span, **asked, correction=SUMMARY_CORRECTION.format(missing=", ".join(missing))
        )
        missing = summary_missing_sections(summary)
        if missing:
            raise RuntimeError("summarizer did not produce the required sections: " + ", ".join(missing))
    users, dropped = cap_user_messages(
        prior_users + extract_user_messages(span),
        prior_dropped=prior.user_messages_dropped if prior is not None else 0,
        limit=user_limit,
    )

    parts, merged = settle_parts(
        [*parts_of(prior), part(summary)],
        provider=provider,
        model=model,
        summary_tokens=summary_tokens,
        output_limit=output_limit,
        usage_sink=usage_sink,
    )
    start = 1 if messages and messages[0].get("role") == "system" else 0
    state = CompactionState(
        boundary_index=boundary,
        summary_text=render_parts(parts),
        parts=parts,
        merged=merged,
        # Over everything cut so far, not the new span alone, the way a fold builds it: the
        # canonical history is all still here, and a list taken from one span forgot the files the
        # first cut wrote at the second.
        working_state=extract_working_state(messages[start:boundary]),
        pruned_upto=prior.pruned_upto if prior is not None else 0,
        user_messages=users,
        user_messages_dropped=dropped,
        created_at=time.time(),
        model_used=model,
        standing_plan=normalize_plan(standing_plan),
        approved_plan=clip_approved_plan(
            approved_plan if approved_plan is not None else (prior.approved_plan if prior is not None else "")
        ),
        plan_path=plan_path if plan_path is not None else (prior.plan_path if prior is not None else ""),
    )
    _assert_smaller(state, span, prior)
    return state


def _assert_smaller(state: CompactionState, span: list[dict[str, Any]], prior: Optional[CompactionState]) -> None:
    """A compaction that grows the context is not a compaction.

    Nothing bounds what a summarizer returns except a max_tokens the provider may ignore, and the
    block carries the verbatim user messages on top of it. Getting bigger here is the silent kind of
    wrong: the next check sees more pressure and compacts again, and the session spends round trips
    going backwards. Raising hands it to the caller's existing failure policy, which retries once and
    then leaves the view untouched and says so (the trim fallback is gone)."""
    from .block import growth

    produced, replaced = growth(state, span, prior)
    if produced >= replaced:
        raise RuntimeError(
            f"compaction would not shrink the context ({produced} tokens of block "
            f"replacing {replaced} tokens of history)"
        )
