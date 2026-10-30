"""Harness self-review: known failure shapes, counted from the session event logs.

Every P0 of the 2026-09-25 audit was found by hand in these logs; this runs the same
questions every time (the audit handoff's W14, grown from its `log_detectors.py`). Each
detector prints a count, the part of the harness that owns the shape, and a few
`(session log, seq)` references — never content, since the logs hold user data.

    masked-checks     a check whose shell status read 0 while its runner's summary said
                      it failed — what the pipe/sequence trailer (W1) must keep
                      at zero; the detail line also counts the earlier-statement failures
                      the shell now reports as the line's exit
    piped-checks      checks run through a pipe (context for the line above)
    unnamed-failures  failed checks whose output names no failure the repair record can use, split
                      by whether the command's own pipe cut it
    edit-misses       replace_in_file / apply_patch that matched nothing, and how many were
                      retried by the very next call (W9's refusals name the place to look)
    gate-blocks       read-before-edit / partial-read / stale-file / out-of-view refusals, by tool (W12)
    gate-block-rereads  a read_file of a file the read guard refused in the same turn: the read the
                      refusal itself can stand for when it shows the file
    deferred-calls    direct calls refused for being behind tool discovery (W5)
    lsp-timeouts      lsp calls that timed out or were skipped after one (W3)
    raw-json-errors   tool results that reached the model as a raw JSON error object
    unverified-ends   turns that ended `completed_unverified` (code changed, nothing ran)
    cache-drops       a step's cache read falling below half of the previous step's, split
                      by what preceded it (compaction or a prune / toolset change / an undo or redo /
                      another model than the step before / idle past the route's cache
                      lifetime / unexplained). Where the provider read the request (Anthropic's
                      cache diagnostics), an unexplained drop is `api <reason>` or
                      `provider dropped` (the request matched the previous one)
    unmarked-prefix-changes  a cache drop the harness logged no cause for, whose request the
                      provider says changed (`messages_changed`, `system_changed`,
                      `tools_changed`, `model_changed`): a prefix this harness rewrote without
                      knowing it did, per `steps`

Added 2026-10-04 from the harness-optimisation papers (docs/harness-papers-2026-10-04.md).
"The Scaffold Effect" (arXiv 2607.22585) measures a harness by its NO-ACTION turns — each
one re-reads the whole context to do nothing — and Co-Harness (2607.22688) / AHE
(2604.25850) attribute every failure to one harness component:

    bookkeeping-steps a step whose only call is `todo_write`: this harness's no-action
                      step. The detail splits it by where it sat (after a nudge / closing
                      the list before the answer / opening the turn / on its own) and
                      gives the prompt tokens those steps re-read
    nudge-alone       a `todo/nudge` answered by a bookkeeping step instead of by work
    nudge-no-tool     a `todo/nudge` in a session that was not offered `todo_write`
    idle-wakes        a turn a background completion opened that made no tool call
    unasked-background a `task` call that came back "running" without asking to
    slot-refusals     a `task` call that ended on the session's subagent cap: refused, or failed
                      after waiting for a slot that never freed. A call past the cap is queued
                      (fact `outcome` "cap"; text before facts)
    invalid-arguments a call refused for its arguments, by `tool:parameter` in the detail
    goal-set          `goal` calls with action `set` (the model arms a goal)
    turn-reuse        a `turn/start` whose number the log had already used
    locator-reuse     a spill path a second tool result was told is ITS full output
    lost-exit-status  a run_shell result whose status the harness does not know: the wait
                      ended without a trailer and the process gave no exit code
    cwd-drift         a run_shell command that failed on a path or a name while the shell sat
                      away from where the session's commands started, after an earlier cd
                      (`tools/shell/cwd_drift.py`); a share of shell results
    cwd-drift-repeats a cwd-drift failure in a later step of the turn that already had one: the
                      model had the first one's note and ran from the wrong folder again; a
                      share of cwd-drift
    reader-cut-pages  a web_fetch answered by a reader model that did not read the whole
                      page
    steer-waits       a message the person typed into a running turn that a shell command, a
                      job read or a foreground subagent of its step held 10 s or more after it
                      was sent (`waited_ms` on `steering/message`, from 2026-10-24); a share of
                      person-steers

Those three are read from the result's `facts` (what a tool declares about its
result, as data on the `tool/result` row). A row written before facts existed is read the
old way, from the sentence the model was shown — the event log's content for the shell,
the transcript beside the log for the other two, which sit past the log's 4,000-character
clip. Sentences were the only reading until 2026-10-04 and it counted wrongly both ways
within an hour of going live: four correct `exit N` commands as lost statuses, and a `wc`
the model ran on a spill file as a second owner of that file.
    page-middle-dropped  a `web_fetch` that returned a long page as its head and its tail,
                      the middle left out and no call named that reads it. Since pages are held a
                      page is returned in order from a position (`start`), so a result
                      with facts counts only when it was cut and carries no position
    surface-shrinks   a request header that no longer lists a tool the header before it in
                      the same session listed. The tool list leads every request, so each
                      one is a full-price read of the conversation, and the tool usually
                      comes back a turn later for a second one. The detail says
                      whether what left was MCP tools, built-ins, or both
    cold-prunes       aged tool results folded while the cache was proven cold (a model
                      switch, a changed tool list, idle past a declared TTL, a staged undo):
                      the free pass. Zero before 2026-10-06, because the only
                      proofs were ones no provider in use could give. The working trim
                      (`reason: rederivable`) is not one: it is counted in the
                      detail as `working-trims` and `tokens trimmed`
    unknown-tool-programs  a `run_code` program that named a tool the session does not
                      hold. It is refused before it starts (detail "before it
                      ran"); one that still failed at the line that made the call, after
                      whatever it did first, reads "at the call". Per `programs`. The same
                      ADR took the list of tool names out of `run_code`'s description, so
                      `tool-list chars` per `tool-lists` is the size it was decided on (and
                      `system chars` per `tool-lists` the system prompt's)
    poll-steps        a step whose only calls are `shell_task_output`, right after another such
                      step in the same turn: asking a background task for news a step at a
                      time, each step re-reading the conversation. `wait` makes it one call
    error-ends        a turn that ended on an error, by its class (`turn/end {error}`): a
                      missing key, an exhausted plan, a request the provider refused as
                      malformed (`bad_request`, the one this harness builds), a bug in this
                      app's code (`code`), a crash. Rows from before 2026-10-06 read
                      "unclassified"
    unnamed-error-ends  an error ending the log cannot name: no class, class `unknown`, or
                      a `code` / `crashed` ending with no `error_at` to say where this app
                      raised. Each is a failure nobody can count or locate
    thinking-drops    a step whose request went without some of the model's own thinking
                      (`thinking/dropped`), per `steps`; the detail is why: `stale`
                      (the harness changed the conversation before the block: a compaction,
                      a prune, a new tool list), `api` (the API saw a change the harness did
                      not), `retried` (a 400 refused the blocks). `other_model` alone is a
                      model switch and is not counted. Each one is reasoning the model wrote
                      and no longer has. A stale block stays out of every request after, so
                      a step counts only for blocks no earlier request went without
                      (`stale_new`; before 2026-10-27, by how far `stale` rose in the log).
                      The re-sends are the detail `thinking blocks re-dropped stale`: the
                      store's first 314 such steps were one session's 3-13 blocks, once a step
    argument-folds    a prune that folded the write payloads of aged calls; the
                      detail is how many calls. Its cost, if it has one, shows as `edit-misses`
                      (an edit quoting a payload the view no longer holds) and `gate-blocks`
                      (a whole-file write refused until the file is read again)
    page-refetch      a `web_fetch` that DOWNLOADED a page the same session had downloaded
                      in the fifteen minutes before, without asking for a fresh copy: the
                      page is read from the session's own copy. The detail
                      line also gives how many of the readers' quotes were not in the page
    fold-unpaid       a fold the model made that the steps after it did not repay.
                      With A the prompt of the step that folded, B the prompt of the next
                      one and n the steps that ran on the folded context, it spared
                      (A - B) x n tokens and spent the note's output, plus A when the step
                      did nothing but fold. Provider counts only, no prices: what it spares
                      is cache reads, so in money a fold repays more slowly than this says
    fold-hint-nothing-open  a fold hint given for a finished to-do item when no item was
                      left TO START (an item in progress is work under way, often the report
                      itself): what follows is the answer, and a fold cannot be repaid
    fold-own-step     a fold whose step did nothing else: a request at the full size, and a
                      wait, before any of the work asked for (the fold rides the
                      step that starts the next piece of work)
    fold-hint-small   a fold hint given when what could be folded was under half of the
                      request: such a fold cannot halve it, and is not worth a rewritten prompt
    notes-over-budget a fold that left the session's notes above 16,000 characters (older rows:
                      the parts' own budget replaced that one)
    parts-over-budget a cut whose block's parts stayed above their budget: a merge failed.
                      `merges of parts` in the detail line is how often one ran
    parts-repriced    a cut that added a part beside earlier ones and merged none, made on a
                      warm cache, whose next request read from cache less than the route's
                      floor (what the request after the session's first cut read: the system
                      prompt and the tools) plus half the earlier parts: they were paid again
                      in full, per `cuts keeping parts`
    cut-details-lost  concrete details (backticked names, flags, paths, long numbers,
                      snake_case names) a block's memory held that the next cut's block no
                      longer holds, per `cut details`; from the transcript's `compacted`
                      notices (a cut adds a part and no longer rewrites the last)
    memory-refused    a write into a memory directory the gate did not admit, by rule
    progress-refused  of those, the progress log's: over its size cap or an unproven done mark
                      (no kind, no anchor in the project, no quote of the person, …).
                      Read with `memory writes saved` (in the detail line): what was tried, and what stood;
                      and with `memory notes read`: whether what was saved is read again
    fold-unsettled    a fold made while the turn held an edit the harness could not call
                      verified: code changed and nothing ran since, or the turn's last check
                      failed (the engine's own state, on `compaction/start`)
    price-off         a `compaction/start` whose own price of the view (`before_tokens`)
                      is more than a quarter away from the provider-based signal on the
                      same row: the two describe one context
    estimate-drift    a prune or compaction whose price of the view (`before_tokens`) is past 1.5x
                      the request the session last sent (the provider's count plus what it said
                      it cleared first): the engine is pricing text the model does not read, a
                      gap `price-off` cannot see once the signal is the estimate
    usage-distrust    a `usage_partial` notice: the engine stopped believing a route's counts
    workflow-refused  a `workflow` call turned away before it ran: an import, a dunder, a
                      blocked call, the meta block (facts `outcome = refused`; a row from
                      before facts by its sentence). Per `workflow-runs`: a run
                      counts once, by the row that says how it ended — a background run's
                      call answers `outcome = background`, and its runner records the end
    unread-citations  an answer that ENDED its turn citing a source this session saw only as
                      a `web_search` snippet and never opened, per `searched-citing answers`
                      (answers that cite a searched source at all). The detail says whether the
                      source gate's note came first. Read from the transcript: the
                      answer is the object counted, and which URLs a search listed and a call
                      named is what the harness itself witnessed
    question-writes   a turn the person opened with a question (how, why, whether, …, and no
                      word asking for work) in which a file was written outside the memory
                      folder, per `question turns`. A question gets an answer; a change is
                      offered, not made (2026-10-19). `requests-without-writes` per `request
                      turns` is the other side: a turn asking for work that wrote nothing.
                      Read from the person's own words, so it is a rough reading by design
    design-unfixed    a design page (`design/*.html`) whose last check in a turn still found
                      something to fix, per `design pages` (pages written in a turn). The
                      check runs after every write of a page and its findings join the
                      write's result; `design first drafts with issues` and `…off a
                      phone` say how often the first write needed the loop at all
    design-unguided   a design page whose first write in a turn came before design_guide was
                      read in the conversation, per `design pages`: drawn without a direction,
                      a recipe or the starting file (the write's result says so)
    compare-unfixed   a design page whose last `compare_design` in a turn still found the app
                      differing from it, per `design compares` (pages compared in a turn): the
                      app was built from a locked design and left unlike it
    design-drift      a write that moved theme tokens a design the person reviewed in the app
                      uses (`design/drift` rows), as a count: each is a page that changed after
                      its review, said in the write's result
    split-search      a session that searched the web through BOTH `web_search` and a
                      connector's own search tool (`mcp__tavily__tavily_search`, …), per
                      `searching sessions`: two kinds of result for one need, and only
                      `web_search`'s are carded, filtered and counted
    grep-capped       a `grep` page that stopped short of every result (facts `capped`), per
                      `grep results`: each is a search the model either paged (`offset`) or
                      narrowed, and a rising share says the default page is too small
    shell-searches    a `run_shell` command that is itself a file search (`rg`, `grep`,
                      `findstr`, `Select-String`, `git grep`), per `grep calls`: work the `grep`
                      tool exists for, done without its bound, spill and facts. 192 of 238 of them
                      (2026-09-28..10-12) searched a folder outside the session
    visual-unplaced   a turn that showed a visual (`show_visual`) and whose closing
                      answer placed none of them with `:visual[name]`: the GUI then draws the
                      last one after the opening paragraph, where the answer did not choose
    visual-drafts     a visual shown in a turn whose answer placed others and not it: a try
                      the person sees only folded on its step row. Per `visuals shown`
    visual-restated   a turn that showed a visual and whose closing answer also holds a
                      Markdown table: the same figures said twice (the owner's report,
                      2026-10-09: 8 of 10 such answers in the probes before the fix). Per
                      `visual turns`
    visual-refused    a `show_visual` call the catalog turned away (facts `shown = false`):
                      the reason went back to the model and nothing was drawn

In the detail line, not a failure shape: `repairs recorded` (a check stopped naming
a failure after an edit), `repairs recalled` (a failing check named one this project repaired
before, and the session was told where), `repairs made after a recall` with the steps they took
and the steps the same failures took the first time, `repairs asked for a lesson` (a repair of
eight steps or more, where the session was asked for its cause) and `memory writes after a
lesson was asked for` (a memory saved later in that same turn).

    .venv\\Scripts\\python.exe scripts\\harness_review.py [<state_or_conversations_dir> ...]
        [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--refs <detector>] [--json]

`--refs <detector>` prints every reference for one detector instead of three: the drill-down
from the count to the rows (AHE's layered evidence: overview, then per-case, then the raw
trace, which is the log itself).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness.session.turn_numbers import monotonic_turns  # noqa: E402

from harness.loop.sources import SourceLedger  # noqa: E402
from harness.tools.shell.cwd_drift import counts_as_drift  # noqa: E402
from harness.tools.checks import cut_by_command, failure_marks, is_check_command as is_verification_command, reports_failure  # noqa: E402


#: Each detector and the part of the harness that owns its shape — the place a fix goes.
#: AHE's finding is that a failure pattern should map to ONE component, so an edit and its
#: effect can be told apart; "prompt" is deliberately absent (no shape here is fixed by prose).
OWNER = {
    "masked-checks": "tools/shell_status",
    "piped-checks": "tools/shell_status",
    "unnamed-failures": "tools/checks",
    "edit-misses": "tools/edit surface",
    "gate-blocks": "tools/read guards",
    "gate-block-rereads": "tools/read guards",
    "deferred-calls": "tools/discovery",
    "lsp-timeouts": "lsp",
    "raw-json-errors": "tools/presenters",
    "unverified-ends": "engine/verify gate",
    "cache-drops": "context/projection",
    "unmarked-prefix-changes": "context/projection + llm/wire (what the provider says changed)",
    "bookkeeping-steps": "tools/todo + engine/nudge",
    "nudge-alone": "engine/nudge",
    "nudge-no-tool": "engine/nudge",
    "idle-wakes": "manager/background delivery",
    "unasked-background": "tools/task",
    "slot-refusals": "tools/task",
    "invalid-arguments": "tools/schemas",
    "goal-set": "goals",
    "turn-reuse": "engine/session log",
    "locator-reuse": "tools/spill",
    "lost-exit-status": "tools/shell",
    "cwd-drift": "tools/shell",
    "cwd-drift-repeats": "tools/shell",
    "reader-cut-pages": "web/ask",
    "steer-waits": "engine/steering",
    "price-off": "engine/context price",
    "estimate-drift": "engine/context price",
    "usage-distrust": "engine/context price",
    "page-middle-dropped": "web/pages",
    "page-refetch": "web/pages",
    "fold-unpaid": "engine/context fold",
    "fold-hint-nothing-open": "engine/context fold",
    "fold-own-step": "engine/context fold",
    "fold-hint-small": "engine/context fold",
    "notes-over-budget": "compaction/fold",
    "parts-over-budget": "compaction/parts",
    "parts-repriced": "compaction/parts + providers/claude cache marks",
    "cut-details-lost": "compaction/parts",
    "fold-unsettled": "engine/context fold",
    "memory-refused": "memory/gate",
    "progress-refused": "memory/gate",
    "workflow-refused": "tools/workflow",
    "surface-shrinks": "manager/mcp + tools/discovery",
    "cold-prunes": "engine/compaction",
    "unknown-tool-programs": "tools/code_names",
    "argument-folds": "compaction/arguments",
    "error-ends": "engine/turn end",
    "unnamed-error-ends": "providers/turn_errors",
    "thinking-drops": "providers/claude binding + engine/compaction",
    "poll-steps": "tools/shell background",
    "unread-citations": "loop/sources",
    "split-search": "web/tool + manager/mcp_plan",
    "grep-capped": "tools/search",
    "shell-searches": "tools/search + permissions/reach",
    "visual-unplaced": "tools/visual",
    "visual-drafts": "tools/visual",
    "visual-restated": "tools/visual",
    "visual-refused": "tools/visual",
    "lost-turns": "manager/continuity",
    "question-writes": "modes/agents (the agent's instructions)",
    "design-unfixed": "tools/design",
    "design-unguided": "modes/agents (the agent's instructions) + tools/design",
    "compare-unfixed": "tools/design (compare_design)",
    "design-drift": "tools/design (drift)",
}
DETECTORS = tuple(OWNER)
#: A person's message that waited this long for its step is one the step held (`steer-waits`).
STEER_WAIT_MS = 10_000
#: What Anthropic's cache diagnostics call a changed request; `unchanged` is the harness's
#: word for a request the provider found matching the previous one.
API_CHANGED = frozenset({"model_changed", "system_changed", "tools_changed", "messages_changed"})
#: The waits a person's message releases (`TurnEngine.person_steered`): what can hold one that long.
STEER_HOLDERS = frozenset({"run_shell", "shell_task_output", "task"})
#: What a detector's count is a share of, where the docstring above names one: the Metrics page
#: draws each shape as that share (`harness/observability/self_review.py`). A detector missing here
#: is shown as a count.
PER = {
    "cwd-drift": "shell-results",
    "steer-waits": "person-steers",
    "cwd-drift-repeats": "cwd-drift",
    "masked-checks": "checks",
    "unnamed-failures": "failed-checks",
    "unverified-ends": "turn-ends",
    "cache-drops": "steps",
    "unmarked-prefix-changes": "steps",
    "bookkeeping-steps": "steps",
    "nudge-alone": "todo-nudges",
    "idle-wakes": "wake-turns",
    "unasked-background": "task-calls",
    "slot-refusals": "task-calls",
    "invalid-arguments": "steps",
    "turn-reuse": "turn-starts",
    "locator-reuse": "spill-locators",
    "lost-exit-status": "shell-results",
    "reader-cut-pages": "reader-pages",
    "page-middle-dropped": "page-fetches",
    "fold-unpaid": "folds",
    "parts-repriced": "cuts keeping parts",
    "fold-own-step": "folds",
    "fold-unsettled": "folds",
    "fold-hint-small": "fold-hints",
    "fold-hint-nothing-open": "fold-item-hints",
    "price-off": "compaction-starts",
    "estimate-drift": "priced cuts",
    "usage-distrust": "steps",
    "workflow-refused": "workflow-runs",
    "unknown-tool-programs": "programs",
    "error-ends": "turn-ends",
    "thinking-drops": "steps",
    "poll-steps": "steps",
    "unread-citations": "searched-citing answers",
    "split-search": "searching sessions",
    "grep-capped": "grep results",
    "shell-searches": "grep calls",
    "visual-unplaced": "visual turns",
    "visual-drafts": "visuals shown",
    "visual-restated": "visual turns",
    "lost-turns": "turn-starts",
    "cut-details-lost": "cut details",
    "question-writes": "question turns",
    "design-unfixed": "design pages",
    "design-unguided": "design pages",
    "compare-unfixed": "design compares",
}
#: A gap longer than a provider's cache lifetime explains a cold cache on its own.
IDLE_S = 300
#: Routes whose cache lives shorter than `IDLE_S`, by a substring of the model id. Measured on the
#: store 2026-09-25..10-09: Gemini on Vertex (implicit caching) kept 93-97% of steps mostly cached
#: under a minute apart, 72% at 1-2 minutes, 18% at 2-5 minutes and none past 5, while DeepSeek kept
#: 96% up to 5 minutes. At 300 s every Gemini drop at 2-5 minutes counted as unexplained.
IDLE_S_BY_ROUTE = {"gemini": 120}


def _idle_s(model: Any) -> int:
    """How long a gap the route's cache is expected to outlive."""
    name = str(model or "").lower()
    return next((s for key, s in IDLE_S_BY_ROUTE.items() if key in name), IDLE_S)


