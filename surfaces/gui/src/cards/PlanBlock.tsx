// The plan the agent proposed (`propose_plan`), as a document in the transcript.
//
// The plan is read here, whole (owner, 2026-10-13). It used to be clamped to 132px behind a
// "View full plan" pill while the card that asked about it repeated its title and first three
// steps: the plan was on screen twice and readable in neither place. Now this is the one place
// it is shown, and the card only asks. A long plan scrolls with the transcript, which is the
// reading surface.

import { Markdown } from "../components/Markdown";

export function PlanBlock({
  text,
  path,
  rejected,
}: {
  text: string;
  path?: string;
  /** The proposal was sent back (the agent is revising): dimmed, so a run of revisions reads
   *  as history rather than as one plan updating under the reader. */
  rejected?: boolean;
}) {
  const body = (text ?? "").trim();
  if (!body) return null;
  return (
    <div className="plan-block" data-rejected={rejected || undefined} data-testid="plan-block">
      <div className="plan-block-body">
        <Markdown text={body} />
      </div>
      {path ? <div className="plan-block-path">Plan file: {path}</div> : null}
    </div>
  );
}
