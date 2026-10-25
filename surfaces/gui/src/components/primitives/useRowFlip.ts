// Rows that slide to their new place when a reader reorders or filters them (FLIP): measure
// where each row stood just before the change, then after the render play each one from there
// to where it now is. A row that just appeared fades in. Only a reader's action measures, so
// rows that move because content streamed in never animate; reduced motion moves them at once.

import { useLayoutEffect, useRef, type RefObject } from "react";

const EASE = "cubic-bezier(.22, 1, .36, 1)";

const reducedMotion = () =>
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Returns `measure`: call it in the handler, before the state change that reorders the rows.
 *  Rows are the container's children matching `selector` and carrying `data-row`. */
export function useRowFlip(container: RefObject<HTMLElement | null>, selector: string, order: unknown): () => void {
  const before = useRef<Map<string, number> | null>(null);
  const rows = () => Array.from(container.current?.querySelectorAll<HTMLElement>(selector) ?? []);

  useLayoutEffect(() => {
    const positions = before.current;
    before.current = null;
    if (!positions || reducedMotion()) return;
    for (const row of rows()) {
      if (typeof row.animate !== "function") continue;
      const was = positions.get(row.dataset.row ?? "");
      if (was === undefined) {
        row.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 240, easing: EASE });
        continue;
      }
      const dy = was - row.getBoundingClientRect().top;
      if (Math.abs(dy) > 0.5) {
        row.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], { duration: 300, easing: EASE });
      }
    }
  }, [order]); // eslint-disable-line react-hooks/exhaustive-deps

  return () => {
    const positions = new Map<string, number>();
    for (const row of rows()) positions.set(row.dataset.row ?? "", row.getBoundingClientRect().top);
    before.current = positions;
  };
}