_WIRES: dict[Path, dict[str, str]] = {}


def _gateway_wire(log: Path, model: Any) -> str:
    """The wire an OpenCode Go model is served over (`chat`, `messages`, `responses`, `gemini`), from
    the catalogue the store keeps beside its logs (`model-facts.json`); "" for any other route. The
    logs name the model, not the wire, and the gateway names the wire per model."""
    name = str(model or "")
    if not name.startswith("opencode-go:"):
        return ""
    state = log.parent.parent
    if state not in _WIRES:
        try:
            entries = json.loads((state / "model-facts.json").read_text(encoding="utf-8")).get("entries") or {}
            _WIRES[state] = {key: str(row.get("wire") or "chat") for key, row in entries.items() if isinstance(row, dict)}
        except (OSError, ValueError, AttributeError):
            _WIRES[state] = {}
    return _WIRES[state].get(name, "chat")
#: How a workflow refusal read before results carried facts (`workflow/validate_script`).
_WORKFLOW_REFUSALS = (
    "workflow scripts must not import",
    "is not available in a workflow script",
    "attribute access to",
    "the workflow script does not parse",
    "meta.name must be",
    "meta.description must be",
)
_EXIT_ZERO = re.compile(r"\[exit 0[;\]]")
_EXIT_ZERO_NAMED = "[exit 0 (but the output reports a failure"
_EARLIER_IN_LINE = "earlier in this line"
_MISS = ("replacement(s), found 0", "Patch context does not match", "does not match")
_INVALID = re.compile(r"Invalid arguments for `([^`]+)`: (?:unexpected|missing)[^']*'([^']+)'")
_ASKED_BACKGROUND = re.compile(r'"background"\s*:\s*true')
_LOST_STATUS = "exited before it reported a status"
_STATUS_CWD = re.compile(r"\[exit (-?\d+|unknown)[^\]]*?; cwd ([^;\]]+)")
#: How long a session's copy of a page answers for its URL (`web/pages.py`).
PAGE_TTL_S = 15 * 60
#: How a foreground wait ends when no trailer was read (`tools/shell_wait.py`).
_UNREPORTED = ("ended", "held")
_READER = re.compile(
    r"answered from the page by \S+ \([\d,]+ characters(?:, read in \d+ parts)?"
    r"(, the middle left out past|, only [\d,]+ of them read)?"
)
_PAGE_CUT = re.compile(r"· truncated, [\d,]+ chars omitted")
_LOCATOR = re.compile(r"\.reeve[/\\]+output[/\\]+([a-z0-9-]+-\d+\.log)")
_PROMPT_KEYS = ("input", "cache_read", "cache_write")
#: How far the harness's price of a view may sit from the provider-based signal for the
#: same context before the row counts as `price-off`.
PRICE_RANGE = (0.8, 1.25)
#: How far past the request it last sent the harness may price its view at a cut before the cut
#: counts as `estimate-drift`. One step's results ride on top of it, hence the width.
DRIFT = 1.5
#: What a fold hint needs of the request, and what a session's notes may hold.
FOLD_HINT_SHARE = 0.5
NOTES_BUDGET_CHARS = 16_000
_WAKES = ("background_task", "background_shell")
TODO_WRITE = "todo_write"
FOLD_CONTEXT = "fold_context"


