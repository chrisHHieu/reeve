// The row model and the five card models: pure functions from a tool item to what a card
// draws.
//
// The shape of these models is the point, and it is worth stating because it is what
// replaced a pile of heuristics:
//
//   Every model returns `null` on ANY doubt. An unknown card kind, a missing field, a hunk
//   that is not a string — all of them produce `null`, and `null` means "the row keeps its
//   plain text". Nothing throws, nothing half-renders, and nothing has to be trusted: the
//   card came over a wire from a host that may be newer than this build. That is the rule, and the
//   reason `card` is a discriminant rather than a set of optional flags.
//
//   The five are tried in a FIXED order — terminal, diff, read, search, web — because a call
//   carries at most one kind and the order is what makes that assumption cheap. A tool that
//   somehow carried two renders the first, which is a stable outcome rather than a race.
//
//   The row model is derived FIRST and independently, so a row with no card at all is still
//   a correct row. Cards are an enhancement to the transcript, never a precondition for it.

import type { ToolItem } from "../types";
export type { ToolItem } from "../types";
import { toolMeta, fileDisplay } from "../toolMeta";
import { humanizeTool } from "../humanize";
import { languageForPath } from "../components/primitives/highlight";
import type {
  FileDiff,
  FileLocation,
  FoldResultCard,
  GenericCallCard,
  GenericResultCard,
  ReadLine,
  ReadResultCard,
  ResultCard,
  SearchFileGroup,
  SearchResultCard,
  TerminalCallCard,
  TerminalResultCard,
  WebFetchCard,
  WebFetchMode,
  WebResultCard,
  WebSource,
} from "./types";

/** How a row reads at a glance. `stopped` is separate from `error` on purpose: an
 *  interruption is not a failure, and colouring it red would say the tool broke. */
export type ToolRowState = "running" | "ok" | "error" | "stopped";

export type ToolVariant =
  | "terminal"
  | "read"
  | "edit"
  | "search"
  | "web"
  | "plan"
  | "code"
  | "other";

/**
 * The row's variant, by wire tool name.
 *
 * Aliases matter here: `shell_task_output` is a terminal row and `read_image` is a read row,
 * even though neither name matches its family's primary tool. A name this map does not know
 * is `other`, which still renders — with the tool's own name and its arguments.
 */
const VARIANT_BY_TOOL: Record<string, ToolVariant> = {
  run_shell: "terminal",
  shell_task_output: "terminal",
  shell_task_kill: "terminal",
  read_file: "read",
  read_image: "read",
  write_file: "edit",
  replace_in_file: "edit",
  apply_patch: "edit",
  apply_unified_diff: "edit",
  grep: "search",
  list_files: "search",
  search_history: "search",
  web_search: "web",
  web_fetch: "web",
  todo_write: "plan",
  run_code: "code",
  load_skill: "code",
};

export function classifyTool(toolName: string): ToolVariant {
  if (VARIANT_BY_TOOL[toolName]) return VARIANT_BY_TOOL[toolName];
  // A terminal row by prefix rather than by a list that goes stale every time a verb is
  // added: `terminal_open`, `terminal_send`, `terminal_read` and their siblings.
  if (toolName.startsWith("terminal_")) return "terminal";
  return "other";
}

/**
 * A settled call's verdict, from the wire status alone.
 *
 * Exported because the turn summary counts the same thing the row colours, and two
 * expressions of one rule is how a header ends up saying "1 failed" over a row drawn amber.
 * `stopped` stays separate from `error` for the reason the row does it: an interruption is
 * not a failure.
 */
export function toolState(item: ToolItem): ToolRowState {
  if (item.status === "…") return "running";
  if (item.status === "interrupted" || item.status === "no result") return "stopped";
  return item.status === "ok" ? "ok" : "error";
}

/** Keys whose value is worth showing on the row, per variant, most specific first. */
const SUMMARY_KEYS: Record<ToolVariant, string[]> = {
  terminal: ["description", "command"],
  read: ["path", "file_path", "url"],
  edit: ["path", "file_path"],
  search: ["pattern", "query", "path", "file_path"],
  web: ["query", "url"],
  plan: ["description"],
  code: ["description"],
  other: [],
};

