// One place that knows what a tool IS: its icon and the word a person reads for it.
//
// The transcript used to render every step as the same green dot + a sentence, and the
// live view as a bare name — nothing told the eye "this was a search" or "this touched a
// file" the way a coding agent's transcript does. The map covers the whole built-in
// surface (catalog.py) plus the two MCP and unknown fallbacks; a tool that arrives with a
// name nobody mapped still gets a sane icon instead of a hole.

import type { IconName } from "./components/Icon";
import type { ConnectorToolIndex } from "./connectorTools";

export interface ToolMeta {
  icon: IconName;
  /** The noun for the tool family, the way the reference design says "Read" / "Shell" / "Explore". */
  label: string;
  /** The gerund shown while the tool is running ("Editing"). Falls back to `label`. */
  running?: string;
  /** The past tense shown once it has finished ("Edited"), the way a Thought row follows
   *  "Thinking". Falls back to `label`, which stays the noun for the family. */
  done?: string;
}

const BY_EXACT: Record<string, ToolMeta> = {
  run_shell: { icon: "terminal", label: "Shell", running: "Running", done: "Ran" },
  shell_task_output: { icon: "terminal", label: "Shell", running: "Running", done: "Ran" },
  shell_task_kill: { icon: "terminal", label: "Shell", running: "Running", done: "Ran" },
  run_code: { icon: "codeXml", label: "Program", running: "Executing", done: "Executed" },
  grep: { icon: "search", label: "Grep", running: "Searching", done: "Searched" },
  read_file: { icon: "file", label: "Read", running: "Reading", done: "Read" },
  read_image: { icon: "file", label: "Read", running: "Reading", done: "Read" },
  list_files: { icon: "folderTree", label: "List", running: "Listing", done: "Listed" },
  write_file: { icon: "pencilLine", label: "Edit", running: "Editing", done: "Edited" },
  // The read-only git tools, retired for the shell; past sessions still show them.
  git_status: { icon: "branch", label: "Git", running: "Inspecting", done: "Inspected" },
  git_diff: { icon: "gitCompare", label: "Git", running: "Inspecting", done: "Inspected" },
  git_log: { icon: "branch", label: "Git", running: "Inspecting", done: "Inspected" },
  todo_write: { icon: "checklist", label: "Plan", running: "Planning", done: "Planned" },
  // Delegation: one row per child, whatever its agent type — the
  // agent rides in `subagent_type` and the row is the same furniture either way.
  task: { icon: "bot", label: "Task", running: "Delegating", done: "Delegated" },
  // What the parent says to a subagent after its brief; the row's title names whom.
  message_task: { icon: "bot", label: "Message", running: "Sending", done: "Sent" },
  ask_user: { icon: "messageQuestion", label: "Ask", running: "Asking", done: "Asked" },
  request_directory: { icon: "folderPlus", label: "Request", running: "Requesting", done: "Requested" },
  request_tool: { icon: "plug", label: "Request", running: "Requesting", done: "Requested" },
  load_skill: { icon: "book", label: "Skill", running: "Loading", done: "Loaded" },
  search_history: { icon: "history", label: "History", running: "Recalling", done: "Recalled" },
  fold_context: { icon: "fold", label: "Fold", running: "Folding", done: "Folded" },
  show_visual: { icon: "chart", label: "Visual", running: "Drawing", done: "Showed" },
  // Designing a page: the guide is read, the page is checked in Reeve's own browser.
  design_guide: { icon: "book", label: "Design", running: "Reading", done: "Read" },
  check_design: { icon: "monitor", label: "Check", running: "Checking", done: "Checked" },
  compare_design: { icon: "gitCompare", label: "Compare", running: "Comparing", done: "Compared" },
  web_search: { icon: "globe", label: "Search", running: "Searching", done: "Searched" },
  web_fetch: { icon: "link", label: "Fetch", running: "Fetching", done: "Fetched" },
  // Tools that fell through to "Tool" with a wrench (GUI tools review, B4).
  lsp: { icon: "braces", label: "Code intel", running: "Inspecting", done: "Inspected" },
  goal: { icon: "goal", label: "Goal", running: "Updating", done: "Updated" },
  tool_search: { icon: "packageSearch", label: "Tools", running: "Finding", done: "Found" },
  tool_describe: { icon: "packageSearch", label: "Tools", running: "Reading", done: "Read" },
  list_subagent_models: { icon: "bot", label: "Models", running: "Listing", done: "Listed" },
  // The edit family beyond `write_file`: the patch tools render a diff card already, but
  // their ROW still read "Tool" because only `write_file` was mapped. Same family, same word.
  replace_in_file: { icon: "pencilLine", label: "Edit", running: "Editing", done: "Edited" },
  apply_patch: { icon: "pencilLine", label: "Edit", running: "Editing", done: "Edited" },
  apply_unified_diff: { icon: "pencilLine", label: "Edit", running: "Editing", done: "Edited" },
  edit_notebook: { icon: "pencilLine", label: "Edit", running: "Editing", done: "Edited" },
  // Memory: the built-in store the agent reads and writes. One icon, one word, for all five verbs.
  remember: { icon: "notebookPen", label: "Memory", running: "Remembering", done: "Remembered" },
  memory_read: { icon: "notebookPen", label: "Memory", running: "Remembering", done: "Remembered" },
  memory_search: { icon: "notebookPen", label: "Memory", running: "Remembering", done: "Remembered" },
  memory_update: { icon: "notebookPen", label: "Memory", running: "Remembering", done: "Remembered" },
  memory_forget: { icon: "notebookPen", label: "Memory", running: "Remembering", done: "Remembered" },
  save_skill: { icon: "bookPlus", label: "Skill", running: "Saving", done: "Saved" },
  // Scheduling + waking: both are the clock.
  create_scheduled_task: { icon: "calendarClock", label: "Schedule", running: "Scheduling", done: "Scheduled" },
  list_scheduled_tasks: { icon: "calendarClock", label: "Schedule", running: "Scheduling", done: "Scheduled" },
  update_scheduled_task: { icon: "calendarClock", label: "Schedule", running: "Scheduling", done: "Scheduled" },
  delete_scheduled_task: { icon: "calendarClock", label: "Schedule", running: "Scheduling", done: "Scheduled" },
  sleep_until: { icon: "alarmClock", label: "Wake", running: "Waiting", done: "Waited" },
  wake_on: { icon: "alarmClock", label: "Wake", running: "Waiting", done: "Waited" },
  wake_on_event: { icon: "alarmClock", label: "Wake", running: "Waiting", done: "Waited" },
  propose_plan: { icon: "checklist", label: "Plan", running: "Planning", done: "Planned" },
  update_plan: { icon: "checklist", label: "Plan", running: "Planning", done: "Planned" },
  // Outbound + channels.
  send_message: { icon: "send", label: "Send", running: "Sending", done: "Sent" },
  send_file: { icon: "send", label: "Send", running: "Sending", done: "Sent" },
  subscribe_channel: { icon: "hash", label: "Channels", running: "Reading", done: "Read" },
  unsubscribe_channel: { icon: "hash", label: "Channels", running: "Reading", done: "Read" },
  list_subscriptions: { icon: "hash", label: "Channels", running: "Reading", done: "Read" },
  get_channel_messages: { icon: "hash", label: "Channels", running: "Reading", done: "Read" },
};

