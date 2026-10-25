import type { Page } from "@playwright/test";
import { fixtureJobs, JOB_ANCHOR_TOTAL } from "./channels";
import type { MockState } from "./state";

// The fake agent's turns. A message is matched against these patterns in order, the first match
// plays its turn on the session socket in the real protocol ({type, data}), and a message nothing
// matches is echoed back. A turn that stops on a person (an approval, a question, a plan, a tool
// to install) sends no `turn_done`: the answer frame, handled in sockets.ts, finishes it.

/** What a turn can do. */
export type Turn = {
  text: string;
  msg: any;
  /** The session the socket belongs to. */
  sid: string;
  page: Page;
  s: MockState;
  send: (type: string, data?: Record<string, unknown>) => void;
  /** Send on the app-wide stream, as the manager announces a child session there. */
  appSend: (event: unknown) => void;
  /** Which call the next approval decides; `fails` makes approving it fail. */
  awaiting: (tool: string, opts?: { fails?: boolean }) => void;
  /** The timer of a stream that an interrupt stops, or null once it has stopped by itself. */
  streaming: (timer: ReturnType<typeof setInterval> | null) => void;
};

const MODEL = "anthropic:claude-opus-4-8";
const ROOT = "/Users/test/Reeve/launch-note";

/** The answer, and the end of the turn. */
function answer(t: Turn, text: string, extra: Record<string, unknown> = {}) {
  t.send("assistant_message", { text, ...extra });
  t.send("turn_done");
}

/** A call proposed and finished in one go. */
function ran(t: Turn, name: string, args: Record<string, unknown>, finished: Record<string, unknown>, proposed: Record<string, unknown> = {}) {
  t.send("tool_proposed", { name, arguments: args, ...proposed });
  t.send("tool_finished", { name, status: "ok", ...finished });
}

/** A call proposed that waits on the person's approval. */
function asks(t: Turn, name: string, proposed: Record<string, unknown>, permission: Record<string, unknown>) {
  t.awaiting(name);
  t.send("tool_proposed", { name, ...proposed });
  t.send("permission_required", { name, ...permission });
}

/** A to-do list written, as the live path takes it: from the call that landed. */
function writesTodos(t: Turn, todos: { content: string; status: string }[], call_id?: string) {
  const id = call_id ? { call_id } : {};
  ran(t, "todo_write", { todos }, { ...id, result_preview: "plan updated" }, id);
}

/** The terminal card the engine sends with a command, before anything has run. */
const terminalCard = (command: string, description: string) =>
  ({ summary: description, kind: "execute", card: { card: "terminal", title: command, description } });
const RUN_REASON = "Reeve wants to run a command.";

/** A delegated child's arguments. */
const child = (label: string, subagent_type: string) => ({ description: label, prompt: label, subagent_type });
/** What a finished child's call returns. */
const childResult = (id: string, text: string) => `<task id="${id}" state="completed">\n<task_result>\n${text}\n</task_result>\n</task>`;
/** A child session, announced on the app-wide stream as the manager does, and busy. */
function spawned(t: Turn, session_id: string, title: string, agent: string) {
  t.appSend({ type: "session_created", data: { session_id, parent_session: t.sid, title, agent, model: MODEL, kind: "subagent" } });
  t.appSend({ type: "session_status", data: { session_id, status: "busy" } });
}
const childIdle = (t: Turn, session_id: string) => t.appSend({ type: "session_status", data: { session_id, status: "idle" } });
const later = (ms: number, then: () => void) => void setTimeout(then, ms);
const nowSeconds = () => Date.now() / 1000;

/** A goal the bar shows, as the ledger keeps it. */
const goal = (goal_id: string, objective: string, rounds_started: number, extra: Record<string, unknown> = {}) =>
  ({ goal_id, objective, phase: "active", activation: "armed", rounds_started, max_rounds: 256, revision: 1, note: "", blocker: null, ...extra });

