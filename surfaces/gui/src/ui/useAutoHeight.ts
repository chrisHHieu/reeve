// A box whose content changes size in place glides to its new height instead of jumping.
//
// `Disclosure` is for a region that is shown and hidden. This is for the other case: the same
// box, different content (a clamped preview opened to all of its lines, a view swapped for
// its edit form, a question replaced by the next one). The box is watched; when its height
// changes and its width did not, the change is replayed from the old height to the new one.
// Nothing is measured ahead of time and no height is ever written to the element's style, so
// content that keeps growing afterwards lays out as it always did.
//
// The observer answers after layout and before paint, so the first frame the person sees is
// the old height, not the new one. Width changes are let through as they are: a window being
// resized reflows text on every frame, and animating those would be motion nobody asked for.

import { useLayoutEffect, type RefObject } from "react";
import { DURATION, EASING, usePrefersReducedMotion } from "./motion";

export function useAutoHeight(ref: RefObject<HTMLElement | null>, enabled = true): void {
  const reduced = usePrefersReducedMotion();
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !enabled || reduced) return;
    if (typeof ResizeObserver === "undefined" || typeof el.animate !== "function") return;
    // The layout size (`offsetWidth` / `offsetHeight`), not the rectangle on screen: a card
    // that arrives with a scale-in is narrower on screen for its first frames, and a width
    // read then made the first change look like a resize of the window, which is let through
    // without a glide.
    const size = () => ({ width: el.offsetWidth, height: el.offsetHeight });
    let last = size();
    let running: Animation | null = null;
    const observer = new ResizeObserver(() => {
      // While the glide runs the box changes size on every frame: that is the glide itself.
      if (running) return;
      const next = size();
      const from = last.height;
      const to = next.height;
      const sameWidth = Math.abs(next.width - last.width) < 0.5;
      last = next;
      if (!sameWidth || from === 0 || to === 0 || Math.abs(to - from) < 2) return;
      const glide = el.animate(
        [
          { height: `${from}px`, overflow: "hidden" },
          { height: `${to}px`, overflow: "hidden" },
        ],
        { duration: DURATION.base, easing: EASING.out },
      );
      running = glide;
      const settle = () => {
        if (running === glide) running = null;
        last = size();
      };
      glide.onfinish = settle;
      glide.oncancel = settle;
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
      running?.cancel();
    };
  }, [ref, enabled, reduced]);
}
