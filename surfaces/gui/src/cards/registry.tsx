// The per-tool renderer registry: one builder per wire tool name, with a generic fallback.
//
// A registration is not a React component per tool but a
// SPEC BUILDER and every row is drawn by the one `ToolRow`. The visuals come out the same
// and there is one place row chrome can change rather than eight.
//
// The key domain is deliberately open, like the reference design's: any wire tool name works, an unknown name
// simply falls through to the generic builder, and a typo therefore renders a plainer row
// instead of throwing. That is also what makes an unmigrated tool (an MCP server's verb, a
// connector's) work on the day it appears without anyone touching this file.

import type { ReactNode } from "react";
import type { IconName } from "../components/Icon";
import type { ToolItem, ToolRowState, ToolVariant } from "./cardModels";
import {
  classifyTool,
  diffCardModel,
  fileLabel,
  foldCardModel,
  genericCallInput,
  genericCardContent,
  readCardModel,
  searchCardModel,
  skillCardModel,
  terminalCardModel,
  terminalDescription,
  terminalFailed,
  toolRowModel,
  webCardModel,
  type DiffCardModel,
  type ReadCardModel,
  type SearchCardModel,
  type TerminalCardModel,
} from "./cardModels";
import type { FileDiff, FoldResultCard, ResultCard } from "./types";
import { designCallId } from "../components/designCards";
import type { DesignCheck } from "../types";
import { formatTokens } from "../usage";
import { diffChurn } from "./diffChurn";
import { fetchRowSuffix } from "./webFetchView";
import { delegationLiveLine, delegationRunning } from "../delegations";
import type { FieldRow } from "./fields";
import { askFields, folderFields, planNotAsked, planVerdict } from "./interaction";
import { planStepCount, planTitle } from "./planText";
import { serverIdentity, toolMeta } from "../toolMeta";
import type { ConnectorToolIndex } from "../connectorTools";
import { readBlocks, type VisualBlock } from "../components/visual/visualModel";

export interface RowSpec {
  variant: ToolVariant;
  icon: IconName;
  /** The raw tool name — lets the row resolve an MCP server's connector logo. */
  name?: string;
  title: string;
  summary: string;
  /** Rendered OUTSIDE the ellipsized summary, so a narrow row cannot clip it. */
  summarySuffix?: string;
  /** Replaces the summary on an error row: the failure's own first line. */
  errorSummary?: string;
  state: ToolRowState;
  /** The call's arguments, for the input section of the expanded body. */
  body: string | null;
  /** The result's text, used when there is no card to draw instead. */
  output: string | null;
  terminal?: TerminalCardModel;
  diff?: DiffCardModel;
  read?: ReadCardModel;
  search?: SearchCardModel;
  web?: Extract<ResultCard, { card: "web" }>;
  /** A loaded skill's body, rendered as markdown instead of a JSON IN/OUT dump. */
  skill?: Extract<ResultCard, { card: "skill" }>;
  /** A program to draw in a code block — `run_code`, `load_skill`. */
  /** `output`: what a program printed and returned (its terminal result card). */
  code?: { text: string; lang?: string; output?: string };
  /** A proposed plan's markdown — `propose_plan`. Rendered as a step inside the turn. */
  plan?: { text: string; path?: string };
  /** A fold of the model's own context — `fold_context`: the note it wrote, and what the
   *  fold did in numbers once it has been applied. */
  fold?: { note: string; card: FoldResultCard | null };
  /** An interactive ask read back as labelled lines (question/answer, folder/result, a
   *  plan's verdict) — `interaction.ts`. The card for asks; under the plan for a plan. */
  fields?: FieldRow[];
  filePath?: string;
  /** A picture the tool fetched for the model to look at (`read_image`) — rendered inline. */
  image?: string;
  /** A delegated child behind this call (`task`): the row offers its session. */
  runId?: string;
  /** Steps that run took, shown as `· N steps` beside the summary. */
  stepCount?: number;
  /** Edit churn, shown on the row as a green `+A` / red `−R`. */
  churn?: { added: number; removed: number };
  /** Extra affordances for the details panel. */
  extras?: ReactNode;
  /** `show_visual`'s blocks, drawn under the row without a disclosure. */
  visual?: VisualBlock[];
  /** What Reeve measured on a design page this call wrote, drawn as a line under the row.
   *  */
  designCheck?: DesignCheck[];
  /** The call a design check belongs to (`designCards`): whether its row shows the page's card. */
  designCall?: string;
  /** A finished `compare_design`: the page and the app's address, for the row's Review. */
  designCompare?: { page: string; url: string };
}

