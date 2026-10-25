// The agents of a background workflow run, under its row in the subagents roster.
//
// A run's agents are the RUNNER's children, a level below the session, so the roster
// listed the run as one line and nothing of the agents that did its work; its "Open" led to
// the runner, whose transcript is one call (owner, 2026-10-13). Opened, the row lists them, each
// with its state and the door into its own transcript.
//
// The list is the server's (`/children`), read when the row opens and again when the run's own
// state or line moves: never on a timer (background work is pushed, not polled).

import { useEffect, useState } from "react";
import { getSessionChildren, type ChildSessionRow } from "../../api/core";
import { Icon } from "../Icon";

/** `shop/cart.py (@general subagent)` → the task and the agent it went to. */
export function agentTitleParts(title: string, agent: string): { task: string; agent: string } {
  const match = /^(.*) \(@([^)]+) subagent\)$/.exec(title);
  return match ? { task: match[1], agent: match[2] } : { task: title, agent };
}

export interface WorkflowAgentsProps {
  /** The runner's session id. */
  runnerId: string;
  /** Read only while the row is open; the last list stays drawn while it closes. */
  active: boolean;
  /** Changes when the run's state or live line does, which is when its agents may have. */
  refreshKey: string;
  onOpenChild?: (child: { runId: string; title: string; group?: string }) => void;
  /** The run these agents belong to: said above an agent's name when it is opened. */
  group?: string;
}

export function WorkflowAgents({ runnerId, active, refreshKey, onOpenChild, group }: WorkflowAgentsProps) {
  const [agents, setAgents] = useState<ChildSessionRow[] | null>(null);
  useEffect(() => {
    if (!active) return;
    let current = true;
    getSessionChildren(runnerId)
      .then((rows) => current && setAgents(rows))
      .catch(() => current && setAgents([]));
    return () => {
      current = false;
    };
  }, [runnerId, active, refreshKey]);

  if (agents === null) return <span className="workflow-agents-empty">Reading its agents…</span>;
  if (agents.length === 0) return <span className="workflow-agents-empty">No agent has started.</span>;
  return (
    <ul className="workflow-agents" data-testid="workflow-agents">
      {agents.map((row) => {
        const { task, agent } = agentTitleParts(row.title, row.agent);
        const busy = row.status === "busy";
        const open = onOpenChild ? () => onOpenChild({ runId: row.session_id, title: task, ...(group ? { group } : {}) }) : undefined;
        return (
          <li key={row.session_id}>
            <button
              type="button"
              className="workflow-agent"
              data-state={busy ? "running" : "ended"}
              data-testid="workflow-agent"
              {...(open ? { onClick: open, title: `Open ${task}` } : { "aria-disabled": true })}
            >
              {/* Busy is the moving mark; an agent that is not busy has ENDED, which says
                  nothing about how — its own transcript does, one click away. */}
              <span className="subagent-mark" data-state={busy ? "running" : "ended"} aria-hidden>
                {busy ? <Icon name="bot" size={13} running /> : <span className="workflow-agent-dot" />}
              </span>
              <span className="workflow-agent-task">{task}</span>
              <span className="workflow-agent-who">{agent}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
