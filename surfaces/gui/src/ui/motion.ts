// Motion tokens and the one capability check the whole UI shares.
//
// The app had grown four different durations (0.08s press, 0.15s hover, 0.28s disclosure,
// 0.6s reveal) written inline at each call site, which is how two rows that do the same
// thing end up moving differently. These are the only numbers a new component needs.
//
// `usePrefersReducedMotion` reports `true` when the environment cannot answer the media
// query at all (a headless DOM, an old webview). That is deliberate: "unknown" must fall
// back to still, because the alternative is animating for someone who asked us not to.

import { useEffect, useState } from "react";

/** Milliseconds. `instant` is for press feedback, `slow` for a settled surface arriving. */
export const DURATION = {
  instant: 90,
  fast: 150,
  base: 240,
  slow: 380,
} as const;

export const EASING = {
  /** Decelerating: starts fast, settles gently. Everything that ENTERS uses this. */
  out: "cubic-bezier(.22, 1, .36, 1)",
  /** Symmetric: for a value moving between two states in place (a slider, a bar). */
  inOut: "cubic-bezier(.4, 0, .2, 1)",
} as const;

export const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

/** The media query, or `null` when this environment has no matchMedia (jsdom, old webview). */
function motionQuery(): MediaQueryList | null {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return null;
  try {
    return window.matchMedia(REDUCED_MOTION_QUERY);
  } catch {
    return null;
  }
}

/** Non-reactive check for code that only runs once (a mount decision, a CSS-in-JS value). */
export function prefersReducedMotion(): boolean {
  const query = motionQuery();
  return query === null ? true : query.matches;
}

/** Reactive variant: a component that animates must stop when the setting flips mid-session. */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion);
  useEffect(() => {
    const query = motionQuery();
    if (query === null) return;
    const onChange = () => setReduced(query.matches);
    query.addEventListener?.("change", onChange);
    return () => query.removeEventListener?.("change", onChange);
  }, []);
  return reduced;
}