const FILE_PATH_KEYS = ["path", "file_path"];

export interface ToolRowModel {
  variant: ToolVariant;
  /** The family word from toolMeta: "Terminal", "Read", "Grep", … */
  title: string;
  summary: string;
  /** Shown outside the ellipsized summary, so a narrow row cannot clip it. */
  summarySuffix?: string;
  filePath?: string;
  state: ToolRowState;
  /** Pretty-printed arguments, for the details panel's input section. */
  body: string | null;
  /** The result text, when there is no card to draw instead. */
  output: string | null;
  /** The first line of a failure, which replaces the summary on an error row. */
  errorSummary: string | null;
}

function firstLine(text: string): string {
  const cut = text.indexOf("\n");
  return (cut === -1 ? text : text.slice(0, cut)).trim();
}

function pickString(parsed: unknown, keys: readonly string[]): string | undefined {
  if (typeof parsed !== "object" || parsed === null) return undefined;
  const record = parsed as Record<string, unknown>;
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  // No key matched: fall back to the first non-empty string anywhere in the arguments.
  // The reference design does the same, and it is what stops a tool nobody has taught this table about from
  // rendering an empty row.
  for (const value of Object.values(record)) {
    if (typeof value === "string" && value.trim()) return value;
  }
  return undefined;
}

function parseArgs(args: unknown): unknown {
  if (typeof args === "string") {
    try {
      return JSON.parse(args);
    } catch {
      return undefined;
    }
  }
  return args;
}

/** The arguments as text for the input section. `code` gets its program, not its envelope. */
function deriveBody(variant: ToolVariant, args: unknown): string | null {
  if (args === undefined || args === null) return null;
  if (typeof args === "string") return args.trim() ? args : null;
  const parsed = args as Record<string, unknown>;
  if (variant === "code" && typeof parsed.code === "string") return parsed.code;
  if (Object.keys(parsed).length === 0) return null;
  try {
    return JSON.stringify(parsed, null, 2);
  } catch {
    return null;
  }
}

function deriveFilePath(variant: ToolVariant, args: unknown): string | undefined {
  if (variant !== "read" && variant !== "edit") return undefined;
  if (typeof args !== "object" || args === null) return undefined;
  const record = args as Record<string, unknown>;
  for (const key of FILE_PATH_KEYS) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  // `url` is deliberately NOT a file path even though a read row can carry one: a web page
  // is not something the host can open in an editor, and offering it would be a link that
  // does the wrong thing.
  return undefined;
}

/** The result's text, when the call has settled. */
function resultText(item: ToolItem): string | null {
  const preview = item.preview;
  if (typeof preview !== "string" || !preview) return null;
  // The live wire sends `result_preview` — JSON with newlines escaped, capped at 300
  // characters. A replay sends the full model-facing text. Both are text; only one of them
  // is pretty, and guessing which is not worth a special case when the argument JSON is
  // the common shape.
  try {
    const parsed = JSON.parse(preview);
    if (typeof parsed === "string") return parsed;
    if (parsed && typeof parsed === "object") {
      const record = parsed as Record<string, unknown>;
      const output = record.output;
      if (typeof output === "string") return output;
      // An `error` before the generic JSON dump: a failed call's `result_preview` is the
      // result dict, and stringifying it makes the row's failure line read `{` — the first
      // line of a pretty-printed object, which is the one thing nobody needs telling.
      const error = record.error;
      if (typeof error === "string" && error.trim()) return error;
      return JSON.stringify(parsed, null, 2);
    }
  } catch {
    // Not JSON — model-facing text from a replay, or a message. Either way, show it.
  }
  return preview;
}