const TERMINAL: ToolMeta = { icon: "wrench", label: "Shell", running: "Running", done: "Ran" };

/**
 * The tools whose generic row is suppressed once their OWN card is on screen.
 *
 * An interactive tool emits both a dedicated event (the question or folder card) AND the
 * ordinary `tool_proposed` row, so the transcript drew the same moment twice. `todo_write`
 * and `update_plan` are the same story one surface over: the plan lives in the To-dos dock
 * and the plan file, not as a step a reader watches.
 *
 * `propose_plan` is deliberately NOT here: its PLAN CONTENT is a step a reader watches — it
 * renders as a `Plan` card inside the turn (see the registry's plan builder) — and only its
 * DECISION moved to the composer dock. So the row shows, the content shows, and the dock
 * carries the buttons.
 *
 * `update_plan`/`todo_write` are ALSO dropped on replay (they have a durable surface — the
 * dock and the file). The interactive asks are not: a reloaded session has no card to stand
 * in for an ask or a folder grant, and the generic row is the only record
 * left, so replay keeps it. That asymmetry is deliberate, not an oversight.
 */
const CARD_SUPERSEDED = new Set([
  "ask_user",
  "request_directory",
  "request_tool",
]);

/** Live suppression: the dedicated card is on screen, so the generic row would duplicate it. */
export const HIDDEN_TOOLS: ReadonlySet<string> = new Set([
  "todo_write",
  "update_plan",
  ...CARD_SUPERSEDED,
]);

