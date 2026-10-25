// ONE row above the composer for everything that is going on in the session: the goal it
// works toward, the subagents and background jobs it has, and its to-dos (redesign of
// 2026-10-12, DESIGN.md "One activity row").
//
// Four strips did this before, stacked, each 36px: the Goal a detached grey box, To-dos /
// Subagents / Jobs three white strips, and the subagent and job counts said a second time in
// the header. Now each is a SEGMENT of one row: an icon, its name, and the few words that
// carry its state ("2 running", "2 of 5"). A segment opens its panel under the row, one at a
// time. Nothing here is a progress bar and no line marks the open segment (owner,
// 2026-10-12): it is the one whose name is bold and whose chevron is turned.
//
// A segment is there while there is something to say: a goal that is not complete, a plan
// with steps, a subagent or a job the session has had. With nothing running, Subagents and
// Jobs read "N finished" in the quiet ink; that roster was a chip in the header.
//
// Each panel stays mounted and opens by height (0fr ↔ 1fr), so closing animates as opening
// does; closed, it is aria-hidden and inert: out of reach, not just out of sight.

import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Icon, type IconName } from "../Icon";
import { Elapsed } from "../../ui/Elapsed";
import type { TodoItem } from "../../types";
import type { DelegationNode } from "../../delegations";
import { delegationSummary, rosterHeadline } from "../../delegations";
import type { JobRow as JobRowData } from "../../api/jobs";
import type { JobOutputBuffer } from "../../live/jobOutput";
import { lastLine, liveJobs, shortCommand } from "../../jobView";
import { StatusGlyph, TodoList, progressCount, progressLabel } from "../TodoPanel";
import { GoalPanel, goalState, type GoalAction, type GoalInfo } from "./GoalDock";
import { SubagentsPanel } from "./SubagentsDock";
import { JobsPanel } from "./JobsDock";
import { StatusMark } from "./SubagentRow";

export type ActivityKey = "goal" | "subagents" | "jobs" | "todos";

/** How much of itself the row says: names and words, icons and numbers, or two lines. */
export type ActivityFit = "wide" | "medium" | "narrow";
const FITS: readonly ActivityFit[] = ["wide", "medium", "narrow"];

export interface ActivityRowProps {
  /** The standing plan (`todo_write`). */
  todo?: readonly TodoItem[];
  /** No turn is running: the plan is where the last one left it. */
  settled?: boolean;
  /** The session's goal, or null/undefined when none/loading. */
  goal?: GoalInfo | null;
  onGoalAction?: GoalAction;
  /** The session's delegated `task` runs, in spawn order. */
  subagents?: readonly DelegationNode[];
  /** Open a child's transcript. Absent where the surface cannot navigate. */
  onOpenChild?: (child: { runId: string; title: string }) => void;
  sessionId?: string;
  /** The session's background jobs, as the engine last pushed them. */
  jobs?: readonly JobRowData[];
  /** The session's streamed job output, by job id (`live/jobOutput.ts`). */
  buffers?: Record<string, JobOutputBuffer>;
  /** Forget some jobs' streamed output: the client-side half of the jobs' Clear. */
  onClearJobOutput?: (ids: readonly string[]) => void;
  /** Where the row stands: the task's head under the top bar (layout step 2, 2026-10-13), or
   *  docked on the composer as it was. The segments and their panels are the same in both. */
  placement?: "head" | "dock";
  /** What the session has amounted to (files, the last check, tokens), at the head's right. */
  facts?: ReactNode;
}

interface Segment {
  key: ActivityKey;
  /** The old strip's test id: the segment, its `-toggle` and its `-body` are named from it. */
  testId: string;
  icon: IconName;
  label: string;
  state?: string;
  title?: string;
  /** The few words that carry the state. Shown where the row has the width for its name. */
  value: ReactNode;
  /** The same state in a number or two ("2", "0/5"), where the row is too narrow for words. */
  short?: ReactNode;
  /** The segment said in full: its accessible name, and the tooltip where the words are hidden. */
  aria: string;
  /** What it is doing right now. Shown where the segment has the row's spare width. */
  peek?: (open: boolean) => ReactNode;
  /** A word in the warm ink: Paused, Blocked, Stalled. */
  warn?: ReactNode;
  time?: ReactNode;
  panel: (open: boolean) => ReactNode;
}

