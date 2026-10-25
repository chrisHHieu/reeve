// A tooltip that survives the transcript's overflow.
//
// The app had no tooltip primitive at all: every affordance was either labelled in place or
// silent. The rows a tooltip is most needed on — icon-only buttons, a truncated path, a
// status dot — live INSIDE disclosure bodies, and those clip their overflow to animate, so
// an absolutely-positioned bubble would be cut in half. This one renders through a portal on
// `document.body` at fixed coordinates instead, which also means it can escape the scroll
// container rather than scrolling away with its trigger.
//
// Timing follows the platform convention the rest of the app already uses for its hover
// popovers: a short delay before showing (so sweeping the pointer across a row of icons does
// not strobe), and none before hiding (a tooltip that lingers after the pointer left is
// just stale). Escape and click dismiss it, because both mean the reader moved on.

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

const OPEN_DELAY_MS = 350;

export function Tooltip({
  label,
  children,
  side = "top",
  disabled,
}: {
  label: string;
  children: ReactNode;
  side?: "top" | "bottom";
  disabled?: boolean;
}) {
  const wrap = useRef<HTMLSpanElement | null>(null);
  const timer = useRef<number | null>(null);
  const [spot, setSpot] = useState<{ left: number; top: number; side: "top" | "bottom" } | null>(
    null,
  );

  const hide = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    setSpot(null);
  }, []);

  const show = useCallback(() => {
    const el = wrap.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    // Flip below when there is no room above (a row at the top of the transcript). 44px
    // clears the tooltip's own height plus its offset, so the flip is not a half-measure.
    const place: "top" | "bottom" = side === "top" && rect.top < 44 ? "bottom" : side;
    setSpot({
      left: rect.left + rect.width / 2,
      top: place === "top" ? rect.top : rect.bottom,
      side: place,
    });
  }, [side]);

  useEffect(() => {
    if (disabled) hide();
    return hide;
  }, [disabled, hide, label]);

  useEffect(() => {
    if (spot === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") hide();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", hide, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", hide, true);
    };
  }, [spot, hide]);

  if (disabled || label === "") return <>{children}</>;

  return (
    <span
      ref={wrap}
      className="ui-tip-wrap"
      onPointerEnter={() => {
        if (timer.current !== null) window.clearTimeout(timer.current);
        timer.current = window.setTimeout(show, OPEN_DELAY_MS);
      }}
      onPointerLeave={hide}
      onFocusCapture={() => {
        // Keyboard focus shows immediately: the reader is not sweeping past, they stopped.
        if (timer.current !== null) window.clearTimeout(timer.current);
        show();
      }}
      onBlurCapture={hide}
      onClick={hide}
    >
      {children}
      {spot !== null &&
        createPortal(
          <div
            className="ui-tip"
            role="tooltip"
            data-side={spot.side}
            data-testid="tooltip"
            style={{ left: spot.left, top: spot.top }}
          >
            {label}
          </div>,
          document.body,
        )}
    </span>
  );
}
