import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import type { ApprovalDecision, Item, StepTokens, ToolItem, ApprovalItem, AssistantItem, SteerItem, NoticeItem, QuestionItem, PlanRequestItem, ToolRequestItem } from "../types";
import { formatTokens, tokensTotal, turnTokens } from "../usage";
import { humanizeAsk, type HumanLine } from "../humanize";
import { Markdown } from "./Markdown";

import { activityLabel, fileDisplay, groupKey, serverIdentity } from "../toolMeta";
import { summarizeTurn } from "../turnSummary";
import type { ServerIdentity } from "../toolMeta";
import { useConnectorTools } from "../connectorTools";
import type { ConnectorToolIndex } from "../connectorTools";
import { ConnectorMessageCard } from "./ConnectorMessageCard";
import { Icon, type IconName } from "./Icon";
import { FieldsBlock } from "../cards/FieldsBlock";
import { answerPairs } from "../cards/interaction";
import { ScrollFade } from "./primitives/ScrollFade";
import { ToolCallRow } from "./ToolCallRow";
import { approvalChip } from "./ToolRow";
import { DelegationCard, type DelegationItem } from "./DelegationCard";
import { ExploreGroupRow, type ExploreEntry } from "./ExploreGroupRow";
import { McpGroupRow } from "./McpGroupRow";
import { FamilyGroupRow } from "./FamilyGroupRow";
import { ThinkRow, thoughtFor } from "./ThinkRow";
import { TurnAnnouncer } from "./TurnAnnouncer";
import { CodeCommentCards } from "./markdown/CodeCommentCards";
import { DeliveredFiles } from "./markdown/Deliverables";
import { declaredFiles, type DeliveredFile } from "./markdown/Deliverables";
import { codeCommentsOf } from "./markdown/directives";
import { copyAnswer } from "./markdown/richCopy";
import { drawnInAnswer, stripVisualMarkers, turnVisuals, type TurnVisual } from "./visual/answerVisuals";
import { AnswerWithVisuals, PlacedVisuals, type VisualPlace } from "./visual/VisualPlacement";
import { ToolRowStateProvider } from "../cards/rowState";
import { DesignCards, designCardCalls } from "./designCards";
import { rowSpec } from "../cards/registry";
import { foldNotice } from "../compactionNotice";
import { Disclosure } from "../ui/Disclosure";
import { seeChangesIn } from "../seeChanges";
import { FileIcon } from "./FileIcon";
import { MessageAttachments } from "./attachments/MessageAttachments";
import { CommandBubble, commandLine } from "./CommandBubble";
import { Tooltip } from "../ui/Tooltip";
import { displayModel, displayModelFull } from "../modelName";
import { McpNotice, ProseNotice, retryAnchor } from "./transcript/notices";
import { UserText } from "./transcript/UserText";

/** Prose beside a `present`-only step that is an answer, not a lead-in: more than one line, or at
 *  least this long. The store's 12 such messages split cleanly: nine lead-ins of one line and at most
 *  94 characters, three answers of 1,012 to 3,569 characters over 12 to 58 lines (2026-10-19). */
const ANSWER_BESIDE_PRESENT = 200;

// Hover affordances for a message bubble: copy the raw text + the message's time,
// and (assistant answers, the message action row) a fork action that branches the whole
// conversation. Lives in a ZERO-HEIGHT strip under the bubble (absolute, inside the
// transcript's 20px gap) so revealing it on group-hover never shifts the layout. `ts` is unix
// seconds — canonical messages carry it, pre-stamp history doesn't, so the time simply omits
// itself when absent.
// Built once. `Date.toLocaleString(locale, options)` constructs a new Intl formatter on every call,
// and each bubble made two per render: with a few hundred bubbles re-rendering per streamed token
// that was 3.3 s of a 4 s profile (measured 2026-10-01, 120 turns).
const STAMP_SHORT = new Intl.DateTimeFormat([], {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});
const STAMP_FULL = new Intl.DateTimeFormat([], {
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "numeric",
  minute: "numeric",
  second: "numeric",
});

/** The models that answered a turn, the one that wrote its closing answer first, each with how
 *  many of the turn's calls it made. Read from the `model` the items carry (the messages'
 *  `usage` sidecar); an item without one (an older server) counts for nobody. */
export function turnModels(items: readonly Item[], closing?: string): { model: string; steps: number }[] {
  const steps = new Map<string, number>();
  for (const it of items) {
    if (it.kind !== "tool" && it.kind !== "assistant") continue;
    if (!it.model) continue;
    steps.set(it.model, (steps.get(it.model) ?? 0) + (it.kind === "tool" ? 1 : 0));
  }
  return [...steps.entries()]
    .map(([model, n]) => ({ model, steps: n }))
    .sort((a, b) => (a.model === closing ? -1 : b.model === closing ? 1 : b.steps - a.steps));
}

// One array per model, so a plain reply's memoized bubble sees the same prop every render
// (a fresh array each time re-rendered every bubble per streamed token, the 2026-10-01 lag).
const SOLO_MODELS = new Map<string, { model: string; steps: number }[]>();
function soloModels(model: string): { model: string; steps: number }[] {
  let hit = SOLO_MODELS.get(model);
  if (!hit) {
    hit = [{ model, steps: 0 }];
    SOLO_MODELS.set(model, hit);
  }
  return hit;
}

/** The footer's tooltip on a turn's tokens: each billing class that is not zero. */
function tokensTip(t: StepTokens): string {
  return (
    [
      ["Input", t.input],
      ["Cache read", t.cache_read],
      ["Cache write", t.cache_write],
      ["Output", t.output],
    ] as const
  )
    .filter(([, n]) => n > 0)
    .map(([label, n]) => `${label} ${formatTokens(n)}`)
    .join("\n");
}

const BubbleMeta = memo(function BubbleMeta({
  text,
  ts,
  align,
  onFork,
  forkDisabled,
  forkTitle,
  shown,
  models,
  bare,
  worked,
  failed = 0,
  declined = 0,
  hidden = 0,
  stopped,
  tokens,
}: {
  text: string;
  ts?: number;
  align: "left" | "right";
  onFork?: () => void;
  forkDisabled?: boolean;
  forkTitle?: string;
  /** No message to copy or fork from: the footer of a turn that ended without an answer. */
  bare?: boolean;
  /** How long the turn worked ("24s"). It sits here once the turn settles; the line of its
   *  own between the steps and the answer is gone (owner, 2026-10-13). */
  worked?: string;
  /** What went against the turn, said only when there is some: a clean turn's footer must
   *  not look like one that had a failure. */
  failed?: number;
  declined?: number;
  hidden?: number;
  stopped?: boolean;
  /** Always on screen, not only on hover: the closing answer of a settled turn (owner,
   *  2026-10-12: "khi xong thì hiện ra luôn"). */
  shown?: boolean;
  /** The models that answered this turn (turnModels): the first is named, the rest are "+N". */
  models?: { model: string; steps: number }[];
  /** What the turn spent, every step and cache reads included (turnTokens). */
  tokens?: StepTokens;
}) {
  const [copied, setCopied] = useState(false);
  const when = typeof ts === "number" ? new Date(ts * 1000) : null;
  // Both forms go to the clipboard: the Markdown for an editor, the drawn answer for Word or
  // Outlook (richCopy.ts). "Copied" waits for the write, which WebKit refuses outside a real
  // press, so a write that did nothing never says it worked.
  const copy = () =>
    copyAnswer(text)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1200);
      })
      .catch(() => {});
  const lead = models?.[0];
  const modelTip = (models ?? [])
    .map((m) => `${displayModelFull(m.model)}${m.steps ? ` · ${m.steps} step${m.steps === 1 ? "" : "s"}` : ""}`)
    .join("\n");
  const row = (
    <>
      {!bare && (
        <button
          className="hit-24 flex items-center gap-1.5 cursor-pointer hover:text-ink"
          data-testid="bubble-copy"
          title="Copy message"
          aria-label="Copy message"
          onClick={copy}
        >
          {copied ? (
            <>
              <Icon name="check" size={15} className="ico-draw" />
              Copied
            </>
          ) : (
            <Icon name="copy" size={15} />
          )}
        </button>
      )}
      {!bare && onFork && (
        <button
          className="hit-24 flex items-center cursor-pointer hover:text-ink disabled:opacity-40 disabled:cursor-default"
          data-testid="bubble-fork"
          title={forkTitle || "Fork session"}
          aria-label="Fork session"
          disabled={forkDisabled}
          onClick={onFork}
        >
          <Icon name="gitFork" size={15} />
        </button>
      )}
      {when && (
        <span data-testid="bubble-ts" title={STAMP_FULL.format(when)}>
          {STAMP_SHORT.format(when)}
        </span>
      )}
      {lead && (
        <Tooltip label={modelTip}>
          <span className="bubble-model" data-testid="bubble-model" tabIndex={0} aria-label={`Answered by ${modelTip.replace(/\n/g, ", ")}`}>
            <Icon name="sparkle" size={14} />
            {displayModel(lead.model)}
            {models!.length > 1 && <span className="bubble-model-more">+{models!.length - 1}</span>}
          </span>
        </Tooltip>
      )}
      {tokens && (
        <Tooltip label={tokensTip(tokens)}>
          <span className="bubble-tokens" data-testid="turn-tokens" tabIndex={0} aria-label={`Spent ${formatTokens(tokensTotal(tokens))} tokens`}>
            <Icon name="chart" size={14} />
            {formatTokens(tokensTotal(tokens))} tokens
          </span>
        </Tooltip>
      )}
      {worked && (
        <span className="bubble-worked" data-testid="turn-worked" title={`Worked for ${worked}`}>
          <Icon name="clock" size={14} />
          <span className="sr-only">Worked for </span>
          {worked}
        </span>
      )}
      {failed > 0 && (
        <span className="text-danger" data-testid="turn-summary-failed">
          {failed} failed
        </span>
      )}
      {declined > 0 && (
        <span className="text-danger" data-testid="stepgroup-declined">
          {declined} declined
        </span>
      )}
      {hidden > 0 && (
        <span className="text-warnInk" data-testid="stepgroup-hidden">
          {hidden} hidden by your filters
        </span>
      )}
      {stopped && (
        <span className="text-warnInk" data-testid="turn-stopped">
          stopped
        </span>
      )}
    </>
  );
  // The closing answer of a settled turn keeps its row on screen, in the flow, quiet until
  // hovered; every other message keeps the on-hover reveal that takes no room.
  if (shown)
    return (
      <div
        className={"bubble-meta shown select-none flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] leading-none whitespace-nowrap " + (align === "right" ? "justify-end" : "")}
        data-testid="bubble-meta"
      >
        {row}
      </div>
    );
  return (
    <div className="relative h-0 select-none">
      {/* The message action row at the reference size (owner 2026-09-19): 15px glyphs, 16px
          spacing, the muted ink the timestamp shares, and the FULL date — "Aug 18, 11:26 AM"
          reads at a glance where the bare clock time did not. Same quiet on-hover reveal. */}
      <div
        className={
          "absolute top-1 flex items-center gap-4 text-[13px] leading-none text-muted whitespace-nowrap opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity " +
          (align === "right" ? "right-0" : "left-0")
        }
        data-testid="bubble-meta"
      >
        {row}
      </div>
    </div>
  );
});