export function toolRowModel(item: ToolItem, home?: string): ToolRowModel {
  const variant = classifyTool(item.name);
  const running = item.status === "…";
  // Two ways a call can end without a verdict, and neither is a failure: the engine
  // interrupted it, or the turn ended without ever answering for it. Both draw the amber
  // `stopped` mark — red would say the tool broke, and nothing said that. The reference design draws an
  // interruption the same way, and for the same reason.
  const state = toolState(item);
  // What the row says, in precedence order, and the order is the design:
  //   1. the tool's own account of the RESULT — the only source that can report what
  //      happened rather than what was asked;
  //   2. the tool's account of the CALL, which is all there is while a call is in flight;
  //   3. Reeve's per-tool prose, which knows more about a connector's arguments than a
  //      mechanical key picker does ("Sent a Slack message to #sales" beats "sales-team");
  //   4. the mechanical pick, for a tool nobody has taught this table about — it is what
  //      stops an MCP verb from rendering an empty row.
  const args = item.args;
  const parsed = parseArgs(args);
  const output = running ? null : resultText(item);
  const fallback = humanizeTool(item.name, args);
  const prose = [fallback.pre, fallback.obj, fallback.post].filter(Boolean).join("");
  const derived =
    typeof parsed === "object" && parsed !== null
      ? pickString(parsed, SUMMARY_KEYS[variant])
      : undefined;
  // While a call is in flight the only account that exists is the CALL's — what is about to
  // happen. Once it has settled, the RESULT's account is the one that can say what did.
  const errorSummary = state === "error" && output !== null ? firstLine(output) : null;
  // A failed call's result account is dropped when it only repeats the failure: `grep_present`
  // returns the error as its summary, and the row draws the failure on its own line under the
  // summary, so the row said the error twice and lost what was searched (owner's screenshot,
  // 2026-10-15). An account that adds something ("Command failed (exit 2)") stays.
  const repeated = (summary: string | undefined) =>
    summary !== undefined && errorSummary !== null && sameLine(summary, errorSummary);
  const accounts = running
    ? [item.callPresentation?.summary, item.presentation?.summary]
    : repeated(item.presentation?.summary)
      ? [item.callPresentation?.summary]
      : [item.presentation?.summary, item.callPresentation?.summary];
  const summary = accounts.find((entry) => entry !== undefined) ?? prose ?? derived ?? item.name;
  const filePath = deriveFilePath(variant, parsed);
  return {
    variant,
    title: toolMeta(item.name).label,
    summary: collapseHome(summary, home),
    filePath,
    state,
    body: deriveBody(variant, parsed),
    output,
    errorSummary,
  };
}

/** Whether a summary is the failure line again, allowing for the presenter's clip ("…"). */
function sameLine(summary: string, failure: string): boolean {
  const a = summary.trim().replace(/(?:…|\.\.\.)$/, "");
  const b = failure.trim();
  return a.length > 0 && (a === b || b.startsWith(a) || a.startsWith(b));
}

/**
 * A leading home or workspace path is noise on a row; the tail is what identifies a file.
 *
 * Separators are normalised on BOTH sides before the comparison, because the two do not
 * arrive in the same dialect: a Windows tool result writes `C:\ws\src\a.py` while the home
 * directory this is handed may use either. Splitting without normalising silently does
 * nothing on exactly the platform where the prefix is widest.
 */
function collapseHome(text: string, home?: string): string {
  if (!home) return text;
  const slashed = (value: string) => value.replace(/\\/g, "/").replace(/\/+$/, "");
  const prefix = slashed(home);
  if (!prefix) return text;
  const haystack = slashed(text);
  return haystack.includes(prefix) ? haystack.split(prefix).join("…") : text;
}

/** The file name and its two parent segments, for the row's path link. */
export function fileLabel(path: string): { name: string; dir: string } {
  const display = fileDisplay(path);
  return { name: display.name, dir: display.dir };
}

// -- narrowing helpers ----------------------------------------------------------
// Each returns null rather than throwing. Everything below reads WIRE DATA: a host that is
// newer than this build may send a shape this build has never seen, and the contract is that
// such a shape costs the card and nothing else.

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asInt(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? Math.trunc(value) : null;
}

