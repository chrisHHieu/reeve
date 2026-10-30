"""The session event log — what HAPPENED, beside what was said.

`messages` is the model's view: an OpenAI-shaped list, rewritten by compaction, stripped of
sidecars on the way out. It is the right shape for talking to a provider and the wrong one
for answering "where did the four minutes go" — by the time a message is appended, the step
that produced it is over and the request that carried it is gone.

These questions are answered by keeping the conversation AS an append-only event log and
deriving the message list from it. reeve keeps the message list and appends this log
BESIDE it, which is the same information without trading a working execution path for a
diagnostic view: nothing here is ever sent to a model, nothing here can break a turn, and a
log that fails to write costs a row in a report rather than the work itself.

The vocabulary is narrowed to what this engine can actually witness (every row
also carries `trace` when the log is attached to a task chain — the chain's id, one
level above `sessionId`; see `SessionLog.trace`):

    turn/start      {turn}                      turn/end    {turn, reason, tokens,
                                                            steps, synthetic?, error?, error_at?}
                                     — `error` = the class of an `error` ending
                                       (`turn_error_class`, `hook_denied`, `crashed`);
                                       `error_at` = `<ExceptionType> at <file>:<function>`
                                       when this app's own code raised (`code`, `crashed`)
    revert/set      {turn, step, boundary, staged_at} | {turn, step, cleared: true}
                                     — the undo marker the next request is built under
                                       changed (`/undo`, `/redo`): the view stops
                                       at message `boundary`, or the marker is gone. Written
                                       before the first request it shapes
    step/start      {turn, step}                step/end    {turn, step}
    user/message    {content, source?}
    assistant/message {turn, step, text, usage?, reasoning?, timing,
                     partial?, checkpoint?, cache_miss?, context_cleared?}
    tool/call       {turn, step, call_id, name, arguments, parallel?, renamed?}
                                     — as it starts, or as it is answered unrun;
                                       `renamed`: names sent under another harness's (0202)
    tool/edited     {turn, step, call_id, name, original, arguments}
                                     — the person edited the call before running it
                                       (approval outcome "modify"): `original` is what the
                                       model asked for, `arguments` what actually ran. The
                                       edited call was re-checked by the engine as a NEW
                                       call (a floor still refuses it), and the
                                       dispatch-time `tool/call` above carries the edited
                                       arguments because that is what was dispatched.
    tool/result     {turn, step, call_id, content, is_error, timing,
                     attachment?, delegation?, tokens?, facts?} — every tool message:
                                     results, denials, interruptions, approvals. `status` = how the dispatch ended; `is_error` = the call did not do what was asked (refused, raised, or the tool's own result carries an `error`). `facts` = what the tool declared as data (exit code, how it ended, locator, sizes).
                                     `delegation` = {run_id, status?} when the
                                     result came back from a child run: the
                                     parent→child edge, read beside this call_id
    plugins/active  {plugins[{id, version, scope, components, skipped}], errors[], reason?} — once per engine build; `reason: "reload"` for the build `/reload-plugins` caused
    delegation/background {turn, step, run_id, tokens} — a background child's spend, charged to the turn that got its result
    steering/message {turn, step, text, source?, display?, waited_ms?} — `waited_ms`: how long the person's
                                     own message waited for its step to end
    hook/run        {event, name, source, plugin?, decision?, reason?, context?, error?, duration_ms}
                                     — one lifecycle hook ran (harness/loop/hooks.py). `source`
                                       is "global" (the user's config.toml) or "workspace"
                                       (`.reeve/hooks.toml`, which needs trust); a hook's
                                       returned context also enters the turn as a
                                       `steering/message` with source "hook:<event>", and
                                       its deny is answered like any other refusal.
    request/header  {reason, system, tools[], model}
    thinking/dropped {turn, step, model, stale?, stale_new?, api?, other_model?, retried?}
                                     — the step's request went without some of the model's own
                                       thinking blocks: `stale` ones the harness left
                                       out as the conversation before them changed (a compaction,
                                       a prune, a new tool list), `stale_new` those no earlier
                                       request went without, `api` and `other_model` ones the API
                                       dropped (a changed prefix it saw, another model's block),
                                       `retried` ones a 400 refused before it went without thinking
    todo/write      {turn, step, call_id, todos[]}
    design/check    {turn, step, call_id, name, path, facts} — a design page the call wrote was opened and measured; what the model read of it is in that call's `tool/result` content, the counts the harness review reads are in `facts`
    design/drift    {turn, step, call_id, name, pages, tokens} — the call's write moved theme tokens a design the person reviewed in the app uses; the sentence the model read is in that call's `tool/result` content
    todo/nudge      {turn, step, tool_calls} — the one nudge, before the next request (tools/todo.py)
    context/budget  {turn, step?, used, trigger, band, hint?, foldable?, moment?} — the turn-start context line (P7), per 10% band; `hint: fold` is a fold hint, `fold_off` the cache verdict (0102)
    verify/nudge    {turn, step, reason, count?}  — a one-shot gate note, at most
                                                    one per turn each. `reason`
                                                    "no_run": code changed and
                                                    nothing has run since;
                                                    "check_failed": the last
                                                    check this session ran FAILED
                                                    and no file changed since
                                                    (the verification ledger);
                                                    "check_loop": the
                                                    same check failed again and
                                                    again with no edit between,
                                                    `count` in a row;
                                                    "unread_sources": the answer
                                                    cited `count` snippet-only sources
                                                    (until 2026-10-10);
                                                    "design_unfixed": `count` design
                                                    pages whose last check this turn
                                                    still found something
    repair/fixed    {turn, step, marks, files, before, diffs, steps, recalled?, earlier_steps?, asked?}
                                     — a check stopped naming `marks` failures its last
                                       run named, after edits to `files` files, `steps`
                                       model steps later; written to the project's repairs
                                       store, with what `before` of those files
                                       looked like before the repair (from the undo
                                       store's before-images) and `diffs` of them with a
                                       change kept. `recalled` = this session had
                                       been told of an earlier repair of one of them,
                                       which took `earlier_steps`. `asked` = the repair
                                       cost enough that one model-visible note asked for
                                       its cause as a `lesson` memory
    repair/recalled {turn, step, marks, repairs, undone, diffs, changed}
                                     — a failing check named `marks` failures the store
                                       holds `repairs` earlier repairs of, and one
                                       model-visible note said where they were; `undone`
                                       = their files back as before the repair, `diffs` =
                                       the changes the note showed, `changed`
                                       = those that differ otherwise or are gone. Counts
                                       only: the names are in the check's own result
    turn/checkpoint {turn, step}                  — the graceful-cap handoff
                                                    prompt (engine _write_checkpoint)
    turn/resumed    {turn, attempt, path, steps_done, jobs_stopped, given_up?} — picked up after a restart (0183)
    turn/continuation {turn, step, attempt, added}  — a provider cut an answer at the
                                                    output-token limit and the engine
                                                    asked for the rest (the
                                                    output-token continuation, at most
                                                    TRUNCATION_CONTINUES times). The
                                                    instruction that requested it is a
                                                    `steering/message` with source
                                                    "truncation-continue"; `added` is
                                                    the fragment's character count.
    plan/update     {turn, step, call_id, path,   — the session's working plan file
                     chars, updated}                was written (engine
                                                    _handle_update_plan)
    mode/changed    {from, to, reason}             — the permission mode moved. One
                                                    live write path (engine.set_mode)
                                                    so every surface — websocket
                                                    set_mode, TUI /mode and /plan,
                                                    plan-approval adoption — logs the
                                                    same fact the requests ran under;
                                                    reason names the doorway. A
                                                    no-op switch writes nothing.
    plan/mode       {active, reason?}              — the plan OVERLAY was toggled. Plan
                                                    is not a mode: it hard-denies
                                                    consequential actions on top of any
                                                    preset. One live write path
                                                    (engine.set_plan), so /plan and an
                                                    approved plan being cleared log the
                                                    same fact; a no-op toggle writes
                                                    nothing.
    tool/code-dispatch-start {turn, step, root_call_id}
    tool/code-dispatch       {turn, step, root_call_id, call_id, name,
                              is_error?, content?, replayed?}
                                     — run_code's serialized sub-calls. `replayed` marks a
                                       workflow `agent()` answered from the journal by a
                                       `resume` run: no start event, no dispatch happened.
    workflow/note    {turn, step, root_call_id, kind, text}
                                     — a workflow script's narration (`phase`/`log`),
                                       recorded here so a replay can group the run's
                                       sub-calls by root_call_id and show its phases.
    workflow/run-start {turn, step, root_call_id, name}
    workflow/run-end   {turn, step, root_call_id, status, agents?, replayed?}
                                     — the run's bounds: written before the script body
                                       starts and after its result is known, so every
                                       run-start in a log is paired and a missing pair
                                       is interruption evidence, not corruption.
                                       `replayed` = the agent() calls this run answered
                                       from the journal (`resume=true`).
    tool/result/budget {turn, step, call_id, name, …}  — the per-turn output budget spent on this result (shrink/spill facts; `reused` when the file is the tool's own)
    llm/retry       {attempt, of, reason, delay, error, abandoned?, retry_after?,
                     rerouted?} — `abandoned` = the provider's Retry-After passed
                                        the wait ceiling, no attempt made; `rerouted`
                                        = retried on a new routing key
    llm/fallback    {from, to, reason, cooldown, error} — model failed; the turn continues on `to` (0049)
    invariant/violation {turn, step, violations[]}   (invariants.py — the
                         "model-visible means logged" audit)
    compaction/start {turn, step, trigger, before_tokens, model, keep_tokens, author?, unsettled?}  (`author: model` = a fold, 0102;
                                        `unsettled: unverified | check_failed` = folded mid-edit, 0107)
                                      — an attempt committed (cheap lever first,
                                        then summarizer); an unmatched start is
                                        a crash mid-attempt
                                        (`dangling_compactions`)
    compaction/summary {turn, step, reason, boundary, model, before_tokens, after_tokens, saved_tokens,
                        saved_pct, summary_chars, images_offloaded?, parts?, rewrites?, merged?,
                        parts_tokens?, parts_budget?} (0184)  — a compacted BLOCK entered the
                         outbound view (summarized / trimmed); the invariant's
                         evidence
    compaction/end  {turn, step, reason, error?, images_offloaded?, arguments_folded?} — what the
                         attempt did: summarized | pruned | trimmed | failed |
                         no_change | interrupted (synthetic repair). Older logs
                         also carry `prepared` and `compaction/prepare`;
                         `error` = the summarizer's exception text on `failed`,
                         so an outage and a harness bug stay distinguishable
    compaction/prune {turn, step, reason, before_tokens, after_tokens, saved_tokens,
                      pruned_upto, folded?, arguments_folded?, images_offloaded?} — a cache-cold pass
                         (`arguments_folded` = calls whose write payload was folded):
                         aged tool results folded while the provider's prefix was
                         already unusable (idle past its cache TTL, or a staged
                         revert), so no cache hit was broken. No summarizer ran.
                         `reason: rederivable` is the working trim: aged output
                         of re-runnable calls left the view, each replaced by a line
                         naming its call or the file it was saved to, in one batch
                         large enough to repay the cache it cost.
    notice          {kind, text, …}

Two rules hold everywhere:

**A step is one model call plus the tools it asked for.** That is the definition and the
unit every timing here is measured against; a turn is a person's request and the steps it
took to answer. A turn's number is its own for the life of the log (`turn_numbers.py`).

**An event is what was observed, never what was inferred.** Nothing computes a duration
here — the reader does that, and it can tell a missing stamp from a zero.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Optional

from .log_fields import MAX_FIELD_CHARS, _clip, field  # noqa: F401 - both re-exported for readers

logger = logging.getLogger("harness.session.session_log")

#: One session's log is capped so a runaway loop cannot fill a disk. The cap is on EVENTS,
#: not bytes, because the expensive shape is a million tiny rows rather than a few large
#: ones — and when it is hit the log stops growing rather than dropping its own beginning,
#: since a log missing its first turn explains nothing.
MAX_EVENTS = 50_000

#: Kept whole: a request must be rebuildable from its log (written per toolset).
_WHOLE = {("request/header", "system"), ("request/header", "tools")}


class SessionLog:
    """An append-only JSONL log for one session, beside its messages.

    Every write is best-effort: this is an observation stream, and losing one line must
    never cost a turn. Failures are logged once per session rather than per event, because
    a full disk would otherwise produce one log line per event about not being able to
    write log lines.
    """

    def __init__(self, path: str | Path, trace: str | None = None) -> None:
        self.path = Path(path)
        #: The task-chain id every row carries (the trace id, one level above
        #: `sessionId`): the id of the session that STARTED the chain, inherited by
        #: everything it spawns. None for a log that is not attached to a chain (tests,
        #: ad-hoc logs) — the field is then simply absent rather than a lie.
        self.trace = trace
        self._lock = threading.Lock()
        self._seq = 0
        self._broken = False
        self._counted = False

    @property
    def enabled(self) -> bool:
        """False for the null sink and for a log that failed to open. A consumer that
        AUDITS against this log (invariants.py) skips when disabled: reporting a
        coverage gap against a log that cannot be read would only report the log's
        own absence."""
        return not self._broken

    # -- writing ------------------------------------------------------------------

    def append(self, type: str, **data: Any) -> None:
        """Record one observation. Never raises."""
        if self._broken:
            return
        try:
            with self._lock:
                if not self._counted:
                    self._seq = self._count_existing()
                    self._counted = True
                if self._seq >= MAX_EVENTS:
                    if not self._broken:
                        self._broken = True
                        logger.warning(
                            "session log %s hit %d events; no longer recording",
                            self.path.name,
                            MAX_EVENTS,
                        )
                    return
                event = {
                    "seq": self._seq,
                    "t": round(time.time(), 3),
                    "type": type,
                    **{k: field(v, whole=(type, k) in _WHOLE)
                       for k, v in data.items() if v is not None},
                    # Stamped LAST so a caller cannot accidentally (or deliberately)
                    # misattribute a row: the log knows which chain it belongs to.
                    **({"trace": self.trace} if self.trace else {}),
                }
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event, default=str) + "\n")
                self._seq += 1
        except Exception:
            self._broken = True
            logger.warning("session log %s is not writable", self.path.name, exc_info=True)

    def dangling_turns(self) -> list[int]:
        """Turn numbers whose `turn/start` never got a `turn/end`, in order.

        A process death leaves exactly one open turn — the one running when it died.
        Legacy logs written before the engine closed every path may hold several. Turns
        pair sequentially (they never nest), so a walk with a stack finds them; the
        caller closes each synthetically (engine `_close_dangling_turn`), which repairs
        the boundary while leaving the turn's real events untouched.
        """
        open_turns: list[int] = []
        for event in self.read():
            kind = event.get("type")
            if kind == "turn/start":
                open_turns.append(event.get("turn"))
            elif kind == "turn/end" and open_turns:
                open_turns.pop()
        return open_turns

    def dangling_compactions(self) -> int:
        """How many compaction attempts were left open by a dead process.

        `compaction/start` is written the moment the engine commits to a summarizer
        attempt and `compaction/end` on every exit, so the pair is a transaction: a
        process that died between the two (or was killed while the summarizer was in
        flight) leaves an unmatched start behind. Nothing is corrupted when that
        happens — the canonical message list was never touched — so the repair is
        bookkeeping: close each one synthetically, the same way `dangling_turns`
        repairs a turn boundary, so the log holds no transaction that reads as still
        running.
        """
        open_attempts = 0
        for event in self.read():
            kind = event.get("type")
            if kind == "compaction/start":
                open_attempts += 1
            elif kind == "compaction/end" and open_attempts:
                open_attempts -= 1
        return open_attempts

    def _count_existing(self) -> int:
        # OSError is the missing/locked file; ValueError is the path itself being
        # unusable (an embedded null) and UnicodeDecodeError, its subclass, is a file
        # written by something else. None of the three is worth a raise from a counter.
        try:
            with open(self.path, encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except (OSError, ValueError):
            return 0

    # -- reading ------------------------------------------------------------------

    def read(
        self, *, limit: Optional[int] = None, after: Optional[int] = None
    ) -> list[dict[str, Any]]:
        """Every event, oldest first. A malformed line costs that line, not the log:
        the file is appended to while it is read, so a torn final write is ordinary.
        `after` skips events with `seq` at or below it — the incremental read the
        invariant audit uses to advance over only what it has not indexed yet."""
        return list(self._iter(limit=limit, after=after))

    def _iter(
        self, *, limit: Optional[int], after: Optional[int] = None
    ) -> Iterator[dict[str, Any]]:
        try:
            with open(self.path, encoding="utf-8") as f:
                for index, line in enumerate(f):
                    if limit is not None and index >= limit:
                        return
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if after is not None:
                        seq = event.get("seq")
                        if not isinstance(seq, int) or seq <= after:
                            continue
                    yield event
        except (OSError, ValueError):
            # The reader has the same contract as the writer: a log that cannot be read
            # is an empty report, never an exception thrown at whoever opened the page.
            return


class _NullLog(SessionLog):
    """Where no session owns the engine — a scripted test, a one-off script. Accepts every
    event and keeps none, so nothing has to check whether a log exists before recording."""

    def __init__(self) -> None:  # noqa: D107 - deliberately does not call super
        self.path = Path()
        self._broken = True

    def append(self, type: str, **data: Any) -> None:
        return

    def read(self, *, limit: Optional[int] = None) -> list[dict[str, Any]]:
        return []


NULL_LOG = _NullLog()