// The line helpers and the seconds formatter moved to ThinkRow.tsx with the row that used
// them; re-exported here because they are part of this module's public surface and are
// imported from it elsewhere. `workedFor` is `thoughtFor` under the name the turn header
// has always called it by — one formatter, two honest names.
export { firstLine, latestLine, thoughtFor as workedFor } from "./ThinkRow";

type DecisionItem = PlanRequestItem | ToolRequestItem;
type TurnItem =
  | ToolItem
  | ApprovalItem
  | AssistantItem
  | SteerItem
  | NoticeItem
  | QuestionItem
  | DecisionItem;

// A turn is drawn as a group of rows: what the agent said on its way (narration) and each
// step in one line of words, then the answer after the group (Transcript's flush, below). An
// approval is a chip on the row of the call it let through; one with no call to sit on (declined,
// mostly) keeps a "Wanted to …" row of its own. A row's arguments and result are a press away.

type TurnRow =
  | { type: "narr"; text: string; reasoning?: string; ts?: number }
  | { type: "steer"; text: string; ts?: number; from?: "agent" }
  | { type: "notice"; item: NoticeItem }
  | { type: "qa"; item: QuestionItem }
  | { type: "decision"; label: string; text: string; icon: IconName; declined: boolean }
  | { type: "step"; tool: ToolItem; approval?: ApprovalItem }
  // A run of delegated `task` calls folds into ONE delegation card (DelegationCard): the
  // aggregate header, the main-agent root and one node per child — the reference design's subagent
  // topology, drawn from the same rows. Nodes are the doorways into the child sessions.
  | { type: "subagents"; items: DelegationItem[]; ts?: number }
  | { type: "explore"; entries: ExploreEntry[]; ts?: number }
  | { type: "server"; identity: ServerIdentity; tools: ToolItem[]; ts?: number }
  | { type: "family"; familyKey: string; tools: ToolItem[]; ts?: number }
  | { type: "ask"; approval: ApprovalItem };

/** A fold the model made itself is the same act as a compaction, with the summary's author
 *  moved, so it wears the same marker (owner, 2026-10-13). A fold still running or
 *  one that was refused stays a tool row: there the call is the thing to read. */
function foldMarker(tool: ToolItem, connectorTools: ConnectorToolIndex): Extract<TurnRow, { type: "notice" }> | null {
  if (tool.name !== "fold_context" || tool.status !== "ok") return null;
  const fold = rowSpec(tool, connectorTools).fold;
  return fold ? { type: "notice", item: foldNotice(fold.note, fold.card) } : null;
}

/** An item's own row, before approvals are paired and looks are folded. */
function rowOf(item: Exclude<TurnItem, ApprovalItem>, connectorTools: ConnectorToolIndex): TurnRow {
  switch (item.kind) {
    // A message that only thought is a row too (owner, 2026-09-04): "Thinking" while it runs,
    // "Thought · N seconds" after; without it the live half of the turn was gone.
    case "assistant":
      return { type: "narr", text: item.text, reasoning: item.reasoning, ts: item.ts };
    case "steer":
      return { type: "steer", text: item.text, ts: item.ts, from: item.from };
    case "notice":
      return { type: "notice", item };
    case "question":
      return { type: "qa", item };
    case "planreq": {
      const approved = item.resolved === "approved";
      return { type: "decision", label: approved ? "Plan approved" : "Plan rejected", text: approved ? "implement it now" : "changes requested", icon: "checklist", declined: !approved };
    }
    case "toolreq": {
      const installed = item.resolved === "installed";
      return { type: "decision", label: installed ? "Tool installed" : "Tool skipped", icon: "plug", declined: !installed, text: [item.tool, item.version].filter(Boolean).join(" ") };
    }
    default:
      return foldMarker(item, connectorTools) ?? { type: "step", tool: item };
  }
}

type StepRow = Extract<TurnRow, { type: "step" }>;

/** Each answered approval put on the row of the nearest call of the same tool that has none yet,
 *  before or after it, since an approval can arrive on either side of its call. One that was
 *  declined, or has no call, becomes an ask row where it stood. */
function pairApprovals(items: TurnItem[], rows: TurnRow[]): void {
  items.forEach((item, at) => {
    if (item.kind !== "approval") return;
    let nearest: StepRow | null = null;
    let distance = Infinity;
    items.forEach((other, i) => {
      if (other.kind !== "tool" || other.name !== item.name) return;
      const row = rows.find((r): r is StepRow => r.type === "step" && r.tool === other);
      if (!row || row.approval || Math.abs(i - at) >= distance) return;
      nearest = row;
      distance = Math.abs(i - at);
    });
    if (nearest && item.resolved !== "deny") (nearest as StepRow).approval = item;
    else rows.splice(items.slice(0, at).filter((it) => it.kind !== "approval").length, 0, { type: "ask", approval: item });
  });
}

function buildRows(items: TurnItem[], connectorTools: ConnectorToolIndex): TurnRow[] {
  // The plan's own row says how it was answered ("Plan approved"); the note of its own is only
  // drawn where there is no such row (an older transcript).
  const planRowed = items.some((it) => it.kind === "tool" && it.name === "propose_plan");
  const rows = items
    .filter((it): it is Exclude<TurnItem, ApprovalItem> => it.kind !== "approval" && !(it.kind === "planreq" && planRowed))
    .map((it) => rowOf(it, connectorTools));
  pairApprovals(items, rows);
  // Third pass: what reads as one line.
  //   · LOOKING (reading, searching, listing, git's read-only commands, looking a tool up):
  //     a run of them is one "Explored" line, the thoughts BETWEEN them included (owner,
  //     2026-10-13, design A). A thought before the first look, or right before something is
  //     done, stays a line: there it is the decision. A look that failed is never folded, and
  //     ends the run. One look alone is a plain row.
  //   · the shell family and one server's run of calls fold as before (>= 2).
  // A step carrying its own approval is never folded (its chip would be lost in a header).
  //
  // Delegated `task` calls are the exception: EVERY consecutive run of them — a singleton
  // included — folds into one delegation card (the aggregate header needs the group, and
  // one child is a group of one). An approval on the spawn does not block it: the chip
  // rides the node.
  const folded: TurnRow[] = [];
  let bucket: Extract<TurnRow, { type: "step" }>[] = [];
  let bucketKey: string | null = null;
  let subagents: DelegationItem[] = [];
  let looks: ExploreEntry[] = [];
  let lookSteps: Extract<TurnRow, { type: "step" }>[] = [];
  let waiting: { row: Extract<TurnRow, { type: "narr" }>; seconds?: number }[] = [];
  // When the last thing before a row ended, so a thought can say how long it took.
  let lastStamp: number | undefined;
  const flushSubagents = () => {
    if (!subagents.length) return;
    folded.push({ type: "subagents", items: subagents, ts: subagents[0].tool.ts });
    subagents = [];
  };
  const flush = () => {
    if (bucketKey === null) return;
    if (bucket.length >= 2) {
      const tools = bucket.map((r) => r.tool);
      if (bucketKey.startsWith("mcp:") || bucketKey.startsWith("connector:")) {
        const identity = serverIdentity(tools[0].name, connectorTools);
        if (identity) folded.push({ type: "server", identity, tools, ts: tools[0].ts });
        else for (const step of bucket) folded.push(step);
      } else {
        folded.push({ type: "family", familyKey: bucketKey, tools, ts: tools[0].ts });
      }
    } else for (const step of bucket) folded.push(step);
    bucket = [];
    bucketKey = null;
  };
  // The looking run ends: a line of its own when it holds two looks or more, else its one row.
  // Thoughts that came after its last look stay rows: what follows is not a look.
  const flushLooks = () => {
    if (lookSteps.length >= 2) folded.push({ type: "explore", entries: looks, ts: lookSteps[0].tool.ts });
    else for (const step of lookSteps) folded.push(step);
    for (const thought of waiting) folded.push(thought.row);
    looks = [];
    lookSteps = [];
    waiting = [];
  };
  const thoughtSeconds = (row: Extract<TurnRow, { type: "narr" }>) =>
    typeof row.ts === "number" && typeof lastStamp === "number" && row.ts > lastStamp ? row.ts - lastStamp : undefined;
  const stampOf = (row: TurnRow) =>
    row.type === "narr" ? row.ts : row.type === "step" ? (row.tool.endTs ?? row.tool.ts) : undefined;
  for (const row of rows) {
    const stamp = stampOf(row);
    if (row.type === "step" && row.tool.name === "task") {
      // A task spawn ends any run above it and joins the delegation card.
      flushLooks();
      flush();
      subagents.push(row.approval ? { tool: row.tool, approval: row.approval } : { tool: row.tool });
      if (typeof stamp === "number" && stamp > 0) lastStamp = stamp;
      continue;
    }
    flushSubagents();
    const thoughtOnly = row.type === "narr" && !row.text.trim() && !!row.reasoning;
    const isLook =
      row.type === "step" && !row.approval && row.tool.status !== "error" && row.tool.looks === true;
    if (thoughtOnly && lookSteps.length > 0) {
      // Between two looks it joins the run; before something done it will stay a row.
      waiting.push({ row, seconds: thoughtSeconds(row) });
      if (typeof stamp === "number" && stamp > 0) lastStamp = stamp;
      continue;
    }
    if (isLook) {
      flush();
      // A thought between two looks joins the run, with how long it took.
      for (const thought of waiting) looks.push({ kind: "thought", text: thought.row.reasoning ?? "", seconds: thought.seconds });
      waiting = [];
      looks.push({ kind: "tool", tool: row.tool });
      lookSteps.push(row);
      if (typeof stamp === "number" && stamp > 0) lastStamp = stamp;
      continue;
    }
    flushLooks();
    if (row.type === "step" && !row.approval) {
      // The shell family is its own header; a run against ONE server (MCP or connector)
      // folds under that server. Everything else stays a row a reader watches one at a time.
      const key = groupKey(row.tool.name) ?? serverIdentity(row.tool.name, connectorTools)?.key ?? null;
      if (key !== null) {
        if (key !== bucketKey) {
          flush();
          bucketKey = key;
        }
        bucket.push(row);
        if (typeof stamp === "number" && stamp > 0) lastStamp = stamp;
        continue;
      }
    }
    flush();
    folded.push(row);
    if (typeof stamp === "number" && stamp > 0) lastStamp = stamp;
  }
  flushLooks();
  flush();
  flushSubagents();
  return folded;
}

