// A run of delegated `task` calls as ONE row of the turn, with its children listed under it.
//
// It was a topology card: a block filled with the paper grey holding a "Main agent" tile, a
// connector bus and a tile per child. That drew the parent, which the reader is already in,
// and spent a diagram on what is a list (owner, 2026-10-13). Now the head is a row in the
// grammar every tool row has (`Delegated · 2 tasks … 1 of 2 finished · 1m 18s`), and each child
// is one line: what it was asked, who it is, how far it got, and what it is doing right now.
// A child's line IS the doorway into its session: its tab in the workspace panel, beside
// the chat (`rail/SubagentPanel`). The child whose tab is on screen is marked here.
//
// The data is all client-side (`delegations.ts`): the `task` tool items, the parent's
// `tool_metadata` frames, the settle frame and the presentation's `run_steps`. A reloaded
// session draws the same card from the replayed rows.
//
// Drawing rules:
//   · auto-open while any child runs; the first user toggle wins and pins;
//   · nodes carry `status · duration`, the description, the live `↳` line while running and
//     `N steps` at rest;
//   · status is the mark at the head of the line; the header counts what failed or stopped;
//   · the group's elapsed is earliest start → latest end, ticking while live.

import { useEffect, useId, useMemo, useState, memo } from "react";
import { sameDelegations } from "./rowMemo";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import type { ApprovalItem } from "../types";
import type { ToolItem } from "../cards/cardModels";
import { useAgentColors } from "../subagents";
import { formatDuration } from "../ui/Elapsed";
import { Disclosure } from "../ui/Disclosure";
import {
  delegationElapsedMs,
  delegationHeaderLabel,
  delegationNodes,
  delegationSummary,
  nodeStatusLabel,
  stepsLabel,
  type DelegationNode,
} from "../delegations";
import { Icon } from "./Icon";
import { approvalChip } from "./ToolRow";
import { useIsShownChild } from "../shownChild";
import { getOpenFinishedTools } from "../cards/toolRowPrefs";

export interface DelegationItem {
  tool: ToolItem;
  approval?: ApprovalItem;
}

function DelegationNodeRow({
  node,
  approval,
  onOpen,
  onPromote,
}: {
  node: DelegationNode;
  /** The call's approval, when it had one: the human's decision is part of the record. */
  approval?: ApprovalItem;
  /** Open the child session this node spawned. Absent with nowhere to put it. */
  onOpen?: () => void;
  /** Promote a running foreground child to background (P6). Absent off the parent chart. */
  onPromote?: () => void;
}) {
  const running = node.outcome === "running";
  // In line for a slot: no child yet, so nothing moves and nothing promotes (and with no child
  // id there is nothing to open).
  const waiting = running && node.waiting !== null;
  const moving = running && !waiting;
  const openable = onOpen !== undefined;
  const promotable = moving && !node.background && onPromote !== undefined;
  const shown = useIsShownChild(node.tool.subagent?.sessionId ?? node.tool.runId);
  const onKeyDown = openable
    ? (event: ReactKeyboardEvent) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        onOpen();
      }
    : undefined;

  return (
    <div
      className="delegation-node"
      role="listitem"
      data-testid="task-row"
      data-outcome={node.outcome}
      data-waiting={waiting ? "true" : undefined}
      data-openable={openable ? "true" : "false"}
      data-shown={shown ? "true" : undefined}
    >
      <div
        className="delegation-node-header"
        {...(openable ? { role: "button", tabIndex: 0, onClick: onOpen, onKeyDown } : {})}
        title={[node.title, node.description].filter(Boolean).join(" · ")}
        aria-label={[node.title, node.description].filter(Boolean).join(": ")}
      >
        <span
          className="delegation-node-avatar"
          // The running mark: tests and the reader both find the live child by it.
          data-testid={moving ? "task-spinner" : "task-leading"}
          aria-hidden
        >
          <Icon
            name={node.outcome === "completed" ? "check" : node.outcome === "failed" ? "x" : node.outcome === "stopped" ? "stop" : "bot"}
            size={14}
            running={moving}
          />
        </span>
        <span className="delegation-node-copy">
          <span className="delegation-node-line">
            <span
              className={"delegation-node-desc" + (moving ? " shimmer-text" : "")}
              data-testid="task-desc"
            >
              {node.description !== "" ? node.description : node.title}
            </span>
            {approval?.resolved && approvalChip(approval.resolved)}
            <span className="delegation-node-facts">
              <span className="delegation-node-title" data-testid="task-title" style={{ color: node.color }}>
                {node.title}
              </span>
              {node.background && (
                <span className="delegation-node-background" data-testid="task-background">
                  (background)
                </span>
              )}
              {node.model !== null && (
                // The model the child actually runs on: after a route override that is a
                // different id than the session's.
                <span className="delegation-node-model" data-testid="task-model" title={node.model}>
                  {node.model}
                </span>
              )}
              <span className="delegation-node-status" data-testid="task-status">
                {nodeStatusLabel(node)}
                {!running && node.durationMs !== null ? ` · ${formatDuration(node.durationMs)}` : ""}
              </span>
            </span>
          </span>
          {node.live !== null && (
            <span className="delegation-node-live" data-testid="task-live">
              {node.live}
            </span>
          )}
          {!running && node.live === null && node.steps !== null && (
            <span className="delegation-node-steps" data-testid="task-steps">
              {stepsLabel(node.steps)}
            </span>
          )}
          {node.failure !== null && node.failure !== "" && (
            <span className="delegation-node-error" data-testid="task-error-body">
              {node.failure}
            </span>
          )}
        </span>
        {/* One group, so a narrow column moves both under the task together. */}
        {(openable || promotable) && (
          <span className="delegation-node-actions">
            {openable && (
              <span className="delegation-node-open" data-testid="task-open" aria-hidden>
                {shown ? "Shown" : "Open"}
              </span>
            )}
            {promotable && (
              <button
                type="button"
                className="tool-run-open task-promote"
                data-testid="task-promote"
                title="Keep this subagent running while you continue"
                onClick={(event) => {
                  event.stopPropagation();
                  onPromote();
                }}
              >
                Run in background
              </button>
            )}
          </span>
        )}
      </div>
    </div>
  );
}

