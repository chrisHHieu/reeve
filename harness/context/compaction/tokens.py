"""Auto-compaction: trần ngân sách, redact, đo token, prune tool result."""
from __future__ import annotations

from __future__ import annotations
import json
import logging
import re
from typing import Any, Optional

from harness.llm.wire import wire_message

from .rederive import REREAD_TOOLS, call_names, rederivable, rederive_stub

logger = logging.getLogger("harness.context.compaction")

__all__ = [
    "logger",
    "DEFAULT_CONTEXT_WINDOW",
    "DEFAULT_BUFFER_TOKENS",
    "DEFAULT_KEEP_TOKENS",
    "SUMMARY_MAX_TOKENS",
    "_SPAN_TOOL_RESULT_CLIP",
    "_SPAN_BUDGET_CHARS",
    "_USER_MESSAGE_CLIP",
    "_USER_MESSAGES_MAX",
    "BLOCK_BUDGET_FRACTION",
    "_BLOCK_FIXED_CHARS",
    "_REDACTED",
    "_SECRET_PATTERNS",
    "redact_secrets",
    "PRUNE_RESULT_BUDGET",
    "FOLD_RESULT_BUDGET",
    "result_shortened",
    "SUMMARY_REASONING_HEADROOM",
    "summary_call_tokens",
    "_SPILL_LOCATOR_RE",
    "_spill_locator",
    "_fold_stub",
    "count_fold_results",
    "PRUNE_KEEP_FRACTION",
    "PRUNE_PROTECT_TOKENS",
    "prune_keep_tokens",
    "IMAGE_TOKEN_ESTIMATE",
    "PDF_TOKEN_ESTIMATE",
    "AUTO_CAP_KEEP_FRACTION",
    "AUTO_CAP_KEEP_CEILING",
    "AUTO_ACCURACY_CEILING",
    "capped_keep_tokens",
    "IMAGE_OFFLOAD_KEEP",
    "IMAGE_OFFLOAD_PLACEHOLDER",
    "estimate_tokens",
    "trigger_tokens",
    "should_compact",
    "block_limits",
    "prune_boundary",
    "prune_tool_results",
    "MIN_SAVINGS_PCT",
    "INEFFECTIVE_STREAK_LIMIT",
    "COMPACTION_COOLDOWN_SECONDS",
    "_OVERFLOW_MARKERS",
    "is_context_overflow",
]


logger = logging.getLogger(__name__)

# Trigger (not a percentage): compact when the window is within
# `max(output, buffer)` tokens of full. There is no threshold fraction and no absolute
# cap — a 1M-window model is used up to ~1M, not clipped at a constant.
#: Models without a verified context_window entry in the matrix.
DEFAULT_CONTEXT_WINDOW = 128_000
#: The safety margin kept for the next answer. The reserve
#: is `max(output_tokens, this)`: room for a large answer when the model has one, but never
#: less than the buffer.
DEFAULT_BUFFER_TOKENS = 20_000
#: The newest tokens kept verbatim across a compaction.
DEFAULT_KEEP_TOKENS = 8_000
# -- the working window -------------------------------------------------------
#
# A catalogue window is not a budget. `max_context_tokens` bounds the window the trigger
# is computed from: a number is obeyed exactly, and `"auto"` (the default) is the model's
# own window, never more than `AUTO_ACCURACY_CEILING`. That is the shape the other
# harnesses have: the model's window less a reserve, one number a person may
# lower, and — where the vendor has measured it — a working window below the advertised
# one, for quality. None of the other agent harnesses surveyed sets the window from a price.
#
# Until 2026-10-05 `"auto"` also took a cap from the model's cache-read/input price ratio
# (250k at 5-15%, 150k above). A ratio is not a cost: it held Gemini 3.8 Flash, whose
# 400k cached prompt is $0.03, to 250k, and an OpenRouter sheet that lists DeepSeek
# V4.1 Flash with equal input and cache-read rates ($0.003) put the cheapest model in the
# catalogue at 150k. A smaller window is more summarizer calls and more rework, bought
# with completion; what a session may spend is `token_budget`'s question.
#: When a cap binds, the verbatim keep scales with it — the fixed 8k default is sized for
#: uncapped 600k+ windows, and keeping 8k of a 50k cap made the model redo work
#: (measured: 2.15x cost on a 30-file read task). A quarter of the capped window, at most
#: 40k (the same absolute ceiling the prune protect-band uses).
AUTO_CAP_KEEP_FRACTION = 0.25
AUTO_CAP_KEEP_CEILING = 40_000
#: The ceiling of `"auto"`: no working window above this, whatever the model's own.
#: Measured 2026-09-24 on 4,700 real steps bucketed by
#: prompt size: the tool-error rate held at 3.2-5.4% up to 400k and doubled past it
#: (7.1%), led by edits written against files that had changed since the model read them.
#: Below 400k the data showed no degradation, so the ceiling sits there and not lower —
#: a lower one would buy summarizer calls and rework for no measured gain.
AUTO_ACCURACY_CEILING = 400_000


