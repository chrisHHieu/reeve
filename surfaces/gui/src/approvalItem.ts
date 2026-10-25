import type { InboxItem } from "./api";
import type { ApprovalItem } from "./types";

/**
 * The approval card's item, from the engine's `permission_required` payload. One reader for
 * both ways a prompt reaches the screen: the live frame, and the Inbox item the server parked
 * for it — which carries the same payload (`data.card`, plus tool/arguments/category/
 * presentation beside it). Two readers had drifted into two cards: back in a session waiting
 * on an approval, Always allow and Edit were gone (GUI UX review, U2).
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function approvalItemFromEvent(d: any): ApprovalItem {
  return {
    kind: "approval",
    name: d.name,
    args: d.arguments,
    reason: d.reason,
    category: d.category || undefined,
    axis: d.axis === "boundary" || d.axis === "consent" ? d.axis : undefined,
    humanOnly: d.human_only === true ? true : undefined,
    uncertain: d.uncertain === true ? true : undefined,
    standingTarget: d.standing_target || undefined,
    searchProvider: d.search_provider || undefined,
    provenance: d.provenance || undefined,
    reach: Array.isArray(d.reach) && d.reach.length ? d.reach : undefined,
    presentation: d.presentation || undefined,
  };
}

/** The same card for a parked prompt, or null when the item predates the payload (no tool). */
export function approvalItemFromInbox(item: InboxItem): ApprovalItem | null {
  const data = item.data || {};
  if (item.kind !== "approval" || typeof data.tool !== "string") return null;
  return approvalItemFromEvent({
    reason: item.body,
    ...(data.card || {}),
    name: data.tool,
    arguments: data.arguments || {},
    category: data.category,
    presentation: data.presentation,
    // An automation run's parked prompt names its standing target beside the task.
    ...(data.standing_target && !data.card?.standing_target ? { standing_target: data.standing_target } : {}),
  });
}