function LineText({ line, big }: { line: HumanLine; big?: boolean }) {
  return (
    <span
      className="min-w-0 leading-relaxed"
      style={big ? { fontSize: "var(--fs-body)" } : { fontSize: "13px" }}
    >
      <span className={big ? "text-ink" : "text-muted"}>{line.pre}</span>
      {line.obj && <span className="text-ink">{line.obj}</span>}
      {line.post && <span className={big ? "text-ink" : "text-muted"}>{line.post}</span>}
    </span>
  );
}

/**
 * Whether two renders of one turn group are equivalent.
 *
 * The transcript re-renders on every streaming delta (they arrive many times a second), and
 * without this every turn in the conversation re-renders with it: a 200-turn session pays 200
 * groups of work per token. The items array is rebuilt on each event, but the ITEMS keep their
 * identity — only the one that changed is a new object — so comparing element identity is both
 * correct and cheap. `running` and `liveActivity` ride along because the running header reads
 * them, and `startTs`/`live` because the clock does.
 *
 * Exported for its own test: a comparator that says "equal" when it should not is a silently
 * stale transcript, which no snapshot would catch.
 */
/** The `changes` receipt that belongs to the turn group at `index`: the first one after it,
 *  before the next turn group or the next user message. */
export function receiptAfter(
  blocks: Array<{ turn: TurnItem[] } | { item: Item }>,
  index: number,
): Extract<Item, { kind: "changes" }> | undefined {
  for (let i = index + 1; i < blocks.length; i++) {
    const block = blocks[i];
    if ("turn" in block) return undefined;
    if (block.item.kind === "user") return undefined;
    if (block.item.kind === "changes") return block.item;
  }
  return undefined;
}

/** The previous array while every element is the same object: an array identity that only
 *  changes when the turn's content does. */
export function useStableItems<T>(items: T[]): T[] {
  const ref = useRef(items);
  const prev = ref.current;
  if (prev !== items) {
    let same = prev.length === items.length;
    for (let i = 0; same && i < items.length; i++) same = prev[i] === items[i];
    if (!same) ref.current = items;
  }
  return ref.current;
}

const splitAssistantCache = new WeakMap<
  AssistantItem,
  { trace: AssistantItem; answer: AssistantItem }
>();

export function splitAssistant(item: AssistantItem): { trace: AssistantItem; answer: AssistantItem } {
  let pair = splitAssistantCache.get(item);
  if (!pair) {
    pair = {
      trace: {
        kind: "assistant",
        text: "",
        reasoning: item.reasoning,
        ...(typeof item.ts === "number" ? { ts: item.ts } : {}),
      },
      answer: {
        kind: "assistant",
        text: item.text,
        ...(typeof item.ts === "number" ? { ts: item.ts } : {}),
        ...(item.model ? { model: item.model } : {}),
        // The step's spend rides the answer, as the model does: left off, a turn whose answer
        // thought lost its footer's tokens (every DeepSeek answer, 2026-10-22).
        ...(item.tokens ? { tokens: item.tokens } : {}),
      },
    };
    splitAssistantCache.set(item, pair);
  }
  return pair;
}

export function sameTurn(a: TurnGroupProps, b: TurnGroupProps): boolean {
  if (a.live !== b.live || a.startTs !== b.startTs) return false;
  if (a.live && a.liveActivity !== b.liveActivity) return false;
  // The live tail changes with every delta; only the running group carries one.
  if (a.tail !== b.tail || a.thought !== b.thought) return false;
  if (a.liveAnswer !== b.liveAnswer || a.answersPrevTs !== b.answersPrevTs) return false;
  if (a.onForkSession !== b.onForkSession || a.sessionRunning !== b.sessionRunning) return false;
  if (a.answerLabel !== b.answerLabel) return false;
  if ((a.answers?.length ?? 0) !== (b.answers?.length ?? 0)) return false;
  for (let i = 0; i < (a.answers?.length ?? 0); i++) {
    if (a.answers![i] !== b.answers![i]) return false;
  }
  if (a.receipt !== b.receipt) return false;
  if (a.items.length !== b.items.length) return false;
  for (let i = 0; i < a.items.length; i++) {
    if (a.items[i] !== b.items[i]) return false;
  }
  return true;
}

const TurnGroup = memo(TurnGroupInner, sameTurn);

export interface TurnGroupProps {
  items: TurnItem[];
  /** The turn's receipt (the `changes` item that follows it): the engine's churn against the
   *  turn-start snapshot. The header prefers it, so the two numbers on one screen agree. */
  receipt?: Extract<Item, { kind: "changes" }>;
  live?: boolean;
  startTs?: number;
  onReveal?: (path: string) => void;
  liveActivity?: string;
  /** Open a delegated call's child session (its tab in the workspace panel). */
  onOpenChild?: (run: { runId: string; title: string }) => void;
  /** Promote a running delegated call to background (P6). */
  onPromoteTask?: () => void;
  /** Start a settled turn EXPANDED (the child session): the child's work is what the reader
   *  opened, so it must not need a second click. */
  defaultOpen?: boolean;
  /** The stream's in-flight step (Transcript's `liveTail`), drawn between the steps and the
   *  status line. Only the running group gets one. */
  tail?: ReactNode;
  /** The newest line of the trace the stream is writing (Transcript's `liveThought`). */
  thought?: string;
  /** The turn's answer: its trailing messages that called no tool. They sit between the
   *  steps and the status line in BOTH states (owner, 2026-09-30), off the timeline, so the
   *  answer that streamed is the answer that stays: it is not peeled into a new bubble
   *  after the status line when the turn ends, which re-mounted it (the "page refreshes
   *  once" the owner saw) and swapped it with "Worked for". */
  answers?: AssistantItem[];
  /** The stamp before the answers, for their Thought duration. */
  answersPrevTs?: number;
  /** The answer still streaming (LiveStep's "answer" half). Only the running group. */
  liveAnswer?: ReactNode;
  onForkSession?: () => void;
  /** The session is running (the answer's Fork waits for the turn to settle). */
  sessionRunning?: boolean;
  /** What this turn's answer is, said above it (a subagent's last answer is its report). */
  answerLabel?: string;
}

/** A rail station: a step's icon, or the dot a narration line or a note hangs on the rail. */
const RAIL_STATIONS =
  ".tool-row-leading, .think-row-leading, .delegation-card-icon, .live-call-ico, .turn-narr, .turn-note";

/**
 * Ends the rail at its last station instead of the bottom of its last row. The rest of that
 * row (under its icon) hung a stub of line pointing at nothing once the status line stopped
 * being the rail's end (owner, 2026-09-30, the "Thought · 26s" row over "Worked for"). The
 * stations sit in nested group wrappers, so CSS cannot pick the last one: it is measured on
 * every size change and written straight onto the element as `--rail-end`, with no render.
 */
function useRailEnd(ref: RefObject<HTMLDivElement | null>) {
  useLayoutEffect(() => {
    const rail = ref.current;
    if (!rail) return;
    const sync = () => {
      const box = rail.getBoundingClientRect();
      const stations = rail.querySelectorAll<HTMLElement>(RAIL_STATIONS);
      let last = -1;
      for (let i = stations.length - 1; i >= 0; i--) {
        const station = stations[i];
        const r = station.getBoundingClientRect();
        if (r.height === 0) continue;
        if (last >= 0 && r.bottom <= last) continue;
        // A dot station's centre is its ::before's, not the line box's.
        const dot = station.matches(".turn-narr, .turn-note")
          ? parseFloat(getComputedStyle(station, "::before").top) + 2.5
          : r.height / 2;
        const mid = r.top + (Number.isFinite(dot) ? dot : r.height / 2);
        // Below the rail's box is content clipped by a closing disclosure: not on screen.
        if (mid <= box.bottom && mid > last) last = mid;
      }
      if (last < 0) return;
      rail.style.setProperty("--rail-end", `${Math.max(0, box.bottom - last)}px`);
    };
    sync();
    // Nothing above can promise the stations are laid out when it runs: a transcript that
    // mounts under a switching session, or rows that land a frame after the group, measured
    // 0 and left the line at full length with no size change to trigger another look
    // (owner, 2026-09-30, the stub still under "Thought · 26s"; reproduced by opening a
    // session from another one). So it also looks again when rows are added or removed, when
    // the rail scrolls into view, and when a row's entry slide lands (a transform, which no
    // ResizeObserver sees).
    let frame = 0;
    const later = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(sync);
    };
    rail.addEventListener("animationend", later);
    rail.addEventListener("transitionend", later);
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(later);
    resize?.observe(rail);
    const mutation = typeof MutationObserver === "undefined" ? null : new MutationObserver(later);
    mutation?.observe(rail, { childList: true, subtree: true });
    const visible = typeof IntersectionObserver === "undefined" ? null : new IntersectionObserver(later);
    visible?.observe(rail);
    return () => {
      cancelAnimationFrame(frame);
      rail.removeEventListener("animationend", later);
      rail.removeEventListener("transitionend", later);
      resize?.disconnect();
      mutation?.disconnect();
      visible?.disconnect();
    };
  }, [ref]);
}