#: A status line's exit code, for a row written before results carried facts.
_STATUS_EXIT = re.compile(r"\[exit (-?\d+)")


def _unnamed(facts: Optional[dict], text: str, command: str, counts: Counter, hit: Any) -> None:
    """A failed check whose output names no failure the repair record can use.

    Counted from the result's facts (`named`). A row written before results carried them is read
    from its text only when the command's own pipe cut the output: such an output is short, so the
    log's copy of it is whole. Any other older output may be cut in the log itself, and is left
    out rather than guessed at."""
    code = (facts or {}).get("exit_code")
    if not isinstance(code, int):
        found = _STATUS_EXIT.search(text)
        code = int(found.group(1)) if found else None
    if not ((isinstance(code, int) and code != 0) or reports_failure(text)):
        return
    counts["failed-checks"] += 1
    cut = cut_by_command(command)
    if facts is not None and "named" in facts:
        named = int(facts.get("named") or 0)
    elif cut:
        named = len(failure_marks(text))
    else:
        counts["failed-checks read before facts"] += 1
        return
    if named == 0:
        why = "cut by its pipe" if cut else "in a whole output"
        counts[f"unnamed-failures {why}"] += 1
        hit(why)
    elif facts is not None and facts.get("setup"):
        counts["failed-checks named by setup"] += 1


#: The file a read-guard refusal names, in each of its four sentences (`tools/observed.py`).
_REFUSED_FILE = re.compile(
    r"blocked by (?:read-before-edit|partial-read|stale-file|out-of-view): "
    r"(?:what you read of |you have not read the part of )?(\S+?)[ ,(]"
)


def _file_name(path: str) -> str:
    """A path's last part, lowercased: a refusal and the read after it may spell one file two ways."""
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()


