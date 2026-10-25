// The goal: the session's standing objective, as a segment of the activity row above the
// composer (`ActivityRow.tsx`) and the panel that opens under it.
//
// The segment says the objective in one line, and a warm word when the goal is not simply
// running (Paused, Blocked, Not running, Stalled). The panel says the rest in words: which
// round, what the rounds spent against the budget, whether they stopped moving,
// each acceptance check as the done-gate reads it, the blocker's own sentence. No progress
// bar (owner, 2026-10-12).
//
//   · resume appears when the goal is paused OR active-but-disarmed (a restart disarms
//     without rewriting the durable phase, so "Not running" is the reopened state);
//   · pause appears only while active AND armed: anything else would be a button that
//     cannot do what it says;
//   · edit opens a form in the panel (objective + round cap + token budget);
//   · clear asks in a dialog before deleting the goal (no tombstone server-side).
// Loading (undefined), absent (null) and complete goals have no segment. Goal CREATION is
// not here: the model's `goal` tool creates one.
//
// Live state arrives from the session socket (`goal` / `goal_activation` frames) and
// reloads from `GET /v1/sessions/{id}/goal`; either way the props are the truth.

import { useEffect, useRef, useState } from "react";
import { Icon } from "../Icon";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { useAutoHeight } from "../../ui/useAutoHeight";
import type { GoalInfo } from "../../api";
import { formatTokens } from "../../usage";

export type { GoalInfo };

/** The reference design validates `max_goal_rounds` as a positive safe integer and enforces no tighter
 * upper bound, so the edit form's cap
 * mirrors `Number.MAX_SAFE_INTEGER` — the same bound the server clamps to. */
export const MAX_GOAL_ROUNDS = Number.MAX_SAFE_INTEGER;

export type GoalActionName = "pause" | "resume" | "edit" | "clear";

/** The server's default round cap (harness/goals.py DEFAULT_MAX_ROUNDS). A goal at the
 *  default names only its round: "1/256" read as a fraction of something (GUI UX review). */
export const DEFAULT_GOAL_ROUNDS = 256;

export function roundsLabel(goal: GoalInfo): string {
  return goal.max_rounds === DEFAULT_GOAL_ROUNDS
    ? `round ${goal.rounds_started}`
    : `round ${goal.rounds_started} of ${goal.max_rounds}`;
}

/** Rounds in a row that moved nothing before the harness blocks the goal
 *  (harness/goal_ledger.py STALL_ROUNDS). */
export const GOAL_STALL_ROUNDS = 3;

/** What the rounds spent: "1.2M tokens", or "1.2M of 5M tokens" under a budget. Empty
 *  before a round has been charged and no budget is set. */
export function spendLabel(goal: GoalInfo): string {
  const used = goal.tokens_used ?? 0;
  const budget = goal.token_budget ?? 0;
  if (budget > 0) return `${formatTokens(used)} of ${formatTokens(budget)} tokens`;
  return used > 0 ? `${formatTokens(used)} tokens` : "";
}

/** The edit form's budget field: "" or "0" → no budget, "500k" / "1.5M" / "250000" →
 *  tokens; anything else → null (the Save button stays off). */
export function parseTokenBudget(text: string): number | null {
  const t = text.trim().toLowerCase().replace(/[,_\s]/g, "");
  if (t === "") return 0;
  const m = /^(\d+(?:\.\d+)?)([km]?)$/.exec(t);
  if (!m) return null;
  const n = Number(m[1]) * (m[2] === "m" ? 1_000_000 : m[2] === "k" ? 1_000 : 1);
  return Number.isFinite(n) ? Math.round(n) : null;
}

const CHECK_MARK: Record<string, string> = {
  passed: "✓",
  failed: "✗",
  stale: "edited since",
  unknown: "exit unknown",
  not_run: "not run",
  variant: "ran piped",
};

export interface GoalActionFields {
  objective?: string;
  max_rounds?: number;
  token_budget?: number;
}

/** `ok: false` renders inline; a resolved mutation arrives as the next goal prop. */
export interface GoalActionOutcome {
  ok: boolean;
  error?: string;
}