function TurnGroupInner({
  items,
  receipt,
  live,
  startTs,
  onReveal,
  liveActivity,
  onOpenChild,
  onPromoteTask,
  defaultOpen,
  tail,
  thought,
  answers,
  answersPrevTs,
  liveAnswer,
  onForkSession,
  sessionRunning,
  answerLabel,
}: TurnGroupProps) {
  // The turn's steps are always shown, running or settled (owner, 2026-09-28: "bỏ luôn cái
  // việc mà khi xong hết thì gộp gọn lại"). The settled turn used to fold into a
  // "› Worked for Ns" disclosure and drop to a quieter 13px, so finishing moved and shrank
  // everything the reader had just watched. Now the status line is the only thing that
  // changes: it sits UNDER the steps and turns from the live clock
  // into "Worked for Ns".
  const railRef = useRef<HTMLDivElement>(null);
  useRailEnd(railRef);
  const connectorTools = useConnectorTools();
  // `items` is a fresh slice on every Transcript render; the running group re-renders on
  // every streamed token, so hold one array while its elements are the same and derive the
  // rows from it once — new row arrays defeated the memo of every group row in a long turn.
  items = useStableItems(items);
  const rows = useMemo(() => buildRows(items, connectorTools), [items, connectorTools]);
  const tools = items.filter((it): it is ToolItem => it.kind === "tool");
  const running = live || tools.some((t) => t.status === "…");
  // Who answered this turn, for the closing answer's footer: the model that wrote it first.
  const closingModel = answers && answers.length > 0 ? answers[answers.length - 1].model : undefined;
  const closingModels = useMemo(
    () => turnModels([...items, ...(answers ?? [])], closingModel),
    [items, answers, closingModel],
  );

  const declined = items.filter((it) => it.kind === "approval" && it.resolved === "deny").length;
  // What this turn HANDED OVER: the files its `present` calls declared, each with the
  // model's own line about it. Read off the turn's own items, so the tail cannot disagree
  // with the steps above it — the same rule the summary and the rows already follow.
  const delivered = useMemo(() => declaredFiles(items), [items]);
  // What the turn SHOWED: its visuals belong to its answer, drawn where the answer
  // places them. Until the answer exists, each one is drawn under its own step row.
  // Recomputed when a visual is shown, not on every streamed token (the items array is new
  // per token; a new `visuals` would redraw the memo'd answer bubble each time).
  const visualKey = items
    .map((it) => (it.kind === "tool" && it.name === "show_visual" && it.presentation?.card ? (it.callId ?? it.id) : ""))
    .filter(Boolean)
    .join("\u0000");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const visuals = useMemo(() => turnVisuals(items), [visualKey]);
  // Once the turn has its closing answer, each visual is either drawn in it or left out of it
  // (a draft): the rows read which. Keyed by a string, not the array: the turn's items are a
  // fresh array on every streamed token, and a fresh map here would redraw every memo'd row.
  const closing = answers && answers.length > 0 ? answers[answers.length - 1].text : null;
  const placedKey = useMemo(() => {
    if (closing === null || visuals.length === 0) return "";
    const drawn = new Set(drawnInAnswer(closing, visuals));
    return visuals.map((visual) => `${drawn.has(visual.callId) ? "answer" : "aside"}:${visual.callId}`).join("\u0000");
  }, [closing, visuals]);
  const placedVisuals = useMemo(
    () =>
      new Map(
        (placedKey ? placedKey.split("\u0000") : []).map((entry) => {
          const at = entry.indexOf(":");
          return [entry.slice(at + 1), entry.slice(0, at) as VisualPlace] as const;
        }),
      ),
    [placedKey],
  );
  const hiddenTotal = tools.reduce((n, t) => n + (t.hidden || 0), 0);
  // What the turn TOUCHED, beside how long it ran. A reader who looked away for the middle of
  // a turn gets the outcome from the header instead of scrolling the rows: files and their
  // churn, how many commands ran, and whether anything failed. Derived from the same items
  // the rows are built from, so the two can never disagree.
  const summary = useMemo(() => summarizeTurn(items, receipt), [items, receipt]);
  // The header is ONE clock in both states (owner call 2026-09-04): seconds tick while the
  // turn runs — "how long has this been going" is the live question, and the settled line
  // keeps the same answer as "Worked for Ns". Derived from the canonical stamps, first→last;
  // pre-stamp history and single-instant groups fall back to the step count.
  const stamps = items
    .flatMap((it) => [(it as { ts?: number }).ts, (it as { endTs?: number }).endTs])
    .filter((t): t is number => typeof t === "number" && t > 0);
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [running]);
  // A turn streaming its first words may have no stamp yet — the breaker that opened it
  // (the user's message, via startTs) is the true t0; the moment this component saw the
  // turn running is the last-resort fallback.
  const startedAt = useRef(0);
  if (running && !startedAt.current) startedAt.current = Date.now();
  const pool = startTs !== undefined ? [...stamps, startTs] : stamps;
  const firstTs = pool.length ? Math.min(...pool) : startedAt.current / 1000;
  const lastTs = stamps.length ? Math.max(...stamps) : undefined;
  const runSeconds = running
    ? Math.max(0, Date.now() / 1000 - firstTs)
    : stamps.length >= 1
      ? Math.max(...stamps) - firstTs
      : 0;
  // How long the settled turn worked, for its footer. Under a second there is nothing to say.
  const worked = !running && runSeconds >= 1 ? thoughtFor(runSeconds) : undefined;
  // Stopped before it answered for a call: amber on the row, and said in the footer.
  const stopped = !running && tools.some((t) => t.status === "interrupted");
  const failed = summary?.failed ?? 0;
  // What the turn spent: every step's tokens, the answers' included.
  const spent = useMemo(() => turnTokens([...items, ...(answers ?? [])]), [items, answers]);

  // The running status names the CURRENT activity, not a flat
  // "Working": the stream's own block ("Thinking", a call being written), else the latest
  // in-flight tool's family, else "Working". It no longer guesses "Thinking" from the last
  // ROW: a trace becomes an item only once its message is complete, so a trailing Thought
  // item is finished — reading it as live put two "Thinking" lines on screen while the
  // model was waiting on a tool (owner report 2026-09-28).
  const lastRunningTool = [...tools].reverse().find((t) => t.status === "…");
  const activity =
    live && liveActivity
      ? liveActivity
      : lastRunningTool
        ? activityLabel(lastRunningTool.name, lastRunningTool.looks)
        : "Working";

  const body = rows.length === 0 ? null : (
    <div
      className="mt-1 flex flex-col gap-0.5"
      // The steps speak at the ANSWER's size in both states, so settling changes no size.
      style={{ fontSize: "var(--fs-body)" }}
    >
      {rows.map((row, i) => {
        // The Thought row's honest duration: the clock between the previous event's
        // stamp and this block's own (the trace started when the last thing ended).
        // `prevStamp` walks the rows ahead of this one — a narration ends at its own ts,
        // a tool at its endTs — and the FIRST row measures from the breaker that opened
        // the turn (startTs, the user's message), which is the wait the reader felt.
        // An item's trace is always settled: the live one is the stream's, shown on the
        // status line (see `thought`).
        let prevStamp = startTs;
        for (let j = 0; j < i; j++) {
          const r = rows[j];
          const end =
            r.type === "narr"
              ? r.ts
              : r.type === "step"
                ? (r.tool.endTs ?? r.tool.ts)
                : undefined;
          if (typeof end === "number" && end > 0) prevStamp = end;
        }
        const thoughtSecs =
          row.type === "narr" &&
          row.reasoning &&
          typeof row.ts === "number" &&
          typeof prevStamp === "number" &&
          row.ts > prevStamp
            ? row.ts - prevStamp
            : undefined;
        if (row.type === "narr")
          return (
            <div key={i}>
              {row.reasoning && (
                <ThinkRow
                  text={row.reasoning}
                  duration={thoughtSecs}
                />
              )}
              {/* A trace-only row (the answer's own Thought, moved into the turn) has no
                  prose: an empty block still hung its rail dot and canvas ring, a stray
                  grey dot with a gap in the line under "Thought" (owner, 2026-09-28). */}
              {row.text.trim() !== "" && (
                <div
                  // No narrower measure than the answer's: the same sentence wrapped at 70ch
                  // read as a cut-off version of itself next to a bubble using the full
                  // column. One type in both states: the settled turn stays as it was read.
                  className="turn-narr reveal-up py-1"
                  data-testid="turn-narration"
                >
                  <Markdown text={stripVisualMarkers(row.text)} />
                </div>
              )}
            </div>
          );
        if (row.type === "steer")
          return (
            // An in-turn note, not a bubble: the user spoke INTO the running turn, and the
            // group above/below is the SAME turn. Right-aligned like a user message (it is
            // one) but compact and accent-marked, so it reads as a splice, not a turn break.
            // In a subagent's session the agent that started it can say something the same way
            // (`message_task`): the label names who spoke.
            <div className="flex justify-end py-1" key={i} data-testid="turn-steer" data-from={row.from || "person"}>
              <span className="inline-flex max-w-[78%] items-baseline gap-2 rounded-[12px_12px_4px_12px] border border-accent/25 bg-accentSoft/50 px-3 py-1.5 text-[13px] leading-relaxed text-ink">
                <span className="text-[10px] font-semibold uppercase tracking-[0.06em] text-accent shrink-0">
                  {row.from === "agent" ? "main agent" : "steered"}
                </span>
                <span className="whitespace-pre-wrap">{row.text}</span>
              </span>
            </div>
          );
        if (row.type === "notice" && row.item.divider)
          // A compaction or a fold inside the turn: the marker stands where it happened and
          // the turn goes on under it.
          return <CompactionMarker key={i} item={row.item} />;
        if (row.type === "notice" && row.item.wakeFacts)
          // A report that landed while the turn was at work: a note inside it.
          return <WakeMarker key={i} item={row.item} onOpenChild={onOpenChild} inline />;
        if (row.type === "notice")
          return (
            // A mid-turn line (a provider retry, a grant's project pointer) is a step of the
            // turn: left on the title column with a dot on the rail, not a centred banner.
            <div
              className={"notice turn-note " + (row.item.tone === "warn" ? "warn" : "")}
              key={i}
              data-testid="turn-note"
            >
              {row.item.text}
            </div>
          );
        if (row.type === "qa") {
          // The agent asked and the user answered: the card is transient, so without this
          // step the exchange left no trace once resolved (owner report 2026-09-18). It is a
          // step on the rail like the rest of the turn, not a bubble on the right (owner,
          // 2026-09-28), and a grouped ask's answers read as pairs, never as their JSON.
          const answer = String(row.item.resolved ?? "");
          const pairs = answerPairs(answer);
          return (
            <div className="tool-row rail-note" key={i} data-testid="turn-qa">
              <div className="tool-row-line">
                <span className="tool-row-leading">
                  <Icon name="chat" size={14} />
                </span>
                <span className="tool-row-title">Asked</span>
                {!pairs && (
                  <>
                    <span className="rv-dot" aria-hidden />
                    <span className="tool-row-summary" title={row.item.question}>
                      {row.item.question}
                    </span>
                  </>
                )}
              </div>
              <div className="rail-note-body">
                <FieldsBlock
                  rows={
                    pairs
                      ? pairs.flatMap((p) => [
                          { label: "Question", value: p.question },
                          { label: "Answer", value: p.answer, tone: "ok" as const },
                        ])
                      : [{ label: "Answer", value: answer || "No answer", tone: answer ? ("ok" as const) : ("muted" as const) }]
                  }
                  testId="turn-qa-fields"
                />
              </div>
            </div>
          );
        }
        if (row.type === "decision")
          return (
            // The human's decision on an ask that has no other trace: approving a plan,
            // granting a folder, installing a tool. The
            // card is transient and the ask's own row is hidden, so without this step the
            // transcript read as if the choice was never made. A step on the rail.
            <div className="tool-row rail-note" key={i} data-testid="turn-decision">
              <div className="tool-row-line">
                <span className="tool-row-leading">
                  <Icon name={row.icon} size={14} />
                </span>
                <span className="tool-row-title">{row.label}</span>
                {row.text && (
                  <>
                    <span className="rv-dot" aria-hidden />
                    <span className="tool-row-summary" title={row.text}>
                      {row.text}
                    </span>
                  </>
                )}
                <span
                  className={"rail-note-mark " + (row.declined ? "text-danger" : "text-ok")}
                  aria-hidden
                >
                  {row.declined ? "✕" : "✓"}
                </span>
              </div>
            </div>
          );
        if (row.type === "ask")
          return (
            <div className="flex items-baseline gap-2 py-0.5" key={i} data-testid="turn-ask">
              <span className={"w-3.5 text-center text-[11px] shrink-0 " + (row.approval.resolved === "deny" ? "text-danger" : "text-ok")}>●</span>
              <LineText line={humanizeAsk(row.approval.name, row.approval.args)} big={running} />
              {approvalChip(row.approval.resolved)}
            </div>
          );
        if (row.type === "subagents")
          return (
            <DelegationCard
              key={i}
              items={row.items}
              onOpenChild={onOpenChild}
              onPromoteTask={onPromoteTask}
              defaultOpen={defaultOpen}
            />
          );
        if (row.type === "explore")
          return <ExploreGroupRow key={i} entries={row.entries} onReveal={onReveal} />;
        if (row.type === "server")
          return (
            <McpGroupRow
              key={i}
              tools={row.tools}
              identity={row.identity}
              onReveal={onReveal}
            />
          );
        if (row.type === "family")
          return (
            <FamilyGroupRow
              key={i}
              familyKey={row.familyKey}
              tools={row.tools}
              onReveal={onReveal}
              onOpenChild={onOpenChild}
            />
          );
        return (
          <ToolCallRow
            key={i}
            tool={row.tool}
            approval={row.approval}
            onOpenFile={onReveal}
            onOpenChild={onOpenChild}
          />
        );
      })}
    </div>
  );

  const status = (
    <div
      className="turn-status"
      data-testid="turn-status"
      data-state="running"
      // Blocked on the person, not working: the wave stops and the label goes warm (the
      // motion is what says "still working", so its absence says "your turn").
      data-waiting={running && live && activity.startsWith("Waiting") ? "" : undefined}
    >
      <span className="turn-status-node" aria-hidden>
        {/* Three dots in a wave (design B): "working" without the spinner's
            loading-bar feel, and one small moving thing on the screen. */}
        <span className="turn-dots">
          <i />
          <i />
          <i />
        </span>
      </span>
      <div className="turn-status-main">
        <div className="turn-status-line">
          <span className="turn-status-label shimmer-text" data-testid="turn-status-label">
            {activity}
          </span>
          <span className="turn-status-clock" data-testid="turn-status-clock">
            {thoughtFor(runSeconds)}
          </span>
          {/* What has gone against the turn so far, while it is still running: the reader
              should not have to wait for the footer to learn a call failed. */}
          {failed > 0 && (
            <>
              {" · "}
              <span className="text-danger" data-testid="turn-summary-failed">
                {failed} failed
              </span>
            </>
          )}
          {declined > 0 && (
            <>
              {" · "}
              <span className="text-danger" data-testid="stepgroup-declined">
                {declined} declined
              </span>
            </>
          )}
          {hiddenTotal > 0 && (
            <>
              {" · "}
              <span className="text-warnInk" data-testid="stepgroup-hidden">
                {hiddenTotal} hidden by your filters
              </span>
            </>
          )}
        </div>
        {/* The trace being written right now, its newest line: it lives HERE rather
            than as its own "Thinking" row, so there is one live line, at the bottom. */}
        {running && thought && (
          <div className="turn-status-thought" data-testid="turn-status-thought">
            {thought}
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div className="stepgroup" data-state={running ? "running" : "settled"}>
      {/* The timeline: every step's icon sits on one vertical rail (x = 8px, the icon
          column). It ends at the steps: the answer and the status line are off it (owner,
          2026-09-30, Claude Code's shape), so neither is drawn as one more step. */}
      <div className="turn-rail" ref={railRef}>
        <PlacedVisuals.Provider value={placedVisuals}>{body}</PlacedVisuals.Provider>
        {tail}
      </div>
      {/* The settled turn has no line of its own here (owner, 2026-10-13): how long it
          worked is in its footer, under the answer. Running, the status line is the footer
          under the streaming answer, and the settled footer takes that place. */}
      {((answers && answers.length > 0) || liveAnswer) && (
        <div className="turn-answer" data-testid="turn-answer">
          {answerLabel && (
            <div className="turn-answer-label" data-testid="turn-answer-label">
              {answerLabel}
            </div>
          )}
          {(answers ?? []).map((answer, i) => (
            <AnswerBubble
              key={i}
              item={answer}
              prevTs={answersPrevTs}
              onForkSession={onForkSession}
              running={sessionRunning}
              inTurn
              // The tail belongs to the turn's CLOSING message — a turn with narration
              // answers before the real one (the model talked, called tools, talked again)
              // would otherwise stack the same file cards under every one of them.
              delivered={i === (answers ?? []).length - 1 ? delivered : undefined}
              visuals={i === (answers ?? []).length - 1 ? visuals : undefined}
              // Every answer of a settled turn keeps its row in the flow. The on-hover row
              // takes no room, so under an answer that another one follows it was drawn over
              // the next answer's first line (owner-hit 2026-10-13).
              footerShown={!running}
              {...(i === (answers ?? []).length - 1 ? { worked, failed, declined, hidden: hiddenTotal, stopped } : {})}
              models={i === (answers ?? []).length - 1 ? closingModels : undefined}
              tokens={i === (answers ?? []).length - 1 ? spent : undefined}
            />
          ))}
          {liveAnswer}
        </div>
      )}
      {running && status}
      {/* A turn that ended without an answer (it only worked, or it was stopped) is still
          closed by a footer: when, who, how long. There is no message to copy or fork from. */}
      {!running && !(answers && answers.length > 0) && !liveAnswer && (worked || spent || failed > 0 || declined > 0 || hiddenTotal > 0 || stopped) && (
        <div className="turn-foot" data-testid="turn-foot">
          <BubbleMeta
            text=""
            ts={lastTs}
            align="left"
            shown
            bare
            models={closingModels}
            worked={worked}
            failed={failed}
            declined={declined}
            hidden={hiddenTotal}
            stopped={stopped}
            tokens={spent}
          />
        </div>
      )}
    </div>
  );
}

/**
 * The answer: its trace (collapsed), its prose, the cards the model put in its text, and
 * the meta row. One component for a plain reply and a turn's answer slot, so the two
 * cannot drift. `inTurn` drops the entry animation: a turn's answer has been on screen
 * since its first streamed word, and replaying the entrance when the message settles is
 * what read as the page refreshing.
 */
const AnswerBubble = memo(function AnswerBubble({
  item,
  prevTs,
  onForkSession,
  running,
  inTurn = false,
  delivered,
  visuals,
  footerShown,
  models,
  worked,
  failed,
  declined,
  hidden,
  stopped,
  tokens,
}: {
  item: AssistantItem;
  prevTs?: number;
  onForkSession?: () => void;
  running?: boolean;
  inTurn?: boolean;
  /** This is the closing answer of a turn that has settled: its footer stays on screen. */
  footerShown?: boolean;
  /** The models that answered the turn (turnModels), for the footer. */
  models?: { model: string; steps: number }[];
  /** The turn's time and what went against it, for the footer of its closing answer. */
  worked?: string;
  failed?: number;
  declined?: number;
  hidden?: number;
  stopped?: boolean;
  /** What the turn spent, for the footer of its closing answer. */
  tokens?: StepTokens;
  /** The files this turn declared with `present`. Only the closing message of a turn gets
   *  them: they are the turn's, not the message's. */
  delivered?: DeliveredFile[];
  /** The visuals this turn showed, drawn inside the closing message where it placed
   *  them, or after its opening paragraph when it placed none. */
  visuals?: TurnVisual[];
}) {
  // The bubble's Thought duration: this message's stamp minus whatever preceded it.
  const answerThought =
    typeof item.ts === "number" && typeof prevTs === "number" && item.ts > prevTs
      ? item.ts - prevTs
      : undefined;
  // A file the turn delivered, mentioned in the sentence as inline code (`hello.md`),
  // resolves to the file itself — the rule: the exact path, or a basename that is unique
  // among this turn's files, and nothing at all when the token is ambiguous (a guess that
  // opens the wrong file is worse than a token that stays code).
  const mentionPaths = useMemo(() => (delivered ?? []).map((file) => file.path), [delivered]);
  const fileMentions = useMemo(
    () => (token: string) => {
      if (mentionPaths.includes(token)) return token;
      const matches = mentionPaths.filter((path) => (path.split(/[\\/]/).pop() || path) === token);
      return matches.length === 1 ? matches[0] : undefined;
    },
    [mentionPaths],
  );
  // Thinking-only item (stopped mid-reasoning): just the disclosure, no bubble — without
  // it the message would render empty.
  if (!item.text && item.reasoning)
    return (
      <div>
        <ThinkRow text={item.reasoning} duration={answerThought} />
      </div>
    );
  return (
    <div className={"group bubble-assistant" + (inTurn ? "" : " reveal-up")}>
      {/* No "assistant" label (owner 2026-09-19): the bubble's placement and the action row
          already say whose message it is. The trace comes with the answer, collapsed and
          raw, the way the reference design renders reasoning in the assistant body like any other block. */}
      {item.reasoning && <ThinkRow text={item.reasoning} duration={answerThought} />}
      {visuals && visuals.length > 0 ? (
        <AnswerWithVisuals
          text={item.text}
          visuals={visuals}
          fallback
          renderText={(text) => <Markdown text={text} fileMentions={fileMentions} />}
        />
      ) : (
        <Markdown text={stripVisualMarkers(item.text)} fileMentions={fileMentions} />
      )}
      {/* What the model put in its own answer, rendered after the prose: the review comments
          it left (`:code-comment{…}`, read from the RAW text — Markdown stripped the
          directives), then the files `present` declared, as openable cards stacked under the
          turn, the way the reference design stacks its file rows. */}
      <CodeCommentCards comments={codeCommentsOf(item.text)} />
      <DeliveredFiles files={delivered ?? []} />
      <BubbleMeta
        text={stripVisualMarkers(item.text)}
        ts={item.ts}
        align="left"
        onFork={onForkSession}
        forkDisabled={!!running}
        forkTitle={running ? "Fork is available once the turn settles" : "Fork session"}
        shown={footerShown}
        models={footerShown ? models : undefined}
        // A plain reply has no steps to time; it says how long the answer took to come.
        worked={footerShown ? (worked ?? (!inTurn && answerThought !== undefined && answerThought >= 1 ? thoughtFor(answerThought) : undefined)) : undefined}
        failed={footerShown ? failed : 0}
        declined={footerShown ? declined : 0}
        hidden={footerShown ? hidden : 0}
        stopped={footerShown ? stopped : false}
        tokens={footerShown ? tokens : undefined}
      />
    </div>
  );
});

// Each `on…` that is left out takes its control with it: a surface that cannot do a thing never
// offers it, since an offer that cannot be kept is worse than none.
interface Props {
  items: Item[];
  onApprove: (decision: ApprovalDecision) => void;
  /** The session is working. Until it stops, the last turn's closing text is still narration:
   *  drawn as the answer early, each line flashed up as a bubble and then went into the group
   *  when the next call came (owner, 2026-07-13). */
  running?: boolean;
  /** What the stream is doing now ("Thinking", "Running a command"), for the running turn's
   *  status line. */
  liveActivity?: string;
  /** Run the failed turn again, with no new message: on the last error of an idle session only.
   *  Anywhere else the error is history. */
  onRetry?: () => void;
  /** An MCP error's "Open Connectors". */
  onOpenConnectors?: () => void;
  /** Show a file a step made, in the system's file manager. */
  onReveal?: (path: string) => void;
  /** Put back the files one step changed. */
  onUndo?: (callId: string) => Promise<{ ok: boolean; error?: string }>;
  /** Open a delegated run (`task`) in its own session view. */
  onOpenChild?: (run: { runId: string; title: string }) => void;
  /** Send a running delegated call to the background (P6). */
  onPromoteTask?: () => void;
  /** Fork the conversation from an answer's actions. */
  onForkSession?: () => void;
  /** Settled turns start expanded (the child session): the work IS the thing being read. */
  defaultOpen?: boolean;
  /** A name for the last settled answer (a subagent's session: "Its report"). */
  answerLabel?: string;
  /** What the stream is writing right now (LiveStep: a trace, a call being written, text) —
   *  not an item yet. It renders INSIDE the running turn, above its status line, so the
   *  status is the last thing on screen (owner, 2026-09-28: it stood above the live trace
   *  when the caller drew this after the transcript). With no running turn it goes last. */
  liveTail?: ReactNode;
  /** The answer half of the live step (LiveStep part="answer"): drawn in the running
   *  turn's answer slot, off the timeline. */
  liveAnswer?: ReactNode;
  /** The newest line of the trace being streamed, shown on the running turn's status line
   *  (the caller hides that trace's own row: one live line, not two). */
  liveThought?: string;
}

// The compaction marker: a divider — the label centred between two faint
// rules — made collapsible (owner call 2026-09-16). The row IS the control (chevron after
// the label, rotating 90°), and the token delta stays on the line so folding hides nothing
// worth keeping. It arrives closed, the way a fold's does (owner, 2026-10-13): a compaction
// that opened and wrote its summary out line by line pushed the conversation down while the
// turn went on, and was not streaming but a replay of text already whole.
function CompactionMarker({ item }: { item: Extract<Item, { kind: "notice" }> }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className={"notice compaction-divider" + (open ? " open" : "")}
      data-testid="compaction-divider"
    >
      <button
        className="compaction-divider-row"
        data-testid="compaction-toggle"
        aria-expanded={open}
        title={open ? "Fold the summary" : "Show the summary"}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="compaction-divider-line" aria-hidden="true" />
        <span className="compaction-label">
          <Icon name="fold" size={14} />
          <span className="compaction-divider-label">{item.text}</span>
          {(item.span || item.freed) && <span className="compaction-freed">{item.span ?? item.freed}</span>}
          {item.detail && <span className="compaction-kept">What it kept</span>}
          <svg
            className="compaction-chev"
            viewBox="0 0 16 16"
            width="11"
            height="11"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M6 3.5 10.5 8 6 12.5" />
          </svg>
        </span>
        <span className="compaction-divider-line" aria-hidden="true" />
      </button>
      {/* Opens and closes as a motion. It was mounted and unmounted on the click, so the
          summary was simply there, or gone, on the next frame (owner, 2026-10-13). */}
      {item.detail && (
        <Disclosure open={open} className="compaction-fold fold-fade">
          <div className="compaction-body" data-testid="compaction-summary">
            <Markdown text={item.detail} />
          </div>
        </Disclosure>
      )}
    </div>
  );
}

