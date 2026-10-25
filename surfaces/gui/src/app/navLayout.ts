// How wide the left navigation may be, given the window and what else is open.
//
// The queue is 300px (--nav-w less the rail), and the conversation needs about 450
// (railSizing.ts CHAT_WIDTH_MIN) plus room for the composer. In a small window the
// conversation used to pay for the sidebar: at 420px the sidebar left the chat ~120px, a
// column of single-letter lines. The navigation is the part the reader can reach in one
// click and the conversation is not, so the navigation gives way:
//
//   · below `NAV_RAIL_BELOW` the sidebar is the 56px rail, whatever the saved preference
//   · with the side panel open, below `NAV_RAIL_WITH_PANEL_BELOW` too: a laptop window holds
//     the conversation and the work beside it, not the queue as well (2026-10-14, as the
//     layout's final mockup drew it; the owner left the call to me). Before the work
//     pane took the room the conversation does not need (layout step 3), keeping the list
//     was the better trade (2026-08-21: switching conversations is the primary move); at
//     1440 it left the conversation its 450px floor and the work 670px.
//   · the reader can still expand it (a "peek"); where it was folded for the panel, or below
//     `NAV_OVERLAY_BELOW`, an expanded queue covers the page rather than squeezing it, and
//     closes when they pick something
//
// The saved preference (⌘B) is for the widths where there is room for either; it is never
// overwritten by a window that happened to be small, nor by a panel that happened to be open.

export const NAV_RAIL_BELOW = 1100;
export const NAV_RAIL_WITH_PANEL_BELOW = 1500;
export const NAV_OVERLAY_BELOW = 760;

export interface NavLayoutInput {
  /** `window.innerWidth`. */
  viewport: number;
  /** The reader's saved preference (⌘B): collapsed to the rail. */
  preferCollapsed: boolean;
  /** The reader expanded the navigation in a window that would otherwise collapse it. */
  peek: boolean;
  /** The side panel (the work pane) is open beside the conversation. */
  panelOpen?: boolean;
}

export interface NavLayout {
  /** The window is too tight for the expanded navigation, so the rail is the default. */
  auto: boolean;
  /** Draw the 56px rail. */
  collapsed: boolean;
  /** Draw the expanded navigation over the page instead of beside it. */
  overlay: boolean;
}

export function navLayout({ viewport, preferCollapsed, peek, panelOpen = false }: NavLayoutInput): NavLayout {
  const forPanel = panelOpen && viewport < NAV_RAIL_WITH_PANEL_BELOW;
  const auto = viewport < NAV_RAIL_BELOW || forPanel;
  // In a tight window the reader's last explicit act (peek or not) decides; in a roomy one,
  // their saved preference does.
  const collapsed = auto ? !peek : preferCollapsed;
  return { auto, collapsed, overlay: !collapsed && (viewport < NAV_OVERLAY_BELOW || forPanel) };
}
