// A turn's visuals, drawn inside its answer, and the step rows that point at them.
// Once the turn has its answer, a visual the answer draws leaves its step row one line with a
// link to where it stands, and one the answer left out (a draft, a second try) is folded on its
// row; before then (the answer is still being written) every row draws its visual.

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { Markdown } from "../Markdown";
import { VisualBlocks } from "./VisualBlocks";
import { answerParts, type TurnVisual } from "./answerVisuals";
import { VisualIdentity } from "./visualState";

/** Where the turn's answer left each call's visual: drawn in it, or out of it. */
export type VisualPlace = "answer" | "aside";

/** The turn's calls with a visual, once it has an answer: where each one's visual is. */
export const PlacedVisuals = createContext<ReadonlyMap<string, VisualPlace>>(new Map());

export function usePlacedVisual(callId: string | undefined): VisualPlace | undefined {
  const placed = useContext(PlacedVisuals);
  return callId === undefined ? undefined : placed.get(callId);
}

export const visualAnchor = (callId: string) => `visual-${callId}`;

/** Scrolls the answer's copy of a visual into view, the way a link to a heading would. */
export function revealVisual(callId: string): void {
  const target = document.getElementById(visualAnchor(callId));
  if (!target) return;
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
  // A ring that fades in and out once, so the eye lands where the scroll stopped.
  target.dataset.revealed = "";
  window.setTimeout(() => delete target.dataset.revealed, 1400);
}

/** An answer's prose with its visuals where the model placed them (or after the opening
 *  paragraph, for the closing answer, when it placed none). */
export function AnswerWithVisuals({
  text,
  visuals,
  fallback,
  renderText,
}: {
  text: string;
  visuals: readonly TurnVisual[];
  fallback: boolean;
  renderText?: (text: string) => ReactNode;
}) {
  const parts = answerParts(text, visuals, fallback);
  return (
    <>
      {parts.map((part, i) =>
        part.kind === "text" ? (
          <div key={i}>{renderText ? renderText(part.text) : <Markdown text={part.text} />}</div>
        ) : (
          <div key={i} id={visualAnchor(part.visual.callId)} className="vz-in-answer">
            <IdentifiedBlocks visual={part.visual} />
          </div>
        ),
      )}
    </>
  );
}

/** A visual's blocks, told which visual they are, so a moved what-if can say where it stands. */
function IdentifiedBlocks({ visual }: { visual: TurnVisual }) {
  const identity = useMemo(() => (visual.id ? { id: visual.id, callId: visual.callId } : null), [visual.id, visual.callId]);
  return (
    <VisualIdentity.Provider value={identity}>
      <VisualBlocks blocks={visual.blocks} />
    </VisualIdentity.Provider>
  );
}