def capped_keep_tokens(current_keep: int, capped_window: int) -> int:
    """The verbatim keep for a capped window: the configured keep, or a quarter of the
    capped window, whichever is larger (the cap's own rework insurance)."""
    floor = min(int(capped_window * AUTO_CAP_KEEP_FRACTION), AUTO_CAP_KEEP_CEILING)
    return max(int(current_keep or 0), max(0, floor))


# -- image offload -----------------------------------------------------------
#
# An image rides EVERY request until it leaves the view: a screenshot is vision-priced
# (thousands of tokens) and a picture from forty turns ago is almost never what the model
# still needs — the summary's description of it is. At the first compaction/prune, images
# older than the newest `IMAGE_OFFLOAD_KEEP` are replaced by a placeholder. Forward-only:
# an index once offloaded stays offloaded, so the cached prefix changes once per image
# instead of drifting under it (the same rule pruning follows).
IMAGE_OFFLOAD_KEEP = 2
IMAGE_OFFLOAD_PLACEHOLDER = (
    "[image offloaded to keep the context small — what the summary says about it still "
    "stands; re-attach or re-read the source if you need to look again]"
)
# The summarizer call itself: tools off, and a real output budget. 3,000 was too small
# for reasoning models: the thinking burns the cap first and the reply comes back empty
# (measured on deepseek-v4.1-flash and glm-5.3-flash, study round 29). 8,192 is the
# default that the summarizer's sections need to fit.
SUMMARY_MAX_TOKENS = 8_192
#: Room a reasoning model spends THINKING before it writes, on top of the summary itself.
#: `block_limits` sizes the summary to the window it frees — 2,000 tokens on a 40k
#: trigger — and that target used to be the call's max_tokens as well, which re-created
#: the empty-summary failure above for every small window: deepseek-v4.1-flash spent the
#: cap on reasoning (`finish_reason=length`, 5.4k reasoning chars) and glm-5.3-flash came
#: back missing six of eight sections, even after the corrective re-ask (harness eval,
#: 2026-09-24). The TARGET is now told to the model; the cap only stops a runaway.
SUMMARY_REASONING_HEADROOM = 8_192


def summary_call_tokens(target: int) -> int:
    """The summarizer call's max_tokens for a summary of about `target` tokens."""
    return max(SUMMARY_MAX_TOKENS, int(target)) + SUMMARY_REASONING_HEADROOM
# Per-message clip when rendering the span for the summarizer; tool results are the
# first casualty (huge and mostly stale — a file read 40 turns ago is better re-read).
_SPAN_TOOL_RESULT_CLIP = 400
_SPAN_BUDGET_CHARS = 400_000
# User messages preserved mechanically in the compacted block ("trimmed of pasted bulk").
# The list is capped to the newest N across repeated compactions — otherwise it appends
# forever and the block slowly reclaims the window it freed. Dropped ones stay counted
# (their intent lives in the summary, which is asked to list user messages too).
_USER_MESSAGE_CLIP = 600
_USER_MESSAGES_MAX = 40
# The block replaces everything before the boundary, so it has to FIT the window it is
# freeing. These two numbers used to be fixed, which meant the block was the same ~10k
# tokens on a 1M model and on a 32k one — a fifth of the smaller window spent on
# bookkeeping, and on a small enough model larger than the trigger itself, so compaction
# could never get under it. `block_limits` scales both against the budget instead.
BLOCK_BUDGET_FRACTION = 0.15
_BLOCK_FIXED_CHARS = 1_200  # the framing text around the summary and the lists


# -- secret redaction ----------------------------------------------------------
# Moved to `harness/safety/redaction.py` (a leaf module) so the memory store can share the
# same table without a memory -> compaction -> tools import cycle. Re-exported here
# because existing callers import it from this module.
from harness.safety.redaction import _REDACTED, _SECRET_PATTERNS, redact_secrets  # noqa: F401



