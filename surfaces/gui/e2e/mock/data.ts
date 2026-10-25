import { readFileSync } from "node:fs";
import { join } from "node:path";

// The mock's fixed data, in the shapes the real server answers with. Each page gets its own copy
// to change (state.ts); nothing here is written to.

/** The real door's `DESIGN_BRIDGE` (harness/interface/server/preview_design.py), read from its
 *  source so the copy the mock serves is always the same script. */
export function designBridge(): string {
  // Playwright runs in surfaces/gui, and the package is ESM: there is no `__dirname` to start from.
  const source = readFileSync(join(process.cwd(), "..", "..", "harness", "interface", "server", "preview_design.py"), "utf-8");
  const open = 'DESIGN_BRIDGE = r"""';
  const rest = source.slice(source.indexOf(open) + open.length);
  return rest.slice(0, rest.indexOf('"""'));
}

const MODEL = "anthropic:claude-opus-4-8";
const nowSeconds = Math.floor(Date.now() / 1000);

/** A time on the day the suite runs, as the server writes it (naive local). The queue folds
 *  anything older than today into Earlier, so the sessions are stamped today. */
export function today(hour: number, minute = 0): string {
  const d = new Date();
  const two = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(hour)}:${two(minute)}:00`;
}

/** The last part of a path. */
export const baseName = (path: string) => path.split("/").filter(Boolean).pop() || path;

export const HEALTH = { status: "ok", default_workspace: null, model: MODEL };

export const SETTINGS = {
  provider: "openai", model: MODEL, models: [MODEL, "gpt-5.5", "gpt-4o", "gpt-4o-mini", "o3-mini"],
  has_key: true, model_ready: true, source: "store", onboarded: true, experimental_connectors: false,
  scratch_base: "~/Reeve", secrets_path: "/Users/test/.config/Reeve/secrets.json", sessions_peek: 5,
  // Automatic shell, on a machine that has Windows PowerShell 5.1 and Git Bash.
  shell: "",
  shells: [{ kind: "powershell", label: "PowerShell 5.1" }, { kind: "bash", label: "Git Bash" }],
  // PDFs over two pages are read as text: the composer's limit test can then use a small file.
  // The product's own default is 20.
  pdf_fallback: "text", pdf_max_pages: 2, pdf_max_mb: 10,
  // Part of the server's lists: the names the pickers show, and the window the usage chip fills.
  model_labels: {
    [MODEL]: "Claude Opus 4.8 · Anthropic",
    "zai:glm-5.2": "GLM-5.2 · Z AI",
    "ark:dola-seed-evolving-latest-version": "Dola Seed Evolving · BytePlus Ark",
    "ark:dola-seed-2-1-turbo-260628": "Dola Seed 2.1 Turbo · BytePlus Ark",
    "ark-agent-plan-cn:doubao-seed-evolving": "Doubao Seed Evolving · Volcengine Agent Plan",
    "ark-agent-plan-cn:doubao-seed-2.1-turbo": "Doubao Seed 2.1 Turbo · Volcengine Agent Plan",
  },
  model_context_windows: { [MODEL]: 200_000 },
};

/** A row of the session list: an idle, unpinned session unless `row` says otherwise. */
function sessionRow<T extends { session_id: string; title: string; updated_at: string; messages: number }>(row: T) {
  return {
    workspace: "", agent: "reeve", model: MODEL, mode: "interactive",
    pinned: false, archived: false, attention: 0, liveness: "idle", subscriptions: [] as unknown[],
    ...row,
  };
}

// The sessions are stamped newest first in this order, so the one a fresh start resumes (the most
// recently updated) is always the pinned one, which specs open by its title.
export const PINNED_SESSION = sessionRow({
  session_id: "pinned-session-1", title: "Draft the launch note", workspace: "/Users/test/Reeve/launch-note",
  updated_at: today(9), messages: 2, pinned: true,
});
// Seven more: past the sidebar's peek of five, so "Show N more" appears. The third waits on the
// approval in the inbox below, and its row says so.
export const EXTRA_SESSIONS = Array.from({ length: 7 }, (_, i) => {
  const waiting = i === 2;
  return sessionRow({
    session_id: `wp-${i + 1}`, title: `Weekly plan ${i + 1}`, updated_at: today(8, 50 - i * 5), messages: 3,
    attention: waiting ? 1 : 0, attention_kind: waiting ? "approval" : null,
  });
});
// Recorded under a persona that is gone: still an ordinary row of the list.
export const OPS_SESSION = sessionRow({
  session_id: "ops-1", title: "Ops triage", workspace: "/Users/test/Reeve/ops-triage", agent: "ops",
  updated_at: today(7), messages: 4,
});
// Started by a mention in Slack: it sits in the folded "From Slack" group, not among the recent.
export const SLACK_SESSION = sessionRow({
  session_id: "slack-thread-1", title: "#general — check the deploy?", updated_at: today(6), messages: 2,
  origin: "slack", origin_label: "#general · T0AB",
});

/** A connector's form field: secret and required unless `extra` says otherwise. */
function field<T extends Record<string, unknown>>(key: string, label: string, extra: T = {} as T) {
  return { key, label, secret: true, required: true, help: "", placeholder: "", ...extra };
}
const OAUTH_TOKEN = field("access_token", "OAuth access token");
/** A catalog entry: available, not connected, no form and no tools unless `entry` says otherwise. */
function catalogEntry<T extends Record<string, unknown>>(entry: T) {
  return {
    two_way: false, channels: false, available: true, logo: "", fields: [] as unknown[], instructions: [] as string[],
    connected: false, account: null, enabled: false, allowed_users: [] as unknown[], tools: [] as unknown[],
    ...entry,
  };
}
const tool = (name: string, label: string, kind: "read" | "write", description: string) =>
  ({ name, label, kind, description, enabled: true, requires_approval: kind === "write" });

export const CONNECTORS = {
  connectors: [
    catalogEntry({ name: "browser", title: "Browser", icon: "B", blurb: "Headless browser.", auth: "none", brand_color: "#6b7280", connected: true, enabled: true }),
    catalogEntry({
      name: "telegram", title: "Telegram", icon: "T", blurb: "Two-way Telegram messaging.", auth: "bot_token", two_way: true, channels: true,
      brand_color: "#229ed9", logo: "telegram", fields: [field("bot_token", "Bot token", { placeholder: "123456:ABC…" })],
    }),
    // Google sign-in in one click, or a pasted token. Its about, access and tools are what the
    // page before connecting shows (available-detail.spec.ts).
    catalogEntry({
      name: "gmail", title: "Gmail", icon: "✉", one_click: true, blurb: "Search, summarize, draft, and send email.",
      about: "Search, summarize, and send over your Gmail.",
      access: ["Reads and searches your mail.", "Sends email as you.", "Never deletes mail or changes account settings."],
      auth: "oauth", brand_color: "#ea4335", logo: "gmail", fields: [OAUTH_TOKEN],
      tools: [tool("gmail_search", "Search mail", "read", "Search messages."), tool("gmail_send", "Send email", "write", "Send a message.")],
    }),
    catalogEntry({
      name: "google_calendar", title: "Google Calendar", icon: "◷", one_click: true, blurb: "Read availability, summarize schedules, and create events.",
      auth: "oauth", brand_color: "#4285f4", logo: "google_calendar", fields: [OAUTH_TOKEN],
    }),
    // Accounts of their own (more than one may be connected): Notion by sign-in or its secret,
    // Outlook keyed by email and connected with a pasted token.
    catalogEntry({
      name: "notion", title: "Notion", icon: "◰", one_click: true, blurb: "Search pages, read content, query databases, create pages.",
      auth: "oauth", brand_color: "#1f2328", fields: [field("access_token", "Integration secret", { placeholder: "ntn_…" })],
    }),
    catalogEntry({
      name: "outlook", title: "Outlook", icon: "◎",
      blurb: "Microsoft 365 mail and calendar: search, draft, and send email; manage events and respond to invites.",
      aliases: ["calendar", "email", "mail", "microsoft", "office"], auth: "oauth", brand_color: "#0078d4", logo: "outlook", fields: [OAUTH_TOKEN],
    }),
    catalogEntry({
      name: "attio", title: "Attio", icon: "▣", blurb: "Search and read Attio CRM records; log notes.",
      auth: "oauth", brand_color: "#2d6ae0", logo: "attio", fields: [OAUTH_TOKEN],
    }),
    // On the vendor's MCP server, signed in locally, with a fixed set of its tools. monday has
    // only the one-click path; Jira also takes a token, so its dialog has two ways in.
    catalogEntry({
      name: "monday", title: "monday.com", icon: "▦", blurb: "Read boards and items, track work, create items and post updates.",
      aliases: ["project management", "tasks", "boards"], auth: "oauth", brand_color: "#6161ff", logo: "monday", mcp: true, one_click: true,
      instructions: ["One click connects via monday.com sign-in in your browser.", "Sign-in is fully local — tokens stay on this computer."],
      tools: [
        tool("mcp__monday__get_board_info", "Read board", "read", "Read a board's columns and groups."),
        tool("mcp__monday__create_item", "Create item", "write", "Create an item on a board."),
      ],
    }),
    catalogEntry({
      name: "jira", title: "Jira", icon: "◆", blurb: "Search, summarize, create, and update issues.",
      aliases: ["issues", "tickets", "atlassian"], auth: "api_token", brand_color: "#0052cc", logo: "jira", mcp: true, one_click: true,
      fields: [
        field("base_url", "Atlassian site URL", { secret: false }),
        field("email", "Account email", { secret: false }),
        field("api_token", "API token"),
      ],
    }),
  ],
};

/** A pending inbox item, with the context the server joins onto it. */
function waitingItem<T extends Record<string, unknown>>(item: T) {
  return { body: "", state: "pending", resolution: null, inbox: "default", resolved_at: null, session_workspace: "", session_exists: true, ...item };
}
// Two items from two personas, so the kind tabs and the persona chips (shown from two personas
// on) both appear. The question's session is left out of the session list on purpose: an item
// carries its own context.
export const INBOX_ITEMS = [
  waitingItem({
    id: "inb-approval-1", session_id: "wp-3", kind: "approval", title: "Approve: run_shell", body: "rm -rf build/",
    created_at: "2026-07-01 08:00:00", session_title: "Weekly plan 3", session_agent: "reeve",
  }),
  waitingItem({
    id: "inb-question-1", session_id: "ops-1", kind: "question", title: "Which environment should I restart?",
    options: ["staging", "production"], allow_text: true, multi: false,
    created_at: "2026-07-01 08:05:00", session_title: "Investigate alerts", session_agent: "ops",
  }),
];

// GET /v1/metrics. The failed turn is the pinned session's, so "Open trace" lands on a real row.
export const OPS_METRICS = {
  range: "7d",
  backfilling: false,
  headline: {
    sessions: 3, turns_started: 20, turns_completed: 19, turns_failed: 1, completion: 0.95,
    tool_calls: 100, tool_ok: 96, tool_success: 0.96, tool_errors: 3, tool_timeouts: 1, blocked: 2,
    llm_calls: 40, retries: 2, retry_rate: 0.05, model_errors: 1,
    tokens_in: 1_000_000, tokens_out: 200_000, cache_read: 3_000_000, cache_write: 0, tokens_total: 4_200_000,
    cache_served: 3_000_000, cache_hit: 0.75, tokens_per_turn: 221052.6,
    ttft_avg_ms: 1240, ttft_p95_ms: 4800, steps: 120, steps_per_turn: 6, loop_flags: 1, failed_spend: 4.6, compactions: 2, subagents: 5,
  },
  daily: [
    { day: "2026-09-18", turns: 8, failed: 0, llm_calls: 14, tool_calls: 40, tool_failures: 1, tool_error_rate: 0.025, completion: 1, steps: 48, steps_per_turn: 6, loops: 0, blocked: 0, tokens_in: 400_000, tokens_out: 80_000, cache_read: 1_000_000, cache_write: 0, tokens: 1_480_000, ttft_avg_ms: 1100, ttft_p95_ms: 3900, wall_ms: 600_000 },
    { day: "2026-09-19", turns: 12, failed: 1, llm_calls: 26, tool_calls: 60, tool_failures: 3, tool_error_rate: 0.05, completion: 0.9167, steps: 72, steps_per_turn: 6, loops: 1, blocked: 2, tokens_in: 600_000, tokens_out: 120_000, cache_read: 2_000_000, cache_write: 0, tokens: 2_720_000, ttft_avg_ms: 1400, ttft_p95_ms: 4800, wall_ms: 900_000 },
  ],
  by_model: [
    {
      name: MODEL, sessions: 2, turns: 14, llm_calls: 28, retries: 1, retry_rate: 0.0357,
      tokens: 3_400_000, tokens_in: 1_000_000, cache_read: 2_200_000, cache_write: 0,
      tool_calls: 70, tool_failures: 3, tool_success: 0.9571, cache_hit: 0.6875, steps: 84, steps_per_turn: 6, ttft_avg_ms: 1300,
      harness: { turns: 14, turns_failed: 1, completion: 0.9286, tool_success: 0.9571, steps_per_turn: 6, tokens_per_turn: 242857, ttft_p95_ms: 4800 },
    },
  ],
  ships: [],
  review: {
    at: nowSeconds - 3600, days: 30, running: false,
    shapes: [{ name: "cache-drops", owner: "context/projection", count: 12, per: "steps", of: 120, share: 0.1 }],
    claims: { verdicts: { confirmed: 2, refuted: 0, "too early": 1, "not live": 0 }, refuted: [] },
  },
  tools: [
    { name: "run_shell", calls: 60, errors: 2, timeouts: 1, blocked: 0, p95_ms: 9400 },
    { name: "read_file", calls: 30, errors: 1, timeouts: 0, blocked: 0, p95_ms: 300 },
  ],
  failures: [{ session_id: "pinned-session-1", turn: 14, day: "2026-09-19", agent: "reeve", tool: "run_shell", status: "error", reason: "exit 1: npm test" }],
  notable: [{ day: "2026-09-19", level: "warn", kind: "tool_error_rate", z: 2.1, text: "Tool failure rate 0.05 — 2.1σ above the 6-day baseline." }],
};

// What the session is connected to. GitHub is two-way with no channels (it relays mentions and
// takes no subscriptions), which once wrongly showed a Channels section (owner, 2026-07-13).
export const CONNECTIONS = {
  connected: ["browser", "slack", "github"].map((connector) => ({
    connector, enabled: true, detail: connector === "github" ? "GitHub" : connector[0].toUpperCase() + connector.slice(1),
  })),
  recommended: [{ connector: "gmail", reason: "email context for morning summaries", tier: "core", connected: false }],
  attention: 1,
};

/** One run of an automation. Its session id follows the real `__run__` form, which is how the
 *  session view knows a run's session and shows its banner. */
function automationRun(run_id: string, task_id: string, started_at: number, finished_at: number | null, status: string) {
  return { run_id, task_id, session_id: `__run__${run_id}`, started_at, finished_at, status, result_text: null, artifacts: [], error: null, trigger: "schedule" };
}
// Scheduled, with a run going now, one standing approval for the detail page to revoke,
// and two runs not yet opened, the newer of them failed (the sidebar's marks).
export const AUTOMATION = {
  id: "task-1", title: "Daily AI News",
  instructions: "Fetch the latest AI news and produce an HTML+Tailwind presentation.",
  schedule: "Every day at ~5:40 PM", schedule_raw: { kind: "cron", cron: "40 17 * * *", fire_at: null, timezone: "local" },
  workspace: "", agent: "reeve", enabled: true,
  next_run: nowSeconds + 3600, last_run: nowSeconds - 60, last_status: "running", run_count: 1, notify_on_completion: false,
  always_allowed: [{ entry: "send_message slack:T1/C1", tool: "send_message", target: "slack:T1/C1" }],
  unseen_runs: 2, unseen_failed: true, seen_runs_at: 0,
};
// A quiet one beside it: its last run finished well and nothing is new.
export const AUTOMATION_CLEAN = {
  ...AUTOMATION,
  id: "task-2", title: "Weekly CRM digest", schedule: "Every Monday at ~9:00 AM", last_status: "ok",
  latest_run: automationRun("r0", "task-2", nowSeconds - 7200, nowSeconds - 7000, "ok"),
  unseen_runs: 0, unseen_failed: false, always_allowed: [],
};
export const AUTOMATION_RUNS = [automationRun("r1", "task-1", nowSeconds - 60, null, "running")];

export const PRIMARY_ROOT = { path: "/Users/test/Reeve/launch-note", writable: true, label: "scratch", primary: true, exists: true };

// The child behind the delegated run (`explore-2`): its own saved messages, which the
// child's view draws with the ordinary transcript: the prompt, the work, the answer.
export const CHILD_SESSION_MESSAGES = [
  { role: "user", content: [{ type: "text", text: "Research repo subagents" }], ts: 1 },
  { role: "assistant", content: "Reading the spec.", reasoning: "Start with the task tool." },
  { role: "assistant", content: "", tool_calls: [{ id: "c1", function: { name: "read_file", arguments: '{"path":"harness/tools/subagent_spec.py"}' } }] },
  { role: "tool", tool_call_id: "c1", content: "216 lines", _presentation: { summary: "harness/tools/subagent_spec.py", kind: "read" }, _looks: true },
  { role: "assistant", content: "Key file map: task.ts, subagent-permissions.ts, max-steps.ts" },
];
// The two children of the parallel delegation, each with a transcript of its own: each node has
// to open its own session (the second once had no id yet, and its node did nothing).
const probe = (name: string, word: string) => [
  { role: "user", content: [{ type: "text", text: `Probe ${name}` }], ts: 1 },
  { role: "assistant", content: `Probe ${name} done — ${word}` },
];
export const PROBE_A_MESSAGES = probe("A", "alpha");
export const PROBE_B_MESSAGES = probe("B", "beta");

/** A model provider: not configured and never used unless `row` says otherwise. */
function provider<T extends Record<string, unknown>>(row: T) {
  return { needs_key: true, configured: false, values: {}, key_set_at: null, last_used_at: null, ...row };
}
const endpoint = (help: string, url: string) => field("base_url", "Endpoint", { secret: false, required: false, help, placeholder: url, default: url });
const prefilled = (what: string) => `Uses ${what} — the endpoint is prefilled, just add your key.`;
export const PROVIDERS = [
  // Configured and used: the "Last used" line and its dot.
  provider({
    name: "openai", title: "OpenAI", fields: [field("api_key", "OpenAI API key", { placeholder: "sk-…" })], configured: true,
    suggested_models: ["gpt-5.5"], key_set_at: "2026-06-12", last_used_at: nowSeconds - 7200,
  }),
  // Configured, never used: "Not used yet".
  provider({ name: "anthropic", title: "Claude (Anthropic)", fields: [field("api_key", "API key", { placeholder: "sk-…" })], configured: true, suggested_models: ["claude-opus-4-8"] }),
  // OpenAI-compatible vendors, not configured, each with its endpoint filled in and editable.
  // Ark is two providers: BytePlus pay-as-you-go and Volcengine's Agent Plan (China) keep their
  // own keys, endpoints and model lists.
  provider({
    name: "zai", title: "Z AI (GLM)", blurb: prefilled("Z AI's OpenAI-compatible API"),
    fields: [field("api_key", "Z AI API key"), endpoint("Prefilled with Z AI's international endpoint.", "https://api.z.ai/api/paas/v4")],
    suggested_models: ["glm-5.2"],
  }),
  provider({
    name: "ark", title: "BytePlus Ark", blurb: prefilled("BytePlus Ark's OpenAI-compatible Responses API"),
    fields: [field("api_key", "BytePlus Ark API key"), endpoint("BytePlus Ark's Asia Pacific endpoint.", "https://ark.ap-southeast.bytepluses.com/api/v3")],
    suggested_models: ["dola-seed-evolving-latest-version", "dola-seed-2-1-turbo-260628"],
  }),
  provider({
    name: "ark-agent-plan-cn", title: "Volcengine Ark Agent Plan", blurb: prefilled("Volcengine Ark Agent Plan's OpenAI-compatible Responses API"),
    fields: [field("api_key", "Volcengine Ark Agent Plan API key"), endpoint("Volcengine Ark Agent Plan's China (Beijing) endpoint.", "https://ark.cn-beijing.volces.com/api/plan/v3")],
    suggested_models: ["doubao-seed-evolving", "doubao-seed-2.1-turbo"],
  }),
  // Local and keyless: counted as configured though nothing proves it runs. Onboarding says
  // "No key needed" and asks only for the endpoint, with Detect.
  provider({ name: "ollama", title: "Ollama (local models)", needs_key: false, fields: [endpoint("", "http://127.0.0.1:11434")], configured: true, suggested_models: ["qwen3-coder:30b"] }),
  // A subscription signed into in the browser: no key form, a sign-in state instead. Signed out.
  provider({
    name: "openai-codex", title: "ChatGPT subscription", needs_key: false, auth: "oauth", signed_in: false, account: null, authorizing: false, last_error: null,
    blurb: "Sign in with your ChatGPT plan and run OpenAI models through your subscription — no API key. Tokens stay on this machine.",
    fields: [], suggested_models: ["gpt-5.6-sol"],
  }),
];
