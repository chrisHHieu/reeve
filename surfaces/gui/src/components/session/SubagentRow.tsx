// One child run's row — the unit the roster is made of (`SubagentsDock.tsx`, the subagents
// panel of the activity row), for a child that is working and for one that finished: a row
// that rendered two different ways would be two truths about the same child.
//
// Every fact is a derivation over rows the transcript already holds (`delegations.ts`), so a
// reloaded session draws the same rows. A RUNNING child's row moves with it: its
// clock ticks, its line says what it is doing now — a tool, or thinking, or writing — and its
// figure is what it has spent so far, told after each of its steps.

import { Icon } from "../Icon";
import { Elapsed, formatDuration } from "../../ui/Elapsed";
import { Disclosure } from "../../ui/Disclosure";
import { formatTokens } from "../../usage";
import { stepsLabel, type DelegationNode, type DelegationOutcome } from "../../delegations";
import { WorktreeHandback } from "./WorktreeHandback";
import { WorkflowAgents } from "./WorkflowAgents";
import { useIsShownChild } from "../../shownChild";

/** What each outcome is called in the row's quiet second line. */
export const OUTCOME_LABEL: Record<DelegationOutcome, string> = {
  running: "Running",
  settled: "Finished",
  completed: "Completed",
  failed: "Failed",
  stopped: "Stopped",
};

/** The child's id for navigation: the child SESSION when the frames named it, else the run
 *  id the model resumes it by (they are the same id in practice — this is the fallback). */
export function runIdOf(node: DelegationNode): string {
  return node.tool.subagent?.sessionId ?? node.tool.runId ?? node.key;
}

/** The row's right-hand fact: what the child has spent (so far, or in all once it settled);
 *  a child that has reported no spend yet shows its step count instead. */
export function factLabel(node: DelegationNode): string {
  if (node.tokens !== null) return `${formatTokens(node.tokens)} tok`;
  if (node.steps !== null) return stepsLabel(node.steps);
  return "";
}

/** The status mark: running is the MOVING glyph in the live accent, and a still one means
 *  the child SETTLED: a green check when it finished, a red cross when it failed, an amber
 *  stop when it was stopped. A green mark on work still in flight would read as finished.
 *  A child whose turn ended while its call waits for the batch's other results wears the
 *  still bot: it stopped, but no check may be drawn before the result says how it went. */
export function StatusMark({ outcome, waiting = false }: { outcome: DelegationOutcome; waiting?: boolean }) {
  if (outcome === "running" && waiting) {
    // In line for a slot: nothing is working yet, so the mark does not move.
    return (
      <span className="subagent-mark" data-state="waiting" data-testid="subagent-waiting-mark" aria-hidden>
        <Icon name="bot" size={14} />
      </span>
    );
  }
  if (outcome === "running") {
    return (
      <span className="subagent-mark" data-state="running" data-testid="subagent-running-mark" aria-hidden>
        <Icon name="bot" size={14} running />
      </span>
    );
  }
  if (outcome === "settled") {
    return (
      <span className="subagent-mark" data-state="settled" data-testid="subagent-dot" aria-hidden>
        <Icon name="bot" size={14} />
      </span>
    );
  }
  return (
    <span className="subagent-mark" data-state={outcome} data-testid="subagent-dot" aria-hidden>
      <Icon name={outcome === "completed" ? "check" : outcome === "failed" ? "x" : "stop"} size={14} />
    </span>
  );
}

export interface SubagentRowProps {
  node: DelegationNode;
  /** This row's detail (the task the child was given) is showing. */
  open: boolean;
  onToggle: () => void;
  /** Open the child's transcript. Absent where the surface cannot navigate. */
  onOpenChild?: (child: { runId: string; title: string }) => void;
}