# -- tool-result pruning ------------------------------------------------------
#
# The cheap lever between "send everything" and "pay a summarizer". A tool result is
# bounded once, when it is produced (run_shell caps at 20_000 chars). It is never
# reconsidered — so forty shell calls occupy 200k tokens of window at full size, however
# stale, and the ONLY way out was an LLM summary of the whole span.
#
# Pruning happens on PRESSURE, never continuously, and the reason is prompt caching:
# providers match a prefix byte for byte, so rewriting a message the model has already
# seen invalidates the cache from that point on. Appending never does. Doing this on
# every request would trade a summarizer call for a cache miss every few steps; doing it
# at the moment compaction was going to fire is free, because compaction invalidates the
# prefix anyway.
#
# The newest results are left ALONE; only older ones are pruned. A result from the current step is what the agent is working with; a result
# from thirty steps ago is what it half-remembers.
PRUNE_RESULT_BUDGET = 5_000

#: Aged tool results larger than this, when they carry a spill locator, fold to a stub:
#: one identifying line plus the path, instead of head+tail in every outbound request.
#: The locator is the gate — a fold without a recovery path is a deletion wearing a
#: costume, and those stay under plain retention.
FOLD_RESULT_BUDGET = 2_000
_SPILL_LOCATOR_RE = re.compile(r"the complete output is at (.+?) ?…")


def _spill_locator(text: str) -> Optional[str]:
    """The path a bounded result names for its dropped middle, if it names one."""
    found = _SPILL_LOCATOR_RE.search(text)
    return found.group(1).strip() if found else None


def _fold_stub(content: str, locator: str) -> str:
    """What replaces a folded output: its first line for identification, then the size
    and the path. Announces itself — a result silently replaced is one the model will
    reason about as if it were whole."""
    first = content.split("\n", 1)[0].strip()
    if len(first) > 160:
        first = first[:159] + "…"
    lines = content.count("\n") + 1
    size = len(content.encode("utf-8", "replace"))
    return (
        f"{first}\n"
        f"[output folded to free context — {lines:,} lines, {size:,} B; "
        f"the complete output is at {locator} — read it back with read_file]"
    )


def result_shortened(content: Any, name: str = "", rederive: bool = False) -> bool:
    """Whether a prune pass shortens this result in the view (`prune_tool_results`): it is
    then no longer whole there, which the read guard has to know. `name` and
    `rederive` say whether the faithful tier applies to it."""
    if not isinstance(content, str):
        return False
    if rederive and rederivable(name, content):
        return True
    if len(content) > FOLD_RESULT_BUDGET and _spill_locator(content):
        return True
    return len(content) > PRUNE_RESULT_BUDGET


def count_fold_results(messages: list[dict[str, Any]], start: int, upto: int) -> int:
    """How many tool results in `messages[start:upto]` qualify for a fold — the number
    a prune pass just turned into stubs, for the human-facing notice."""
    n = 0
    for index in range(max(0, start), min(upto, len(messages))):
        message = messages[index]
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if (
            isinstance(content, str)
            and len(content) > FOLD_RESULT_BUDGET
            and _spill_locator(content)
        ):
            n += 1
    return n
PRUNE_KEEP_FRACTION = 0.10

#: Newest tool output a prune pass never touches, in tokens.
#: The fraction above scales with the window; this is the absolute floor that stops a
#: small window from folding the working set out from under the agent. A result from the
#: last few calls is not "aged": folding it would force a re-read of the very output the
#: agent is mid-way through using.
PRUNE_PROTECT_TOKENS = 40_000


def prune_keep_tokens(trigger: int) -> int:
    """How much recent history a prune pass must leave intact (the protect band)."""
    return max(
        int(PRUNE_KEEP_FRACTION * trigger),
        min(PRUNE_PROTECT_TOKENS, int(trigger)),
    )


# -- token math ---------------------------------------------------------------


