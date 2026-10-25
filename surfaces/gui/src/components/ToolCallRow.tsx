// One tool call in the transcript: the collapsed row and its expanded body.
//
// This exists as its own component for one reason: a row needs two things that live in two
// different places — its spec (the registry) and whether it is expanded (the transcript's
// hoisted set, see rowState.tsx). Threading both through TurnGroup and buildRows would put
// the transcript's own state into a pure row-layout function.
//
// The expanded body itself carries everything: IN/OUT, the card, the failure copy, undo —
// there is no separate details panel (owner 2026-09-19).

import { useEffect, memo } from "react";
import type { ApprovalItem } from "../types";
import { terminalResultShell, type ToolItem } from "../cards/cardModels";
import { rowSpec } from "../cards/registry";
import { useRowOpen, useRowStore } from "../cards/rowState";
import { getOpenFinishedTools } from "../cards/toolRowPrefs";
import { useConnectorTools } from "../connectorTools";
import { shellHost, shellKind } from "../shellKind";
import { useSessionShell } from "../sessionShell";
import { RowBoundary } from "./RowBoundary";
import { ToolRow, approvalChip, hasBody } from "./ToolRow";
import { usePlacedVisual } from "./visual/VisualPlacement";
import { delegationLabel } from "../delegations";

/** How long a running call may stay folded before it opens itself: 500ms. */
const RUNNING_OPEN_DELAY_MS = 500;

/**
 * The row for one tool call.
 *
 * Delegated `task` calls never reach this component: `buildRows` folds every run of them
 * into one `DelegationCard`, whose nodes are the doorways into the child sessions (ADR
 * 0018, step E). This row draws everything else — including a legacy row that still
 * carries a `runId`, which is why the child-view affordance survives here.
 */
function ToolCallRowInner({
  tool,
  approval,
  onOpenFile,
  onOpenChild,
}: {
  tool: ToolItem;
  /** The call's approval, when it had one. Its outcome reaches the details panel. */
  approval?: ApprovalItem;
  onOpenFile?: (path: string) => void;
  /** A delegated call: open the child session (its tab in the workspace panel). */
  onOpenChild?: (run: { runId: string; title: string }) => void;
}) {
  return (
    <GenericToolCallRow
      tool={tool}
      approval={approval}
      onOpenFile={onOpenFile}
      onOpenChild={onOpenChild}
    />
  );
}