export type GoalAction = (
  action: GoalActionName,
  fields?: GoalActionFields,
) => Promise<GoalActionOutcome | void>;

/**
 * The word beside a goal that is not simply running. Active is split by activation: an armed
 * goal is ongoing and says nothing; a disarmed one is not running in this process.
 */
export function goalState(goal: GoalInfo): string {
  if (goal.phase === "paused") return "Paused";
  if (goal.phase === "blocked") return "Blocked";
  if (goal.phase === "active") {
    if (goal.activation === "disarmed") return "Not running";
    if ((goal.stalled_rounds ?? 0) > 0) return "Stalled";
  }
  return "";
}

/** The goal's panel. What is known of the goal and its edit form take each other's place in
 *  one box, which glides to the other's height while the new one fades in. */
export function GoalPanel(props: { goal: GoalInfo; onAction: GoalAction }) {
  const box = useRef<HTMLDivElement>(null);
  useAutoHeight(box);
  return (
    <div ref={box}>
      <GoalPanelBody {...props} />
    </div>
  );
}

function GoalPanelBody({ goal, onAction }: { goal: GoalInfo; onAction: GoalAction }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [draftCap, setDraftCap] = useState("");
  const [draftBudget, setDraftBudget] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  // React state disables the controls on the next render; the ref closes the same-render
  // window so a rapid double-click cannot submit the same action twice.
  const pendingRef = useRef(false);
  // The view fades in when it comes back from the form, not when the panel first shows it.
  const returned = useRef(false);
  if (editing) returned.current = true;

  // A new goal identity (cleared/completed/replaced externally) invalidates the local
  // form state: without the reset, a surviving draft's Enter would write over the NEW
  // goal. A revision bump for the SAME goal (a round charged, the model edited the
  // objective) deliberately does not close an editor someone is typing in.
  const goalId = goal.goal_id;
  useEffect(() => {
    setEditing(false);
    setConfirming(false);
    setActionError(null);
  }, [goalId]);

  const run = async (action: GoalActionName, fields?: GoalActionFields): Promise<boolean> => {
    if (pendingRef.current) return false;
    pendingRef.current = true;
    setPending(true);
    setActionError(null);
    try {
      const result = await onAction(action, fields);
      if (result && !result.ok) {
        setActionError(result.error || "The goal action failed.");
        return false;
      }
      setEditing(false);
      setConfirming(false);
      return true;
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
      return false;
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  };

  if (editing) {
    const cap = Number(draftCap);
    const capOk = Number.isInteger(cap) && cap >= 1;
    const budget = parseTokenBudget(draftBudget);
    const save = () => {
      const objective = draft.trim();
      if (!objective || !capOk || budget === null) return;
      void run("edit", { objective, max_rounds: cap, token_budget: budget });
    };
    const keys = (e: { key: string }) => {
      if (e.key === "Enter") save();
      if (e.key === "Escape") setEditing(false);
    };
    return (
      <div key="edit" className="goal-edit ui-fade" data-testid="goal-dock-edit">
        <input
          className="goal-field goal-field-objective"
          type="text"
          aria-label="Goal objective"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={keys}
          autoFocus
        />
        <div className="goal-edit-row">
          <label className="goal-edit-l">
            Round limit
            <input
              className="goal-field goal-field-cap"
              type="number"
              min={1}
              max={MAX_GOAL_ROUNDS}
              aria-label="Round limit"
              value={draftCap}
              onChange={(e) => setDraftCap(e.target.value)}
              onKeyDown={keys}
            />
          </label>
          <label className="goal-edit-l">
            Token budget
            <input
              className="goal-field goal-field-budget"
              type="text"
              inputMode="decimal"
              aria-label="Token budget"
              placeholder="no budget"
              title="Tokens the goal's rounds may spend in all, e.g. 5M or 500k. Empty = no budget."
              aria-invalid={budget === null}
              value={draftBudget}
              onChange={(e) => setDraftBudget(e.target.value)}
              onKeyDown={keys}
            />
          </label>
          <span className="goal-edit-sp" />
          <button type="button" className="act-tiny" aria-label="Cancel edit" disabled={pending} onClick={() => setEditing(false)}>
            Cancel
          </button>
          <button
            type="button"
            className="act-tiny strong"
            aria-label="Save goal"
            disabled={pending || draft.trim() === "" || !capOk || budget === null}
            onClick={save}
          >
            Save
          </button>
        </div>
        {actionError !== null && (
          <p className="goal-error" role="alert">
            {actionError}
          </p>
        )}
      </div>
    );
  }

  const showPause = goal.phase === "active" && goal.activation === "armed";
  const showResume = goal.phase === "paused" || (goal.phase === "active" && goal.activation === "disarmed");
  const spend = spendLabel(goal);
  const stalled = goal.phase === "active" ? goal.stalled_rounds ?? 0 : 0;

  return (
    <div key="view" className={"goal-panel" + (returned.current ? " ui-fade" : "")}>
      <p className="goal-o" title={goal.checks?.length ? `Checks: ${goal.checks.join("; ")}` : undefined}>
        {goal.objective}
      </p>
      {goal.phase === "blocked" && goal.blocker?.message && (
        <p className="goal-blocker" data-testid="goal-dock-blocker">
          {goal.blocker.message}
        </p>
      )}
      <div className="goal-m">
        <span data-testid="goal-dock-rounds" title={`Round limit: ${goal.max_rounds}`}>
          {roundsLabel(goal)}
          {spend && (
            <span data-testid="goal-dock-spend">
              {" · "}
              {spend}
            </span>
          )}
        </span>
        {stalled > 0 && (
          <span
            className="goal-warn"
            data-testid="goal-dock-stall"
            title={`The last ${stalled} round(s) made no new tool call, changed no file and moved no check. The goal stops after ${GOAL_STALL_ROUNDS}.`}
          >
            No progress in the last {stalled} {stalled === 1 ? "round" : "rounds"}, stops at {GOAL_STALL_ROUNDS}
          </span>
        )}
      </div>
      {/* The goal's real progress: each acceptance check as the done-gate reads it. */}
      {goal.check_status && goal.check_status.length > 0 && (
        <div className="goal-m goal-checks" data-testid="goal-dock-checks">
          {goal.check_status.map((check) => (
            <span key={check.command} className="goal-check" data-state={check.state}>
              {check.command} {CHECK_MARK[check.state] ?? check.state}
            </span>
          ))}
        </div>
      )}
      {actionError !== null && (
        <p className="goal-error" role="alert">
          {actionError}
        </p>
      )}
      <div className="goal-a">
        {showPause && (
          <button type="button" className="act-tiny" aria-label="Pause goal" disabled={pending} onClick={() => void run("pause")}>
            <Icon name="pause" size={13} />
            Pause
          </button>
        )}
        {showResume && (
          <button type="button" className="act-tiny" aria-label="Resume goal" disabled={pending} onClick={() => void run("resume")}>
            <Icon name="play" size={13} />
            Resume
          </button>
        )}
        <button
          type="button"
          className="act-tiny"
          aria-label="Edit goal"
          disabled={pending}
          onClick={() => {
            setDraft(goal.objective);
            setDraftCap(String(goal.max_rounds));
            setDraftBudget(goal.token_budget ? formatTokens(goal.token_budget) : "");
            setEditing(true);
          }}
        >
          <Icon name="edit" size={13} />
          Edit
        </button>
        <button
          type="button"
          className="act-tiny danger"
          aria-label="Clear goal"
          disabled={pending}
          onClick={() => setConfirming(true)}
        >
          <Icon name="trash" size={13} />
          Clear
        </button>
      </div>
      {/* Clearing deletes the goal with no way back, so it asks in a dialog (it was a tick
          and a cross that appeared in the strip, a click away from the Clear that drew them). */}
      {confirming && (
        <ConfirmDialog
          title="Clear this goal?"
          body="The goal and its round count are removed. The conversation and everything done so far stay."
          confirmLabel="Clear goal"
          busyLabel="Clearing…"
          onCancel={() => setConfirming(false)}
          onConfirm={async () => {
            const ok = await run("clear");
            if (!ok) throw new Error("The goal could not be cleared.");
          }}
        />
      )}
    </div>
  );
}
