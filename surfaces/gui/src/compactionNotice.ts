// The marker a compaction leaves in the transcript, built in one place for the three ways one
// arrives: the engine's `compacted` frame, the persisted notice on a reload, and a fold the
// model made itself (`fold_context`).
//
// It says what happened in one word and what it did to the context as "from → to", which is
// the fact a reader wants: "12.5k freed" needed the size before it to mean anything.

import type { FoldResultCard } from "./cards/types";
import type { NoticeItem } from "./types";
import { formatTokens } from "./usage";

const count = (value: unknown): number => (typeof value === "number" && value > 0 ? value : 0);

const tookWords = (seconds: number): string =>
  seconds < 60 ? `${Math.max(1, Math.round(seconds))}s` : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;

/** The marker for a compaction the engine ran. It arrives closed, live or replayed. */
export function compactionNotice(source: object): NoticeItem {
  const data = source as { saved_tokens?: unknown; now_tokens?: unknown; summary?: unknown; seconds?: unknown };
  const saved = count(data.saved_tokens);
  const now = count(data.now_tokens);
  // How long it took, where the frame says (a `/compact` between turns).
  const took = count(data.seconds) ? ` · ${tookWords(count(data.seconds))}` : "";
  const summary = typeof data.summary === "string" && data.summary ? data.summary : undefined;
  return {
    kind: "notice",
    tone: "info",
    text: "Compacted",
    divider: true,
    ...(saved > 0 && now > 0
      ? { span: `${formatTokens(saved + now)} → ${formatTokens(now)} tokens${took}` }
      : saved > 0
        ? { freed: `${formatTokens(saved)} freed${took}` }
        : {}),
    ...(summary ? { detail: summary } : {}),
  };
}

/** The same marker for a fold the model made: the note is what it now reads in place of the
 *  folded turns. */
export function foldNotice(note: string, card: FoldResultCard | null): NoticeItem {
  return {
    kind: "notice",
    tone: "info",
    text: "Folded by the agent",
    divider: true,
    ...(card !== null
      ? { span: `${formatTokens(card.before_tokens)} → ${formatTokens(card.after_tokens)} tokens` }
      : {}),
    detail: note,
  };
}
