// The note a model folded its own context into (`fold_context`), for the person reading the
// turn.
//
// The note is what the model sees from its next step in place of the folded turns, so it is
// drawn as prose, the way a loaded skill's body is: without this the row expanded to the
// call's JSON, the note one escaped string inside it. The banner carries what the fold did
// to the context, which is the other half of the fact: the size before and after.

import type { FoldResultCard } from "./types";
import { Markdown } from "../components/Markdown";
import { ScrollFade } from "../components/primitives/ScrollFade";
import { formatTokens } from "../usage";

export function FoldBlock({ note, card }: { note: string; card: FoldResultCard | null }) {
  return (
    <div className="rv-card" data-fold="">
      <div className="rv-banner">
        <span className="rv-banner-label">
          {card?.replaced_notes ? "Note · replaces the earlier notes" : "Note"}
        </span>
        <span className="rv-banner-spacer" />
        {card !== null && (
          <span className="fold-size" data-testid="fold-size">
            {formatTokens(card.before_tokens)} → {formatTokens(card.after_tokens)} tokens
          </span>
        )}
      </div>
      <ScrollFade className="fold-note rv-scroll" data-testid="fold-note">
        <Markdown text={note.trim()} />
      </ScrollFade>
    </div>
  );
}