def _arguments(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("arguments")
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _step_calls(event: dict[str, Any]) -> list[str]:
    return [
        str(c.get("name") or (c.get("function") or {}).get("name") or "")
        for c in event.get("tool_calls") or []
    ]


def _prompt_tokens(event: dict[str, Any]) -> int:
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return 0
    return sum(int(usage.get(key) or 0) for key in _PROMPT_KEYS)


def _tool_names(header: dict[str, Any]) -> Optional[set[str]]:
    tools = header.get("tools")
    if not isinstance(tools, list):
        return None
    names = set()
    for tool in tools:
        if isinstance(tool, dict):
            names.add(str(tool.get("name") or (tool.get("function") or {}).get("name") or ""))
    return names


def _read(log: Path) -> list[dict[str, Any]]:
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue  # a torn final write is ordinary: the file is appended to while read
        if isinstance(event, dict):
            events.append(event)
    return events


_MEMORY_RULE = re.compile(r'"rule": "(memory-[a-z-]+)"|blocked by (?:the )?(memory-[a-z-]+)')
_WRITE_TOOLS = ("write_file", "replace_in_file", "apply_patch", "auto_memory")


def _in_memory_dir(arguments: dict[str, Any]) -> bool:
    """Whether a call's path is a memory directory's (`…/projects/<key>/memory/…`)."""
    path = str(arguments.get("path") or arguments.get("file_path") or "").replace("\\", "/")
    return "/memory/" in path and "/projects/" in path


def _is_index(arguments: dict[str, Any]) -> bool:
    """Whether a memory-directory path is an index rather than a note."""
    path = str(arguments.get("path") or arguments.get("file_path") or "").replace("\\", "/")
    return path.rsplit("/", 1)[-1].lower() in ("memory.md", "memory.legacy.md")


def review(
    logs: Iterable[Path],
    since: Optional[float] = None,
    until: Optional[float] = None,
    keep: int = 3,
    keep_all: str = "",
) -> tuple[Counter, dict[str, list]]:
    """Counts and references over `logs`, for rows stamped in `[since, until)`.

    The whole log is walked either way — what a row means can depend on rows before the
    window (the toolset in force, the call a result answers) — and only rows inside the
    window are counted.
    """
    counts: Counter = Counter()
    refs: dict[str, list] = {name: [] for name in DETECTORS}

    def hit(name: str, log: Path, seq: Any, detail: str = "") -> None:
        counts[name] += 1
        if name == keep_all or len(refs[name]) < keep:
            refs[name].append((log.name, seq) + ((detail,) if detail else ()))

    def inside(event: dict[str, Any]) -> bool:
        stamp = event.get("t") or 0
        return (since is None or stamp >= since) and (until is None or stamp < until)

    for log in logs:
        raw = _read(log)
        if not any(inside(e) for e in raw):
            continue
        counts["sessions"] += 1
        highest = 0
        for event in raw:  # the log's OWN numbers, before any reader repairs them
            if event.get("type") == "turn/start" and isinstance(event.get("turn"), int):
                if inside(event):
                    counts["turn-starts"] += 1
                    if event["turn"] <= highest:
                        hit("turn-reuse", log, event.get("seq"))
                highest = max(highest, event["turn"])
        events = list(monotonic_turns(raw))
        calls = {e.get("call_id"): e for e in events if e.get("type") == "tool/call"}
        steps = [e for e in events if e.get("type") == "assistant/message"]
        following = {id(a): b for a, b in zip(steps, steps[1:])}
        acted: Counter = Counter()  # turn -> steps that called a tool
        for step in steps:
            if _step_calls(step):
                acted[step.get("turn")] += 1
        last_missed: Optional[str] = None
        refused_files: dict[str, Any] = {}  # file name -> the turn the read guard last refused it in
        last_sent = 0  # the request the session last sent, as the provider priced it
        started_in: Optional[str] = None  # where the log's first shell command ran
        drifted_at: Optional[tuple[Any, Any]] = None  # (turn, step) of the log's last cwd-drift
        last_in: Optional[str] = None  # where the one before this ended
        cited: dict[str, set] = {}  # spill file -> the calls told it is their output
        with_facts: set = set()  # calls whose result row carries facts
        downloaded: dict[tuple[str, str], float] = {}  # (url, format) -> when last downloaded
        previous_read, previous_at, why = 0, 0.0, ""
        previous_model: Any = None  # a cache belongs to one model: another one starts cold
        before = ""  # what came just before this step: "turn/start", "todo/nudge", or ""
        holds_todo: Optional[bool] = None  # None = no header seen: unknown, not "no"
        listed: Optional[set[str]] = None  # the tool names of the header before this one
        polled_turn: Any = None  # the turn whose last step only asked a background task
        open_items: Optional[int] = None  # on the task list as last written; None = no list
        asked_turn: Any = None  # the turn a lesson was asked for in, until a memory write answers it
        last_stale = 0  # the stale blocks of the log's last `thinking/dropped` row without `stale_new`
        step_results: list[dict[str, Any]] = []  # tool results so far, read by `steer-waits`
        for event in events:
            kind = event.get("type")
            counted = inside(event)
            # Repairs are not a failure shape: what the harness recorded, what it
            # recalled, and what a recalled repair cost against the first time.
            # A person's message the step's own wait held; only rows that say how long.
            if kind == "tool/result":
                step_results.append(event)
            if counted and kind == "steering/message" and isinstance(event.get("waited_ms"), (int, float)):
                counts["person-steers"] += 1
                sent = float(event.get("t") or 0) - event["waited_ms"] / 1000
                held = [
                    r.get("name") for r in step_results
                    if r.get("turn") == event.get("turn") and r.get("step") == event.get("step")
                    and r.get("name") in STEER_HOLDERS and float(r.get("t") or 0) >= sent + STEER_WAIT_MS / 1000
                ]
                if held:
                    hit("steer-waits", log, event.get("seq"), f"{held[0]} {event['waited_ms'] / 1000:.0f}s")
            if counted and kind == "repair/recalled":
                counts["repairs recalled"] += 1
            if counted and kind == "repair/fixed":
                counts["repairs recorded"] += 1
                if event.get("recalled"):
                    counts["repairs made after a recall"] += 1
                    counts["steps of repairs made after a recall"] += int(event.get("steps") or 0)
                    counts["steps those repairs took the first time"] += int(event.get("earlier_steps") or 0)
                if event.get("asked"):
                    counts["repairs asked for a lesson"] += 1
                    asked_turn = event.get("turn")
            if kind == "request/header":
                names = _tool_names(event)
                if names is not None:
                    holds_todo = TODO_WRITE in names
                    if counted:
                        counts["tool-lists"] += 1
                        counts["tool-list chars"] += len(json.dumps(event.get("tools"), ensure_ascii=False))
                        if isinstance(event.get("system"), str):
                            counts["system chars"] += len(event["system"])
                            # The headers that carry a progress log, whole or as its outline: the
                            # ones the outline acts on (two in three headers carry none).
                            if "the progress log of work that spans sessions" in event["system"].lower():
                                counts["progress headers"] += 1
                                counts["progress header chars"] += len(event["system"])
                    gone = (listed or set()) - names
                    if gone and counted:
                        remote = sum(1 for name in gone if name.startswith("mcp__"))
                        what = "mcp" if remote == len(gone) else "built-in" if remote == 0 else "both"
                        hit("surface-shrinks", log, event.get("seq"), what)
                        counts[f"surface-shrinks {what}"] += 1
                    listed = names
            if kind == "todo/write" and isinstance(event.get("todos"), list):
                open_items = sum(
                    1 for item in event["todos"]
                    if isinstance(item, dict) and item.get("status") == "pending"
                )
            if kind == "context/budget" and event.get("hint") == "fold" and counted:
                counts["fold-hints"] += 1
                if int(event.get("foldable") or 0) < FOLD_HINT_SHARE * int(event.get("used") or 0):
                    hit("fold-hint-small", log, event.get("seq"), str(event.get("moment")))
                if event.get("moment") == "item":
                    counts["fold-item-hints"] += 1
                    if open_items == 0:
                        hit("fold-hint-nothing-open", log, event.get("seq"))
            if kind in ("turn/start", "todo/nudge"):
                before = kind
            if kind == "todo/nudge" and counted:
                counts["todo-nudges"] += 1
                if holds_todo is False:
                    hit("nudge-no-tool", log, event.get("seq"))
            if kind == "user/message" and counted:
                source = event.get("source")
                if isinstance(source, dict) and source.get("synthetic") in _WAKES:
                    counts["wake-turns"] += 1
                    if not acted[event.get("turn")]:
                        hit("idle-wakes", log, event.get("seq"))
            if kind == "tool/call":
                if last_missed is not None:
                    if event.get("name") == last_missed and counted:
                        counts["edit-misses retried at once"] += 1
                    last_missed = None
                if event.get("name") == "read_file" and refused_files:
                    read_name = _file_name(str(_arguments(event).get("path") or ""))
                    if refused_files.pop(read_name, None) == event.get("turn") and counted:
                        hit("gate-block-rereads", log, event.get("seq"))
                if counted and event.get("name") == "task":
                    counts["task-calls"] += 1
                if counted and event.get("name") == "run_code":
                    counts["programs"] += 1
                if counted and event.get("renamed"):
                    # Sent under another harness's parameter names and run under ours:
                    # each of these used to be an invalid-arguments refusal and a retry.
                    counts["arguments renamed"] += 1
                # A search loads its best matches: a describe after it is a step spent
                # on what the search could have shown.
                if counted and event.get("name") in ("tool_search", "tool_describe"):
                    counts["tool-searches" if event["name"] == "tool_search" else "tool-describes"] += 1
                if counted and event.get("name") == "goal":
                    if str(_arguments(event).get("action") or "") == "set":
                        hit("goal-set", log, event.get("seq"))
            if kind == "tool/result" and isinstance(event.get("facts"), dict):
                with_facts.add(event.get("call_id"))  # in or out of the window
            if kind == "tool/result" and counted:
                text = str(event.get("content") or "")
                call = calls.get(event.get("call_id")) or {}
                name = event.get("name") or call.get("name")
                facts = event.get("facts") if isinstance(event.get("facts"), dict) else None
                if facts is not None:
                    # Whose file it is. A fetched page is held by the session and read many
                    # times, so its file belongs to the PAGE: three reads of one
                    # page are one owner, where three calls used to be counted as three.
                    owner: Any = event.get("call_id")
                    if name == "web_fetch":
                        asked = _arguments(call)
                        owner = ("page", str(asked.get("url") or "").split("#", 1)[0], str(asked.get("format") or "markdown"))
                    for key in ("locator", "page_file"):
                        if isinstance(facts.get(key), str) and facts[key]:
                            where = Path(facts[key].replace("\\", "/")).name
                            cited.setdefault(where, set()).add(owner)
                    if facts.get("reader"):
                        counts["reader-pages"] += 1
                        if int(facts.get("page_unread") or 0) > 0:
                            hit("reader-cut-pages", log, event.get("seq"))
                if name == "web_fetch" and not event.get("is_error") and not text.startswith("Error:"):
                    asked = _arguments(call)
                    page = (str(asked.get("url") or "").split("#", 1)[0], str(asked.get("format") or "markdown"))
                    counts["page-fetches"] += 1
                    if not (facts or {}).get("cached"):
                        last, at = downloaded.get(page), float(event.get("t") or 0)
                        if last is not None and at - last <= PAGE_TTL_S and asked.get("refresh") is not True:
                            hit("page-refetch", log, event.get("seq"))
                        downloaded[page] = at
                    if facts is not None and facts.get("shown_chars") and "start" not in facts:
                        if facts.get("format") not in ("answer", "find"):
                            hit("page-middle-dropped", log, event.get("seq"))
                    counts["reader-quotes"] += int((facts or {}).get("quotes") or 0)
                    counts["reader-quotes not in the page"] += int((facts or {}).get("quotes") or 0) - int(
                        (facts or {}).get("quotes_found") or 0
                    )
                if name == "workflow" and (facts or {}).get("outcome") != "background":
                    # A background run has two rows: the call's "running" answer in the parent,
                    # and the runner's own, which says how it ended. Counted once, by the second.
                    counts["workflow-runs"] += 1
                    if facts is not None:
                        refused = facts.get("outcome") == "refused"
                    else:
                        refused = any(sign in text for sign in _WORKFLOW_REFUSALS)
                    if refused:
                        hit("workflow-refused", log, event.get("seq"))
                if name == "run_shell":
                    counts["shell-results"] += 1
                    if facts is None:
                        lost = _LOST_STATUS in text
                    else:
                        lost = facts.get("ended") in _UNREPORTED and "exit_code" not in facts
                    if lost:
                        hit("lost-exit-status", log, event.get("seq"))
                    # Read from the fact once it exists; before it, from the status line: a failure
                    # on a path or a name, in another folder than the log's first command ran in.
                    status = _STATUS_CWD.search(text)
                    where = os.path.normcase(status.group(2).strip()) if status else None
                    if started_in is None:
                        started_in = where
                    # A command that moved the shell itself chose its folder: as the product reads it.
                    stayed = where is not None and where == last_in
                    last_in = where or last_in
                    if facts is not None and "cwd_drift" in facts:
                        drifted = bool(facts.get("cwd_drift"))
                    else:
                        drifted = bool(
                            status and stayed and started_in and where != started_in
                            and status.group(1) != "0" and counts_as_drift(str(_arguments(call).get("command") or ""), text)
                        )
                    if drifted:
                        hit("cwd-drift", log, event.get("seq"))
                        # The note reaches the model with the step's results; a call written in
                        # the same step could not have read it, a later step of the turn could.
                        turn_step = (event.get("turn"), event.get("step"))
                        if drifted_at is not None and drifted_at[0] == turn_step[0] and drifted_at[1] != turn_step[1]:
                            hit("cwd-drift-repeats", log, event.get("seq"))
                        drifted_at = turn_step
                    command = str(_arguments(call).get("command") or "")
                    if is_verification_command(command):
                        counts["checks"] += 1
                        if "|" in command:
                            hit("piped-checks", log, event.get("seq"))
                        _unnamed(facts, text, command, counts, lambda why: hit("unnamed-failures", log, event.get("seq"), why))
                        if _EXIT_ZERO.search(text) and reports_failure(text):
                            hit("masked-checks", log, event.get("seq"))
                        elif _EXIT_ZERO_NAMED in text:
                            # exit 0 whose status line names the failure its output reports
                            # (tools/shell.py `_masked_failure_note`): seen, not masked.
                            counts["masked-checks named on the status line"] += 1
                        if _EARLIER_IN_LINE in text:
                            # an earlier statement's failure the shell reported as the
                            # line's exit (tools/shell_sequence.py): never masked.
                            counts["checks failed earlier in their line, reported"] += 1
                if name == "run_code":
                    if (facts or {}).get("outcome") == "unknown_tool":
                        hit("unknown-tool-programs", log, event.get("seq"), "before it ran")
                    elif "no tool named" in text:
                        hit("unknown-tool-programs", log, event.get("seq"), "at the call")
                if name in ("replace_in_file", "apply_patch") and any(m in text for m in _MISS):
                    hit("edit-misses", log, event.get("seq"))
                    last_missed = name
                refused = _MEMORY_RULE.search(text)
                if refused:
                    rule = refused.group(1) or refused.group(2)
                    hit("memory-refused", log, event.get("seq"), rule)
                    if rule == "memory-progress":
                        # The progress log's own refusals: its size cap, or an unproven done mark.
                        hit("progress-refused", log, event.get("seq"))
                elif _in_memory_dir(_arguments(call)) and name in _WRITE_TOOLS and not text.lstrip().startswith("Error"):
                    counts["memory writes saved"] += 1
                    if asked_turn is not None and event.get("turn") == asked_turn:
                        counts["memory writes after a lesson was asked for"] += 1
                        asked_turn = None  # one request, answered once
                if name == "read_file" and _in_memory_dir(_arguments(call)) and not _is_index(_arguments(call)):
                    counts["memory notes read"] += 1
                for rule in ("read-before-edit", "partial-read", "stale-file", "out-of-view"):
                    if f"blocked by {rule}" in text or f'"rule": "{rule}"' in text:
                        hit("gate-blocks", log, event.get("seq"), f"{name}:{rule}")
                        # A refused call has its own `tool/call` row; in older logs
                        # its file was named only in the refusal's sentence.
                        refused_path = str(_arguments(call).get("path") or "")
                        named = _REFUSED_FILE.search(text) if not refused_path else None
                        refused_path = named.group(1) if named else refused_path
                        if refused_path:
                            refused_files[_file_name(refused_path)] = event.get("turn")
                if "behind progressive discovery" in text:
                    hit("deferred-calls", log, event.get("seq"))
                if name == "lsp" and ("timed out" in text or "stopped answering" in text):
                    hit("lsp-timeouts", log, event.get("seq"))
                if text.lstrip().startswith('{"error"'):
                    hit("raw-json-errors", log, event.get("seq"), str(name))
                refused = _INVALID.search(text)
                if refused:
                    where = f"{refused.group(1)}:{refused.group(2)}"
                    hit("invalid-arguments", log, event.get("seq"), where)
                    counts[f"invalid-arguments {where}"] += 1
                if name == "task":
                    task_facts = event.get("facts") if isinstance(event.get("facts"), dict) else None
                    capped = (
                        task_facts.get("outcome") == "cap"
                        if task_facts is not None
                        else '"subagent_max_parallel" is' in text or "tasks are already running in this session" in text
                    )
                    if capped:
                        hit("slot-refusals", log, event.get("seq"))
                delegation = event.get("delegation")
                if name == "task" and isinstance(delegation, dict) and delegation.get("status") == "running":
                    if not _ASKED_BACKGROUND.search(str(call.get("arguments") or "")):
                        hit("unasked-background", log, event.get("seq"))
            if kind == "turn/end" and counted:
                counts["turn-ends"] += 1
                if event.get("reason") == "error":
                    what = str(event.get("error") or "unclassified")
                    hit("error-ends", log, event.get("seq"), what)
                    counts[f"error-ends {what}"] += 1
                    if what in ("unclassified", "unknown") or (
                        what in ("code", "crashed") and not event.get("error_at")
                    ):
                        hit("unnamed-error-ends", log, event.get("seq"), what)
                if event.get("reason") == "completed_unverified":
                    hit("unverified-ends", log, event.get("seq"))
                elif event.get("reason") == "interrupted":
                    counts["interrupted-ends"] += 1
            if kind == "thinking/dropped":
                # A stale block stays out of every later request, so only its first is a loss.
                # `stale_new` says which; an older row is read by how far `stale` rose.
                stale = int(event.get("stale") or 0)
                if "stale_new" in event:
                    lost = {"stale": int(event.get("stale_new") or 0)}
                else:
                    lost = {"stale": max(0, stale - last_stale)}
                    last_stale = stale
                lost.update({cause: int(event.get(cause) or 0) for cause in ("api", "retried")})
                causes = [cause for cause in ("stale", "api", "retried") if lost[cause]]
                if causes and counted:
                    hit("thinking-drops", log, event.get("seq"), "+".join(causes))
                    for cause in causes:
                        counts[f"thinking blocks dropped {cause}"] += lost[cause]
                if stale and counted:
                    counts["thinking blocks re-dropped stale"] += stale - lost["stale"]
            if kind == "compaction/start" and counted:
                price, signal = event.get("before_tokens"), event.get("signal")
                if isinstance(price, (int, float)) and isinstance(signal, (int, float)) and signal > 0:
                    counts["compaction-starts"] += 1
                    if not PRICE_RANGE[0] <= price / signal <= PRICE_RANGE[1]:
                        hit("price-off", log, event.get("seq"), f"{price / signal:.2f}")
            if kind == "compaction/summary" and counted and event.get("parts"):
                counts["merges of parts"] += int(event.get("merged") or 0)
                if int(event.get("parts_tokens") or 0) > int(event.get("parts_budget") or 0) > 0:
                    hit("parts-over-budget", log, event.get("seq"), f"{int(event['parts'])} parts")
            if kind == "compaction/prune" and counted:
                if event.get("reason") == "rederivable":
                    # The working trim runs on a warm cache, by its own size bar.
                    counts["working-trims"] += 1
                    counts["tokens trimmed"] += int(event.get("saved_tokens") or 0)
                else:
                    hit("cold-prunes", log, event.get("seq"))
            # The harness's price of its view at a cut, against the request it last sent (the provider's
            # count plus what the provider said it cleared first). The cut's price adds one
            # step's results, so only a wide gap counts: session 90e83048-f57 cut at 2.0x on 2026-10-30.
            if kind in ("compaction/prune", "compaction/start") and counted and last_sent:
                price = event.get("before_tokens")
                if isinstance(price, (int, float)) and price > 0:
                    counts["priced cuts"] += 1
                    if price > DRIFT * last_sent:
                        hit("estimate-drift", log, event.get("seq"), f"{price / last_sent:.2f}x")
            if kind in ("compaction/start", "compaction/end", "compaction/prune"):
                last_sent = 0  # the next request is priced against the cut view
            if kind == "assistant/message" and isinstance(event.get("usage"), dict):
                cleared = event.get("context_cleared") if isinstance(event.get("context_cleared"), dict) else {}
                last_sent = _prompt_tokens(event) + int(cleared.get("input_tokens") or 0)
            if kind == "notice" and event.get("kind") == "usage_partial" and counted:
                hit("usage-distrust", log, event.get("seq"), str(event.get("model") or ""))
            if kind in ("compaction/prune", "compaction/end") and counted and event.get("arguments_folded"):
                hit("argument-folds", log, event.get("seq"), str(event.get("arguments_folded")))
            # A prune rewrites tool results the cached prefix already holds (the working
            # trim does it on a warm cache, by design), so the drop after it is the harness's own,
            # not the provider's: 10 of 96 "unexplained" drops in the store 2026-10-15..21 followed one.
            if kind in ("request/header", "compaction/start", "compaction/end", "compaction/prune", "revert/set"):
                why = kind
            if kind == "assistant/message":
                names = _step_calls(event)
                tokens = _prompt_tokens(event)
                if counted:
                    counts["steps"] += 1
                    counts["tokens re-read by steps"] += tokens
                polls = bool(names) and all(name == "shell_task_output" for name in names)
                if polls and counted and polled_turn is not None and polled_turn == event.get("turn"):
                    hit("poll-steps", log, event.get("seq"))
                polled_turn = event.get("turn") if polls else None
                if counted and names and all(name == TODO_WRITE for name in names):
                    after = following.get(id(event))
                    closes = (
                        after is not None
                        and after.get("turn") == event.get("turn")
                        and not _step_calls(after)
                    )
                    if before == "todo/nudge":
                        place = "after a nudge"
                        hit("nudge-alone", log, event.get("seq"))
                    elif closes:
                        place = "closing the list before the answer"
                    elif before == "turn/start":
                        place = "opening the turn"
                    else:
                        place = "on their own, mid-work"
                    hit("bookkeeping-steps", log, event.get("seq"), place)
                    counts[f"bookkeeping-steps {place}"] += 1
                    counts["tokens re-read by bookkeeping-steps"] += tokens
                before = ""
            if kind == "assistant/message" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
                read = int(usage.get("cache_read") or 0)
                model = event.get("model")
                gap = (event.get("t") or 0) - previous_at if previous_at else 0
                idle = gap > _idle_s(model) if previous_at else False
                switched = bool(previous_model and model and model != previous_model)
                # OpenCode Go's Messages, Responses and Gemini wires sent one routing session for
                # every conversation until 2026-10-21; the chat wire always sent the session's own.
                if counted and _gateway_wire(log, model) not in ("", "chat"):
                    prompt = _prompt_tokens(event)
                    counts["gateway-wire prompt tokens"] += prompt
                    counts["gateway-wire uncached tokens"] += max(prompt - read, 0)
                if counted and previous_read > 20_000 and read < previous_read / 2:
                    cause = why or ("model" if switched else "idle" if idle else "unexplained")
                    # The provider's own reading of the request, where it gave one: a
                    # drop the harness logged no cause for is either a prefix it changed without
                    # saying so, or an entry the provider dropped although the request matched.
                    told = str((event.get("cache_miss") or {}).get("type") or "")
                    if cause == "unexplained" and told in API_CHANGED:
                        cause = f"api {told}"
                        hit("unmarked-prefix-changes", log, event.get("seq"), told)
                    elif cause == "unexplained" and told == "unchanged":
                        cause = "provider dropped"
                    hit("cache-drops", log, event.get("seq"), cause)
                    counts[f"cache-drops {cause}"] += 1
                    if cause == "unexplained" and str(model or "").startswith("opencode-go:"):
                        counts["opencode-go unexplained cache-drops"] += 1
                if counted and str(model or "").startswith("opencode-go:"):
                    # The gateway's own share (prediction `opencode-affinity-headers`).
                    counts["opencode-go steps"] += 1
                previous_read = read or int(usage.get("input") or 0)
                previous_at = event.get("t") or 0
                previous_model = model or previous_model
                why = ""
        _locators(log, since, until, counts, hit, cited, with_facts)
        _folds(log, events, inside, counts, hit)
        _parts_cached(log, events, inside, counts, hit)
        _sources(log, since, until, counts, hit)
        _visuals(log, events, inside, counts, hit)
        _split_search(log, events, inside, counts, hit)
        _searches(log, events, inside, counts, hit)
        _restarts(log, raw, inside, counts, hit)
        _cuts(log, since, until, counts, hit)
        _questions(log, events, inside, counts, hit)
        _designs(log, events, inside, counts, hit)
    return counts, refs


#: A concrete detail of a block's memory: a backticked span, a flag, a path, a number of four
#: digits or more, a snake_case name. What a later step needs word for word, and what a summary
#: written over a summary drops first.
_DETAIL = re.compile(
    r"`([^`\n]{3,80})`|(--[a-z][a-z0-9-]{2,})|([A-Za-z]:\\[^\s`'\"]+|(?:[\w.-]+/)+[\w.-]+\.\w{1,5})"
    r"|\b(\d{4,})\b|\b([a-z]+_[a-z0-9_]{3,})\b"
)


def _details(text: str) -> set[str]:
    found = set()
    for match in _DETAIL.finditer(text or ""):
        token = next(group for group in match.groups() if group).strip().rstrip(".,:;)").lower()
        if len(token) >= 4:
            found.add(token)
    return found


def _cuts(log: Path, since: Optional[float], until: Optional[float], counts: Counter, hit) -> None:
    """Details a block's memory held that the block of the next cut no longer holds.

    Read from the transcript's `compacted` notices, which carry the block's memory as the person
    sees it. A cut is counted in the window its later notice falls in, per `cut details` (what
    the earlier block held). Some of what is lost was finished and rightly dropped; the share
    is what moves when a cut stops rewriting the one before it."""
    transcript = log.with_name(log.name.replace(".events.jsonl", ".jsonl"))
    try:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    cuts = []
    for line in lines:
        if '"compacted"' not in line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("role") == "notice" and message.get("kind") == "compacted":
            if str(message.get("summary") or "").strip():
                cuts.append(message)
    for earlier, later in zip(cuts, cuts[1:]):
        stamp = later.get("ts") or 0
        if (since is not None and stamp < since) or (until is not None and stamp >= until):
            continue
        held = _details(str(earlier.get("summary")))
        lost = held - _details(str(later.get("summary")))
        counts["cut details"] += len(held)
        counts["cuts after a cut"] += 1
        if lost:
            hit("cut-details-lost", log, None, f"{len(lost)} of {len(held)}")
            counts["cut-details-lost"] += len(lost) - 1  # `hit` counted one


def _restarts(log: Path, raw: list, inside: Any, counts: Counter, hit: Any) -> None:
    """A turn the process died in leaves a synthetic `turn/end` (written by the engine that adopts
    the log); it is lost unless a later `turn/resumed` names it. The log's own turn
    numbers, since the two rows carry the same one."""
    open_ends: dict[Any, dict] = {}
    for event in raw:
        kind = event.get("type")
        if kind == "turn/end" and event.get("synthetic") and event.get("reason") == "interrupted":
            open_ends[event.get("turn")] = event
        elif kind == "turn/resumed":
            if inside(event):
                counts["picked-up turns"] += 1
            open_ends.pop(event.get("turn"), None)
    for event in open_ends.values():
        if inside(event):
            hit("lost-turns", log, event.get("seq"))


#: A shell command that is a file search: the search tool at its head, after an optional `cd`.
_SHELL_SEARCH = re.compile(
    r"^\s*(?:cd\s+[^;&|]+(?:;|&&)\s*)?(?:rg|grep|findstr|Select-String|sls|git\s+grep)\b"
    r"|Get-ChildItem.{0,200}\|\s*(?:Select-String|sls)\b",
    re.I | re.S,
)


#: Words that ask for work, in the person's languages: a message with one is a request, whatever
#: else it asks. "làm sao" (how) is a question, so `làm` counts only with what makes it an order.
_WORK = re.compile(
    r"\b(làm đi|làm luôn|làm giúp|hãy làm|làm lại|sửa|fix|tạo|thêm|viết|xoá|xóa|đổi|cài|nâng cấp|triển khai|"
    r"format|lập|lấy|"
    r"build|implement|create|add|write|make|update|refactor|rename|delete|remove|install|deploy|"
    r"change|edit|generate)\b",
    re.IGNORECASE,
)
#: How a question ends or begins, in the same languages.
_QUESTION_END = re.compile(
    r"(\?|\b(nhỉ|không|chưa|à|hả|sao|gì|nào|đúng không|phải không|được không|thế nào|ra sao))\s*[.!…]*\s*$",
    re.IGNORECASE,
)
_QUESTION_START = re.compile(
    r"^(how|why|what|which|where|when|who|is|are|does|do|did|can|could|should|would|will)\b",
    re.IGNORECASE,
)
#: The tools that write a file. A shell command that writes cannot be told from its text here.
_FILE_WRITERS = frozenset({"write_file", "replace_in_file", "apply_patch", "apply_unified_diff", "edit_notebook"})


def _message_kind(text: str) -> str:
    """`request`, `question` or `other` for a person's message, from its words alone."""
    text = " ".join(str(text or "").split())
    if not text:
        return "other"
    if _WORK.search(text):
        return "request"
    if _QUESTION_END.search(text) or _QUESTION_START.search(text):
        return "question"
    return "other"


def _questions(log: Path, events: list[dict[str, Any]], inside, counts: Counter, hit) -> None:
    """Questions answered with file writes, and requests met with none (`question-writes`)."""
    turns: dict[Any, list] = {}  # turn -> [kind, wrote, seq]
    for event in events:
        kind, turn = event.get("type"), event.get("turn")
        if kind == "user/message" and inside(event) and not event.get("source"):
            turns[turn] = [_message_kind(str(event.get("content") or "")), False, event.get("seq")]
        elif kind == "tool/call" and turn in turns and event.get("name") in _FILE_WRITERS:
            if not _in_memory_dir(_arguments(event)):
                turns[turn][1] = True
    for message_kind, wrote, seq in turns.values():
        if message_kind == "question":
            counts["question turns"] += 1
            if wrote:
                hit("question-writes", log, seq)
        elif message_kind == "request":
            counts["request turns"] += 1
            if not wrote:
                counts["requests-without-writes"] += 1


def _designs(log: Path, events: list[dict[str, Any]], inside, counts: Counter, hit) -> None:
    """Design pages a turn left with something to fix (`design-unfixed`), read from the
    `design/check` rows the engine writes after each write of a page."""
    pages: dict[tuple[Any, str], list[tuple[dict[str, Any], Any]]] = {}
    for event in events:
        if event.get("type") != "design/check" or not inside(event):
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        if not facts.get("design_checked"):
            counts["design checks not run"] += 1
            continue
        counts["design checks"] += 1
        pages.setdefault((event.get("turn"), str(event.get("path") or "")), []).append((facts, event.get("seq")))
    for checks in pages.values():
        counts["design pages"] += 1
        first, first_seq = checks[0]
        if int(first.get("design_unguided") or 0):
            hit("design-unguided", log, first_seq)
        if int(first.get("design_issues") or 0):
            counts["design first drafts with issues"] += 1
        if int(first.get("design_overflow_phone") or 0):
            counts["design first drafts off a phone"] += 1
        if int(first.get("design_behaviour") or 0):
            counts["design first drafts that misbehave when used"] += 1
        last, seq = checks[-1]
        if int(last.get("design_issues") or 0):
            hit("design-unfixed", log, seq)
        # Controls that still did nothing on a page not declared a static mockup.
        if int(last.get("design_dead_clicks") or 0) and not last.get("design_static"):
            counts["design pages left with dead clicks"] += 1
    # The app held to its locked design: the last compare of a page in a turn, and the
    # writes that moved a reviewed design's tokens.
    compares: dict[tuple[Any, str], tuple[dict[str, Any], Any]] = {}
    for event in events:
        if not inside(event):
            continue
        if event.get("type") == "design/drift":
            hit("design-drift", log, event.get("seq"))
            continue
        if event.get("type") != "tool/result" or event.get("name") != "compare_design":
            continue
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        if not facts.get("compare_checked"):
            continue
        counts["design compare runs"] += 1
        compares[(event.get("turn"), str(facts.get("compare_page") or event.get("call_id") or ""))] = (facts, event.get("seq"))
    for facts, seq in compares.values():
        counts["design compares"] += 1
        if int(facts.get("compare_issues") or 0):
            hit("compare-unfixed", log, seq)


def _searches(log: Path, events: list[dict[str, Any]], inside, counts: Counter, hit) -> None:
    """`grep` pages cut short, and file searches written as shell commands."""
    for event in events:
        if not inside(event):
            continue
        kind, name = event.get("type"), event.get("name")
        if kind == "tool/result" and name == "grep":
            facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
            counts["grep results"] += 1
            if facts.get("capped") is True:
                hit("grep-capped", log, event.get("seq"))
        elif kind == "tool/call" and name == "grep":
            counts["grep calls"] += 1
        elif kind == "tool/call" and name == "run_shell":
            command = str(_arguments(event).get("command") or "")
            if _SHELL_SEARCH.search(command):
                hit("shell-searches", log, event.get("seq"))


#: A connector's own web search tool, the kind `web_search` can stand in for.
_CONNECTOR_SEARCH = re.compile(r"^mcp__(?:tavily|firecrawl|exa|brave)\w*__\w*search\w*$")


def _split_search(log: Path, events: list[dict[str, Any]], inside, counts: Counter, hit) -> None:
    """A session whose searches went through two tools, counted once per session."""
    builtin = connector = False
    first = None
    for event in events:
        if event.get("type") != "tool/result" or not inside(event):
            continue
        name = str(event.get("name") or "")
        if name == "web_search":
            builtin = True
        elif _CONNECTOR_SEARCH.match(name):
            connector = True
            first = first if first is not None else event.get("seq")
    if builtin or connector:
        counts["searching sessions"] += 1
    if builtin and connector:
        hit("split-search", log, first)


#: A visual's place in an answer, and a Markdown table's header rule.
_PLACED = re.compile(r"^[ \t]*:visual\[([\w-]+)\][ \t]*$", re.M)
_TABLE_RULE = re.compile(r"^\s*\|[\s:|-]+\|\s*$", re.M)


def _visuals(log: Path, events: list[dict[str, Any]], inside, counts: Counter, hit) -> None:
    """What each turn that showed a visual did with it in its closing answer.

    A visual is known by the facts on its result row (`shown`, `name`); a row from before facts
    by the sentence it began with. The closing answer is the turn's last assistant message
    with text; where a turn has none, it ended some other way and is not counted."""
    turns: dict[Any, dict[str, Any]] = {}
    for event in events:
        kind = event.get("type")
        turn = turns.setdefault(event.get("turn"), {"shown": [], "answer": None, "row": None})
        if kind == "tool/result" and event.get("name") == "show_visual":
            facts = event.get("facts") if isinstance(event.get("facts"), dict) else None
            if facts is not None:
                shown, name = bool(facts.get("shown")), str(facts.get("name") or "")
            else:
                text = str(event.get("content") or "")
                shown = text.startswith("Shown to the user")
                found = re.match(r"Shown to the user as ([\w-]+):", text)
                name = found.group(1) if found else ""
            if not shown:
                if inside(event):
                    hit("visual-refused", log, event.get("seq"))
                continue
            turn["shown"].append(name)
            turn["row"] = turn["row"] or event
        elif kind == "assistant/message" and str(event.get("text") or "").strip():
            turn["answer"] = event
    for turn in turns.values():
        answer = turn["answer"]
        if not turn["shown"] or answer is None or not inside(answer):
            continue
        counts["visual turns"] += 1
        counts["visuals shown"] += len(turn["shown"])
        text = str(answer.get("text") or "")
        placed = set(_PLACED.findall(text))
        if not placed & set(turn["shown"]):
            hit("visual-unplaced", log, answer.get("seq"), f"{len(turn['shown'])} shown")
        else:
            for name in turn["shown"]:
                if name not in placed:
                    hit("visual-drafts", log, answer.get("seq"), name)
        if _TABLE_RULE.search(text):
            hit("visual-restated", log, answer.get("seq"))


def _sources(log: Path, since: Optional[float], until: Optional[float], counts: Counter, hit) -> None:
    """Answers that ended their turn citing a searched source nothing opened.

    An answer followed by a note the harness attached did not end the turn (a gate sent the
    model around again), so only the answer after it is counted. A deleted session has no
    transcript."""
    transcript = log.with_name(log.name.replace(".events.jsonl", ".jsonl"))
    try:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    messages = []
    for line in lines:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict):
            messages.append(message)
    ledger = SourceLedger()
    noted = False  # the source note was sent since the person last spoke
    for index, message in enumerate(messages):
        ledger.add(message)
        role, tag = message.get("role"), message.get("_attached_by")
        if role == "user":
            noted = tag == "source_nudge" or (noted and tag is not None)
            continue
        if role != "assistant" or message.get("tool_calls"):
            continue
        after = messages[index + 1] if index + 1 < len(messages) else None
        if after is not None and after.get("role") == "user" and after.get("_attached_by"):
            continue
        stamp = message.get("ts") or 0
        if (since is not None and stamp < since) or (until is not None and stamp >= until):
            continue
        content = message.get("content")
        answer = content if isinstance(content, str) else ""
        if not ledger.cited(answer):
            continue
        counts["searched-citing answers"] += 1
        if ledger.unread(answer):
            where = "after the note" if noted else "no note"
            hit("unread-citations", log, None, where)
            counts[f"unread-citations {where}"] += 1


