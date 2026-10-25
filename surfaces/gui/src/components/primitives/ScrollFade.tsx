// A scroll container that SAYS it scrolls: a soft fade on whichever edge still has content
// behind it, and the answer to the one real complaint about
// our cards — a body that stopped mid-output looked like the whole output.
//
// The fade is a `mask-image` on the scroller itself, so no overlay div is
// inserted and the content's own background stays true. The element keeps whatever classes
// the card gave it (`.rv-scroll` and friends own the cap); this component only adds the
// state attribute the CSS needs.
//
// It is a DOM-state component, not a React-state one: scrolling must not re-render a card
// with a 20,000-character body on every frame.

import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";

export type ScrollFadeState = "none" | "top" | "bottom" | "both";

/** Which edges are hiding content, from the three numbers a scroller offers. Pure, so the
 *  rule can be tested without a layout engine. */
export function scrollFadeState(
  scrollTop: number,
  scrollHeight: number,
  clientHeight: number,
): ScrollFadeState {
  const maxScroll = Math.max(0, scrollHeight - clientHeight);
  // A scroller that cannot scroll has nothing to announce (tolerance: sub-pixel layouts).
  if (maxScroll <= 1) return "none";
  const atTop = scrollTop <= 1;
  const atBottom = scrollTop >= maxScroll - 1;
  if (atTop && atBottom) return "none";
  if (atTop) return "bottom";
  if (atBottom) return "top";
  return "both";
}

/** The attribute value for an axis: the same state, named the way that axis reads. */
export function fadeEdge(
  axis: "vertical" | "horizontal",
  state: ScrollFadeState,
): ScrollFadeState | "left" | "right" {
  if (axis === "vertical") return state;
  if (state === "top") return "left";
  if (state === "bottom") return "right";
  return state;
}

export function ScrollFade({
  className,
  children,
  style,
  axis = "vertical",
  ...rest
}: {
  className?: string;
  children: ReactNode;
  style?: CSSProperties;
  /** Which way the content scrolls: the fade follows the hidden edge on that axis. */
  axis?: "vertical" | "horizontal";
} & Record<string, unknown>) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // The state helper is written once for "the edge that hides content": for a vertical
    // scroller that is top/bottom, for a horizontal one left/right. The attribute carries the
    // axis-specific name so the CSS can read as CSS.
    const apply = () => {
      const state =
        axis === "horizontal"
          ? scrollFadeState(el.scrollLeft, el.scrollWidth, el.clientWidth)
          : scrollFadeState(el.scrollTop, el.scrollHeight, el.clientHeight);
      const next = fadeEdge(axis, state);
      if (el.getAttribute("data-fade") !== next) el.setAttribute("data-fade", next);
    };
    apply();
    el.addEventListener("scroll", apply, { passive: true });
    // A grammar's token spans, a resize, or an async card body all change the height with
    // no scroll event — the observer is what keeps the state honest.
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(apply);
    observer?.observe(el);
    if (el.firstElementChild) observer?.observe(el.firstElementChild);
    return () => {
      el.removeEventListener("scroll", apply);
      observer?.disconnect();
    };
  }, [axis]);

  return (
    <div ref={ref} className={className} style={style} data-fade="none" {...rest}>
      {children}
    </div>
  );
}