export function narrowDiffs(value: unknown): FileDiff[] | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  const out: FileDiff[] = [];
  for (const entry of value) {
    const record = asRecord(entry);
    if (!record) return null;
    const path = asString(record.path);
    const newText = asString(record.new_text);
    if (!path || !path.trim() || newText === null) return null;
    const oldText = record.old_text;
    if (oldText !== null && typeof oldText !== "string") return null;
    const hunk: FileDiff = { path, old_text: oldText as string | null, new_text: newText };
    const added = asInt(record.added);
    const removed = asInt(record.removed);
    if (added !== null && added >= 0) hunk.added = added;
    if (removed !== null && removed >= 0) hunk.removed = removed;
    // Where the window starts in its file. Dropped here, the diff block had no
    // way to number the file's real lines — found on the live app, 2026-09-25.
    const oldStart = asInt(record.old_start);
    const newStart = asInt(record.new_start);
    if (oldStart !== null && oldStart >= 1) hunk.old_start = oldStart;
    if (newStart !== null && newStart >= 1) hunk.new_start = newStart;
    out.push(hunk);
  }
  return out;
}

function narrowLines(value: unknown): ReadLine[] | null {
  if (!Array.isArray(value)) return null;
  const out: ReadLine[] = [];
  for (const entry of value) {
    const record = asRecord(entry);
    if (!record) return null;
    const number = asInt(record.number);
    const text = asString(record.text);
    if (number === null || text === null) return null;
    out.push({ number, text });
  }
  return out;
}

function narrowFileGroups(value: unknown): SearchFileGroup[] | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  const out: SearchFileGroup[] = [];
  for (const entry of value) {
    const record = asRecord(entry);
    if (!record) return null;
    const path = asString(record.path);
    if (!path) return null;
    const rawMatches = record.matches;
    if (!Array.isArray(rawMatches)) return null;
    const matches: SearchFileGroup["matches"] = [];
    for (const match of rawMatches) {
      const row = asRecord(match);
      if (!row) return null;
      const lineNumber = asInt(row.line_number);
      const line = asString(row.line);
      if (lineNumber === null || line === null) return null;
      matches.push({ line_number: lineNumber, line });
    }
    out.push({ path, matches });
  }
  return out;
}

function narrowSources(value: unknown): WebSource[] | null {
  if (!Array.isArray(value)) return null;
  const out: WebSource[] = [];
  for (const entry of value) {
    const record = asRecord(entry);
    if (!record) return null;
    const url = asString(record.url);
    if (!url) return null;
    const source: WebSource = { url };
    const title = asString(record.title);
    const snippet = asString(record.snippet);
    // The date and the byline were sent and dropped here, so no search card ever showed
    // when a page was published (found 2026-10-05 against real Exa results).
    const published = asString(record.published_at);
    const author = asString(record.author);
    if (title) source.title = title;
    if (snippet) source.snippet = snippet;
    if (published) source.published_at = published;
    if (author) source.author = author;
    // The kept-icon flag was dropped here the same way, so no card ever asked for one.
    if (record.icon === true) source.icon = true;
    out.push(source);
  }
  return out;
}

const FETCH_MODES: readonly WebFetchMode[] = ["page", "answer", "find", "redirect", "image", "pdf"];
const EXCERPT_KINDS = ["markdown", "text", "code"] as const;

/** A number-valued record's whole-number fields, the rest dropped. */
function intFields(value: unknown, keys: readonly string[]): Record<string, number> | undefined {
  const record = asRecord(value);
  if (!record) return undefined;
  const out: Record<string, number> = {};
  for (const key of keys) {
    const n = asInt(record[key]);
    if (n !== null) out[key] = n;
  }
  return Object.keys(out).length ? out : undefined;
}

/** How a fetch read its page (`fetch_present._fetch_card`). Every part is optional, and a
 *  malformed one costs that part, never the card: a fetch card always names its page. */
