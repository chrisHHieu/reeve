"""The system notes the engine adds to a turn, and when each fires.

Each is one note, said once per turn. A reminder that repeats is spam, and one that fires after the
agent has already acted is an insult.
"""

from __future__ import annotations

from typing import Optional

# The todo nudge: after this many executed tool calls in one turn with no `todo_write` seen, the
# next outbound prompt carries one system note.
#
# 10 and not 4 (2026-09-24, real store): at 4 the nudge fired 275 times and was acted on 28% of the
# time, and 72% of the ignored ones came in turns that ended within 3 more calls, so the model was
# right. Swept on the same turns, 10 removes 78% of those while keeping 88% of the nudges models
# acted on (those turns ran a median 17 calls); 12 loses twice as many.
TODO_NUDGE_AFTER = 10
TODO_NUDGE_TEXT = (
    "System note: this turn is running long and no task list exists yet. If several "
    "steps remain, maintain a plan with todo_write — send the full list each call, keep "
    "exactly one item in_progress, and mark items done as you finish them. Call it in the "
    "same step as your next working tool call, not in a step of its own."
)

# The verification gate: code changed this turn, nothing has run since the last change, and the model
# is about to call the work done. One note per turn; the second completion attempt goes through,
# because a gate that can loop is worse than one that can be ignored once.
VERIFY_NUDGE_TEXT = (
    "System note: you changed code files this turn, and nothing has been executed since "
    "the last change. Before finishing, run the narrowest relevant check — a test, a "
    "lint pass, or the command that exercises the change — and confirm its result. If "
    "there is genuinely nothing to run, say so explicitly in your answer."
)

# The red-check gate: the last verification that ran in this session failed, no file has
# changed since, and the model is about to finish. One note per turn, with the same one-shot rule as
# the gate above. Running the check and then ignoring its result is a failure of its own, distinct
# from never running one.
CHECK_FAILED_NUDGE_TEXT = (
    "System note: the last check that ran in this session FAILED, and no file has "
    "changed since. Its result was: {command} -> {summary}. Before finishing, fix the "
    "failure, or say explicitly what is still failing and why. Re-running the same "
    "check unchanged is not progress."
)

# The unfixed-design note: a design page's last check this turn still found something.
DESIGN_UNFIXED_NUDGE_TEXT = (
    "System note: the last check of {pages} still found something to fix, and the page has not "
    "been written since. Before finishing, fix what the check found, or say which findings stay "
    "and why."
)

# The check loop: the same failing check keeps running with no file change between runs.
# The repeat guard only sees byte-identical calls, so equivalent commands (`pytest -q`,
# `python -m pytest`, `pytest tests/`) walk past it. What shows the loop is the ledger's "a new
# failed check landed and nothing changed since". The note is model-visible (role user,
# `ATTACHED_BY: check_loop_nudge`), speaks once per turn, and at the stop threshold the turn ends on
# the same graceful checkpoint path every rail uses.
CHECK_LOOP_NOTICE_AFTER = 3
CHECK_LOOP_STOP_AFTER = 5
CHECK_LOOP_NOTICE_TEXT = (
    "System note: a check keeps FAILING with no file change since — {count} in a row. "
    "Its result was: {command} -> {summary}. You are repeating a check that cannot pass "
    "unchanged. Change the code, change the approach, or stop and say explicitly what is "
    "still failing and why; running it again without a change is not progress."
)

# The graceful cap: when the budget ends, the model gets one final tool-less call to write a handoff
# (what was done, what remains, the exact next step) and the turn does not die as an error. If that
# call itself fails, the old behaviour applies silently.
CHECKPOINT_PROMPT = (
    "System note: the step budget for this turn is exhausted. Do not attempt any "
    "further tool calls. Write a short handoff instead: what was completed this turn, "
    "what remains, and the exact next action to take — two or three sentences, then "
    "stop."
)

# The instruction asking for the rest of a message cut at the output-token limit. It is
# model-visible, so it is logged (`steering/message` with `source: "truncation-continue"`) and
# removed from history as soon as the continuation is folded in, so no synthetic instruction
# outlives the turn. It never reaches a provider alone: the fold happens before the next request is
# built, and the model sees the partial message it continues.
TRUNCATION_CONTINUE_TEXT = (
    "System note: your previous message was cut off by the output-token limit before it "
    "finished. Continue from exactly where it stopped — the same message, no repetition "
    "of what is already written, no preamble, no summary of what you are about to do. "
    "If there is a lot left, keep writing: this message may be extended again."
)

# The notes appended to a truncated tool result. The branch depends on whether the acting agent can
# delegate: one holding the task tool is told to hand a spilled report to a child, which is the cheap
# lever, and any other agent is told to search or read the file itself. `{where}` is the spill
# locator, or an honest note when the session has nowhere to write. The tool and parameter names are
# this registry's own, since this is the one message a model reads at the moment it has lost part of
# a result.
_HINT_DELEGATE = (
    "The tool call succeeded but the output was truncated: it is {full:,} characters in "
    "all. Full output saved to: {where}\n"
    "Use the task tool to have an explore agent process this file with grep and read_file "
    "(start_line/max_lines). Do NOT read the full file yourself - delegate to save context."
)
_HINT_READ = (
    "The tool call succeeded but the output was truncated: it is {full:,} characters in "
    "all. Full output saved to: {where}\n"
    "Use grep to search the full content or read_file with start_line/max_lines to view "
    "specific sections."
)


def truncation_hint(may_delegate: bool, where: Optional[str], full: int) -> str:
    """The sentence added to a truncated tool result.

    The delegate wording is for an agent whose ruleset leaves the task tool available and the
    search-and-read wording is for any other. The hint is part of the bounded text, so the step
    budget reserves its length inside the ceiling.

    `full` is the size of the complete output. A result cut by its tool and then again here carried
    two counts, and a model read the smaller one as the whole of what it was missing (session
    14715fae-61f, 2026-10-04: "10124 chars omitted" of a page it had been shown less than half of)."""
    hint = _HINT_DELEGATE if may_delegate else _HINT_READ
    return hint.format(where=where or "(the session has nowhere to save it)", full=full)