/** Replay suppression: only the tools with a durable surface besides the transcript. */
export function isReplayHiddenTool(name: string): boolean {
  return name === "todo_write" || name === "update_plan";
}

export function isHiddenTool(name: string): boolean {
  return HIDDEN_TOOLS.has(name);
}

/** The read-only family folds together under one name, the way the reference design says "Explore". */
const EXPLORE: ToolMeta = { icon: "search", label: "Explore", running: "Exploring", done: "Explored" };
// Which calls only look is the server's verdict, on the call (`looks`): a run of them,
// with the thoughts between them, is one "Explored" line on the timeline (owner, 2026-10-13). It
// was a list of tool names kept here, which held three removed git tools and never `git status`
// run through the shell.

/**
 * The bucket consecutive same-bucket steps fold into — "shell" for a shell and its polls,
 * "mcp:<server>" for one server's calls in a row (the looking calls fold by `looks`). Everything consequential
 * (shell, code, writes, asks) stays on its own row: those are the steps a person reads
 * one at a time. `null` = never grouped.
 *
 * `task` is deliberately NOT grouped: the reference design renders each delegated child as its own
 * card (`task-tool.tsx`), so a fan-out of three researchers is three rows a reader can
 * open one by one — a family header over them would hide the cards that ARE the result.
 */
export function groupKey(name: string): string | null {
  if (SHELL_FAMILY.has(name)) return "shell";
  return null;
}

/**
 * The server a tool calls out to, for grouping and branding — either a vendor MCP server
 * (`mcp__<server>__<tool>`) or a connector tool (`gmail_get_message`). `null` = no server.
 *
 * The two shapes share one identity so the transcript can fold and brand them the same way.
 * `index` (built from `/v1/connectors`) is what makes the connector half work: it supplies
 * the title/logo/brand color for a name that carries no server prefix of its own.
 */
export interface ServerIdentity {
  /** The fold key: `mcp:<server>` or `connector:<id>`. */
  key: string;
  /** The raw server id — the MCP server name, or the connector id. */
  server: string;
  /** The display name: a connector's title ("Gmail"), else a prettified server id. */
  serverTitle: string;
  /** Registry logo id (falls back to the server id). */
  logo: string;
  /** Brand color; empty for an unknown MCP server. */
  brandColor: string;
  /** A connector tool's own label ("Read message"); absent for MCP tools. */
  toolLabel?: string;
  isMcp: boolean;
  /** The plugin an MCP server came with: its server is `plugin_<plugin>_<name>`. */
  plugin?: string;
}

/** `plugin_<plugin>_<name>` → its plugin and server name. A plugin name is kebab-case, so
 *  the first `_` after the prefix ends it. */
export function pluginServer(server: string): { plugin: string; name: string } | null {
  const match = /^plugin_([a-z0-9]+(?:-[a-z0-9]+)*)_(.+)$/.exec(server);
  return match ? { plugin: match[1], name: match[2] } : null;
}