function narrowFetchReading(record: Record<string, unknown>): Partial<WebFetchCard> {
  const out: Partial<WebFetchCard> = {};
  const mode = asString(record.mode);
  if (mode && (FETCH_MODES as readonly string[]).includes(mode)) out.mode = mode as WebFetchMode;
  const kind = asString(record.excerpt_kind);
  if (kind && (EXCERPT_KINDS as readonly string[]).includes(kind)) {
    out.excerpt_kind = kind as (typeof EXCERPT_KINDS)[number];
  }
  out.read = intFields(record.read, ["start", "end", "page_chars"]);
  const about = asRecord(record.about);
  if (about) {
    out.about = {
      ...intFields(about, ["left_out_chars", "cached_seconds"]),
      ...(about.plain_http === true ? { plain_http: true } : {}),
      ...(about.source_cut === true ? { source_cut: true } : {}),
      ...(about.js_required === true ? { js_required: true } : {}),
      ...(asString(about.unanswered) ? { unanswered: asString(about.unanswered)! } : {}),
    };
  }
  const answer = asRecord(record.answer);
  const said = answer && asString(answer.text);
  if (answer && said !== null && said !== undefined) {
    const quotes = Array.isArray(answer.quotes) ? answer.quotes : [];
    out.answer = {
      text: said,
      question: asString(answer.question) ?? undefined,
      by: asString(answer.by) ?? undefined,
      quotes: quotes.flatMap((entry) => {
        const quote = asRecord(entry);
        const words = quote && asString(quote.quote);
        return quote && words ? [{ quote: words, found: quote.found === true }] : [];
      }),
      ...intFields(answer, ["quotes_total", "quotes_found", "parts", "unread_chars"]),
    };
  }
  const found = asRecord(record.found);
  if (found && asString(found.phrase) !== null) {
    const matches = Array.isArray(found.matches) ? found.matches : [];
    out.found = {
      phrase: asString(found.phrase)!,
      count: asInt(found.count) ?? 0,
      matches: matches.flatMap((entry) => {
        const match = asRecord(entry);
        const text = match && asString(match.text);
        const at = match && asInt(match.at);
        return match && text !== null && text !== undefined && at !== null && at !== undefined ? [{ at, text }] : [];
      }),
    };
  }
  const redirect = asRecord(record.redirect);
  if (redirect) {
    out.redirect = {
      host: asString(redirect.host) ?? undefined,
      url: asString(redirect.url) ?? undefined,
    };
  }
  return out;
}

function asLocations(value: unknown): FileLocation[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const out: FileLocation[] = [];
  for (const entry of value) {
    const record = asRecord(entry);
    if (!record) continue;
    const path = asString(record.path);
    if (!path) continue;
    const line = asInt(record.line);
    out.push(line === null ? { path } : { path, line });
  }
  return out.length ? out : undefined;
}

// -- per-kind narrowers ---------------------------------------------------------
//
// One per card kind, each checking the FIELDS it will read rather than trusting a cast on
// the discriminant alone. The distinction is not academic: `card` is the only thing the
// wire schema validates (the engine sends `z.looseObject({ card: z.string() })`-equivalent
// looseness), so a card whose discriminant says `terminal` and whose `title` is a number is
// a shape that can genuinely arrive — and one that a cast would hand straight to a component
// expecting a string.

function narrowGenericCall(value: unknown): GenericCallCard | null {
  const record = asRecord(value);
  if (!record || record.card !== "generic") return null;
  const title = asString(record.title);
  if (!title || !title.trim()) return null;
  return {
    card: "generic",
    title,
    kind: asString(record.kind) ?? undefined,
    raw_input: record.raw_input,
    content: asStringArray(record.content),
    locations: asLocations(record.locations),
  };
}

function narrowGenericResult(value: unknown): GenericResultCard | null {
  const record = asRecord(value);
  if (!record || record.card !== "generic") return null;
  return {
    card: "generic",
    title: asString(record.title) ?? undefined,
    content: asStringArray(record.content),
  };
}

function asStringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const out = value.filter((entry): entry is string => typeof entry === "string");
  return out.length ? out : undefined;
}

function narrowTerminalCall(value: unknown): TerminalCallCard | null {
  const record = asRecord(value);
  if (!record || record.card !== "terminal") return null;
  const title = asString(record.title);
  if (!title || !title.trim()) return null;
  return {
    card: "terminal",
    title,
    description: asString(record.description) ?? undefined,
    cwd: asString(record.cwd) ?? undefined,
  };
}