/** The icon a variant draws when its tool has no opinion. */
const VARIANT_ICONS: Record<ToolVariant, IconName> = {
  terminal: "terminal",
  read: "file",
  edit: "pencilLine",
  search: "search",
  web: "globe",
  plan: "checklist",
  code: "codeXml",
  other: "wrench",
};

type Builder = (item: ToolItem) => RowSpec;

/**
 * Every builder starts here, so the shared derivation happens once.
 *
 * The row model supplies title, summary, body, output and the state machine; a builder then
 * adds whatever its tool knows on top. That ordering is what makes an unknown tool render
 * correctly with no registration at all: the generic builder IS the base.
 */
function base(item: ToolItem): RowSpec {
  const model = toolRowModel(item);
  const meta = toolMeta(item.name);
  // A picture the tool returned for the model (`read_image`, an MCP screenshot) is attached
  // to the call. Draw it inline for ANY tool — a screenshot row should show the screen. Not a
  // design write's: those pictures are the check's screenshots for the model, and the
  // row is its diff with the check's line under it, which has its own small picture. Drawn as
  // the body, they put a page-tall screenshot in place of the edit (owner report 2026-10-20).
  const image = item.designCheck?.length ? undefined : item.attachments?.find((a) => a.kind === "image")?.data_url;
  return {
    variant: model.variant,
    // toolMeta owns the per-NAME icon (`cardTerminal` for run_shell, `checklist` for
    // todo_write) and answers `wrench` for a name it does not know. When it has no opinion
    // but the VARIANT does — a `terminal_open` classified by prefix, an MCP verb — the
    // family icon is a better answer than a generic wrench.
    icon:
      meta.icon === "wrench" && model.variant !== "other"
        ? VARIANT_ICONS[model.variant]
        : (meta.icon as IconName),
    name: item.name,
    // running → "Editing", ok → "Edited". A failed or stopped call keeps the noun: "Edited"
    // over a red error would say it happened. A call still next in line (`queued`) keeps it
    // too: "Editing f1.txt · waiting to run" said it was at work while it waited.
    title:
      model.state === "running" && meta.running && !item.queued
        ? meta.running
        : model.state === "ok" && meta.done
          ? meta.done
          : meta.label,
    summary: model.summary,
    errorSummary: model.errorSummary ?? undefined,
    state: model.state,
    body: model.body,
    output: model.output,
    filePath: model.filePath,
    ...(image ? { image } : {}),
    // A delegated call: the run reference rides the result (see `runRef.ts`), and the row
    // uses it for `· N steps` and the `↗ Open` affordance.
    ...(item.runId ? { runId: item.runId } : {}),
    ...(item.stepCount !== undefined ? { stepCount: item.stepCount } : {}),
    ...(item.designCheck?.length ? { designCheck: item.designCheck, designCall: designCallId(item) } : {}),
    ...(item.name === "compare_design" && model.state === "ok" && typeof item.args?.page === "string" && typeof item.args?.url === "string"
      ? { designCompare: { page: item.args.page, url: item.args.url } }
      : {}),
  };
}

/** A terminal row's exit status outranks the call's own verdict — see `terminalFailed`. */
function withTerminal(item: ToolItem): RowSpec {
  const spec = base(item);
  const terminal = terminalCardModel(item);
  if (terminal === null) return spec;
  return {
    ...spec,
    terminal,
    state: spec.state === "ok" && terminalFailed(terminal) ? "error" : spec.state,
    icon: "terminal",
  };
}

function terminalBuilder(item: ToolItem): RowSpec {
  const spec = withTerminal(item);
  if (spec.terminal === null) return spec;
  // The precedence for a terminal row: the DESCRIPTION the model wrote outranks the
  // derived summary, because intent is what a person judges and the command is right there
  // on the card's prompt line. With no description the row keeps whatever the tool's own
  // account said — which for a failure is "Command failed (exit 2)", the fact worth reading.
  const description = terminalDescription(item);
  return description === null ? spec : { ...spec, summary: description };
}