def _folds(log: Path, events: list[dict[str, Any]], inside, counts: Counter, hit) -> None:
    """What each fold the model made was worth, in the provider's own counts.

    A fold changes the size of every request after it, until the next compaction of any
    kind. Those requests are the whole of its benefit; the request that carried the fold
    call is its cost when that step did nothing else (a to-do write beside it is not work).
    """
    marks = [i for i, e in enumerate(events) if e.get("type") == "compaction/summary"]
    for place, at in enumerate(marks):
        row = events[at]
        if row.get("reason") != "folded" or not inside(row):
            continue
        counts["folds"] += 1
        began = next((e for e in reversed(events[:at]) if e.get("type") == "compaction/start"), {})
        if began.get("unsettled"):
            hit("fold-unsettled", log, row.get("seq"), str(began["unsettled"]))
        # The notes' own budget held them alone; since the parts' one a row carries the block's parts and
        # is held to their budget (`parts-over-budget`), so only a row without them is this one.
        if "parts" not in row and int(row.get("summary_chars") or 0) > NOTES_BUDGET_CHARS:
            hit("notes-over-budget", log, row.get("seq"), f"{int(row['summary_chars']):,} characters")
        stop = marks[place + 1] if place + 1 < len(marks) else len(events)
        earlier = [e for e in events[:at] if e.get("type") == "assistant/message"]
        later = [e for e in events[at:stop] if e.get("type") == "assistant/message"]
        if not earlier or not later:
            hit("fold-unpaid", log, row.get("seq"), "no step ran after it")
            continue
        folded_in, full, small = earlier[-1], _prompt_tokens(earlier[-1]), _prompt_tokens(later[0])
        if not full or not small:
            counts["folds with no provider count"] += 1
            continue
        own_step = all(name in (FOLD_CONTEXT, TODO_WRITE) for name in _step_calls(folded_in))
        if own_step:
            hit("fold-own-step", log, row.get("seq"))
        spent = int((folded_in.get("usage") or {}).get("output") or 0) + (full if own_step else 0)
        spared = max(0, full - small) * len(later)
        counts["tokens spared by folds"] += spared
        counts["tokens spent on folds"] += spent
        if spared < spent:
            hit("fold-unpaid", log, row.get("seq"), f"{len(later)} steps after it, {spared - spent:+,} tokens")