function DelegationCardInner({
  items,
  onOpenChild,
  onPromoteTask,
  defaultOpen,
}: {
  items: DelegationItem[];
  /** Open a node's child session (its tab in the workspace panel), one per node. */
  onOpenChild?: (run: { runId: string; title: string }) => void;
  /** Promote this session's running foreground children to background (P6). */
  onPromoteTask?: () => void;
  /** A child session's own transcript starts its cards expanded, like its turns. */
  defaultOpen?: boolean;
}) {
  const colors = useAgentColors();
  const nodes = useMemo(
    () => delegationNodes(items.map((it) => it.tool), colors),
    [items, colors],
  );
  const summary = useMemo(() => delegationSummary(nodes), [nodes]);
  const live = summary.running > 0;
  const [userToggle, setUserToggle] = useState<boolean | null>(null);
  // Open while its children run, in every setting: its rows carry Run in background and the
  // live line of what each child is doing, which a folded header would hide. Once they finish
  // it folds, or stays open when the person asked for finished work to stay open (Settings ▸
  // General ▸ Open tools when they finish).
  const [sawLive, setSawLive] = useState(live);
  if (live && !sawLive) setSawLive(true);
  const open = userToggle ?? (live || defaultOpen === true || (sawLive && getOpenFinishedTools()));
  const bodyId = useId();

  // The elapsed metric ticks while any child runs — the same clock the turn header keeps,
  // scoped to the spawns on this card.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [live]);
  const elapsedMs = delegationElapsedMs(summary, nowMs);

  const count = `${summary.total} task${summary.total === 1 ? "" : "s"}`;
  const metrics = [
    `${summary.finished} of ${summary.total} finished`,
    summary.failed > 0 ? `${summary.failed} failed` : "",
    summary.stopped > 0 ? `${summary.stopped} stopped` : "",
    elapsedMs !== null ? formatDuration(elapsedMs) : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      className={"delegation-card" + (open ? " open" : "") + (live ? " active" : "")}
      data-testid="delegation-card"
    >
      <button
        type="button"
        className="delegation-card-head"
        data-testid="delegation-card-head"
        aria-expanded={open}
        aria-controls={bodyId}
        title={delegationHeaderLabel(summary)}
        onClick={() => setUserToggle(!open)}
      >
        <span className="delegation-card-icon" aria-hidden>
          <Icon name="bot" size={14} running={live} />
        </span>
        <span className={"delegation-card-label" + (live ? " shimmer-text" : "")}>Delegated</span>
        <span className="rv-dot" aria-hidden />
        <span className="delegation-card-count">{count}</span>
        <span className={"delegation-card-chev" + (open ? " open" : "")} aria-hidden>
          <Icon name="chevronRight" size={12} />
        </span>
        <span className="delegation-card-metrics" data-testid="delegation-metrics">
          {metrics}
        </span>
      </button>
      <Disclosure open={open}>
        <div className="delegation-card-body" id={bodyId}>
            <div className="delegation-agents" role="list" aria-label="Delegated tasks">
              {nodes.map((node, i) => {
                const childId = node.tool.subagent?.sessionId ?? node.tool.runId;
                return (
                  <DelegationNodeRow
                    key={node.key}
                    node={node}
                    {...(items[i]?.approval ? { approval: items[i].approval } : {})}
                    {...(onOpenChild && childId
                      ? {
                          onOpen: () => onOpenChild({ runId: childId, title: node.description }),
                        }
                      : {})}
                    {...(onPromoteTask !== undefined ? { onPromote: onPromoteTask } : {})}
                  />
                );
              })}
            </div>
        </div>
      </Disclosure>
    </div>
  );
}

export const DelegationCard = memo(
  DelegationCardInner,
  (a, b) =>
    sameDelegations(a.items, b.items) &&
    a.onOpenChild === b.onOpenChild &&
    a.onPromoteTask === b.onPromoteTask &&
    a.defaultOpen === b.defaultOpen,
);