function readBuilder(item: ToolItem): RowSpec {
  const spec = withFileLabel(base(item));
  const read = readCardModel(item);
  if (read !== null) return { ...spec, read, icon: "file" };
  // `read_image` has no lines; the picture rides on the call's attachments. Show it in the
  // body instead of the "… is attached below" text.
  const image = item.attachments?.find((a) => a.kind === "image")?.data_url;
  return { ...spec, icon: "file", ...(image ? { image } : {}) };
}

function editBuilder(item: ToolItem): RowSpec {
  const spec = withFileLabel(base(item));
  const diff = diffCardModel(item);
  return diff === null
    ? { ...spec, icon: "pencilLine" }
    : { ...spec, diff, icon: "pencilLine", churn: churnOf(diff.diffs) };
}

/** A file mutation/read row's headline reads: the file NAME as the payload
 *  and the folder as the quiet context after it (the same split the changes receipt and the
 *  file manager use). The tool's own summary — "Edit pipeline.py", "Read big.py" — only
 *  repeats the row's label and the name it already carries, and it does so as one
 *  ellipsizable string, so a narrow row loses the name first and the folder never. */
function withFileLabel(spec: RowSpec): RowSpec {
  if (spec.filePath === undefined) return spec;
  const { name, dir } = fileLabel(spec.filePath);
  return { ...spec, summary: name, ...(dir ? { summarySuffix: dir } : {}) };
}

/** Total `+added`/`−removed` across a card's hunks (`diffChurn`: the engine's counts, else a
 *  real diff of the sides — never their raw line counts, which include context). */
function churnOf(diffs: FileDiff[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const d of diffs) {
    const churn = diffChurn(d);
    added += churn.added;
    removed += churn.removed;
  }
  return { added, removed };
}

function searchBuilder(item: ToolItem): RowSpec {
  const spec = base(item);
  const search = searchCardModel(item);
  // The card's own title is a replacement, not a refinement: a glob's card says how many
  // paths it found, which is more use on the row than the pattern alone.
  return search === null
    ? { ...spec, icon: "search" }
    : { ...spec, search, icon: "search" };
}

function webBuilder(item: ToolItem): RowSpec {
  const spec = base(item);
  const web = webCardModel(item);
  if (web === null) return { ...spec, icon: "globe" };
  if (web.kind === "search") {
    // The row reads `Search · "the query" · 3 sources`: the query is what the reader asked
    // for, and the source count is the suffix so a narrow row cannot clip it away.
    // Several merged searches have no single query: the first one, and how many rode with it.
    const merged = (web.queries ?? []).filter((q) => q.trim());
    const query = (web.query ?? "").trim() || merged[0]?.trim() || "";
    const more = !web.query && merged.length > 1 ? ` +${merged.length - 1}` : "";
    const count = web.sources.length;
    return {
      ...spec,
      web,
      icon: "globe",
      summary: query ? `“${query}”${more}` : spec.summary,
      summarySuffix: `${count} source${count === 1 ? "" : "s"}`,
    };
  }
  // `Fetch · docs.python.org · 20k of 54k`: the bare host, then what came of it.
  const host = (() => {
    try {
      return new URL(web.url).hostname.replace(/^www\./, "") || web.url;
    } catch {
      return web.url;
    }
  })();
  return {
    ...spec,
    web,
    icon: "globe",
    summary: host,
    summarySuffix: fetchRowSuffix(web),
  };
}

/**
 * The plan row, and the one piece of todo behaviour that matters: the
 * parallel-active count is a SUFFIX rather than part of the summary.
 *
 * It is drawn outside the ellipsized span so a narrow row cannot clip it away — which is the
 * whole failure it exists to prevent, since "3 of these are running at once" is the thing a
 * reader needs and the summary is the thing that gets truncated.
 */
