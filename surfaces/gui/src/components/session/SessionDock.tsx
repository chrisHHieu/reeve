// What stands directly above the composer: the line that says an agent is asleep. The activity
// row (`ActivityRow.tsx`: the goal, subagents, jobs and to-dos) is the task's head under the top
// bar since 2026-10-13 (`TaskHead` below).

import type { ReactNode } from "react";
import { ActivityRow } from "./ActivityRow";
import type { GoalAction, GoalInfo } from "./GoalDock";
import type { DelegationNode } from "../../delegations";
import type { JobOutputBuffer } from "../../live/jobOutput";
import type { JobRow } from "../../api/jobs";

export interface SessionDockProps {
  activeInfo: any;
  running: any;
  send: any;
  todo: any;
  /** The session on screen: the jobs panel reads its own background shell tasks. */
  sessionId?: string;
  /** The session's goal, or null/undefined when none/loading — the strip hides for both. */
  goal?: GoalInfo | null | undefined;
  onGoalAction?: GoalAction;
  /** The session's delegated `task` runs, in spawn order (the subagents roster). */
  subagents?: readonly DelegationNode[];
  /** Open a child's transcript. Absent where the surface cannot navigate. */
  onOpenChild?: (child: { runId: string; title: string }) => void;
  /** The session's background jobs, as the engine last pushed them. */
  jobs?: readonly JobRow[];
  /** The session's streamed job output, by job id (`live/jobOutput.ts`). */
  buffers?: Record<string, JobOutputBuffer>;
  /** Forget some jobs' streamed output: the client-side half of the jobs' Clear. */
  onClearJobOutput?: (ids: readonly string[]) => void;
}

export function SessionDock(props: SessionDockProps) {
  const { activeInfo, running, send } = props;
  return (
    <>
      {/* A scheduled agent must never read as a dead one: while a self-wake is
          pending and no turn is running, say so and offer the obvious action. */}
      {activeInfo?.liveness === "sleeping" && !running && (
        <div className="sleep-strip" data-testid="sleep-strip">
          <span className="sleep-dot" />
          <span className="sleep-text">
            Sleeping
            {activeInfo.sleeping_until
              ? ` until ${new Date(activeInfo.sleeping_until).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
              : ""}
            {" — it wakes on its trigger. "}
            Talk to it anytime.
          </span>
          <button
            className="btn sm"
            data-testid="sleep-status-btn"
            onClick={() =>
              send(
                "Quick status check, please — what's moving, what's blocked, and does anything need me?",
              )
            }
          >
            Ask for a status
          </button>
        </div>
      )}
    </>
  );
}

/**
 * The task's head (layout step 2, owner 2026-10-13: "B frame, A inside"): under the top bar, one
 * row for where the task stands. The goal outlives every plan, the subagents and jobs are the
 * work being done now, the to-dos are the plan itself: that is the order of the segments; what
 * the session has amounted to (files, the last check, tokens) is at the right. It was the
 * activity row docked on the composer; at the top it is read before the conversation, and a
 * decision that takes the composer's place no longer pushes the plan out of sight.
 */
export function TaskHead(props: Omit<SessionDockProps, "activeInfo" | "send"> & { facts?: ReactNode }) {
  const { running, todo, goal, onGoalAction, subagents, onOpenChild, sessionId, jobs, buffers, onClearJobOutput, facts } = props;
  return (
    <ActivityRow
      placement="head"
      facts={facts}
      todo={todo}
      settled={!running}
      goal={goal}
      onGoalAction={onGoalAction}
      subagents={subagents}
      onOpenChild={onOpenChild}
      sessionId={sessionId}
      jobs={jobs}
      buffers={buffers}
      onClearJobOutput={onClearJobOutput}
    />
  );
}