def _parts_cached(log: Path, events: list[dict[str, Any]], inside, counts: Counter, hit) -> None:
    """Whether a cut that kept the block's earlier parts had them read from cache.

    The floor is per model: what the request after the log's first cut on that model read,
    when the block was new and only the system prompt and the tools could come from cache.
    A cut is judged when it has earlier parts, merged none, and both the request before it
    (a warm cache) and the one after it ran on the same model as the floor."""
    floor: dict[str, int] = {}
    prior_parts = 0
    for at, row in enumerate(events):
        if row.get("type") != "compaction/summary":
            continue
        before = next((e for e in reversed(events[:at]) if e.get("type") == "assistant/message"), None)
        after = next((e for e in events[at + 1:] if e.get("type") == "assistant/message"), None)
        earlier, prior_parts = prior_parts, int(row.get("parts_tokens") or 0)
        if before is None or after is None or before.get("model") != after.get("model"):
            continue
        model = str(after.get("model") or "")
        read = int((after.get("usage") or {}).get("cache_read") or 0)
        if model not in floor:
            floor[model] = read
            continue
        warm = int((before.get("usage") or {}).get("cache_read") or 0) > 0
        if not earlier or int(row.get("merged") or 0) or not warm or not inside(row):
            continue
        counts["cuts keeping parts"] += 1
        if read < floor[model] + earlier // 2:
            hit("parts-repriced", log, row.get("seq"), f"read {read:,}, floor {floor[model]:,}, earlier parts ~{earlier:,}")


