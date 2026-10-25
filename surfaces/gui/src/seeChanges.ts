// "Show me what changed in this file": asked from the transcript (a row of a turn's receipt),
// answered by the side panel's Review, which may not be open or even mounted. The app hears
// the event, opens the panel, and hands Review the path (the way a dev-server URL reaches the
// Browser tab).

export const SEE_CHANGES_EVENT = "reeve-see-changes";

export function seeChangesIn(path: string): void {
  window.dispatchEvent(new CustomEvent(SEE_CHANGES_EVENT, { detail: { path } }));
}

/** Open the side panel on Review's list, no file singled out: what a turn that changed files
 *  shows when it ends (layout step 3, 2026-10-13). */
export function openReview(): void {
  window.dispatchEvent(new CustomEvent(SEE_CHANGES_EVENT, { detail: { path: "" } }));
}