#: What one inlined attachment counts as, in tokens, for the estimator. Attachments
#: arrive as data URLs (attachments.py, tools/files.py's `read_image`), and a data URL's
#: character count is a function of the FILE SIZE while what a provider bills is a
#: function of the PIXELS (or pages). Left in the char count, one screenshot is a
#: quarter-million "tokens" against a 128k window: the trigger fires on a context that
#: is mostly empty, every compaction re-fires on the same image, and the bias
#: calibration folds a ratio a thousand times too small. The numbers below are
#: deliberately coarse placeholders — the estimator is chars/4, not a tokenizer — chosen
#: high enough that an image is never free and low enough that it never impersonates a
#: megabyte of conversation. A document whose real price is higher still has the
#: provider's own overflow 400 and the forced compaction behind it.
IMAGE_TOKEN_ESTIMATE = 1_500
#: A PDF inlined for a model with native document support. Page pricing is unknowable
#: from bytes (Anthropic bills per page, Gemini per tile), so this says "a document is
#: expensive" without pretending to be exact.
PDF_TOKEN_ESTIMATE = 20_000
#: The most one audio or video part is priced at, however large its bytes: a coarse ceiling, like
#: the others, so one recording cannot impersonate a whole conversation.
MEDIA_TOKEN_CAP = 60_000


def _attachment_tokens(part: Any) -> int:
    """What one content part costs beyond its characters, or 0 for ordinary parts."""
    if not isinstance(part, dict):
        return 0
    kind = part.get("type")
    if kind == "image_url":
        holder = part.get("image_url")
        url = holder.get("url") if isinstance(holder, dict) else None
        if isinstance(url, str) and url.startswith("data:"):
            return IMAGE_TOKEN_ESTIMATE
    elif kind == "file":
        holder = part.get("file")
        data = holder.get("file_data") if isinstance(holder, dict) else None
        if isinstance(data, str) and data.startswith("data:"):
            if data.startswith(("data:audio/", "data:video/")):
                # Priced by duration, which bytes only approximate: Gemini reads audio at 32
                # tokens a second and video at about 100, and 128 kbps audio is ~16 KB a second.
                per_byte = 1 / 500 if data.startswith("data:audio/") else 1 / 10_000
                return min(MEDIA_TOKEN_CAP, max(1_000, int(len(data) * 0.75 * per_byte)))
            return PDF_TOKEN_ESTIMATE
    return 0


def _measurable(message: Any) -> tuple[Any, int]:
    """`(message with inlined payloads blanked, attachment token cost)`.

    Blanking is what keeps a data URL's base64 out of the character count; it is done on
    a copy, because `messages` is the canonical history and this function is a reader.
    """
    if not isinstance(message, dict):
        return message, 0
    content = message.get("content")
    if not isinstance(content, list):
        return message, 0
    extra = 0
    parts: list[Any] = []
    for part in content:
        cost = _attachment_tokens(part)
        if cost and isinstance(part, dict):
            extra += cost
            if part.get("type") == "image_url" and isinstance(part.get("image_url"), dict):
                part = {**part, "image_url": {**part["image_url"], "url": ""}}
            elif part.get("type") == "file" and isinstance(part.get("file"), dict):
                part = {**part, "file": {**part["file"], "file_data": ""}}
        parts.append(part)
    return {**message, "content": parts}, extra


def estimate_tokens(messages: list[dict[str, Any]], *, echo: Optional[str] = None) -> int:
    """chars/4 over the messages AS THEY ARE SENT, plus a fixed price per inlined
    attachment.

    Each message is put in wire form first (`harness/llm/wire.py`): a stored row also holds
    the model's thinking, the card its result is drawn with, timings, and a second copy
    of a stamped message, none of which a provider receives. Priced as stored, the kept
    tail of a compaction was a third of what was configured and one session's image
    counted as 370k tokens of prose. A notice is never sent and costs nothing. `echo`
    is the field a thinking-mode route wants the reasoning back under; the engine
    passes it, a pure caller prices the reasoning as dropped. Messages already in wire
    form price the same, so a caller need not know which it holds.

    `ensure_ascii=False` is load-bearing, not cosmetic. The default escapes every non-ASCII
    character to `\\uXXXX`, so a 33-character Vietnamese sentence measured as 80
    "characters" and a session written in Vietnamese estimated its window at roughly twice
    its real size. The engine's bias correction cannot absorb that reliably — it is ONE
    factor for the session, and a session mixing an English system prompt with a
    Vietnamese conversation fits neither — and before a provider has priced a request
    there is no factor at all. Measuring the text instead of its ASCII-escaped envelope
    is also what the real tokenizers and the UTF-8 wire form see.
    """
    chars = 0
    attachment_tokens = 0
    for msg in messages:
        msg = wire_message(msg, echo=echo)
        if msg is None:
            continue
        probe, extra = _measurable(msg)
        attachment_tokens += extra
        try:
            chars += len(json.dumps(probe, default=str, ensure_ascii=False))
        except (TypeError, ValueError):
            chars += len(str(probe))
    return chars // 4 + attachment_tokens