def _locators(
    log: Path,
    since: Optional[float],
    until: Optional[float],
    counts: Counter,
    hit,
    cited: dict[str, set],
    with_facts: set,
) -> None:
    """Spill files and the results told each is theirs; one named by two results is a reuse.

    `cited` arrives holding what the results' own facts say. This adds the results written
    BEFORE facts existed, read from the end of their text in the canonical transcript (the
    event log clips a result at 4,000 characters, which is where the locator is), and the
    reader's footer the same way. A result with facts is never read by its text: a path in
    output the model asked for is not a locator. A deleted session has no transcript.
    """
    transcript = log.with_name(log.name.replace(".events.jsonl", ".jsonl"))
    try:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        if message.get("tool_call_id") in with_facts:
            continue
        stamp = message.get("ts") or 0
        if (since is not None and stamp < since) or (until is not None and stamp >= until):
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        # Only the locator the tool appended (the end of the result), not a path that
        # happens to appear in output the model asked for.
        for name in set(_LOCATOR.findall(content[-600:])):
            cited.setdefault(name, set()).add(message.get("tool_call_id"))
        if _PAGE_CUT.search(content[-900:]):
            hit("page-middle-dropped", log, None)
        # The same tail says who answered a web_fetch and from how much of the page.
        read = _READER.search(content[-900:])
        if read:
            counts["reader-pages"] += 1
            if read.group(1):
                hit("reader-cut-pages", log, None)
    for name, owners in sorted(cited.items()):
        counts["spill-locators"] += len(owners)
        for _ in range(len(owners) - 1):
            hit("locator-reuse", log, None, name)