function titleFromServer(server: string): string {
  return server
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function serverIdentity(
  name: string,
  index?: ConnectorToolIndex,
): ServerIdentity | null {
  const entry = index?.get(name);
  if (name.startsWith("mcp__")) {
    const server = name.split("__")[1] || "server";
    // A plugin's server says whose it is, wherever its title shows.
    const fromPlugin = pluginServer(server);
    return {
      key: "mcp:" + server,
      server,
      serverTitle:
        entry?.title ??
        (fromPlugin ? `${titleFromServer(fromPlugin.name)} (plugin ${fromPlugin.plugin})` : titleFromServer(server)),
      ...(fromPlugin ? { plugin: fromPlugin.plugin } : {}),
      logo: entry?.logo ?? server,
      brandColor: entry?.brandColor ?? "",
      ...(entry ? { toolLabel: entry.label } : {}),
      isMcp: true,
    };
  }
  if (entry) {
    return {
      key: "connector:" + entry.connector,
      server: entry.connector,
      serverTitle: entry.title,
      logo: entry.logo,
      brandColor: entry.brandColor,
      toolLabel: entry.label,
      isMcp: false,
    };
  }
  return null;
}

/**
 * The family whose detached-run lifecycle folds together: `shell_task_output`/`_kill`
 * are not separate steps a reader tracks — they are the poll and the stop of the shell that
 * opened them. A delegated `task` child is deliberately NOT in a family (see `groupKey`):
 * each child is its own card, the way the reference design draws it.
 */
const SHELL_FAMILY = new Set(["run_shell", "shell_task_output", "shell_task_kill"]);

/**
 * Whether a call ran a command in a shell — the one family a turn summary counts separately,
 * because "how many commands did it run" is a question a developer asks of a coding agent.
 *
 * The `terminal_*` prefix is the same rule the row variant uses (`classifyTool`), so a new
 * terminal verb is counted the moment it can be drawn.
 */
export function isCommandTool(name: string): boolean {
  return SHELL_FAMILY.has(name) || name.startsWith("terminal_");
}

/** The server's name as a row's title: a plugin's server says whose it is in its tag, not
 *  in its name. */
export function serverName(identity: ServerIdentity): string {
  return identity.plugin ? identity.serverTitle.replace(/\s*\(plugin [^)]*\)\s*$/, "") : identity.serverTitle;
}

/** What a server's tool is called on its row: a connector's own label ("Read message"), else
 *  the wire name as words, without the server's name in front (`browser_take_screenshot` →
 *  "Take screenshot"). These tools are not a fixed set, so nothing here is written per tool. */
export function toolWords(name: string, identity: ServerIdentity): string {
  if (identity.toolLabel) return identity.toolLabel;
  const wire = name.startsWith("mcp__") ? name.split("__").slice(2).join("__") : name;
  const prefix = identity.server.replace(/-/g, "_").toLowerCase() + "_";
  const bare = wire.toLowerCase().startsWith(prefix) && wire.length > prefix.length ? wire.slice(prefix.length) : wire;
  const words = bare.replace(/[_-]+/g, " ").replace(/([a-z0-9])([A-Z])/g, "$1 $2").trim().toLowerCase();
  return words ? words[0].toUpperCase() + words.slice(1) : name;
}

/** A call's arguments on one line, as they were sent: `filename="a.png" scale="css"`. The first
 *  few plain values only; an object or a list is behind the row, in the IN tree. */
export function argsLine(body: string | null, limit = 3): string {
  if (!body) return "";
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return "";
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return "";
  const parts: string[] = [];
  for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (parts.length >= limit) break;
    if (typeof value === "string") {
      const flat = value.replace(/\s+/g, " ").trim();
      parts.push(`${key}=${JSON.stringify(flat.length > 48 ? flat.slice(0, 47) + "…" : flat)}`);
    } else if (typeof value === "number" || typeof value === "boolean") {
      parts.push(`${key}=${String(value)}`);
    }
  }
  return parts.join(" ");
}