// The line that opens a turn the engine started (wakeNotice.ts): a finished subagent or a
// background command reported back. Between turns it is a boundary, the divider's shape;
// landed inside a running turn it is a note of that turn. It names what reported, and a
// subagent's line opens that subagent's session.
function WakeMarker({
  item,
  onOpenChild,
  inline = false,
  anchor,
}: {
  item: Extract<Item, { kind: "notice" }>;
  onOpenChild?: (run: { runId: string; title: string }) => void;
  inline?: boolean;
  /** The marker opens a turn nobody typed: the question line names it by these marks. */
  anchor?: number;
}) {
  const facts = item.wakeFacts!;
  const child = facts.kind === "task" ? facts.sessionId : undefined;
  const [showOutput, setShowOutput] = useState(false);
  const icon = facts.kind === "shell" ? "terminal" : facts.kind === "restart" ? "history" : "bot";
  const outputLines = facts.output ? facts.output.split("\n").length : 0;
  const body = (
    <>
      <Icon name={icon} size={14} />
      {facts.kind === "restart" ? (
        // The turn went on after Reeve's process ended. Said as what happened, quietly.
        // Its detail can carry a stopped command whole: the detail is cut, never the line's width
        // or "Reeve restarted" (a docker command pushed the transcript sideways, 2026-10-21).
        <>
          <b className="wake-title wake-fixed">{facts.verb}</b>
          {facts.detail && (
            <span className="wake-detail wake-detail-cut" title={facts.detail}>
              · {facts.detail}
            </span>
          )}
        </>
      ) : (
        <span className="wake-verb" data-failed={facts.failed || undefined}>
          {facts.title ? facts.verb : item.text}
        </span>
      )}
      {facts.title && (
        <b className={"wake-title" + (facts.kind === "shell" ? " wake-cmd" : "")} title={facts.title}>
          {facts.title}
        </b>
      )}
      {facts.title && facts.detail && <span className="wake-detail">· {facts.detail}</span>}
      {child && onOpenChild && (
        <button
          type="button"
          className="wake-open"
          data-testid="wake-open"
          onClick={() => onOpenChild({ runId: child, title: facts.title ?? "Subagent" })}
        >
          Open
        </button>
      )}
      {facts.output && (
        <button
          type="button"
          className="wake-open"
          aria-expanded={showOutput}
          data-testid="wake-output-toggle"
          onClick={() => setShowOutput((open) => !open)}
        >
          {showOutput ? "Hide output" : `Show output · ${outputLines} line${outputLines === 1 ? "" : "s"}`}
        </button>
      )}
    </>
  );
  // What a stopped command had printed, opened in place as a motion (DESIGN.md: every
  // show/hide animates both ways).
  const output = facts.output ? (
    <Disclosure open={showOutput} className="fold-fade">
      <pre className="wake-output" data-testid="wake-output">{facts.output}</pre>
    </Disclosure>
  ) : null;
  if (inline)
    return (
      <>
        <div className="notice turn-note wake-note" data-testid="turn-note">
          {body}
        </div>
        {output}
      </>
    );
  return (
    <div
      className="notice compaction-divider wake-marker"
      data-testid="wake-marker"
      {...(anchor !== undefined
        ? {
            "data-turn-anchor": anchor,
            "data-turn-kind": "engine",
            "data-turn-label": facts.title ? facts.verb : item.text,
            "data-turn-text": facts.title ?? "",
            "data-turn-icon": icon,
            "data-turn-ts": item.ts,
          }
        : {})}
    >
      <div className="compaction-divider-row">
        <span className="compaction-divider-line" aria-hidden="true" />
        <span className="compaction-label">{body}</span>
        <span className="compaction-divider-line" aria-hidden="true" />
      </div>
      {output}
    </div>
  );
}