def _logs(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        root = Path(raw)
        if (root / "conversations").is_dir():
            root = root / "conversations"
        found += sorted(root.rglob("*.events.jsonl"))
    return found


def stamp(day: str) -> Optional[float]:
    """`YYYY-MM-DD` or an ISO datetime, as unix seconds; None for an empty string."""
    if not day:
        return None
    return datetime.fromisoformat(day).timestamp()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--since", default="")
    parser.add_argument("--until", default="")
    parser.add_argument("--refs", default="", choices=("",) + DETECTORS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.paths:
        from harness.safety.secrets import state_dir

        args.paths = [str(state_dir())]
    counts, refs = review(
        _logs(args.paths), stamp(args.since), stamp(args.until), keep_all=args.refs
    )
    if args.json:
        print(json.dumps({"counts": dict(counts), "refs": refs, "owner": OWNER}, indent=1))
        return 0
    if args.refs:
        print(f"{args.refs}: {counts[args.refs]}  ({OWNER[args.refs]})")
        for ref in refs[args.refs]:
            print("  ", *ref)
        return 0
    print(
        f"sessions {counts['sessions']} | steps {counts['steps']} | checks {counts['checks']} "
        f"| turn starts {counts['turn-starts']} | task calls {counts['task-calls']}"
    )
    for name in DETECTORS:
        print(f"{name:18} {counts[name]:5}  {OWNER[name]:28} e.g. {refs[name]}")
    extra = {k: v for k, v in counts.items() if " " in k}
    if extra:
        print("detail:", dict(sorted(extra.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
