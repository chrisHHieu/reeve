// A region that grows open and shrinks closed, with its content mounted only while it is
// doing one of those two things.
//
// The app used to mount the body with `{open && body}` and animate the OPEN with a
// max-height keyframe. That reads well on the way in and then the row snaps shut, which is
// the half of the interaction a reader notices most: they clicked to put something away and
// the layout jumped. Closing is animated here too, and the body is unmounted once the close
// finishes so a folded transcript does not keep every diff it ever showed in the DOM.
//
// The animation is `grid-template-rows: 0fr → 1fr`, not a measured max-height: it needs no
// ResizeObserver, it is correct for content of any height, and it animates on the compositor
// path the same way. The body must be mounted for the closing frames, so `mounted` lags
// `open` by exactly one transition, and never when motion is off.

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { DURATION, usePrefersReducedMotion } from "./motion";

export function Disclosure({
  open,
  children,
  className,
  testId,
}: {
  open: boolean;
  children: ReactNode;
  className?: string;
  testId?: string;
}) {
  const reduced = usePrefersReducedMotion();
  const [mounted, setMounted] = useState(open);
  const [expanded, setExpanded] = useState(open);

  const node = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      setMounted(true);
      return;
    }
    setExpanded(false);
    if (reduced) {
      setMounted(false);
      return;
    }
    const timer = window.setTimeout(() => setMounted(false), DURATION.base);
    return () => window.clearTimeout(timer);
  }, [open, reduced]);

  // The open is two steps, and the second waits for the first to be IN the page: the body
  // mounts at `0fr`, the browser is made to see it there, and only then is it flipped to
  // `1fr`, so there is a starting value to move from. The flip used to be asked for in the
  // same effect that asked for the mount, on the next animation frame; when that frame came
  // before React had committed the mount, both landed in one commit and the body was simply
  // there, fully open (measured 2026-10-12: a region closed as a motion and opened as a jump,
  // about half of the time).
  useLayoutEffect(() => {
    if (!open || !mounted) return;
    if (reduced) {
      setExpanded(true);
      return;
    }
    node.current?.getBoundingClientRect();
    const frame = requestAnimationFrame(() => setExpanded(true));
    return () => cancelAnimationFrame(frame);
  }, [open, mounted, reduced]);

  if (!mounted) return null;
  return (
    <div
      ref={node}
      className={className ? `ui-disclosure ${className}` : "ui-disclosure"}
      data-open={expanded || undefined}
      data-testid={testId}
    >
      <div className="ui-disclosure-inner">{children}</div>
    </div>
  );
}
