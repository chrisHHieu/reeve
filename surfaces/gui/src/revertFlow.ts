// The composer's `/undo` / `/redo` gates and the draft hand-back.
//
// The reference design gates its commands on live message state: `/undo` is disabled when no visible
// user message remains (`visibleUserMessages().length === 0`)
// and `/redo` when no revert marker is staged (`!info()?.revert?.messageID`, :475). Our
// transcript is the canonical record with the boundary drawn INTO it as a notice item, so
// "visible" is "before the boundary item" — one predicate over `Item[]`, testable without
// standing up the app.

import type { RevertResult } from "./api";
import type { Attachment, Item } from "./types";

/** Index of the revert boundary item in a transcript, -1 when none is staged. */
export function revertBoundaryAt(items: Item[]): number {
  return items.findIndex((it) => it.kind === "notice" && it.boundary === "revert");
}

/** Whether a user message the model still sees exists — the `/undo` gate. */
export function canUndoItems(items: Item[]): boolean {
  const boundary = revertBoundaryAt(items);
  const end = boundary < 0 ? items.length : boundary;
  return items.slice(0, end).some((it) => it.kind === "user");
}

/** Whether the marker is staged — the `/redo` gate. */
export function canRedoMarker(marker: unknown): boolean {
  return marker !== null && marker !== undefined;
}

/** The text + attachments `/undo` pulls back into the composer draft, or null. */
export function revertDraft(
  res: RevertResult,
): { text: string; attachments?: Attachment[] } | null {
  if (!res.ok || !res.message?.text) return null;
  return { text: res.message.text, attachments: res.message.attachments };
}