// The revert boundary: the compaction divider's pattern — a centred label
// between two faint rules — with no disclosure, because there is no summary here. The
// hidden messages stay visible below it; the line says what the MODEL no longer sees.
function RevertBoundary({ item }: { item: Extract<Item, { kind: "notice" }> }) {
  return (
    <div
      className="notice compaction-divider revert-divider"
      data-testid="revert-divider"
    >
      <div className="compaction-divider-row">
        <span className="compaction-divider-line" aria-hidden="true" />
        <span className="compaction-label">
          <span className="compaction-divider-label">{item.text}</span>
          {typeof item.hidden === "number" && item.hidden > 0 && (
            <span className="compaction-freed" data-testid="revert-hidden">
              {item.hidden} hidden
            </span>
          )}
        </span>
        <span className="compaction-divider-line" aria-hidden="true" />
      </div>
    </div>
  );
}

// Item 4.1: what the turn actually changed, computed by the engine from the calls it
// saw. Deliberately NOT derived from the model's answer — "I converted all 200 files" is
// a claim, and a receipt that inherited the claim would be worth nothing.
//
// The reference design shape: ONE flat row — chevron, "N files changed", the churn — with the Undo
// at the right; expanding it lists the files (badge, name, folder tail, per-file churn,
// Open / Undo pills). The engine's non-file "did" lines ride inside the expansion as
// quiet rows instead of spaming the transcript. No "Looks right" / "That's wrong"
// buttons — a button that only dismisses itself teaches people to dismiss.
/** Rows a folded receipt lists before "Show N more". */
const RECEIPT_ROWS = 6;
/** Past this many files an opened receipt offers a filter. */
const RECEIPT_FILTER_AT = 20;

