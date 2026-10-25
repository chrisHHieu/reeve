// Which design-checked calls show the page's card. A page gets one card a turn, under the
// call that changed it last: the card draws the page as it is now, so a card under the first write
// with chips under every later edit put the newest picture in the oldest place and showed the page
// twice (owner, 2026-10-20). The earlier calls keep their check line, which says whether that change
// left anything to fix.
import { createContext } from "react";
import type { Item } from "../types";
import { designKey } from "./rail/tabs";

/** The calls whose rows show their page's card; null outside a transcript (every row shows it). */
export const DesignCards = createContext<ReadonlySet<string> | null>(null);

/** A design-checked call's id, as `designCardCalls` names it. */
export const designCallId = (item: { callId?: string; id?: string }) => item.callId ?? item.id ?? "";

export function designCardCalls(items: readonly Item[]): Set<string> {
  const shown = new Set<string>();
  let turn = new Map<string, string>();
  const close = () => {
    turn.forEach((call) => shown.add(call));
    turn = new Map();
  };
  for (const item of items) {
    if (item.kind === "user") {
      close();
      continue;
    }
    if (item.kind !== "tool" || !item.designCheck?.length) continue;
    for (const check of item.designCheck) {
      if (check.path) turn.set(designKey(check.path), designCallId(item));
    }
  }
  close();
  return shown;
}