function GenericToolCallRow({
  tool,
  approval,
  onOpenFile,
  onOpenChild,
}: {
  tool: ToolItem;
  approval?: ApprovalItem;
  onOpenFile?: (path: string) => void;
  onOpenChild?: (run: { runId: string; title: string }) => void;
}) {
  // Keyed by the call id where there is one. It is the only identifier stable across a
  // reload: `id` is minted per browser session, so keying on it would forget every open row
  // the moment the history is replayed.
  const rowKey = tool.callId ?? tool.id;
  const store = useRowStore();
  const [open, toggle] = useRowOpen(rowKey, store);
  const connectorTools = useConnectorTools();
  const spec = rowSpec(tool, connectorTools);
  // A visual the turn's answer draws is not drawn twice: its row links to it, and one the
  // answer left out is folded on its row.
  const visualPlace = usePlacedVisual(spec.visual ? (tool.callId ?? tool.id) : undefined);
  // Which shell this call ran in: the session knows the machine (remote SSH vs this OS), and
  // the command may override it (`cmd /c`, `pwsh`). The terminal card and the row title both
  // read it; a non-terminal row passes neither.
  const sessionShell = useSessionShell();
  const command =
    spec.terminal?.command ??
    (typeof (tool.args as { command?: unknown } | undefined)?.command === "string"
      ? (tool.args as { command: string }).command
      : undefined);
  const shell =
    spec.variant === "terminal"
      ? shellKind(
          sessionShell,
          command,
          // What the model named, else what the result says it ran in.
          (tool.args as { shell?: unknown } | undefined)?.shell ?? terminalResultShell(tool),
        )
      : undefined;
  const host = spec.variant === "terminal" ? shellHost(sessionShell) : null;
  // With Open tools when they finish on, a command that is STILL running after half a second
  // opens itself: it has output to show (streamed live, `tool_output_delta`), and the
  // collapsed row is where that output would otherwise be hidden. Off (the default), nothing
  // opens on its own while it runs: the owner saw a row open and fold back again as a jump
  // (2026-10-21); the running row is one line with its clock, and a click shows the stream.
  //
  // Half a second, and the timer is cancelled when the call settles first:
  // a `git status` that returns in 80ms never unfurls the transcript, so a turn full of quick
  // reads stays a list of one-line rows.
  //
  // A call still next in line (`queued`) has nothing to stream yet, so its clock starts when
  // it is dispatched, not when it was written.
  const queued = tool.status === "…" && tool.queued === true;
  const running = spec.state === "running";
  useEffect(() => {
    if (!running) return;
    store.sawRunning(rowKey);
    if (queued || !hasBody(spec) || !getOpenFinishedTools()) return;
    const timer = window.setTimeout(() => store.setByHarness(rowKey, true), RUNNING_OPEN_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [running, queued, store, rowKey]);
  // What a call does to its row when it finishes. Only a finish this view watched counts (the
  // store remembers it ran, across the remount a finish causes): a call that was already
  // history when it mounted stays folded with the rest of the transcript, or a reloaded
  // session would open every old error at once.
  useEffect(() => {
    if (running || !store.takeFinish(rowKey)) return;
    // A row the person opened or closed stays as they left it (`setByHarness` leaves it).
    // A FAILURE opens, whatever the setting. Being told something went wrong and then having
    // to click to find out what is the difference between an error that can be acted on and
    // a red line. A plan the person sent back is not a failure to read: it stays folded.
    if (spec.state === "error") {
      if (!spec.plan) store.setByHarness(rowKey, true);
      return;
    }
    // Otherwise the setting decides. On, a finished call opens to show what it did, except a
    // stop (amber, nothing to read) and an answered plan, which folds behind its row. Off, it
    // stays one line (and folds, should the switch have been turned off while it ran).
    const show = getOpenFinishedTools() && spec.state !== "stopped" && !spec.plan && hasBody(spec);
    store.setByHarness(rowKey, show);
  }, [running, spec.state, store, rowKey]);
  return (
    <RowBoundary
      resetKey={`${tool.callId ?? tool.id}:${spec.state}:${open}`}
      title={spec.title ?? spec.name ?? "Tool call"}
    >
    <ToolRow
      spec={spec}
      open={open}
      onToggle={toggle}
      // The reader's OWN decision, recorded on the call it governed: a permission the
      // human granted is part of the turn's record, not only a fact in a details panel.
      trailing={approval?.resolved ? approvalChip(approval.resolved) : undefined}
      // `tool.ts` is unix SECONDS (the model's unit, like `m.ts` on replay); `Elapsed`
      // counts from epoch MILLISECONDS. Passing seconds straight through made a running
      // row read "29795733m 57s" (owner screenshot 2026-09-16) — the conversion belongs
      // at this boundary.
      startedAt={tool.ts !== undefined ? tool.ts * 1000 : undefined}
      queued={queued}
      onOpenFile={onOpenFile}
      visualInAnswer={visualPlace === "answer" ? (tool.callId ?? tool.id) : undefined}
      visualFolded={visualPlace === "aside"}
      shell={shell}
      host={host ?? undefined}
      tookMs={tool.durationMs}
      // A delegated call (a legacy row without metadata): the child's task is the title a
      // person recognises it by, and the row already has it in its args.
      onOpenChild={
        onOpenChild && tool.runId
          ? () => onOpenChild({ runId: tool.runId as string, title: delegationLabel(tool) })
          : undefined
      }
    />
    </RowBoundary>
  );
}

// Re-rendered only when its own call changed: a streamed token must not redraw every earlier row of the turn.
export const ToolCallRow = memo(ToolCallRowInner);