function ChangeReceipt({
  item,
  onUndo,
}: {
  item: Extract<Item, { kind: "changes" }>;
  onUndo?: (callId: string) => Promise<{ ok: boolean; error?: string }>;
  onReveal?: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  const [query, setQuery] = useState("");
  const [undone, setUndone] = useState<Record<string, boolean>>({});
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Undo all asks once, in place: the button becomes its own question for a few seconds.
  const [asking, setAsking] = useState(false);
  useEffect(() => {
    if (!asking) return;
    const timer = window.setTimeout(() => setAsking(false), 4000);
    return () => window.clearTimeout(timer);
  }, [asking]);
  const statsFor = (path: string) => item.stats?.find((s) => s.path === path);
  const deleted = new Set(item.deleted ?? []);
  const created = new Set(item.created ?? []);
  // The agent's own notes (its memory files) are not the person's files: they are said in one
  // quiet line inside, never counted as "files changed" (owner, 2026-10-13).
  const notes = new Set(item.notes ?? []);
  const files = Array.from(new Set([...(item.created ?? []), ...(item.overwritten ?? []), ...deleted])).filter(
    (path) => !notes.has(path),
  );
  // No files, no receipt: the "Done · …" lines only ever duplicated the step rows above, and a
  // turn that only wrote its notes has nothing this line should say.
  if (files.length === 0) return null;
  // Six rows, then a real control for the rest. Opened, the whole list lives in a bounded box
  // that scrolls; past RECEIPT_FILTER_AT files a filter finds one by name. It folds back.
  const needle = query.trim().toLowerCase();
  const matching = all && needle ? files.filter((p) => p.toLowerCase().includes(needle)) : files;
  const shown = all ? matching : files.slice(0, RECEIPT_ROWS);
  const withChurn = files.map((p) => ({ path: p, stat: statsFor(p) })).filter(({ stat }) => stat && (stat.added > 0 || stat.removed > 0));
  const totals = withChurn.reduce(
    (acc, { stat }) => ({ added: acc.added + (stat?.added ?? 0), removed: acc.removed + (stat?.removed ?? 0) }),
    { added: 0, removed: 0 },
  );
  const undoIds = Array.from(
    new Set(
      files.flatMap((p) => {
        const s = statsFor(p);
        return s?.undoable && s.callId ? [s.callId] : [];
      }),
    ),
  );
  const allUndone = undoIds.length > 0 && undoIds.every((id) => undone[id]);
  const undoableCount = files.filter((p) => statsFor(p)?.undoable && statsFor(p)?.callId).length;

  const runUndo = async (ids: string[]) => {
    setBusy(true);
    setFailed(null);
    setAsking(false);
    // Reverse order: each undo restores its call's pre-call content, so the newest
    // change must give way first or an earlier restore would be overwritten.
    let firstError: string | null = null;
    for (const id of [...ids].reverse()) {
      try {
        const r = onUndo ? await onUndo(id) : { ok: false, error: "undo unavailable" };
        if (r?.ok) setUndone((u) => ({ ...u, [id]: true }));
        else firstError = firstError ?? (r?.error || "Undo failed");
      } catch (e) {
        firstError = firstError ?? (e instanceof Error ? e.message : "Undo failed");
      }
    }
    if (firstError) setFailed(firstError);
    setBusy(false);
  };

  return (
    <div className="rc" data-testid="turn-changes" data-open={open} data-undone={allUndone || undefined}>
      <div className="rc-line">
        <button
          type="button"
          className="rc-toggle"
          data-testid="changes-toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <span className={"rc-chev" + (open ? " open" : "")} aria-hidden>
            <Icon name="chevronRight" size={12} />
          </span>
          <span className="rc-title" data-testid="changes-count">
            {files.length} {files.length === 1 ? "file" : "files"} changed
          </span>
          <Churn added={totals.added} removed={totals.removed} testId="receipt-diff" />
        </button>
        {/* Only what changed and by how much: the check's last line read as noise here, and a
            runner's colour codes reached it as boxes (owner, 2026-10-15). Review still says it. */}
        <span className="rc-actions">
          {allUndone ? (
            <span className="rc-note">Put back</span>
          ) : undoIds.length > 0 && onUndo ? (
            asking ? (
              <>
                <button type="button" className="rc-act danger" data-testid="receipt-undo-confirm" disabled={busy} onClick={() => void runUndo(undoIds)}>
                  Put {undoableCount === 1 ? "1 file" : `${undoableCount} files`} back?
                </button>
                <button type="button" className="rc-act" onClick={() => setAsking(false)}>
                  Keep
                </button>
              </>
            ) : (
              <button
                type="button"
                className="rc-act"
                data-testid="receipt-undo"
                title="Put every file in this change set back the way it was"
                disabled={busy}
                onClick={() => setAsking(true)}
              >
                <Icon name="undo" size={13} />
                Undo all
              </button>
            )
          ) : null}
        </span>
      </div>
      <Disclosure open={open} className="fold-fade">
        <div className="rc-body">
          {all && files.length > RECEIPT_FILTER_AT && (
            <input
              className="zc-filter"
              type="search"
              data-testid="receipt-filter"
              placeholder={`Filter ${files.length} files`}
              aria-label="Filter the changed files"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          )}
          <ScrollFade className={"rc-files scroll-fade" + (all ? " expanded" : "")} data-testid="receipt-files">
            {shown.map((path) => {
              const stat = statsFor(path);
              const fd = fileDisplay(path);
              const id = stat?.callId;
              const wasUndone = id ? undone[id] : false;
              const gone = deleted.has(path);
              const word = gone ? "deleted" : created.has(path) ? "new" : stat?.source === "shell" ? "by a command" : "";
              return (
                <div className="rc-file" key={path} title={path} data-testid="receipt-file" data-undone={wasUndone || undefined}>
                  {/* The row opens what changed in the file, in Review. A deleted file has
                      nothing left to show there but its removal, which Review still has. */}
                  <button type="button" className="rc-file-open" onClick={() => seeChangesIn(path)}>
                    {/* The file's own mark (a language, a tool), as the transcript's rows show
                        it. It wore the Files panel's one-ink kind icon for a morning; the owner
                        wanted the marks back here (2026-10-13). The panel stays one ink. */}
                    <FileIcon path={path} size={14} className="rc-file-ico" />
                    <span className="rc-file-name">{fd.name}</span>
                    <span className="rc-file-where">
                      {word && (
                        <span className="rc-word" data-word={word.replace(/\s.*/, "")}>
                          {word}
                        </span>
                      )}
                      {fd.dir}
                    </span>
                    {stat && <Churn added={stat.added} removed={stat.removed} testId="receipt-file-diff" />}
                  </button>
                  {wasUndone ? (
                    <span className="rc-note">Put back</span>
                  ) : id && stat?.undoable && onUndo ? (
                    <button
                      type="button"
                      className="rc-undo"
                      data-testid="receipt-file-undo"
                      aria-label={created.has(path) ? `Remove ${fd.name}` : `Put ${fd.name} back`}
                      title={created.has(path) ? `Remove ${fd.name}` : `Put ${fd.name} back`}
                      disabled={busy}
                      onClick={() => void runUndo([id])}
                    >
                      <Icon name="undo" size={13} />
                    </button>
                  ) : (
                    <span />
                  )}
                </div>
              );
            })}
            {all && needle && matching.length === 0 && <div className="rc-quiet">No changed file matches “{query.trim()}”</div>}
          </ScrollFade>
          {files.length > RECEIPT_ROWS && (
            <button
              type="button"
              className="zc-more"
              data-testid="receipt-more"
              aria-expanded={all}
              onClick={() => {
                setAll((v) => !v);
                setQuery("");
              }}
            >
              {all ? "Show less" : `Show ${files.length - RECEIPT_ROWS} more`}
            </button>
          )}
          {/* The check's command is not repeated here: a long command line read as noise under
              the files (owner, 2026-10-15). Review still names it. */}
          {notes.size > 0 && (
            <div className="rc-quiet" data-testid="receipt-notes">
              It also updated {notes.size} of its own notes.
            </div>
          )}
          {failed && <div className="rc-quiet text-danger">{failed}</div>}
        </div>
      </Disclosure>
    </div>
  );
}

/** A file or change set's churn: only the sides that moved. "+193 −0" read as a deletion
 *  of nothing in alarm red; a zero side now says nothing, and no churn renders nothing. */
function Churn({ added, removed, testId }: { added: number; removed: number; testId: string }) {
  if (added <= 0 && removed <= 0) return null;
  return (
    <span className="zc-churn" data-testid={testId}>
      {added > 0 && <span className="text-ok">+{added}</span>}
      {added > 0 && removed > 0 ? " " : null}
      {removed > 0 && <span className="text-danger">−{removed}</span>}
    </span>
  );
}

export function Transcript({ items, running, onRetry, onOpenConnectors, onReveal, onUndo, liveActivity, onOpenChild, onPromoteTask, onForkSession, defaultOpen, answerLabel, liveTail, liveThought, liveAnswer }: Props) {
  // Which rows show their design page's card: one a page a turn, at its latest change.
  const designCards = useMemo(() => designCardCalls(items), [items]);
  // A turn is the longest run of answers, calls and answered approvals between two things
  // that break one (a person's message, a connector's, a notice, a request). Its closing texts are
  // the answer, drawn after the group; the texts inside it are narration. A run with nothing done
  // in it is only bubbles, as a chat is.
  const blocks: Array<
    | { turn: TurnItem[]; live?: boolean; startTs?: number; answers?: AssistantItem[]; answersPrevTs?: number }
    | { item: Item; i: number; prevTs?: number }
  > = [];
  let run: TurnItem[] = [];
  // When the turn STARTED: the stamp of the breaker that opened it (the user's message,
  // almost always). Without it the first Thought row of every turn had nothing to measure
  // against and silently dropped its duration — the one row a reader most wants timed,
  // because it covers the wait between hitting send and the model saying anything.
  let breakerTs: number | undefined;
  let turnStartTs: number | undefined;
  // The last stamp SEEN, walked item by item: when a trailing assistant text is peeled off
  // as the answer bubble, its Thought row measures from whatever preceded it in the turn —
  // the last tool's endTs, or the breaker when the turn was thought-and-answered straight.
  let lastStamp: number | undefined;
  const flush = (live = false) => {
    if (!run.length) return;
    const turn = run;
    run = [];
    turnStartTs = breakerTs;
    const answers: AssistantItem[] = [];
    // The trailing messages are the answer, peeled off in both states: a group draws them in
    // its answer slot ABOVE its status line (TurnGroup), so peeling a live one no longer puts
    // the sentence below "Working" (the 2026-09-28 report that kept it inside). Live, a
    // message that also called tools is narration before them, not the answer: it stays a
    // step, instead of flashing into the answer slot for the moment before its calls land.
    {
      const trailing: AssistantItem[] = [];
      // Only a message with prose is an answer: a thinking-only one is a step (its Thought
      // row), so peeling it emptied the group and dropped the answer out of it.
      while (
        turn.length &&
        turn[turn.length - 1].kind === "assistant" &&
        (turn[turn.length - 1] as AssistantItem).text.trim() !== "" &&
        !(live && (turn[turn.length - 1] as AssistantItem).withCalls)
      )
        trailing.unshift(turn.pop() as AssistantItem);
      // An answer written beside `present`: the model wrote its whole answer in the message
      // that declared the files, so the answer sat inside "Worked" and the line after the call
      // read as the answer (owner, 2026-10-19; 3 of 70 `present` calls in the store). `present`
      // does no work, so prose that only calls it is part of the answer, not a step's lead-in.
      // A short line stays a step; live, it waits for the turn's answer or its end.
      {
        let k = turn.length;
        while (k > 0 && turn[k - 1].kind === "tool" && (turn[k - 1] as { name?: string }).name === "present") k--;
        const lead = k > 0 && k < turn.length ? turn[k - 1] : undefined;
        if (
          lead &&
          lead.kind === "assistant" &&
          (lead as AssistantItem).withCalls &&
          ((lead as AssistantItem).text.trim().includes("\n") ||
            (lead as AssistantItem).text.trim().length >= ANSWER_BESIDE_PRESENT) &&
          (!live || trailing.length > 0)
        ) {
          turn.splice(k - 1, 1);
          trailing.unshift(lead as AssistantItem);
        }
      }
      // The final assistant message carries BOTH the trace (`reasoning`) and the answer
      // (`text`). The trace is a step like any other, so it moves back INTO the turn and only
      // the text is peeled off as the answer — otherwise the "Thought" row sat outside "Worked
      // for Ns" (owner-hit). A thinking-only message has no answer to peel, so it keeps its
      // current shape (one ThinkRow, no bubble).
      const traces: AssistantItem[] = [];
      for (const answer of trailing) {
        if (answer.reasoning && answer.text.trim() !== "") {
          // Split once per stored item (WeakMap): fresh literals on every render made
          // `sameTurn` fail for every settled turn that thought, so each streamed token
          // re-rendered the whole history.
          const pair = splitAssistant(answer);
          traces.push(pair.trace);
          answers.push(pair.answer);
        } else {
          answers.push(answer);
        }
      }
      turn.push(...traces);
    }
    // A turn folds into "Worked for Ns" when it has any WORK — a tool/approval, OR a reasoning
    // trace. A bare Q&A that still thought gets the group, so the Thought sits INSIDE it and
    // only the answer is the bubble outside. A turn with neither is plain chat and
    // stays bubbles.
    const hasTrace = turn.some((it) => it.kind === "assistant" && !!it.reasoning);
    if (turn.some((it) => it.kind !== "assistant") || hasTrace)
      blocks.push({ turn, live, startTs: turnStartTs, answers, answersPrevTs: lastStamp });
    else {
      turn.forEach((t) => blocks.push({ item: t, i: -1, prevTs: lastStamp }));
      answers.forEach((a) => blocks.push({ item: a, i: -1, prevTs: lastStamp }));
    }
  };
  // The ask kinds whose decision is recorded by a compact note (see `TurnRow.decision`):
  // they are never turn breakers, and their RESOLVED state joins the run.
  const isAskKind = (it: Item) =>
    it.kind === "planreq" || it.kind === "toolreq";
  const isResolvedAsk = (it: Item): it is DecisionItem =>
    isAskKind(it) && !!(it as { resolved?: unknown }).resolved;

  /** Whether the turn goes on after item `i`: more of its work follows, or it is still running
   *  and this is the newest thing in it. */
  const goesOn = (i: number): boolean => {
    const next = items[i + 1];
    if (!next) return !!running;
    return next.kind === "tool" || next.kind === "assistant" || next.kind === "steer";
  };

  items.forEach((item, i) => {
    if (
      item.kind === "tool" ||
      item.kind === "assistant" ||
      // A steer is an INPUT to the running turn, not a turn breaker: pushing it into the
      // run keeps one continuous group (and one running clock) across the splice, which
      // is what the engine actually did.
      item.kind === "steer" ||
      // A flagged notice (a provider retry) is the same kind of mid-turn fact.
      (item.kind === "notice" && item.inline) ||
      // A compaction that lands while the turn is at work is a moment of that turn. As a
      // boundary it cut one turn into two groups, each with its own clock. It joins the run
      // when the turn has begun and goes on after it; between turns it stays a boundary.
      (item.kind === "notice" && !!item.divider && run.length > 0 && goesOn(i)) ||
      // An ANSWERED question is a resolved ask: it joins the run and leaves a compact
      // Q&A note, because once the card is gone nothing else in the transcript records
      // what the agent asked or what the user said.
      (item.kind === "question" && !!item.resolved) ||
      // A resolved plan/folder/tool ask leaves its decision note the same way.
      isResolvedAsk(item) ||
      (item.kind === "approval" && item.resolved)
    )
      run.push(item);
    else if (
      // Interactive asks are NEVER turn breakers. PENDING ones render in the composer
      // head and nothing here; a RESOLVED one joins the run as
      // the note above. Flushing on a resolved ask split one running turn into two
      // groups — the "Worked for 6s / Working for 14s" jump right where a question was
      // answered or a folder was granted, with an empty block where the card had been.
      (isAskKind(item) && !isResolvedAsk(item)) ||
      (item.kind === "question" && !item.resolved) ||
      (item.kind === "approval" && !item.resolved)
    ) {
      return;
    } else {
      flush();
      blocks.push({ item, i });
      const ts = item.kind === "connector" ? item.source.ts : (item as { ts?: number }).ts;
      if (typeof ts === "number" && ts > 0) breakerTs = ts;
    }
    // `lastStamp` walks EVERY item, not just breakers: the answer bubble's Thought row
    // measures from the last thing that actually happened in the turn (a tool's endTs
    // when there was one), which is the wait the reader sat through before the answer.
    const seen = (item as { ts?: number; endTs?: number });
    const end = Math.max(seen.ts ?? 0, seen.endTs ?? 0);
    if (end > 0) lastStamp = end;
  });
  flush(!!running);

  // The reference design shows the working clock the INSTANT a turn starts (owner call 2026-09-04), not
  // once the first thought lands. Until real activity builds a live group, an empty group
  // carries just the header line — timed from the message that started the turn; the first
  // streamed token hands the floor back to the plain reply path.
  let liveTurnAt = -1;
  blocks.forEach((b, bi) => {
    if ("turn" in b && b.live) liveTurnAt = bi;
  });
  const earlyWorking = !!running && liveTurnAt < 0;
  // The answer a name is given to: the last turn's, once nothing is running.
  let labelAt = -1;
  if (answerLabel && !running) {
    blocks.forEach((b, bi) => {
      if ("turn" in b ? (b.answers?.length ?? 0) > 0 : b.item.kind === "assistant" && b.item.text.trim() !== "") labelAt = bi;
    });
  }
  return (
    <ToolRowStateProvider>
      <DesignCards.Provider value={designCards}>
      <div className="transcript">
        <TurnAnnouncer items={items} running={!!running} />
          {[...blocks.map((block, bi) => {
            if ("turn" in block)
              return (
                <TurnGroup
                  items={block.turn}
                  receipt={receiptAfter(blocks, bi)}
                  live={block.live}
                  startTs={block.startTs}
                  onReveal={onReveal}
                  liveActivity={liveActivity}
                  onOpenChild={onOpenChild}
                  onPromoteTask={onPromoteTask}
                  defaultOpen={defaultOpen}
                  tail={bi === liveTurnAt ? liveTail : undefined}
                  thought={bi === liveTurnAt ? liveThought : undefined}
                  answers={block.answers}
                  answersPrevTs={block.answersPrevTs}
                  liveAnswer={bi === liveTurnAt ? liveAnswer : undefined}
                  onForkSession={onForkSession}
                  sessionRunning={!!running}
                  answerLabel={bi === labelAt ? answerLabel : undefined}
                  key={bi}
                />
              );
        const { item } = block;
        switch (item.kind) {
          case "connector":
            return <ConnectorMessageCard source={item.source} key={bi} />;
          case "user": {
            // A message sent with a slash command is drawn, copied and listed as the line the
            // person wrote ("/init keep it short"), not as the template it expanded to.
            const said = item.command ? commandLine(item.command) : item.text;
            return (
              <div
                className="group self-end max-w-[78%] flex flex-col items-end"
                key={bi}
                // The question line (session/TurnPin.tsx) reads which turn a scrolled-away
                // reader is inside of, and the list of everything they asked, from these marks.
                data-user-turn={bi}
                data-user-text={said}
                data-turn-anchor={bi}
                data-turn-kind="user"
                data-turn-text={said}
                data-turn-files={item.attachments?.length || undefined}
                data-turn-ts={item.ts}
              >
                {/* What the message carried sits above the bubble, out of it: the bubble is the
                    person's words, and a message that is only a picture has no empty bubble. */}
                {item.attachments && item.attachments.length > 0 && (
                  <MessageAttachments attachments={item.attachments} />
                )}
                {/* neutral bubble: the theme's soft chrome tint says "a surface",
                    direction says "you" — no accent edge, no fill trick, both themes read the
                    same quiet gray. */}
                {/* A pasted link has no space to wrap at: without `overflow-wrap: anywhere` it
                    widened the bubble past its column, and, the bubble being aligned right, the
                    overflow went left and was cut (a paste of cited URLs, 2026-10-29). */}
                {(item.text || item.command) && (
                  <div className="bubble-user max-w-full min-w-0 [overflow-wrap:anywhere] px-3.5 py-2.5 rounded-[14px_14px_4px_14px] bg-chromeHover text-ink text-[14px] leading-relaxed whitespace-pre-wrap">
                    {item.command ? <CommandBubble item={item} /> : <UserText text={item.text} />}
                  </div>
                )}
                <BubbleMeta text={said} ts={item.ts} align="right" />
              </div>
            );
          }
          case "assistant":
            if (bi === labelAt)
              return (
                <div className="turn-answer" data-testid="turn-answer" key={bi}>
                  <div className="turn-answer-label" data-testid="turn-answer-label">
                    {answerLabel}
                  </div>
                  <AnswerBubble item={item} prevTs={block.prevTs} running={running} footerShown models={item.model ? soloModels(item.model) : undefined} tokens={item.tokens} />
                </div>
              );
            return (
              <AnswerBubble
                key={bi}
                item={item}
                prevTs={block.prevTs}
                onForkSession={onForkSession}
                running={running}
                // A plain reply is a turn of its own: its footer stays once it is not the
                // message still being written.
                footerShown={!(running && bi === blocks.length - 1)}
                models={item.model ? soloModels(item.model) : undefined}
                tokens={item.tokens}
              />
            );
          case "planreq":
            // The plan's CONTENT is a step on the `propose_plan` row (registry → PlanBlock),
            // and its DECISION is the composer dock. Nothing renders here in either state —
            // a resolved plan in a separate bubble duplicated the row (mockup split).
            return null;
          case "changes":
            return <ChangeReceipt key={bi} item={item} onUndo={onUndo} onReveal={onReveal} />;
          case "notice":
            if (item.server && item.detail) return <McpNotice key={bi} item={item} onOpenConnectors={onOpenConnectors} />;
            if (item.command || item.title) return <ProseNotice key={bi} item={item} />;
            // The compaction marker draws a real transcript divider
            // shape: the label centred between two faint rules, made collapsible so the
            // summary it stands for is readable where the reading happens.
            if (item.divider) {
              return <CompactionMarker key={bi} item={item} />;
            }
            // The revert boundary — the same divider pattern, no fold.
            if (item.boundary === "revert") {
              return <RevertBoundary key={bi} item={item} />;
            }
            // What opened a turn the engine started: which thing reported back.
            if (item.wakeFacts) return <WakeMarker key={bi} item={item} onOpenChild={onOpenChild} anchor={bi} />;
            // A status line: with a summary it can open (what a compaction kept, which is not sent
            // to the model), and Retry on the error that ended the session's last turn.
            return (
              <div className={"notice " + (item.tone === "warn" ? "warn" : "")} key={bi}>
                {item.text}
                {item.detail && !item.server && (
                  <details className="notice-detail" data-testid="notice-detail">
                    <summary>What it kept</summary>
                    <pre className="notice-detail-body">{item.detail}</pre>
                  </details>
                )}
                {item.retriable && !running && onRetry && block.i === retryAnchor(items) && (
                  <button className="btn ml-2" data-testid="notice-retry" onClick={onRetry}>
                    Retry
                  </button>
                )}
              </div>
            );
          default:
            // Questions, approvals and the rest are drawn in the composer's place, not here.
            return null;
        }
      }),
        // The early clock sits in the SAME list, keyed by the index the real live group will
        // take when the turn's first item lands — so React keeps one instance across that
        // hand-over and the live tail inside it (a trace mid-thought, its clock) is not
        // remounted and reset.
        ...(earlyWorking
          ? [
              <TurnGroup
                items={[]}
                live
                startTs={breakerTs}
                liveActivity={liveActivity}
                tail={liveTail}
                thought={liveThought}
                liveAnswer={liveAnswer}
                onForkSession={onForkSession}
                sessionRunning={!!running}
                key={blocks.length}
              />,
            ]
          : []),
      ]}
      {!earlyWorking && liveTurnAt < 0 && liveTail}
      {!earlyWorking && liveTurnAt < 0 && liveAnswer}
      </div>
      </DesignCards.Provider>
    </ToolRowStateProvider>
  );
}