const NONE: readonly never[] = [];

export function ActivityRow({
  todo = NONE,
  settled = false,
  goal,
  onGoalAction,
  subagents = NONE,
  onOpenChild,
  sessionId = "",
  jobs: roster = NONE,
  buffers,
  onClearJobOutput,
  placement = "dock",
  facts,
}: ActivityRowProps) {
  const [open, setOpen] = useState<ActivityKey | null>(null);
  // The row at the width it has (owner, 2026-10-18: with the side panel open it broke into
  // overlapping words). A width alone could not say it: the column is 540-850px in most windows
  // and what the row holds changes, so it tries the fullest form first and steps down while its
  // segments overflow. It runs before paint on every render and on every resize, so no form is
  // ever drawn that does not fit.
  const [fit, setFit] = useState<ActivityFit>("wide");
  const rowRef = useRef<HTMLElement>(null);
  const segsRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const row = rowRef.current;
    const segs = segsRef.current;
    if (!row || !segs) return;
    // A form fits when nothing overflows and the goal's sentence still has room to be read.
    // Each segment is measured, not only the row of them: a segment may shrink to nothing
    // (`min-width: 0`, the growing one from a basis of 0), so the row never overflows while a
    // segment's words run out of it and under the facts beside it ("3 of 3 done" over
    // "29 files", owner's screenshot 2026-10-20; reproduced with a wider face in the hermetic
    // app). Words meant to be cut (`.cut`: the goal's sentence, a peek) clip inside their own
    // box and do not count.
    const fits = () => {
      const sentence = segs.querySelector<HTMLElement>('[data-testid="goal-dock-objective"]');
      const spills = Array.from(segs.querySelectorAll<HTMLElement>(".act-btn")).some((btn) => btn.scrollWidth > btn.clientWidth + 1);
      return !spills && segs.scrollWidth <= segs.clientWidth + 1 && (!sentence || sentence.clientWidth >= 96);
    };
    const measure = () => {
      for (const tier of FITS) {
        row.dataset.fit = tier;
        if (tier === "narrow" || fits()) {
          setFit(tier);
          return;
        }
      }
    };
    measure();
    let live = true;
    // Words widen when the face loads; nothing else would say so.
    document.fonts?.ready.then(() => live && measure());
    if (typeof ResizeObserver === "undefined") return () => void (live = false);
    // The row's width, and each segment's own: a count, a clock or the face can change them.
    const observer = new ResizeObserver(measure);
    observer.observe(row);
    for (const child of Array.from(segs.children)) observer.observe(child);
    return () => {
      live = false;
      observer.disconnect();
    };
  });
  // Finished jobs the person cleared: gone from this row, kept by the engine.
  const [cleared, setCleared] = useState<ReadonlySet<string>>(() => new Set());
  const jobs = useMemo(() => (cleared.size ? roster.filter((job) => !cleared.has(job.id)) : roster), [roster, cleared]);
  const clearJobs = (ids: readonly string[]) => {
    setCleared((current) => new Set([...current, ...ids]));
    onClearJobOutput?.(ids);
  };
  const baseId = useId();
  const summary = useMemo(() => delegationSummary(subagents), [subagents]);
  const live = useMemo(() => liveJobs(jobs), [jobs]);

  const segments: Segment[] = [];

  if (goal && goal.phase !== "complete" && onGoalAction) {
    const state = goalState(goal);
    segments.push({
      key: "goal",
      testId: "goal-dock",
      icon: "goal",
      label: "Goal",
      state: goal.phase,
      title: goal.phase === "blocked" ? goal.blocker?.message : undefined,
      aria: `Goal: ${goal.objective}${state ? ` · ${state}` : ""}`,
      value: (
        <span className="act-val cut" data-testid="goal-dock-objective" title={goal.objective}>
          {goal.objective}
        </span>
      ),
      warn: state ? <span data-testid="goal-dock-state">{state}</span> : undefined,
      panel: () => <GoalPanel goal={goal} onAction={onGoalAction} />,
    });
  }

  if (subagents.length > 0) {
    const issues = summary.failed + summary.stopped;
    const waiting = subagents.filter((n) => n.kept).length;
    const working = subagents.find((n) => n.outcome === "running");
    const quiet = summary.running === 0 && waiting === 0 && issues === 0;
    const said =
      summary.running > 0
        ? `${summary.running} running`
        : waiting > 0
          ? `${waiting} waiting for you`
          : issues > 0
            ? rosterHeadline(summary)
            : `${subagents.length} finished`;
    const count = summary.running > 0 ? summary.running : waiting > 0 ? waiting : issues > 0 ? issues : subagents.length;
    segments.push({
      key: "subagents",
      testId: "subagents-dock",
      icon: "bot",
      label: "Subagents",
      state: summary.running > 0 ? "live" : waiting > 0 ? "waiting" : issues > 0 ? "issues" : "done",
      value: (
        <span className={"act-val act-full" + (quiet ? " quiet" : "")} data-testid="subagents-dock-progress">
          {said}
        </span>
      ),
      short: <span className={"act-val act-short" + (quiet ? " quiet" : "")}>{count}</span>,
      aria: `Subagents: ${said}`,
      peek: (isOpen) =>
        working ? (
          <span className="act-peek" data-shown={!isOpen} aria-hidden={isOpen}>
            <StatusMark outcome="running" />
            <span className="cut">{working.description || working.title}</span>
            {/* What that child is doing now: the row moves with the work, closed. */}
            {working.live && (
              <span className="act-peek-more cut" data-testid="subagents-dock-live">
                {working.live}
              </span>
            )}
          </span>
        ) : null,
      time:
        summary.running > 0 && summary.startedAtMs !== null ? (
          <Elapsed from={summary.startedAtMs} className="act-time" />
        ) : undefined,
      panel: () => <SubagentsPanel nodes={subagents} onOpenChild={onOpenChild} />,
    });
  }

  if (jobs.length > 0) {
    const speaking = [...live].reverse().find((job) => lastLine(job) !== "") ?? live[live.length - 1];
    const said = speaking ? lastLine(speaking) : "";
    const since = live.reduce(
      (first, job) => (typeof job.started_at === "number" ? Math.min(first, job.started_at * 1000) : first),
      Number.POSITIVE_INFINITY,
    );
    segments.push({
      key: "jobs",
      testId: "jobs-dock",
      icon: "terminal",
      label: "Jobs",
      state: live.length > 0 ? "live" : "done",
      value: (
        <span className={"act-val act-full" + (live.length > 0 ? "" : " quiet")} data-testid="jobs-dock-progress">
          {live.length > 0 ? `${live.length} running` : `${jobs.length} finished`}
        </span>
      ),
      short: (
        <span className={"act-val act-short" + (live.length > 0 ? "" : " quiet")}>
          {live.length > 0 ? live.length : jobs.length}
        </span>
      ),
      aria: `Jobs: ${live.length > 0 ? `${live.length} running` : `${jobs.length} finished`}`,
      peek: (isOpen) =>
        speaking ? (
          <span className="act-peek" data-shown={!isOpen} aria-hidden={isOpen}>
            <span className="turn-dots" aria-hidden>
              <i />
              <i />
            </span>
            <span className="cut mono" title={speaking.command}>
              {shortCommand(speaking.command, said ? 36 : 90)}
            </span>
            {said && (
              <span className="act-peek-more cut" data-testid="jobs-dock-said">
                {said}
              </span>
            )}
          </span>
        ) : null,
      time: Number.isFinite(since) ? <Elapsed from={since} className="act-time" /> : undefined,
      panel: (isOpen) => <JobsPanel sessionId={sessionId} jobs={jobs} buffers={buffers} active={isOpen} onClear={clearJobs} />,
    });
  }

  if (todo.length > 0) {
    const allDone = todo.every((t) => t.status === "done");
    // Closed with work left, the row names the step under way (else the next one), so a
    // person who never opens the list still sees what the agent is doing.
    const current = allDone
      ? undefined
      : (todo.find((t) => t.status === "in_progress") ?? todo.find((t) => t.status !== "done"));
    segments.push({
      key: "todos",
      testId: "todo-dock",
      icon: "checklist",
      label: "To-dos",
      state: settled ? "settled" : "live",
      value: (
        <span className="act-val act-full" data-testid="todo-dock-progress" title={progressLabel(todo)}>
          {progressCount(todo)}
        </span>
      ),
      short: (
        <span className="act-val act-short">
          {todo.filter((t) => t.status === "done").length}/{todo.length}
        </span>
      ),
      aria: `To-dos: ${progressCount(todo)}`,
      peek: (isOpen) => (
        <span className="act-peek" data-testid="todo-dock-peek" data-shown={!isOpen && !!current} aria-hidden={isOpen}>
          {current && (
            <>
              <span className="todo-dock-glyph" aria-hidden>
                <StatusGlyph status={current.status} />
              </span>
              <span className="cut">{current.content}</span>
            </>
          )}
        </span>
      ),
      panel: () => <TodoList items={todo} />,
    });
  }

  // A segment that went away (the goal was cleared, the plan emptied) takes its panel with it.
  const present = segments.map((s) => s.key).join(",");
  useEffect(() => {
    setOpen((cur) => (cur && !present.split(",").includes(cur) ? null : cur));
  }, [present]);

  if (segments.length === 0 && !facts) return null;
  // The spare width goes to the goal's sentence; with no goal, to the to-dos' current step;
  // else to the only thing there is.
  const grow = segments.find((s) => s.key === "goal") ?? segments.find((s) => s.key === "todos") ?? segments[0];
  const head = placement === "head";

  return (
    <div className={"act-wrap" + (head ? " in-head" : "")}>
      <section
        ref={rowRef}
        className={"act" + (head ? " act-head" : "")}
        data-fit={fit}
        data-testid="activity-row"
        data-placement={placement}
        aria-label={head ? "Where this task stands" : "Session activity"}
      >
        <div className="act-segs" ref={segsRef}>
          {segments.map((seg) => {
            const on = open === seg.key;
            return (
              <div
                key={seg.key}
                className={"act-seg" + (seg === grow ? " grow" : "") + (on ? " on" : "")}
                data-testid={seg.testId}
                data-state={seg.state}
                title={seg.title}
              >
                <button
                  type="button"
                  className="act-btn"
                  aria-expanded={on}
                  aria-controls={`${baseId}-${seg.key}`}
                  aria-label={seg.aria}
                  title={seg.aria}
                  data-testid={`${seg.testId}-toggle`}
                  onClick={() => setOpen(on ? null : seg.key)}
                >
                  <Icon name={seg.icon} size={14} className="act-ico" />
                  <span className="act-lbl">{seg.label}</span>
                  {seg.value}
                  {seg.short}
                  {seg === grow && seg.peek?.(on)}
                  {seg.warn && <span className="act-warn">{seg.warn}</span>}
                  {seg.time}
                  <span className="act-car" aria-hidden>
                    <Icon name="chevronDown" size={14} />
                  </span>
                </button>
              </div>
            );
          })}
          {facts}
        </div>
        {segments.map((seg) => {
          const on = open === seg.key;
          return (
            <div
              key={seg.key}
              id={`${baseId}-${seg.key}`}
              className="act-body"
              data-testid={`${seg.testId}-body`}
              data-open={on}
              aria-hidden={!on}
              {...(on ? {} : { inert: "" })}
            >
              <div className="act-body-in">
                <div className="act-panel">{seg.panel(on)}</div>
              </div>
            </div>
          );
        })}
      </section>
    </div>
  );
}