def trigger_tokens(
    context_window: Optional[int],
    *,
    output_tokens: int = 0,
    buffer_tokens: int = DEFAULT_BUFFER_TOKENS,
) -> int:
    """When the outbound view should be compacted.

    The rule: compact when the window is within `max(output, buffer)` of full.
    `output` is the model's answer budget (its `limit.output`, or the request's
    `max_tokens`), so a model with a large answer keeps proportionally more room; the buffer
    is the floor so even a model with no declared output never runs the window completely
    full. There is no threshold fraction and no absolute cap: a 1M-window model is used up
    to ~1M (V2 `packages/core`, `compactIfNeeded`; V1's `overflow.ts` prefers
    `min(buffer, output)` when the model declares a separate `limit.input` — the buffer is
    the safer shape when the catalogue gives only one number).

    The answer budget is capped at HALF the window. Some catalogues state an output limit
    equal to (or larger than) the context window — models.dev lists grok-4.6 as
    `output_length == context_length == 500000` — and `window - output` then lands on 1,
    which made `_compaction_due()` true on every step for that model. A reserve larger than
    half the window can never be a real answer budget; clamping keeps the trigger
    meaningful for any metadata a catalogue publishes, without changing the numbers for
    every model whose output fits inside its window.
    """
    window = max(int(context_window or 0), 0) or DEFAULT_CONTEXT_WINDOW
    buffer = max(int(buffer_tokens or 0), 0)
    output = max(int(output_tokens or 0), 0)
    reserve = max(buffer, min(output, window // 2))
    return max(1, window - reserve)


def should_compact(
    signal: int,
    context_window: Optional[int],
    *,
    output_tokens: int = 0,
    buffer_tokens: int = DEFAULT_BUFFER_TOKENS,
) -> bool:
    return signal >= trigger_tokens(
        context_window,
        output_tokens=output_tokens,
        buffer_tokens=buffer_tokens,
    )


def block_limits(trigger: int) -> tuple[int, int]:
    """`(summary max_tokens, user-message count)` that keep the compacted block inside its
    share of the window it is freeing.

    Both were constants, and a constant does not shrink when the window does. The floors
    are deliberate: a block with no summary and no user messages is not a smaller block,
    it is a compaction that lost the two things worth keeping.
    """
    budget_tokens = max(1, int(trigger * BLOCK_BUDGET_FRACTION))
    summary_tokens = max(500, min(SUMMARY_MAX_TOKENS, budget_tokens // 3))
    room_chars = (budget_tokens - summary_tokens) * 4 - _BLOCK_FIXED_CHARS
    users = max(5, min(_USER_MESSAGES_MAX, room_chars // (_USER_MESSAGE_CLIP + 4)))
    return summary_tokens, users


# -- tool-result pruning ------------------------------------------------------


def prune_boundary(messages: list[dict[str, Any]], *, keep_tokens: int) -> int:
    """The canonical index before which tool results have aged out of active use.

    Walks back from the newest message until `keep_tokens` is accounted for, so the slice
    the agent is actually working in is never touched. Returns 0 when everything is recent
    — there is nothing aged to prune, and saying so costs nothing.
    """
    total = 0
    for index in range(len(messages) - 1, -1, -1):
        total += estimate_tokens([messages[index]])
        if total >= keep_tokens:
            return index
    return 0


def prune_tool_results(
    messages: list[dict[str, Any]],
    upto: int,
    *,
    budget: int = PRUNE_RESULT_BUDGET,
    spill: Optional[Any] = None,
    rederive: bool = False,
) -> list[dict[str, Any]]:
    """An OUTBOUND copy with aged tool results reduced to their two ends.

    Same shape as every other tool bound in this codebase (`retain`): keep the head and
    the tail, and name the gap in the text the model reads, because a result that was
    silently shortened is one the model will reason about as if it were whole.

    Never mutates the input, and never changes its LENGTH — `boundary_index` is an index
    into this list, so a projection that dropped messages would move the boundary under
    everything that cites it.

    `spill` is a one-argument sink (`tools/spill.py`'s `SpillStore.sink`) for results that
    have no locator yet: without one, the middle of a large locator-less result was cut
    with no reading path anywhere (the model view lost it and `search_history` excludes
    tool roles). With one, the retained text names the file holding the whole output. The
    sink is optional so pure callers (measurement, tests) stay side-effect free; the
    engine passes a content-addressed sink that writes each text once.

    `rederive` adds the faithful tier first: an aged result of a call that can be made again
    (`REREAD_TOOLS`) is replaced by a line naming the call, and one that cannot (`SAVED_TOOLS`)
    by its first line and the file the sink saved it to. Nothing kept is rewritten, so a cut
    never says something the session did not see (a summary can: Slipstream, arXiv 2605.08580).
    """
    if upto <= 0:
        return messages
    from harness.tools.retention import retain

    out = list(messages)
    calls = call_names(out, upto) if rederive else {}
    for index in range(min(upto, len(out))):
        message = out[index]
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        name, arguments = calls.get(index, ("", {}))
        if rederive and rederivable(name, content):
            if name in REREAD_TOOLS:
                out[index] = {**message, "content": rederive_stub(name, arguments, content)}
                continue
            located = _spill_locator(content) or (spill(content) if spill is not None else None)
            if located:
                out[index] = {**message, "content": _fold_stub(content, located)}
                continue
        # The fold tier comes first: with a recovery path on file, even a
        # previously-untouched result shrinks to its stub rather than keeping its
        # head and tail in every request from here on.
        if len(content) > FOLD_RESULT_BUDGET:
            locator = _spill_locator(content)
            if locator:
                out[index] = {**message, "content": _fold_stub(content, locator)}
                continue
        if len(content) <= budget:
            continue
        out[index] = {
            **message,
            "content": retain(content, budget=budget, spill=spill).text,
        }
    return out


# -- state --------------------------------------------------------------------


# -- anti-thrash -----------------------------------------------------------------
#
# Compaction is the one expensive thing the loop does on its own initiative, and it had no
# memory of its own attempts: `_compaction_due()` is a pure reading of pressure, so a
# session that compaction could not shrink re-attempted on EVERY iteration — a failed
# summarizer call per step, which is how a bad context problem becomes a bill. The two
# numbers below are the memory: a cooldown that applies after any attempt whose result was
# not worth having, and a savings floor below which a successful compaction counts as one.

#: Below this fraction of the pre-compaction view, a compaction that "succeeded" did not
#: buy enough to be worth the round-trip it cost, and counts toward the streak.
MIN_SAVINGS_PCT = 0.10

#: Consecutive unproductive attempts before the policy stops trying for a while. Two,
#: because one unproductive pass can be a genuinely awkward span; two in a row means the
#: tool is not the problem.
INEFFECTIVE_STREAK_LIMIT = 2

#: How long to leave compaction alone after it has been unproductive or has failed
#: outright. Long enough to stop paying per-iteration, short enough that a session which
#: grew (or whose summarizer recovered) gets another chance without a restart.
COMPACTION_COOLDOWN_SECONDS = 120.0

#: Vendor phrasings for "the prompt did not fit", taken verbatim from the error bodies.
#: Each line is a provider whose refusal would otherwise surface as an opaque 400 the
#: turn dies on, when the recovery path (prune → summarize → retry) is right there:
#:   OpenAI       "This model's maximum context length is X tokens. … Please reduce the
#:                length of the messages."
#:   Anthropic    "prompt is too long: X tokens > Y maximum"
#:   Gemini       "The input token count (X) exceeds the maximum number of tokens allowed"
#:   Bedrock/AWS  "Input is too long for requested model"
#:   xAI          "maximum prompt length is X but the request contains Y"
#:   Mistral      "Prompt contains X tokens, but the maximum context length is Y"
#:   compat hosts "context_length_exceeded" / "context window" / "too many tokens"
#: False positives are cheap here (a forced compaction on a permanent error costs one
#: summarizer round-trip and the error still surfaces); false negatives are a dead turn.
_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "maximum context length",
    "context window",
    "prompt is too long",
    "input is too long",
    "too many tokens",
    "input length and `max_tokens` exceed",
    "exceeds the maximum number of tokens",
    "reduce the length of the messages",
    "input token count",
    "maximum prompt length",
    "exceeded model token limit",
)


def is_context_overflow(exc: BaseException) -> bool:
    """A raw context-overflow 400 from the main model (compaction mispredicted, e.g. the
    estimate path) — routed into the compaction policy instead of surfacing."""
    text = str(exc).lower()
    return any(marker in text for marker in _OVERFLOW_MARKERS)