function narrowTerminalResult(value: unknown): TerminalResultCard | null {
  const record = asRecord(value);
  if (!record || record.card !== "terminal") return null;
  const output = asString(record.output);
  const exitCode = asInt(record.exit_code);
  const signal = asString(record.signal);
  const title = asString(record.title);
  const cwd = asString(record.cwd);
  return {
    card: "terminal",
    title: title ?? undefined,
    output: output ?? undefined,
    exit_code: exitCode ?? undefined,
    signal: signal ?? undefined,
    cwd: cwd ?? undefined,
    truncated: record.truncated === true,
    omitted_lines: asInt(record.omitted_lines) ?? undefined,
    shell: asString(record.shell) ?? undefined,
  };
}

/** The shell a finished command ran in, as its result card says; undefined while it runs and on
 *  results from before the card carried it. The session default is a Setting, so the
 *  OS alone cannot say it. */
export function terminalResultShell(item: ToolItem): string | undefined {
  return narrowTerminalResult(item.presentation?.card)?.shell;
}

function narrowDiffCard(value: unknown): FileDiff[] | null {
  const record = asRecord(value);
  if (!record || record.card !== "diff") return null;
  return narrowDiffs(record.diffs);
}

function narrowReadCard(value: unknown): ReadResultCard | null {
  const record = asRecord(value);
  if (!record || record.card !== "read") return null;
  const path = asString(record.path);
  const totalLines = asInt(record.total_lines);
  const offset = asInt(record.offset);
  const lines = narrowLines(record.lines);
  if (!path || totalLines === null || lines === null) return null;
  return {
    card: "read",
    path,
    offset: offset ?? 1,
    lines,
    total_lines: totalLines,
    lang: asString(record.lang) ?? undefined,
    title: asString(record.title) ?? undefined,
  };
}

function narrowSearchCard(value: unknown): SearchResultCard | null {
  const record = asRecord(value);
  if (!record || record.card !== "search") return null;
  const total = asInt(record.total);
  const truncated = record.truncated === true;
  const title = asString(record.title) ?? undefined;
  if (total === null) return null;
  if (record.shape === "matches") {
    const files = narrowFileGroups(record.files);
    return files === null
      ? null
      : { card: "search", shape: "matches", files, truncated, total, title };
  }
  if (record.shape === "paths") {
    const paths = asStringArray(record.paths);
    return paths === undefined
      ? null
      : { card: "search", shape: "paths", paths, truncated, total, title };
  }
  // An unknown shape arrived from a newer host. Reading it as `paths` would draw an empty
  // list; returning null draws the row's plain summary text instead.
  return null;
}

function narrowWebCard(value: unknown): WebResultCard | null {
  const record = asRecord(value);
  if (!record || record.card !== "web") return null;
  const title = asString(record.title) ?? undefined;
  if (record.kind === "search") {
    const sources = narrowSources(record.sources);
    if (sources === null) return null;
    return {
      card: "web",
      kind: "search",
      sources,
      truncated: record.truncated === true,
      title,
      answer: asString(record.answer) ?? undefined,
      provider: asString(record.provider) ?? undefined,
      query: asString(record.query) ?? undefined,
      fallback_from: asString(record.fallback_from) ?? undefined,
      fallback_reason: asString(record.fallback_reason) ?? undefined,
      queries: asStringArray(record.queries),
      filtered_out: asInt(record.filtered_out) ?? undefined,
      failed_queries: asInt(record.failed_queries) ?? undefined,
    };
  }
  if (record.kind === "fetch") {
    const url = asString(record.url);
    if (!url) return null;
    return {
      card: "web",
      kind: "fetch",
      url,
      truncated: record.truncated === true,
      title,
      status_code: asInt(record.status_code) ?? undefined,
      excerpt: asString(record.excerpt) ?? undefined,
      chars: asInt(record.chars) ?? undefined,
      ...narrowFetchReading(record),
    };
  }
  // An unknown `kind` from a newer host: reading it as a fetch would draw an empty URL and
  // "HTTP undefined" beside it.
  return null;
}

function narrowSkillCard(value: unknown): Extract<ResultCard, { card: "skill" }> | null {
  const record = asRecord(value);
  if (!record || record.card !== "skill") return null;
  const name = asString(record.name);
  if (!name || !name.trim()) return null;
  return {
    card: "skill",
    name,
    description: asString(record.description) ?? undefined,
    instructions: asString(record.instructions) ?? undefined,
  };
}

