// Keep a scroll region on its last line while text is still arriving.
//
// A bounded log that grows below its own fold looks frozen: the new lines are there, and the
// reader is looking at the ones from a minute ago. The rule is a terminal's own — the view
// follows the end UNTIL the reader scrolls up to read something, and follows again once they
// return to the end. `stuck` is DOM state held in a ref on purpose: scrolling must not
// re-render a card with thousands of lines.

import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";

/** How close to the end still counts as "at the end" (a line and a bit). */
const NEAR_END_PX = 24;

/** True when the reader is at (or within a line of) the end of the region. */
export function atEnd(scrollTop: number, scrollHeight: number, clientHeight: number): boolean {
  return scrollHeight - scrollTop - clientHeight <= NEAR_END_PX;
}

/**
 * Follow the end of the scroller found by `selector` inside `root`.
 *
 * `content` is whatever changes when text arrives (the parsed lines); `present` says whether
 * the scroller is mounted at all — a card with no output yet has none, and the listener has to
 * be attached when it appears.
 */
export function useFollowTail(
  root: RefObject<HTMLElement | null>,
  selector: string,
  on: boolean,
  content: unknown,
  present: boolean,
): void {
  const stuck = useRef(true);

  useEffect(() => {
    if (!on || !present) return;
    const el = root.current?.querySelector<HTMLElement>(selector);
    if (!el) return;
    const note = () => {
      stuck.current = atEnd(el.scrollTop, el.scrollHeight, el.clientHeight);
    };
    el.addEventListener("scroll", note, { passive: true });
    return () => el.removeEventListener("scroll", note);
  }, [root, selector, on, present]);

  // Layout effect: the jump happens before paint, so a new line never flashes below the fold.
  useLayoutEffect(() => {
    if (!on || !present || !stuck.current) return;
    const el = root.current?.querySelector<HTMLElement>(selector);
    if (el) el.scrollTop = el.scrollHeight;
  }, [root, selector, on, present, content]);
}