function planBuilder(item: ToolItem): RowSpec {
  const spec = base(item);
  const todos = todoItems(item);
  if (todos.length === 0) return { ...spec, icon: "checklist" };
  const active = todos.filter((entry) => status(entry) === "in_progress");
  const named = active.length > 0 ? String(active[0].content ?? "").trim() : "";
  return {
    ...spec,
    icon: "checklist",
    summarySuffix: named && active.length > 1 ? `+${active.length - 1}` : undefined,
  };
}

function todoItems(item: ToolItem): Array<Record<string, unknown>> {
  const args = item.args;
  if (typeof args !== "object" || args === null) return [];
  const raw = (args as Record<string, unknown>).todos ?? (args as Record<string, unknown>).items;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (entry): entry is Record<string, unknown> =>
      typeof entry === "object" && entry !== null && !Array.isArray(entry),
  );
}

function status(entry: Record<string, unknown>): string {
  // `completed` is the alias models reach for; the engine normalizes it, the arguments do
  // not, and this reads the arguments.
  const value = entry.status;
  if (value === "completed" || value === "done") return "done";
  return typeof value === "string" ? value : "pending";
}

/** `run_code`: the program travels in the call card's raw input. */
function codeBuilder(item: ToolItem): RowSpec {
  const spec = base(item);
  const program = genericCallInput(item) ?? argsCode(item.args);
  const content = genericCardContent(item);
  const text = program ?? (content && content.length > 0 ? content.join("\n") : null);
  if (text === null) return { ...spec, icon: "codeXml" };
  // What the program printed rides its terminal result card; without drawing it, the reason
  // the program ran showed nowhere (GUI tools review, B4).
  const result = item.presentation?.card as { card?: string; output?: unknown } | undefined;
  const output = result?.card === "terminal" && typeof result.output === "string" ? result.output : undefined;
  return {
    ...spec,
    icon: "codeXml",
    code: { text, lang: "python", ...(output !== undefined ? { output } : {}) },
  };
}

/**
 * The program from a call's own arguments.
 *
 * A live call carries it in the generic CALL card, but a replay can arrive without one — and
 * then the row used to fall through to the generic IN dump, printing the program as wrapped
 * text with no line numbers. Reading `args.code` keeps a restored `run_code` row a CodeBlock.
 */
function argsCode(args: unknown): string | null {
  return argsText(args, "code");
}