/** The MCP server id inside `mcp__<server>__<tool>`, or null for a non-MCP name. */
export function mcpServer(name: string): string | null {
  if (!name.startsWith("mcp__")) return null;
  return name.split("__")[1] || null;
}

/**
 * The per-tool breakdown a grouped server header shows: counts by
 * tool, e.g. "get_portfolio ×2, get_transactions ×1". The connector-tool index, when given,
 * supplies a friendlier label ("Read message ×28") than the wire name.
 */
export function mcpToolBreakdown(
  names: readonly string[],
  index?: ConnectorToolIndex,
): string {
  const counts = new Map<string, number>();
  for (const n of names) {
    const tool = index?.get(n)?.label ?? n.split("__")[2] ?? n;
    counts.set(tool, (counts.get(tool) ?? 0) + 1);
  }
  return Array.from(counts, ([tool, count]) => `${tool}${count > 1 ? ` ×${count}` : ""}`).join(", ");
}

/**
 * The Explore group's count summary: "2 reads · 1 search" — the buckets the reference design uses
 * (read/search/list), with `git_*` and anything else uncounted.
 */
/** What a looking run looked at: the files by name (three, then a count), then the other
 *  kinds of looking counted, then how long it thought between. `names` is the short form a
 *  narrow column shows. */
export function exploreLine(
  tools: ReadonlyArray<{ name: string; args?: unknown }>,
  thoughtSeconds = 0,
): { full: string; short: string } {
  const files: string[] = [];
  let search = 0;
  let list = 0;
  let git = 0;
  let lookup = 0;
  for (const tool of tools) {
    const args = (tool.args ?? {}) as Record<string, unknown>;
    if (tool.name === "read_file" || tool.name === "read_image") {
      const raw = args.path ?? args.file_path;
      const name = typeof raw === "string" ? raw.split(/[\\/]/).filter(Boolean).pop() ?? raw : "";
      if (name && !files.includes(name)) files.push(name);
      else if (!name) files.push("a file");
    } else if (tool.name === "grep" || tool.name === "search_history") search += 1;
    else if (tool.name === "list_files") list += 1;
    else if (tool.name.startsWith("git_")) git += 1;
    else if (tool.name.startsWith("tool_")) lookup += 1;
  }
  const counted = [
    search ? `${search} search${search === 1 ? "" : "es"}` : "",
    list ? `${list} list${list === 1 ? "" : "s"}` : "",
    git ? `${git} git look${git === 1 ? "" : "s"}` : "",
    lookup ? `${lookup} tool lookup${lookup === 1 ? "" : "s"}` : "",
    thoughtSeconds >= 1 ? `thought ${Math.round(thoughtSeconds)}s` : "",
  ].filter(Boolean);
  const named = files.length ? [files.slice(0, 3).join(", ") + (files.length > 3 ? ` · +${files.length - 3}` : "")] : [];
  const fileCount = files.length ? [`${files.length} file${files.length === 1 ? "" : "s"}`] : [];
  return { full: [...named, ...counted].join(" · "), short: [...fileCount, ...counted.filter((c) => !c.startsWith("thought"))].join(" · ") };
}

