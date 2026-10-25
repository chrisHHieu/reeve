/** Pure sizing rules for the right rail (the Changes/file review panel).
 *
 * Mirrors the session-panel geometry:
 *
 *   SESSION_PANEL_WIDTH_MIN = 450   -> CHAT_WIDTH_MIN: the conversation never gets less
 *   REVIEW_PANE_WIDTH_MIN   = 480   -> PANEL_WIDTH_MIN (unified diff)
 *   REVIEW_PANE_WIDTH_MIN_SPLIT = 800 -> PANEL_WIDTH_MIN_SPLIT
 *
 * The reference design renders the chat panel at a fixed pixel width and lets the review pane take
 * whatever is left; the chat's cap reserves the pane's own minimum. We render the PANE at a
 * fixed width and reserve the CHAT's minimum instead — same constants, resolved the other
 * way: when the window cannot fit both minimums the conversation's minimum wins (the reference design's
 * `sessionPanelWidthMax` never drops below SESSION_PANEL_WIDTH_MIN either).
 */

// The reference design keeps the file tree itself at a fixed width next to the flexing review pane.
// FILE_TREE_WIDTH_MIN = 240 in session-side-panel.tsx; the resize handle caps it at 480.
export const TREE_WIDTH_DEFAULT = 248;
export const TREE_WIDTH_MIN = 240;
export const TREE_WIDTH_MAX = 480;

export const CHAT_WIDTH_MIN = 450;
export const PANEL_WIDTH_MIN = 480;
export const PANEL_WIDTH_MIN_SPLIT = 800;

// The pre-drag default mirrors the CSS fallback `--review-rail-w: clamp(480px, 44vw, 720px)`
// (40vw up to LAPTOP_MAX, see styles.css)
// (a moderate panel that leaves a laptop's composer usable).
const PANEL_WIDTH_DEFAULT_VW = 0.44;
// A laptop (<= LAPTOP_MAX) gives the panel a smaller share: at 1366px, 44% left the conversation
// 466px beside a 300px sidebar, and the panel's own start page needs far less than that.
export const LAPTOP_MAX = 1440;
const PANEL_WIDTH_DEFAULT_VW_LAPTOP = 0.4;
const PANEL_WIDTH_DEFAULT_MAX = 720;

export function defaultPanelWidth(viewport: number): number {
  const vw = Number.isFinite(viewport) && viewport > 0 ? viewport : 0;
  return Math.round(
    Math.min(
      PANEL_WIDTH_DEFAULT_MAX,
      Math.max(PANEL_WIDTH_MIN, vw * (vw <= LAPTOP_MAX ? PANEL_WIDTH_DEFAULT_VW_LAPTOP : PANEL_WIDTH_DEFAULT_VW)),
    ),
  );
}

/** The conversation's floor for a window this wide: CHAT_WIDTH_MIN on a small window, and
 *  a third of the room (up to the transcript's own column, ~720px) on a big one. A flat 450px
 *  let a panel dragged wide once (or sized on a bigger monitor) keep a 1900px window's
 *  conversation at 450px for good, with the rest of the screen empty. It was 40% until the
 *  layout put the work in front (2026-10-13, "B frame, A inside"): the conversation steers, the
 *  work pane is what is looked at, so the pane may take two thirds. */
export function chatWidthMin(available: number): number {
  return Math.min(CHAT_WIDTH_FLOOR_MAX, Math.max(CHAT_WIDTH_MIN, Math.round(available * CHAT_SHARE)));
}
const CHAT_WIDTH_FLOOR_MAX = 720;
const CHAT_SHARE = 0.33;

/** The pane's width before the person has dragged it: the work is the stage, so the pane takes
 *  all the room the conversation does not need (its floor, above). Unmeasured, the vw default. */
export function workPanelWidth(available: number | undefined, viewport: number): number {
  return available && available > 0 ? Math.max(PANEL_WIDTH_MIN, panelWidthMax(available)) : defaultPanelWidth(viewport);
}

/** The widest the pane may be while the conversation keeps its floor. */
export function panelWidthMax(available: number): number {
  return Math.max(0, Math.round(available - chatWidthMin(available)));
}

/** The pane's own minimum, yielded when the window cannot fit both minimums. */
export function panelWidthMin(available: number, split: boolean): number {
  return Math.min(split ? PANEL_WIDTH_MIN_SPLIT : PANEL_WIDTH_MIN, panelWidthMax(available));
}

/** The clampSessionPanelWidth with both bounds. An unmeasured (or degenerate)
 *  layout leaves the stored width untouched so there is no first-frame snap. */
export function clampPanelWidth(input: { width: number; available?: number; split: boolean }): number {
  const { width, available, split } = input;
  if (available === undefined || !(available > 0)) return width;
  return Math.min(Math.max(width, panelWidthMin(available, split)), panelWidthMax(available));
}

export function clampTreeWidth(width: number): number {
  const w = Number.isFinite(width) ? Math.round(width) : TREE_WIDTH_DEFAULT;
  return Math.min(TREE_WIDTH_MAX, Math.max(TREE_WIDTH_MIN, w));
}

// Per-device preference storage, matching the rail's localStorage pattern (appHelpers keys).
export function readStoredSize(key: string): number | undefined {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return undefined;
    const value = Number(raw);
    return Number.isFinite(value) && value > 0 ? value : undefined;
  } catch {
    return undefined;
  }
}

export function writeStoredSize(key: string, value: number) {
  try {
    localStorage.setItem(key, String(Math.round(value)));
  } catch {
    /* best effort */
  }
}

export function clearStoredSize(key: string) {
  try {
    localStorage.removeItem(key);
  } catch {
    /* best effort */
  }
}