function narrowFoldCard(value: unknown): FoldResultCard | null {
  const record = asRecord(value);
  if (!record || record.card !== "fold") return null;
  const counts = [
    asInt(record.folded_messages),
    asInt(record.kept_messages),
    asInt(record.before_tokens),
    asInt(record.after_tokens),
    asInt(record.saved_tokens),
  ];
  // Every count or no card: a row that says "0 freed" for a number that never arrived
  // would be a claim about the fold.
  if (counts.some((count) => count === null || count < 0)) return null;
  const [folded, kept, before, after, saved] = counts as number[];
  return {
    card: "fold",
    folded_messages: folded,
    kept_messages: kept,
    before_tokens: before,
    after_tokens: after,
    saved_tokens: saved,
    ...(record.replaced_notes === true ? { replaced_notes: true } : {}),
  };
}

// -- the five card models -------------------------------------------------------

/**
 * The description the model wrote for a terminal call, or null.
 *
 * It rides the CALL card and nowhere else, which is why the row has to ask for it
 * separately: the result card is about what happened, and by then the intent is only in the
 * arguments and the call card.
 */
export function terminalDescription(item: ToolItem): string | null {
  const description = narrowTerminalCall(item.callPresentation?.card)?.description;
  return description && description.trim() ? description.trim() : null;
}

export interface TerminalCardModel {
  command: string;
  cwd?: string;
  output?: string;
  exitCode?: number;
  signal?: string;
  running?: boolean;
  truncated?: boolean;
  /** Lines the retention budget dropped — what "truncated" means in a number. */
  omittedLines?: number;
}

export function terminalCardModel(item: ToolItem): TerminalCardModel | null {
  const running = item.status === "…";
  const callCard = narrowTerminalCall(item.callPresentation?.card);
  if (running) {
    // A running terminal is headed by the command the CALL declared. With no call card there
    // is nothing to head it with, and the row keeps its one-line summary.
    return callCard === null
      ? null
      : {
          command: callCard.title,
          cwd: callCard.cwd,
          running: true,
          // What the command has printed SO FAR (tools/output.py → `tool_output_delta`). The
          // card used to be a bare prompt line until the call ended, which is the one moment
          // a person watching a build wants the output most. Replaced wholesale by the result
          // card below, so the live text can never outlive the call it belongs to.
          ...(item.liveOutput ? { output: item.liveOutput } : {}),
        };
  }
  // A completed terminal REPLACES the pending one. When there is no result card there is no
  // terminal at all — a call whose result is a background acknowledgement or a start failure
  // reads as its summary, which is what the presenter intended by sending a generic card.
  const resultCard = narrowTerminalResult(item.presentation?.card);
  if (resultCard === null) return null;
  // A replacement title wins when the tool supplied one; otherwise the call's command still
  // describes what ran. With neither there is no terminal to draw — a card headed by an empty
  // prompt line says nothing, and the row keeps its sentence instead.
  const command = resultCard.title ?? callCard?.title ?? "";
  if (!command) return null;
  return {
    command,
    cwd: resultCard.cwd ?? callCard?.cwd,
    output: resultCard.output,
    exitCode: resultCard.exit_code,
    signal: resultCard.signal,
    truncated: resultCard.truncated,
    omittedLines: resultCard.omitted_lines,
  };
}

/**
 * Whether a terminal row should read as FAILED even though the call succeeded.
 *
 * A command that ran and reported failure is a failure to the person reading, and the whole
 * point of a terminal card is that it says so. The reference design applies this to every card row, not just
 * the shell's: the exit status is the tool's own verdict and it outranks the call's.
 */
export function terminalFailed(model: TerminalCardModel): boolean {
  if (model.running === true) return false;
  return (model.exitCode !== undefined && model.exitCode !== 0) || model.signal !== undefined;
}

export interface DiffCardModel {
  diffs: FileDiff[];
  path?: string;
}

