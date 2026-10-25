// The one decision card (redesign of 2026-10-12, DESIGN.md "A decision is one card").
//
// Everything that waits on the person (a command, a file write, a message going out, a plan,
// a question, a tool to install, a folder, an automation, a subagent's ask) is drawn in this
// shell, so the answer is always in the same place:
//
//   head      an icon, one sentence saying what is asked, and at the right a few words of
//             context or a "1 of N" pager when several wait
//   the thing the command, the change, the message, the plan, the options: in one inset block
//   why       a warm line when something about it is risky, and the agent's own reason
//   answers   ONE filled button at the left (the answer most people give), wider grants as
//             outlined buttons, then at the right the quiet ones: Edit, and the refusal
//
// Six shapes did this job before, each with its own border, radius, button style and side
// for the primary. The keys are unchanged (`primitives/decisionKeys.tsx`): 1..N in the order
// the options are drawn, and the stylesheet numbers them from that same order, so the number
// on a button cannot disagree with the key that presses it.

import { useRef, type ButtonHTMLAttributes, type ReactNode, type Ref } from "react";
import { Icon, type IconName } from "../Icon";
import { useAutoHeight } from "../../ui/useAutoHeight";

export function Decision({
  icon,
  title,
  context,
  warn,
  pager,
  children,
  className = "",
  testId,
  innerRef,
  label = "Decision",
  fluid = false,
}: {
  icon: IconName;
  title: ReactNode;
  /** A few quiet words at the right of the head; under the sentence in a narrow column. */
  context?: ReactNode;
  /** The context is a caution (it leaves this computer, or where it acts is unknown). */
  warn?: boolean;
  /** Takes the context's place when several decisions wait. */
  pager?: ReactNode;
  children?: ReactNode;
  className?: string;
  testId?: string;
  innerRef?: Ref<HTMLDivElement>;
  label?: string;
  /** The card's content is replaced in place (a question by the next one): its height glides.
   *  Not for a card whose parts open by themselves (`Disclosure`), which would move twice. */
  fluid?: boolean;
}) {
  const own = useRef<HTMLDivElement | null>(null);
  useAutoHeight(own, fluid);
  const attach = (node: HTMLDivElement | null) => {
    own.current = node;
    if (typeof innerRef === "function") innerRef(node);
    else if (innerRef) (innerRef as { current: HTMLDivElement | null }).current = node;
  };
  return (
    <div className={"dec " + className} data-testid={testId} ref={attach} role="group" aria-label={label}>
      <div className="dec-h">
        <Icon name={icon} size={15} className="dec-ico" />
        <h3 className="dec-t">{title}</h3>
        {pager ? <span className="dec-pager">{pager}</span> : context ? <span className={"dec-c" + (warn ? " out" : "")}>{context}</span> : null}
      </div>
      {children}
    </div>
  );
}

/** The thing that is asked about, in one inset block. `code` is monospace and wraps anywhere. */
export function DecisionBody({ children, code, className = "" }: { children: ReactNode; code?: boolean; className?: string }) {
  return <div className={"dec-b" + (code ? " code" : "") + (className ? " " + className : "")}>{children}</div>;
}

/** The agent's own reason, said as its words and not as the product's. */
export function DecisionReason({ who = "Reeve", children }: { who?: string; children: ReactNode }) {
  return (
    <p className="dec-w">
      <b>{who}:</b> {children}
    </p>
  );
}

/** A warm line: something about this the person should know before answering. */
export function DecisionRisk({ children, testId, title }: { children: ReactNode; testId?: string; title?: string }) {
  return (
    <div className="dec-risk" data-testid={testId} title={title}>
      <Icon name="warning" size={13} />
      <span>{children}</span>
    </div>
  );
}

export function DecisionActions({ children }: { children: ReactNode }) {
  return <div className="dec-a">{children}</div>;
}

/** Pushes what follows to the right end of the answers. */
export function DecisionSpacer() {
  return <span className="dec-sp" />;
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  /** The one filled button of the card. */
  primary?: boolean;
  /** A text button: Edit, Deny, Dismiss. */
  quiet?: boolean;
  /** An answer the number keys reach (default). False for a control that is not an answer. */
  option?: boolean;
};

export function DecisionButton({ primary, quiet, option = true, className = "", children, ...rest }: ButtonProps) {
  const kind = primary ? " pri" : quiet ? " quiet" : "";
  return (
    <button type="button" className={"dec-btn" + kind + (className ? " " + className : "")} {...(option ? { "data-option": true } : {})} {...rest}>
      {children}
    </button>
  );
}

/** "1 of 3" with a way back and forward, where the surface can step. */
export function DecisionPager({
  index,
  count,
  onPrev,
  onNext,
  testId,
  prevLabel = "Previous",
  nextLabel = "Next",
}: {
  index: number;
  count: number;
  onPrev?: () => void;
  onNext?: () => void;
  testId?: string;
  prevLabel?: string;
  nextLabel?: string;
}) {
  return (
    <span className="dec-pager-in" data-testid={testId}>
      {onPrev && (
        <button type="button" aria-label={prevLabel} title={prevLabel} onClick={onPrev} disabled={index <= 0}>
          <Icon name="chevronRight" size={12} className="rotate-180" />
        </button>
      )}
      <span className="tabular-nums">
        {index + 1} of {count}
      </span>
      {onNext && (
        <button type="button" aria-label={nextLabel} title={nextLabel} onClick={onNext} disabled={index >= count - 1}>
          <Icon name="chevronRight" size={12} />
        </button>
      )}
    </span>
  );
}