export function SubagentRow({ node, open, onToggle, onOpenChild }: SubagentRowProps) {
  const fact = factLabel(node);
  const running = node.outcome === "running";
  const duration = !running && node.durationMs !== null ? formatDuration(node.durationMs) : "";
  const child = { runId: runIdOf(node), title: node.description || node.title };
  const openChild = onOpenChild ? () => onOpenChild(child) : undefined;
  // The child whose tab is on screen in the panel is marked, as its line in the chat is.
  const shown = useIsShownChild(child.runId);
  return (
    <li
      className="subagent-row"
      data-outcome={node.outcome}
      data-openable={openChild ? "true" : "false"}
      data-shown={shown ? "true" : undefined}
      data-testid="subagent-row"
    >
      <button
        type="button"
        className="subagent-row-head"
        // The row IS the door into the child's transcript when the surface can navigate; with
        // nowhere to go it stays a plain row and only the disclosure below is interactive.
        {...(openChild ? { onClick: openChild, title: `Open ${child.title}` } : { "aria-disabled": true })}
        data-testid="subagent-open-main"
      >
        <StatusMark outcome={node.outcome} waiting={node.waiting !== null} />
        <span className="subagent-copy">
          <span className="subagent-line">
            {/* The task the child was given is the name a reader recognises. Who the child
                is sits with its figures on the right (owner, 2026-10-13). */}
            <span className={"subagent-name" + (running && !node.waiting ? " shimmer-text" : "")} title={node.description}>
              {node.description || node.title}
            </span>
            {node.background && <span className="subagent-tag">(background)</span>}
            {node.kept && (
              <span className="subagent-tag waiting" data-testid="subagent-waiting">
                waiting for you
              </span>
            )}
          </span>
          {/* Under the name: what it is doing now, or why it did not finish. A child that
              finished cleanly is one line. */}
          {(node.live || (!running && node.outcome !== "completed")) && (
            <span className="subagent-sub">
              {node.live ? (
                <span className="subagent-live" data-testid="subagent-live">
                  {node.live}
                </span>
              ) : (
                <span className="subagent-meta">{node.failure || OUTCOME_LABEL[node.outcome]}</span>
              )}
            </span>
          )}
        </span>
        <span className="subagent-agent" style={{ color: node.color }}>
          {node.title}
        </span>
        {fact && (
          <span className="subagent-fact" data-testid="subagent-fact">
            {fact}
          </span>
        )}
        {running && node.startedAtMs !== null ? (
          <Elapsed from={node.startedAtMs} className="subagent-time" />
        ) : (
          duration && <span className="subagent-time">{duration}</span>
        )}
      </button>
      {/* The two affordances, kept apart on purpose: the row BODY opens the child's
          transcript (what a reader wants almost every time), and its own chevron expands what
          the child was asked to do. One control doing both made the common action need a
          second click. */}
      <button
        type="button"
        className="subagent-disclosure"
        aria-expanded={open}
        aria-label={open ? "Hide the child's task" : "Show the child's task"}
        data-testid="subagent-row-toggle"
        onClick={onToggle}
      >
        <span className={"subagent-chevron" + (open ? " open" : "")} aria-hidden>
          <Icon name="chevronDown" size={13} />
        </span>
      </button>
      {/* Work kept in the child's own copy: under the row, not behind its
          disclosure — it is the one thing here that waits on the person. */}
      {node.kept && node.childId !== null && <WorktreeHandback childId={node.childId} />}
      {/* The task opens under its row by height, and closes the same way. */}
      <Disclosure open={open} className="fold-fade subagent-fold">
        <dl className="subagent-body" data-testid="subagent-body">
          {node.failure !== null && (
            <>
              <dt>Ended</dt>
              <dd className="subagent-failure">{node.failure}</dd>
            </>
          )}
          {/* A workflow has no prompt: its row already names it, and its agents say the rest. */}
          {node.agentId !== "workflow" && (
            <>
              <dt>Asked to</dt>
              <dd className="subagent-prompt">{String(node.tool.args?.prompt ?? "").trim() || node.description}</dd>
            </>
          )}
          {node.live && (
            <>
              <dt>Now</dt>
              <dd>{node.live}</dd>
            </>
          )}
          {(node.model || node.steps !== null) && (
            <>
              <dt>Ran on</dt>
              <dd>{[node.model, node.steps !== null ? stepsLabel(node.steps) : ""].filter(Boolean).join(" · ")}</dd>
            </>
          )}
          {/* A background workflow's agents are the work: they are listed here, each a door
              into its own transcript (`WorkflowAgents`). */}
          {node.agentId === "workflow" && node.childId !== null && (
            <>
              <dt>Agents</dt>
              <dd>
                <WorkflowAgents
                  runnerId={node.childId}
                  active={open}
                  refreshKey={`${node.outcome}|${node.live ?? ""}`}
                  group={node.description || node.title}
                  onOpenChild={onOpenChild}
                />
              </dd>
            </>
          )}
        </dl>
      </Disclosure>
    </li>
  );
}