export function diffCardModel(item: ToolItem): DiffCardModel | null {
  // A result card wins; a running mutation falls back to the diff its ARGUMENTS describe,
  // which is what a person is consenting to before anything has been written.
  const diffs =
    narrowDiffCard(item.presentation?.card) ?? narrowDiffCard(item.callPresentation?.card);
  if (diffs === null) return null;
  return { diffs, path: diffs.length === 1 ? diffs[0].path : undefined };
}

export interface ReadCardModel {
  path: string;
  lines: ReadLine[];
  totalLines: number;
  lang?: string;
}

export function readCardModel(item: ToolItem): ReadCardModel | null {
  // Result-side only: a read has no content until it has run, so a running read row is its
  // summary and nothing else.
  if (item.status === "…") return null;
  const card = narrowReadCard(item.presentation?.card);
  if (card === null) return null;
  // The language is inferred from the path when the card does not name one — one
  // extension→language table, in the surface, shared with every other highlighting card.
  const lang = card.lang ?? languageForPath(card.path);
  return {
    path: card.path,
    lines: card.lines,
    totalLines: card.total_lines,
    ...(lang ? { lang } : {}),
  };
}

export interface SearchCardModel {
  card: Extract<ResultCard, { card: "search" }>;
  recovery?: string;
  /** The query the search ran, for highlighting hits in the row. */
  pattern?: string;
}

export function searchCardModel(item: ToolItem): SearchCardModel | null {
  if (item.status === "…") return null;
  const card = narrowSearchCard(item.presentation?.card);
  if (card === null) return null;
  // The raw result text is the only place a spilled full result's locator lives, so it is
  // passed through only when the search was capped — the one case where a reader needs
  // somewhere else to look.
  const recovery = card.truncated ? recoveryText(item) : undefined;
  const pattern = typeof item.args?.pattern === "string" ? item.args.pattern : undefined;
  return {
    card,
    ...(recovery === undefined ? {} : { recovery }),
    ...(pattern ? { pattern } : {}),
  };
}

function recoveryText(item: ToolItem): string | undefined {
  const text = item.preview;
  if (typeof text !== "string") return undefined;
  try {
    const parsed = JSON.parse(text);
    const record = asRecord(parsed);
    if (record) {
      for (const key of ["full_output", "note", "output"]) {
        const value = asString(record[key]);
        if (value && value.trim()) return firstLine(value);
      }
    }
  } catch {
    // Not JSON: a replay's model-facing text. One line of it is still a locator.
  }
  return firstLine(text);
}

export function webCardModel(item: ToolItem): Extract<ResultCard, { card: "web" }> | null {
  if (item.status === "…") return null;
  return narrowWebCard(item.presentation?.card);
}

/** A loaded skill's body for the transcript card. Result-side only: nothing to show until
 *  the skill has actually loaded. */
export function skillCardModel(item: ToolItem): Extract<ResultCard, { card: "skill" }> | null {
  if (item.status === "…") return null;
  return narrowSkillCard(item.presentation?.card);
}

/** What a fold did, in numbers, once it has been applied. */
export function foldCardModel(item: ToolItem): FoldResultCard | null {
  if (item.status === "…") return null;
  return narrowFoldCard(item.presentation?.card);
}

/**
 * A `generic` card's content lines, for the expanded body.
 *
 * Falls back to the CALL card, which is where a `generic` call's salient input lives — a
 * program before it runs, a patch envelope. Reading both is what lets the code toolview show
 * a program while it is still running.
 */
export function genericCardContent(item: ToolItem): string[] | null {
  return (
    narrowGenericResult(item.presentation?.card)?.content ??
    narrowGenericCall(item.callPresentation?.card)?.content ??
    null
  );
}

/** A `generic` call card's raw input — a program, a query, a patch envelope. */
export function genericCallInput(item: ToolItem): string | null {
  const raw = narrowGenericCall(item.callPresentation?.card)?.raw_input;
  return typeof raw === "string" && raw.trim() ? raw : null;
}

/** A `generic` card's replacement title, when the tool sent one. */
export function genericTitle(item: ToolItem): string | null {
  return (
    narrowGenericResult(item.presentation?.card)?.title ??
    narrowGenericCall(item.callPresentation?.card)?.title ??
    null
  );
}