/** Reads that each finish at once, `looks` so the row folds them together. */
function reads(t: Turn, prefix: string, count: number, between?: (n: number) => string) {
  for (let n = 0; n < count; n++) {
    if (between) t.send("assistant_message", { text: between(n) });
    const call_id = `${prefix}-${n}`;
    ran(t, "read_file", { path: `src/part_${n}.ts` }, { call_id, result_preview: "ok", looks: true }, { call_id, looks: true });
  }
}

const TURNS: Array<[RegExp, (t: Turn) => void]> = [
  // Edits through the file tools in a folder that is now a repository: Review lists the changes
  // and the source control, each edit with its undo copy, and the receipt names the two files,
  // the check that ran and a note the agent wrote for itself, which is not one of the person's.
  [/edit the upload client/i, (t) => {
    Object.assign(t.s.scmState, {
      git: true,
      files: [
        { path: "notes.md", status: "M", add: 1, del: 0, staged: false },
        { path: "test_upload.py", status: "A", add: 2, del: 0, staged: false },
        { path: "settings.toml", status: "M", add: 2, del: 2, staged: true },
      ],
      commits: [
        { hash: "c2".padEnd(40, "0"), short: "c2e1a9f", author: "Tester", when: "2 hours ago", subject: "Add the upload client", refs: ["HEAD -> main", "origin/main"] },
        { hash: "c1".padEnd(40, "0"), short: "b7d03aa", author: "Tester", when: "yesterday", subject: "Initial commit", refs: [] },
      ],
    });
    const edits = [
      ["edit-up", "notes.md", "hello from the explorer\n", "hello from the explorer\nretry uploads up to three times\n"],
      ["edit-test", "test_upload.py", null, "def test_retry():\n    assert retry(3) == 3\n"],
    ] as const;
    for (const [call_id, name, old_text, new_text] of edits) {
      const path = `${ROOT}/${name}`;
      const presentation = { summary: name, kind: "edit", card: { card: "diff", diffs: [{ path, old_text, new_text }] } };
      t.send("tool_proposed", { name: "replace_in_file", arguments: { path }, call_id, presentation });
      t.send("tool_finished", { name: "replace_in_file", call_id, status: "ok", undoable: true, result_preview: "edited", presentation });
    }
    t.send("assistant_message", { text: "Added retry and a test." });
    const note = "/Users/test/.reeve/memory/PROGRESS.md";
    t.send("turn_end", {
      status: "completed",
      changes: {
        created: [`${ROOT}/test_upload.py`],
        overwritten: [`${ROOT}/notes.md`, note],
        notes: [note],
        stats: [
          { path: `${ROOT}/notes.md`, added: 1, removed: 0, call_id: "edit-up", undoable: true },
          { path: `${ROOT}/test_upload.py`, added: 2, removed: 0, call_id: "edit-test", undoable: true },
        ],
        check: { command: "pytest -q", ok: true, summary: "3 passed", edits_since: 0, timed_out: false },
      },
    });
    t.send("turn_done");
  }],
  // A command that needs approval, described as the person reads it.
  [/run a tool/i, (t) => {
    const args = { command: "npm test" };
    asks(t, "run_shell", { arguments: args, call_id: "shell-1", presentation: terminalCard("npm test", "Run the tests") }, { arguments: args, reason: RUN_REASON });
  }],
  // A goal the ledger sees stall: spending against its budget, two rounds that moved
  // nothing. Matched before the plain goal below, whose words it contains.
  [/start a stalling goal/i, (t) => {
    const check = "python -m pytest -q tests/integration";
    t.s.goalState.goal = goal("goal-2", "Get the flaky integration suite green on the release branch", 6, {
      checks: [check], check_status: [{ command: check, state: "failed" }],
      token_budget: 5_000_000, tokens_used: 3_420_000, stalled_rounds: 2,
    });
    t.send("goal", { ...t.s.goalState.goal, driver: false });
    answer(t, "Still red; trying another approach.");
  }],
  // A goal armed during the turn, with the driver's own frame: the bar has to appear without a
  // reload, and its buttons then go through the goal route.
  [/start a goal/i, (t) => {
    t.s.goalState.goal = goal("goal-1", "Ship the launch note", 2);
    t.send("goal", { ...t.s.goalState.goal, driver: true, text: "Goal armed: Ship the launch note" });
    answer(t, "Goal armed — I'll keep going between turns.");
  }],
  // Turns that write a to-do list. A list stands until the model writes another (src/todoPlan.ts).
  [/make a checklist/i, (t) => {
    writesTodos(t, [{ content: "Read the build log", status: "done" }, { content: "Patch the flaky test", status: "done" }]);
    answer(t, "Both steps done.");
  }],
  [/plan the release note/i, (t) => {
    writesTodos(t, [{ content: "Draft the release note", status: "in_progress" }, { content: "Ask for review", status: "pending" }]);
    answer(t, "Drafting it now.");
  }],
  // An answer that links its deliverable as an artifact chip.
  [/show the report/i, (t) => answer(t, "Done — [Security review](artifact:reports/security-review.html)")],
  // Deliverables declared with `present` (2026-10-03): the row under the turn is built from the
  // call, and the backticked names in the answer resolve against the same files.
  [/deliver the report/i, (t) => {
    const files = [{ path: "reports/security-review.html", description: "Báo cáo an ninh, bản đầy đủ" }, { path: "reports/q3.xlsx" }];
    ran(t, "present", { files }, {
      call_id: "present-1", result_preview: "Presented reports/security-review.html",
      presentation: { summary: "Đã giao 2 tệp", kind: "write", card: { card: "files", files } },
    });
    answer(t, "Xong — báo cáo nằm ở `security-review.html` và bảng số ở `q3.xlsx`.");
  }],
  // A background job started some steps into the turn, as a real one is. The client learns of it
  // from the pushed `jobs` frame alone: its read when the socket opened found none.
  [/start a background job/i, (t) => {
    const now = Math.floor(Date.now() / 1000);
    const roster = [
      {
        id: "bg-1", command: "npm run build", cwd: "D:\\Project\\reeve\\surfaces\\gui", shell: "pwsh", status: "running",
        started_at: now - 12, exit_code: null, output_chars: JOB_ANCHOR_TOTAL, last_line: "\u001b[32m✓\u001b[0m 145 modules transformed.",
      },
      {
        id: "bg-0", command: "npm test", shell: "pwsh", status: "finished", started_at: now - 43, ended_at: now - 12,
        exit_code: 0, duration_ms: 31_000, output_chars: 2_310, last_line: "Tests  1653 passed (1653)",
      },
    ];
    t.send("tool_proposed", { name: "run_shell", arguments: { command: "npm run build", run_in_background: true }, call_id: "bg-shell" });
    fixtureJobs.set(t.page, roster);
    t.send("jobs", { jobs: roster });
    t.send("tool_finished", {
      name: "run_shell", status: "ok", call_id: "bg-shell", result_preview: '{"task_id": "bg-1", "status": "running"}',
      presentation: { summary: "Started in the background: npm run build", kind: "execute" },
    });
    t.send("assistant_message", { text: "Build đang chạy nền — bảng Jobs ở ngay trên ô nhập." });
    // What the job prints, in two frames counted on from the anchor the tail route serves (ADR
    // 0096; a frame before the anchor would rightly be cut as text already had). The second skips
    // 300 characters, so a spec sees the gap said, not closed over.
    const first = "[build] vite building the client bundle…\n";
    const last = "✓ built in 4.31s\n";
    t.send("job_output", { task_id: "bg-1", chunk: first, total: JOB_ANCHOR_TOTAL + first.length });
    t.send("job_output", { task_id: "bg-1", chunk: last, total: JOB_ANCHOR_TOTAL + first.length + 300 + last.length });
    // The roster moves with it: the row's line changes whether or not anyone opened the job.
    const printed = [{ ...roster[0], last_line: "✓ built in 4.31s" }, roster[1]];
    fixtureJobs.set(t.page, printed);
    t.send("jobs", { jobs: printed });
    t.send("turn_done");
  }],
  // A tool asked for in the old shape, with no installable, version or summary (seen 2026-08-14):
  // it must read as not installable, never as a guessed offer to install.
  [/request an unpinned tool/i, (t) => t.send("tool_requested", { name: "somescanner", reason: "scan the Terraform for misconfigurations" })],
  // A scanner the check needs and is missing, asked for instead of the check being skipped.
  [/scan for secrets/i, (t) => t.send("tool_requested", {
    name: "gitleaks", reason: "scan the git history for committed secrets", installable: true, version: "8.30.1",
    summary: "scans git history and the working tree for committed secrets", source: "github.com/gitleaks",
  })],
  // A command that fails once approved: its row opens by itself, with the failure's own text.
  [/fail a tool/i, (t) => {
    const args = { command: "pytest -q" };
    asks(t, "run_shell", { arguments: args, presentation: terminalCard("pytest -q", "Run the tests") }, { arguments: args, reason: RUN_REASON });
    t.awaiting("run_shell", { fails: true });
  }],
  // A routine write inside the folder, its content in the arguments: the compact row.
  [/write a file/i, (t) => {
    const args = { path: "src/fetch_data.py", content: "import json\nimport urllib.request\n\ncompanies = [\"NVDA\", \"AMD\"]\nprint(len(companies))\ndone = True" };
    asks(t, "write_file", { arguments: args }, { arguments: args, reason: "" });
  }],
  // A one-paragraph digest with no line breaks, the shape that once stretched the card to the
  // transcript's height (2026-07-15). Matched before the short digest.
  [/post the long digest/i, (t) => {
    const paragraph = "aisuite — last 24 hours of work (through Jul 15): 5 PRs merged covering chat-completion streaming with unified chunks across providers, multimodal input conversion, Slack collaboration improvements, human attribution for outbound posts, and repo-wide formatting. ";
    const args = { target: "slack:T1/C1", text: paragraph.repeat(6) };
    asks(t, "send_message", { arguments: args }, { arguments: args, reason: "", category: "messaging" });
  }],
  // A write a standing approval can cover: the event names the target to pin, as the
  // engine works it out.
  [/post the digest/i, (t) => {
    const args = { target: "slack:T1/C1", text: "Weekly digest ready" };
    asks(t, "send_message", { arguments: args }, { arguments: args, reason: "", category: "messaging", standing_target: "slack:T1/C1" });
  }],
  // A delegated run whose child is a real session: announced on the app-wide stream,
  // working on the parent's socket as `tool_metadata`, and settled with its id and steps.
  [/delegate the research/i, (t) => {
    t.send("tool_proposed", { name: "task", arguments: child("Research repo subagents", "explore"), call_id: "sub-1" });
    spawned(t, "explore-2", "Research repo subagents (@explore subagent)", "explore");
    t.send("tool_metadata", { call_id: "sub-1", metadata: { session_id: "explore-2", toolcalls: 0 } });
    later(120, () => t.send("tool_metadata", {
      call_id: "sub-1", metadata: { session_id: "explore-2", tool: "grep", title: "max_iterations", toolcalls: 1, phase: "start" },
    }));
    later(250, () => {
      t.send("tool_finished", {
        name: "task", status: "ok", call_id: "sub-1",
        result_preview: childResult("explore-2", "Key file map: task.ts, subagent-permissions.ts, max-steps.ts"),
        presentation: { summary: "Explore finished", run_id: "explore-2", run_steps: 3 },
      });
      childIdle(t, "explore-2");
      answer(t, "The explorer reported back — see the run.");
    });
  }],
  // One child finished, and one between its tools that stays there, thinking after a paid first
  // step. It never settles, so a spec reads the running row with no timer to race.
  [/delegate a long think/i, (t) => {
    t.send("tool_proposed", { name: "task", arguments: child("Quick look", "explore"), call_id: "sub-q" });
    t.send("tool_metadata", { call_id: "sub-q", metadata: { session_id: "quick-1", toolcalls: 2, ended_at: nowSeconds(), duration_ms: 1_900 } });
    t.send("tool_finished", {
      name: "task", status: "ok", call_id: "sub-q", result_preview: '{"task_id": "quick-1", "status": "completed", "tokens": 1800, "steps": 2}',
      presentation: { summary: "Explore finished", run_id: "quick-1", run_steps: 2 },
    });
    t.send("tool_proposed", { name: "task", arguments: child("Deep dive", "explore"), call_id: "sub-d" });
    for (const metadata of [
      { session_id: "deep-1", toolcalls: 0, started_at: nowSeconds() - 75 },
      { session_id: "deep-1", tool: "grep", title: "cache", toolcalls: 1, phase: "start" },
      { session_id: "deep-1", tokens: 4_200 },
      { session_id: "deep-1", phase: "thinking" },
    ]) t.send("tool_metadata", { call_id: "sub-d", metadata });
  }],
  // More children than `subagent_max_parallel` allows: the first runs, the second is
  // queued as a background row whose call returns at once, and its child appears when a slot frees.
  [/delegate past the cap/i, (t) => {
    const flash = "deepseek:deepseek-flash";
    t.send("tool_proposed", { name: "task", arguments: child("Rewrite docs/API.md", "general"), call_id: "sub-run" });
    t.send("tool_metadata", { call_id: "sub-run", metadata: { session_id: "run-1", toolcalls: 0, model: flash, started_at: nowSeconds() } });
    t.send("tool_metadata", { call_id: "sub-run", metadata: { session_id: "run-1", phase: "thinking" } });
    t.send("tool_proposed", { name: "task", arguments: child("Rewrite docs/DEPLOYMENT.md", "general"), call_id: "sub-line" });
    t.send("tool_metadata", { call_id: "sub-line", metadata: { waiting: { running: 4, limit: 4 }, background: true } });
    t.send("tool_finished", {
      name: "task", status: "ok", call_id: "sub-line", result_preview: '{"task_id": "", "status": "queued", "report": "This task is queued"}',
      presentation: { summary: "General queued — starts when a slot frees" },
    });
    later(1_500, () => t.send("tool_metadata", {
      call_id: "sub-line", metadata: { session_id: "line-1", toolcalls: 0, model: flash, started_at: nowSeconds(), background: true, job_id: "line-1" },
    }));
  }],
  // Two children from one message. Each is announced at once, since its id is what lets its node
  // open while it works, and they finish at different times so the card is seen half done. A
  // child's settling frame comes before its result, as the manager's watcher sends them.
  [/delegate two probes/i, (t) => {
    const probes = [
      { call: "sub-a", id: "probe-a", name: "Probe A", word: "alpha", model: "deepseek:deepseek-flash", firstAt: 120, doneAt: 900, toolcalls: 2, ms: 2_400 },
      { call: "sub-b", id: "probe-b", name: "Probe B", word: "beta", model: "zai-coding-plan:glm-5.3-flash", firstAt: 200, doneAt: 2_600, toolcalls: 4, ms: 3_100 },
    ];
    for (const p of probes) t.send("tool_proposed", { name: "task", arguments: child(p.name, "explore"), call_id: p.call });
    for (const p of probes) spawned(t, p.id, `${p.name} (@explore subagent)`, "explore");
    for (const p of probes) t.send("tool_metadata", { call_id: p.call, metadata: { session_id: p.id, model: p.model, toolcalls: 0 } });
    for (const p of probes) {
      later(p.firstAt, () => t.send("tool_metadata", { call_id: p.call, metadata: { session_id: p.id, tool: "grep", title: p.word, toolcalls: 1, phase: "start" } }));
    }
    probes.forEach((p, i) => later(p.doneAt, () => {
      t.send("tool_metadata", { call_id: p.call, metadata: { session_id: p.id, toolcalls: p.toolcalls, ended_at: nowSeconds(), duration_ms: p.ms } });
      t.send("tool_finished", {
        name: "task", status: "ok", call_id: p.call, result_preview: childResult(p.id, `${p.name} done — ${p.word}`),
        presentation: { summary: `${p.name} finished`, run_id: p.id, run_steps: p.toolcalls },
      });
      childIdle(t, p.id);
      if (i === probes.length - 1) answer(t, "Both probes reported back — see the runs.");
    }));
  }],
  // An automation's consent: the create call carries the permissions it would hold, and
  // the approval card lists them.
  [/create an automation/i, (t) => asks(t, "create_scheduled_task", { arguments: {} }, {
    arguments: {
      title: "Weekly digest", instructions: "Summarize the week and post it.", cron: "0 9 * * 1",
      permissions: [
        { tool: "send_message", target: "slack:T1/C1", access: "write" },
        { tool: "github_list_commits", target: "acme/agent-platform", access: "read" },
      ],
    },
    reason: "", category: "automation",
  })],
  // A reasoning model: the trace arrives a piece at a time, then the answer carries all of it.
  [/think hard/i, (t) => {
    const thoughts = ["Weighing options. ", "Comparing tradeoffs. ", "Settling it. "];
    let tick = 0;
    const timer = setInterval(() => {
      if (tick < thoughts.length) {
        t.send("reasoning_delta", { text: thoughts[tick++] });
        return;
      }
      clearInterval(timer);
      t.send("assistant_delta", { text: "Decision made." });
      answer(t, "Decision made.", { reasoning: thoughts.join("") });
    }, 120);
  }],
  // Compaction during a turn: `compacting` while it summarizes, then the marker with the
  // engine's numbers (`now_tokens` moves the usage meter with no new turn), and the turn goes on.
  [/compact the context/i, (t) => {
    t.send("compacting", {});
    later(400, () => {
      t.send("compacted", {
        text: "Context compacted — earlier turns were summarized", reason: "summarized", saved_tokens: 12_500, now_tokens: 30_000,
        summary: "Objective: keep the launch note moving.\nNext: draft the follow-up.",
      });
      answer(t, "Still on it — continuing where I left off.");
    });
  }],
  // A provider error ends the turn; a `retry` frame recovers it.
  [/fail the turn/i, (t) => {
    t.send("error", { error: "model unreachable" });
    t.send("turn_done");
  }],
  // A question while a to-do list is up: its card takes the composer's place (the list steps
  // aside), and the `question_response` finishes the turn.
  [/ask me which first/i, (t) => {
    writesTodos(t, [
      { content: "Collect the evidence", status: "done" },
      { content: "Backend gap report", status: "in_progress" },
      { content: "Frontend gap report", status: "pending" },
      { content: "Rank by cost", status: "pending" },
    ], "aq-todo");
    t.send("assistant_message", { text: "Four candidates. Which one first?" });
    t.send("tool_proposed", { name: "ask_user", arguments: { question: "Where do we start?" }, call_id: "aq-ask" });
    t.send("question_requested", {
      question: "Where do we start?", header: "Priority", allow_text: true,
      options: [
        { label: "#1 Goal ↔ account", description: "Close the invariant hole: a goal backed by an account.", recommended: true },
        { label: "#2 Report by tag", description: "Spending grouped by tag. No new MCP tool.", recommended: true },
        { label: "#4 Merchants", description: "Autofill the merchant on new transactions." },
        { label: "#3 Alerts", description: "Budget and bill reminders. The most expensive one." },
      ],
    });
    t.awaiting("ask_user");
  }],
  // Plans put up for approval: the plan is the turn's `propose_plan` step, its decision the bar in
  // the composer's place. One with no heading is named by its first step, a whole sentence (the
  // owner's screenshot of 2026-10-15 had the question squeezed to a word a line).
  [/propose an unheaded plan/i, (t) => proposes(t, "pp-unheaded", [
    "1. Review the existing provider and sign-in code, the GUI conventions and the related decisions; check how the source repository handles its own sign-in and licence notes.",
    "2. Design the change so the current flow keeps working.",
    "3. Build it.",
    "4. Add tests for both paths.",
  ])],
  [/propose a plan/i, (t) => proposes(t, "pp-plan", [
    "# Goal contributions back the figure",
    "",
    "## Steps",
    ...Array.from({ length: 14 }, (_, n) => `${n + 1}. Step ${n + 1}: change one thing and pin it with a test.`),
    "",
    "## Risks",
    "- The migration rewrites every goal row.",
    "- Old clients still send `current_amount`.",
  ])],
  // A long session's reading: 222k in context, compacting at 380k (the server's `compact_at`).
  [/show the context gauge/i, (t) => answer(t, "Here is where the context stands.", {
    usage: { model: MODEL, input: 2_000, output: 1_000, cache_read: 219_000, cache_write: 0 },
    compact_at: 380_000,
  })],
  // A view that grows with no new transcript item: a command left running (its row opens after
  // half a second) and a to-do list that brings up its dock, then a line of trace and silence.
  // The reads have words between them, so they stay separate steps (bare ones fold into one row)
  // and the turn is taller than the view.
  [/run the build job/i, (t) => {
    reads(t, "bj-read", 14, (n) => `Part ${n} checks out; moving to the next one.`);
    t.send("tool_proposed", { name: "run_shell", arguments: { command: "npm run build", description: "Build the app" }, call_id: "bj-shell" });
    later(900, () => writesTodos(t, [
      { content: "Build the app", status: "in_progress" },
      { content: "Check the drawer on mobile", status: "pending" },
      { content: "Report back", status: "pending" },
    ], "bj-todo"));
    t.send("reasoning_delta", { text: "Waiting on the build." });
  }],
  // A turn that never ends by itself: two calls left running as its last step, and trace that
  // keeps coming on the status line. The side panel's spec reads it.
  [/run the long job/i, (t) => {
    reads(t, "lj-read", 24);
    t.send("assistant_message", { text: "Everything is read — building it now." });
    t.send("tool_proposed", { name: "run_shell", arguments: { command: "npm run build" }, call_id: "lj-shell" });
    t.send("tool_proposed", { name: "shell_task_output", arguments: { task_id: "build-1" }, call_id: "lj-poll" });
    let pass = 0;
    t.streaming(setInterval(() => t.send("reasoning_delta", { text: `Watching the build, pass ${++pass}. ` }), 150));
  }],
  // A stream of several seconds (40 ticks of 120ms), long enough to scroll, pin and interrupt in.
  [/stream the epic/i, (t) => {
    const line = "The epic scrolls ever onward, line upon line upon line. ";
    let ticks = 0;
    const timer = setInterval(() => {
      t.send("assistant_delta", { text: line.repeat(3) + "\n\n" });
      if (++ticks < 40) return;
      clearInterval(timer);
      t.streaming(null);
      answer(t, ("The epic concludes. " + line).repeat(20));
    }, 120);
    t.streaming(timer);
  }],
];

function proposes(t: Turn, call_id: string, lines: string[]) {
  const plan = lines.join("\n");
  t.send("tool_proposed", { name: "propose_plan", arguments: { plan }, call_id });
  t.send("plan_proposed", { plan });
  t.awaiting("propose_plan");
}

/** Play the turn a message asks for, or echo it. The echo names the model and the skill the
 *  message carried, each as its own field, and fixed usage numbers the usage chip adds up. */
export function playTurn(t: Turn) {
  const turn = TURNS.find(([pattern]) => pattern.test(t.text));
  if (turn) return turn[1](t);
  const { msg } = t;
  t.send("assistant_delta", { text: "Echo: " });
  t.send("assistant_delta", { text: msg.text });
  answer(t, `Echo: ${msg.text} [model=${msg.model || "none"}]${msg.skill ? ` [skill=${msg.skill}]` : ""}`, {
    usage: { model: msg.model || MODEL, input: 1_000, output: 200, cache_read: 8_000, cache_write: 800 },
  });
}
