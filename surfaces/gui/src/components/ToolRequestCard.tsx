import { useRef } from "react";
import type { ToolRequestItem } from "../types";
import {
  Decision,
  DecisionActions,
  DecisionBody,
  DecisionButton,
  DecisionReason,
} from "./decision/Decision";
import { useDecisionKeyboard } from "./primitives/decisionKeys";

// The card for `request_tool`: the agent wants a command-line tool this computer lacks, most
// often a scanner. Going on without it is an answer as good as installing — the agent works
// around it and says which checks it could not run — so nothing here leans toward Install.
//
// What the card says comes from two places, and each is said as what it is: the facts are the
// tool registry's, the reason is the agent's, in its own words and in quotes. Run together they
// read as one voice, and the card made no sense.
export function ToolRequestCard({ item, onRespond }: { item: ToolRequestItem; onRespond: (approved: boolean) => void }) {
  const dock = useRef<HTMLDivElement>(null);
  useDecisionKeyboard(dock);
  const title = item.installable ? (
    <>
      Install <code>{item.tool}</code>?
    </>
  ) : (
    <>
      <code>{item.tool}</code> is not on this computer
    </>
  );
  return (
    <Decision
      icon="wrench"
      title={title}
      context={item.installable ? "Verified build" : "No verified build"}
      className="toolreq-card"
      testId="toolreq-card"
      innerRef={dock}
      label="Tool request"
    >
      <DecisionBody className="toolreq-facts">
        {item.installable ? (
          <dl className="dec-facts">
            <dt>Tool</dt>
            <dd>
              <code>
                {item.tool}
                {item.version ? ` ${item.version}` : ""}
              </code>
            </dd>
            {item.summary && (
              <>
                <dt>What it is</dt>
                <dd>{item.summary}</dd>
              </>
            )}
          </dl>
        ) : (
          <p>
            Reeve has no verified build for this machine. Install it yourself if you want this check, or continue and
            Reeve will note the gap.
          </p>
        )}
      </DecisionBody>
      {/* Plain-language consent: who installs, from where, and that installing it yourself
          is as good an answer (owner feedback 2026-08-15: no supply-chain jargon here). */}
      {item.installable && (
        <p className="dec-note">
          Reeve installs its own verified copy{item.source ? ` from ${item.source}` : ""}, or you install it yourself and
          continue.
        </p>
      )}
      {item.reason && <DecisionReason>“{item.reason}”</DecisionReason>}
      {/* With no verified build there is nothing to install, so the card does not draw a
          button that cannot be pressed: one answer, and it is the filled one. */}
      <DecisionActions>
        {item.installable && (
          <DecisionButton primary data-testid="toolreq-install" onClick={() => onRespond(true)}>
            Install
          </DecisionButton>
        )}
        <DecisionButton primary={!item.installable} data-testid="toolreq-skip" onClick={() => onRespond(false)}>
          Continue without it
        </DecisionButton>
      </DecisionActions>
    </Decision>
  );
}
