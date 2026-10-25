import { useEffect, useRef, useState } from "react";
import type { PlanRequestItem } from "../types";
import { planStepCount, planTitle } from "../cards/planText";
import {
  Decision,
  DecisionActions,
  DecisionButton,
  DecisionSpacer,
} from "./decision/Decision";
import { useDecisionKeyboard } from "./primitives/decisionKeys";
import { Disclosure } from "../ui/Disclosure";
export { planSteps, planTitle } from "../cards/planText";

// The plan DECISION: it takes the composer's place while the session waits on it. The plan
// itself is read in the transcript, whole (the `propose_plan` step → PlanBlock); this card
// only asks. It used to repeat the plan's title and first three steps, so the plan was on
// screen twice (owner, 2026-10-13). It names the plan in one line and carries the answer.
//
//   Approve                 no mode passed: the session keeps its current preset.
//   Approve in Full access  "danger-full-access": the plan runs without further asks. Not
//                           offered when the session is in Full access already, where it
//                           is what Approve does (owner, 2026-10-12).
//   Request changes         type feedback in the card and send it back.
// Approving also leaves the Plan overlay server-side (`plan_changed`). Dismiss rejects
// without feedback and is deliberately not numbered: it must not be one digit away.

/** The plan the card is about, as it is drawn in the transcript. */
function planBlock(): HTMLElement | null {
  const blocks = document.querySelectorAll<HTMLElement>(".plan-block");
  return blocks[blocks.length - 1] ?? null;
}

/** A plan is read from its first line. The transcript follows its newest content, so a plan
 *  taller than the window arrived scrolled to its end, the title out of sight. Once, when the
 *  card that asks about it appears, the plan's start is brought into view. */
function useOpenAtPlanStart(): void {
  useEffect(() => {
    // After the card has taken the composer's place: the transcript's room changes with it.
    const timer = window.setTimeout(() => {
      const block = planBlock();
      if (block && block.getBoundingClientRect().top < 0) block.scrollIntoView({ block: "start" });
    }, 120);
    return () => window.clearTimeout(timer);
  }, []);
}

/** Whether the plan's first line has scrolled out of view, so the card can offer the way back. */
function usePlanScrolledAway(): boolean {
  const [away, setAway] = useState(false);
  useEffect(() => {
    const block = planBlock();
    if (!block || typeof IntersectionObserver === "undefined") return;
    // A one-pixel line at the plan's top edge: out of view means the reader is past the start.
    const head = document.createElement("div");
    head.style.cssText = "height:1px;margin-bottom:-1px;pointer-events:none";
    block.prepend(head);
    const observer = new IntersectionObserver(([entry]) => setAway(!entry.isIntersecting));
    observer.observe(head);
    return () => {
      observer.disconnect();
      head.remove();
    };
  }, []);
  return away;
}

type PlanCardProps = {
  item: PlanRequestItem;
  /** Approve (with the preset to run in, if any), or send the plan back with what to change. */
  onRespond: (approved: boolean, mode?: string, feedback?: string) => void;
  /** The session is in Full access already: approving cannot ask for less. */
  fullAccess?: boolean;
};

export function PlanCard({ item, onRespond, fullAccess = false }: PlanCardProps) {
  // Request changes opens a box in the card for what to change.
  const [rejecting, setRejecting] = useState(false);
  const [feedback, setFeedback] = useState("");
  const dockRef = useRef<HTMLDivElement>(null);
  useDecisionKeyboard(dockRef, { enabled: !rejecting });
  useOpenAtPlanStart();
  const away = usePlanScrolledAway();
  // The plan's name is the part that gives way: a plan with no heading is named by its first
  // step, a whole sentence, which squeezed the question to one word a line and ran past the
  // card (owner's screenshot, 2026-10-15). The step count and the preset always show.
  const name = planTitle(item.plan);
  const facts = [planStepCount(item.plan), fullAccess ? "Full access" : ""].filter(Boolean).join(" · ");
  const context = (
    <span className="dec-c-row">
      {name && (
        <span className="dec-c-clip" title={name}>
          {name}
        </span>
      )}
      {facts && <span>{name ? ` · ${facts}` : facts}</span>}
    </span>
  );
  const send = () => feedback.trim() && onRespond(false, undefined, feedback.trim());

  return (
    <Decision
      icon="checklist"
      title={rejecting ? "What should change?" : "Approve this plan?"}
      context={context}
      className="plan-decision"
      testId="plan-dock"
      innerRef={dockRef}
      label="Plan"
    >
      {away && (
        <button
          type="button"
          className="dec-more"
          data-testid="plan-view"
          onClick={() => planBlock()?.scrollIntoView({ behavior: "smooth", block: "start" })}
        >
          Back to the top of the plan
        </button>
      )}
      {/* The field opens above the answers by height and closes the same way; the answers
          under it change in place. */}
      <Disclosure open={rejecting} className="fold-fade">
        <textarea
          className="dec-field"
          placeholder="Tell the agent what to change. Enter sends, Shift+Enter for a new line"
          aria-label="What should change"
          rows={2}
          autoFocus
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && feedback.trim()) {
              e.preventDefault();
              send();
            }
          }}
        />
      </Disclosure>
      {rejecting ? (
        <>
          <DecisionActions>
            <DecisionButton primary option={false} data-testid="plan-send-feedback" disabled={!feedback.trim()} onClick={send}>
              Send feedback
            </DecisionButton>
            <DecisionSpacer />
            <DecisionButton quiet option={false} onClick={() => setRejecting(false)}>
              Back
            </DecisionButton>
          </DecisionActions>
        </>
      ) : (
        <DecisionActions>
          <DecisionButton
            primary
            data-testid="plan-approve"
            title="Leave plan mode and start implementing"
            onClick={() => onRespond(true, undefined)}
          >
            Approve
          </DecisionButton>
          {!fullAccess && (
            <DecisionButton
              data-testid="plan-run"
              title="Approve, and switch this session to Full access: it stops asking before it acts"
              onClick={() => onRespond(true, "danger-full-access")}
            >
              Approve in Full access
            </DecisionButton>
          )}
          <DecisionButton
            data-testid="plan-changes"
            title="Send feedback so the agent revises the plan"
            onClick={() => setRejecting(true)}
          >
            Request changes
          </DecisionButton>
          <DecisionSpacer />
          <DecisionButton quiet option={false} data-testid="plan-dismiss" onClick={() => onRespond(false)}>
            Dismiss
          </DecisionButton>
        </DecisionActions>
      )}
    </Decision>
  );
}
