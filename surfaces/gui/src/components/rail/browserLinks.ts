// A web link in the conversation opens in the panel's Browser, in the desktop app (owner,
// 2026-10-20: a link the agent gave opened the computer's own browser, away from the work). The
// desktop Browser shows any site; in a web page most sites refuse to be framed, so there the link
// keeps its new tab. Ctrl, Shift, Alt or ⌘ with the click, or a middle click, is the way out to
// the computer's browser, as in any browser.
import type { MouseEvent } from "react";
import { canEmbedBrowser } from "../../desktop";
import { OPEN_BROWSER_EVENT } from "./tabs";

/** A link's click handler: takes a plain left click on a web link inside the session's view and
 *  hands the address to the panel. Anything else is left to the link (and the external router). */
export function openLinkInPanel(event: MouseEvent<HTMLAnchorElement>): void {
  if (!canEmbedBrowser() || event.defaultPrevented) return;
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const anchor = event.currentTarget;
  if (!/^https?:$/i.test(anchor.protocol)) return;
  // Only where the panel is beside it: a link in Settings or a dialog leaves as before.
  if (!anchor.closest('[data-testid="session-surface"]')) return;
  event.preventDefault();
  window.dispatchEvent(new CustomEvent(OPEN_BROWSER_EVENT, { detail: { url: anchor.href } }));
}