/** One string argument off the call's arguments (object or JSON text). */
function argsText(args: unknown, key: string): string | null {
  let parsed = args;
  if (typeof args === "string") {
    try {
      parsed = JSON.parse(args);
    } catch {
      return null;
    }
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const value = (parsed as Record<string, unknown>)[key];
  return typeof value === "string" && value.trim() ? value : null;
}

/**
 * `workflow`: the script renders as the row's code block (like `run_code`), and while it
 * runs its narration (`phase`/`log`, streamed as `tool_output_delta`) is drawn as a live
 * terminal body — headed by "‹name› (workflow)", so the header never claims the script is
 * a shell command. Once the call settles the terminal is dropped and the row keeps the
 * script plus the returned JSON.
 */
function workflowBuilder(item: ToolItem): RowSpec {
  const spec = base(item);
  const script = argsText(item.args, "script") ?? genericCallInput(item);
  const meta = parsedMeta(item.args);
  const name = typeof meta?.name === "string" && meta.name ? meta.name : "workflow";
  const description = typeof meta?.description === "string" ? meta.description : "";
  const phases = Array.isArray(meta?.phases) ? meta.phases.length : 0;
  const withScript: RowSpec = {
    ...spec,
    icon: "codeXml",
    // The identity block is the model's own sentence about the run — better on the row
    // than the script's first line, and the phase count is the "how big is this" fact.
    ...(description ? { summary: description } : {}),
    ...(phases > 0 ? { summarySuffix: `${phases} phase${phases === 1 ? "" : "s"}` } : {}),
    ...(script === null ? {} : { code: { text: script, lang: "python" } }),
  };
  if (item.subagent?.background === true) {
    // A background run: the call returned at once with the run's id, and the run
    // is a child session whose frames keep arriving. The row stays running until its settle
    // frame, and says what the run is on — the phase, else the agent it just started.
    const running = delegationRunning(item);
    const live = running ? (delegationLiveLine(item) ?? "").replace(/^↳ /, "") : "";
    return {
      ...withScript,
      state: running ? "running" : withScript.state,
      summarySuffix: running ? live || "in the background" : "ran in the background",
    };
  }
  if (item.status !== "…" || script === null) return withScript;
  return {
    ...withScript,
    terminal: {
      command: `${name} (workflow)`,
      running: true,
      ...(item.liveOutput ? { output: item.liveOutput } : {}),
    },
  };
}

/** The `meta` block off the call's arguments (object or JSON text), or null. */
function parsedMeta(args: unknown): Record<string, unknown> | null {
  let parsed = args;
  if (typeof parsed === "string") {
    try {
      parsed = JSON.parse(parsed);
    } catch {
      return null;
    }
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const meta = (parsed as Record<string, unknown>).meta;
  return typeof meta === "object" && meta !== null
    ? (meta as Record<string, unknown>)
    : null;
}

function loadSkillBuilder(item: ToolItem): RowSpec {
  const spec = base(item);
  const skill = skillCardModel(item);
  // A skill load has no card and no file: its result is prose the model reads. The row
  // keeps the summary and the body.
  return skill === null ? { ...spec, icon: "book" } : { ...spec, icon: "book", skill };
}

/**
 * `propose_plan`: the plan's markdown is the row's CARD — a step inside the turn (the mockup
 * split: content in the transcript, the decision in the composer). The content is read from
 * the call's own arguments, so it renders the instant the call is proposed and survives a
 * reload (the arguments are persisted).
 */
function proposedPlanBuilder(item: ToolItem): RowSpec {
  const spec = base(item);
  const plan = typeof item.args?.plan === "string" ? item.args.plan : "";
  const path = typeof item.args?.path === "string" ? item.args.path : undefined;
  const verdict = planVerdict(item);
  const count = planStepCount(plan);
  const named = [planTitle(plan), count].filter(Boolean).join(" · ");
  // In full access nobody was asked, so the row must not say anyone approved it.
  const unasked = planNotAsked(item);
  // The row says where the plan stands. Waiting, it is the plan to read (drawn whole under
  // the row). Answered, the plan folds behind this one line, which says the answer.
  const words =
    spec.state === "running"
      ? { title: "Proposed a plan", summary: count }
      : spec.state === "ok"
        ? { title: unasked ? "Planned" : "Plan approved", summary: named }
        : {
            title: spec.state === "stopped" ? "Plan not answered" : "Plan sent back",
            summary: verdict.find((row) => row.label === "Feedback")?.value ?? named,
            // The feedback is the person's words, not a failure: it is not drawn as one.
            errorSummary: undefined,
          };
  return {
    ...spec,
    icon: "checklist",
    ...words,
    plan: { text: plan, ...(path ? { path } : {}) },
    fields: verdict,
  };
}

/** `ask_user` — the question and the answer, not the call's JSON. */
function askBuilder(item: ToolItem): RowSpec {
  return { ...base(item), icon: "chat", fields: askFields(item) };
}

/** `request_directory` — which folder, what access, and what the person decided. */
function folderBuilder(item: ToolItem): RowSpec {
  return { ...base(item), icon: "folderPlus", fields: folderFields(item) };
}

function taskBuilder(item: ToolItem): RowSpec {
  return { ...base(item), icon: "search" };
}

/**
 * `fold_context`: the model replaced the older part of its own context with a note.
 *
 * A fold is a step of the turn, so this row is the whole account of it: `Folded · 38 earlier
 * messages · 41.5k freed`, and the note behind the disclosure. It used to be told a second
 * time by the compaction divider, which is a turn breaker — the turn read "Worked for 1m
 * 10s", a rule across the transcript, then the rest of the same turn as a second group
 * (owner, 2026-10-05).
 *
 * The note is read from the call's own arguments, like a proposed plan: it is there the
 * instant the call is proposed and it survives a reload. The numbers ride the result card;
 * a row from before the card existed shows its note without them.
 */
function foldBuilder(item: ToolItem): RowSpec {
  const spec = base(item);
  const note = argsText(item.args, "note") ?? "";
  const card = foldCardModel(item);
  // A refused fold keeps the plain IN/OUT body: the refusal is the thing to read there, and
  // a note card would stand where it is shown.
  const fold = note && spec.state !== "error" ? { fold: { note, card } } : {};
  if (card === null) {
    // No verdict in numbers: a running call, a refusal, or an older row. The sentence the
    // tool wrote starts with the row's own title ("Folded its own context into a note").
    return { ...spec, icon: "fold", summary: "its own context into a note", ...fold };
  }
  const n = card.folded_messages;
  return {
    ...spec,
    icon: "fold",
    summary: `${n} earlier message${n === 1 ? "" : "s"}`,
    ...(card.saved_tokens > 0 ? { summarySuffix: `${formatTokens(card.saved_tokens)} freed` } : {}),
    ...fold,
  };
}

/**
 * `show_visual`: the blocks drawn under the row, not behind it — the visual is the
 * answer's own content. A finished call draws the harness's checked blocks from its result card
 * (files already read); a running one draws the arguments it was given. A refused call keeps the
 * plain row, whose summary is the reason.
 */
function visualBuilder(item: ToolItem): RowSpec {
  const spec = base(item);
  if (spec.state === "error") return { ...spec, icon: "chart" };
  const card = item.presentation?.card as { card?: string; blocks?: unknown } | undefined;
  const blocks = card?.card === "visual" ? readBlocks(card.blocks) : readBlocks(parsedArgs(item.args));
  return { ...spec, icon: "chart", visual: blocks };
}

function parsedArgs(args: unknown): unknown {
  if (typeof args !== "string") return args;
  try {
    return JSON.parse(args);
  } catch {
    return null;
  }
}

const REGISTRY: Record<string, Builder> = {
  run_shell: terminalBuilder,
  shell_task_output: terminalBuilder,
  shell_task_kill: terminalBuilder,
  read_file: readBuilder,
  read_image: readBuilder,
  write_file: editBuilder,
  replace_in_file: editBuilder,
  apply_patch: editBuilder,
  apply_unified_diff: editBuilder,
  grep: searchBuilder,
  list_files: searchBuilder,
  search_history: searchBuilder,
  web_search: webBuilder,
  web_fetch: webBuilder,
  todo_write: planBuilder,
  run_code: codeBuilder,
  workflow: workflowBuilder,
  load_skill: loadSkillBuilder,
  propose_plan: proposedPlanBuilder,
  ask_user: askBuilder,
  request_directory: folderBuilder,
  task: taskBuilder,
  fold_context: foldBuilder,
  show_visual: visualBuilder,
};

/**
 * Prefix rules, for families whose members are not worth listing one by one.
 *
 * `terminal_*` gains a verb every so often; `mcp__<server>__<tool>` is unbounded by
 * construction. A prefix rule is how a family stays covered without a table that goes stale.
 */
const PREFIXES: ReadonlyArray<readonly [string, Builder]> = [
  ["terminal_", terminalBuilder],
];

/** The builder for a tool, or the generic one. Never null: every tool renders. */
export function builderFor(toolName: string): Builder {
  const exact = REGISTRY[toolName];
  if (exact) return exact;
  for (const [prefix, builder] of PREFIXES) {
    if (toolName.startsWith(prefix)) return builder;
  }
  return base;
}

/**
 * The row for a tool, optionally knowing its connector. A connector tool (`gmail_get_message`)
 * is not in `toolMeta`'s table — it would read "Tool" — so when the connector index resolves
 * it, the row takes the connector's title ("Gmail") and the tool's own label ("Read message").
 * It is a post-process rather than a change to every builder: the builders stay unaware of
 * connectors, and only the rows that HAVE one change.
 */
export function rowSpec(item: ToolItem, connectorTools?: ConnectorToolIndex): RowSpec {
  const spec = builderFor(item.name)(item);
  const identity = serverIdentity(item.name, connectorTools);
  if (identity && !identity.isMcp) {
    return {
      ...spec,
      title: identity.serverTitle,
      summary: identity.toolLabel ?? spec.summary,
    };
  }
  return spec;
}

/** Whether a tool has a registration of its own — used by tests, not by rendering. */
export function isRegistered(toolName: string): boolean {
  return (
    Object.prototype.hasOwnProperty.call(REGISTRY, toolName) ||
    PREFIXES.some(([prefix]) => toolName.startsWith(prefix))
  );
}

export { classifyTool };