export function exploreSummary(names: readonly string[]): string {
  const read = names.filter((n) => n === "read_file" || n === "read_image").length;
  const search = names.filter((n) => n === "grep" || n === "search_history").length;
  const list = names.filter((n) => n === "list_files").length;
  return [
    read ? `${read} read${read === 1 ? "" : "s"}` : "",
    search ? `${search} search${search === 1 ? "" : "es"}` : "",
    list ? `${list} list${list === 1 ? "" : "s"}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

/** The header a fold renders with. */
export function groupMeta(key: string): ToolMeta {
  if (key === "explore") return EXPLORE;
  if (key === "shell") return { icon: "terminal", label: "Shell", running: "Running", done: "Ran" };
  if (key.startsWith("mcp:")) return toolMeta(`mcp__${key.slice(4)}__x`);
  return { icon: "wrench", label: "Tool" };
}

const LANG_BADGE: Record<string, string> = {
  py: "🐍",
  ts: "🟦",
  tsx: "🟦",
  js: "🟨",
  jsx: "🟨",
  rs: "🦀",
  go: "🐹",
  rb: "💎",
  php: "🐘",
  md: "📝",
  json: "🧾",
  toml: "🧾",
  yaml: "🧾",
  yml: "🧾",
  html: "🌐",
  css: "🎨",
  sh: "🐚",
  ps1: "🐚",
  sql: "🗃️",
  txt: "📄",
};

export interface FileDisplay {
  badge: string;
  name: string;
  dir: string;
}

/** Split a tool path the way the transcript row shows it: an emoji badge for the
 * language, the basename as the payload, and the folder as the quiet context after it —
 * the last two segments only ("harness/providers"), the way the reference design abbreviates. */
export function fileDisplay(path: string): FileDisplay {
  const norm = (path || "").replace(/\\/g, "/");
  const at = norm.lastIndexOf("/");
  const name = at === -1 ? norm : norm.slice(at + 1);
  const segs = (at === -1 ? "" : norm.slice(0, at)).split("/").filter(Boolean);
  const dir = segs.length > 2 ? "…/" + segs.slice(-2).join("/") : segs.join("/");
  const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
  return { badge: LANG_BADGE[ext] ?? "📄", name, dir };
}

/**
 * Identity for a tool name. MCP tools (`mcp__wealthlog__get_portfolio`) read as the
 * server's name — that prefix is the part a person recognizes; everything unmapped
 * (including the terminal_* family, spelled out there by prefix to keep the exact map
 * small) falls back to the plug/wrench defaults.
 */
export function toolMeta(name: string): ToolMeta {
  const exact = BY_EXACT[name];
  if (exact) return exact;
  if (name.startsWith("terminal_")) return TERMINAL;
  if (name.startsWith("mcp__")) {
    const server = name.split("__")[1]?.replace(/_/g, " ") || "MCP";
    const label = server.charAt(0).toUpperCase() + server.slice(1);
    return { icon: "plug", label, running: label, done: label };
  }
  return { icon: "wrench", label: "Tool", running: "Working", done: "Worked" };
}

/**
 * The verb the running TURN STATUS shows for a tool family ("Running a command",
 * "Editing files", "Browsing the web"…). Distinct from `running` (the row's gerund): the
 * status line reads as a sentence, the row as a label. Falls back to "Working".
 */
const ACTIVITY: Record<string, string> = {
  run_shell: "Running a command",
  shell_task_output: "Running a command",
  shell_task_kill: "Running a command",
  run_code: "Running a command",
  write_file: "Editing files",
  replace_in_file: "Editing files",
  apply_patch: "Editing files",
  apply_unified_diff: "Editing files",
  todo_write: "Planning",
  propose_plan: "Planning",
  fold_context: "Folding its context",
  show_visual: "Drawing a visual",
  design_guide: "Designing",
  check_design: "Checking the design",
  compare_design: "Comparing the app with the design",
  task: "Delegating",
  message_task: "Messaging a subagent",
  ask_user: "Asking",
  web_search: "Browsing the web",
  web_fetch: "Browsing the web",
};

export function activityLabel(name: string, looks?: boolean): string {
  if (name.startsWith("terminal_")) return "Running a command";
  if (name.startsWith("mcp__")) {
    const server = name.split("__")[1]?.replace(/_/g, " ") || "MCP";
    return `Calling ${server.charAt(0).toUpperCase() + server.slice(1)}`;
  }
  return ACTIVITY[name] ?? (looks ? "Exploring" : "Working");
}
