// A popover that ESCAPES its trigger's stacking context — portaled to `document.body`,
// positioned from the trigger's rectangle.
//
// Why not an absolutely-positioned child (the obvious thing): a panel anchored inside the
// topbar competes with the transcript for paint order, and loses. `.main-topbar` is a
// positioned box with no z-index, while transcript furniture carries its own (`.turn-pin-strip`
// is 5, a card's own menu is 30): the roster came up UNDER a user bubble (owner-hit,
// 2026-10-03) — and a card menu that happens to be open paints over it too. The reference design hits the same
// wall and solves it the same way (`SubagentHeaderLineage.tsx`: `createPortal(…, document.body)`
// with a `position: fixed` menu computed from `getBoundingClientRect`).
//
// So: fixed position, measured anchor, re-measured on resize/scroll while open, and the two
// dismissals every popover here owes the reader (outside pointerdown, Escape). `pointerdown`
// and not `click`: a press that starts outside must close before it finishes somewhere else.

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

/** Never let the panel touch the viewport edge; the menu margin is 16. */
const VIEWPORT_MARGIN = 12;

/** The panel's layer on the page: over everything the page draws (the question line is 5, the
 *  phone-width navigation 50), under a dialog (80). Being portaled is not enough by itself: a
 *  panel with no layer of its own is under any positioned box that has one, and the session
 *  menu opened with its first item behind the question line (owner-hit, 2026-10-13; proven in
 *  e2e/turn-pin.spec.ts by asking what is drawn where the two meet). */
const PANEL_LAYER = 60;

export interface AnchoredPopoverProps {
  /** The trigger: the panel is placed under its bottom-right corner. */
  anchorRef: RefObject<HTMLElement | null>;
  open: boolean;
  onClose: () => void;
  /** The panel's width in px; the height is content-driven and clamped to the viewport. */
  width: number;
  label: string;
  testId?: string;
  className: string;
  children: ReactNode;
  /** Which edge of the trigger the panel lines up with: its right (default) or its left, for a
   *  trigger at the right end of a narrow column whose panel should open over the page. */
  align?: "end" | "start";
}

export function AnchoredPopover({
  anchorRef,
  open,
  onClose,
  width,
  label,
  testId,
  className,
  children,
  align = "end",
}: AnchoredPopoverProps) {
  const panel = useRef<HTMLDivElement>(null);
  const [style, setStyle] = useState<{ top: number; left: number; maxHeight: number } | null>(null);

  const place = useCallback(() => {
    const anchor = anchorRef.current;
    if (anchor === null) return;
    const rect = anchor.getBoundingClientRect();
    const w = Math.min(width, window.innerWidth - VIEWPORT_MARGIN * 2);
    // Right-aligned to the trigger, then pulled back inside the viewport — so the panel never
    // hangs off the edge on a narrow window (or under the sidebar, which is what made a row
    // un-clickable once).
    const left = Math.max(
      VIEWPORT_MARGIN,
      Math.min(align === "start" ? rect.left : rect.right - w, window.innerWidth - w - VIEWPORT_MARGIN),
    );
    // Whole pixels: a trigger centred in a bar ends on a half pixel, and the panel then sat
    // half a pixel on the bar's lower edge.
    const below = Math.ceil(rect.bottom + 8);
    // Once drawn, a panel with no room under its trigger opens above it when it fits there: a
    // delivered file's menu sits just over the composer, and its last row went off the window.
    const height = panel.current?.offsetHeight ?? 0;
    const above = Math.floor(rect.top - 8 - height);
    const flip = height > 0 && below + height > window.innerHeight - VIEWPORT_MARGIN && above >= VIEWPORT_MARGIN;
    const top = flip ? above : below;
    setStyle({ top, left, maxHeight: flip ? height : Math.max(160, window.innerHeight - top - VIEWPORT_MARGIN) });
  }, [anchorRef, width, align]);

  // Measure before paint, then keep up with the viewport. The topbar does not scroll, but the
  // window can be resized (or the rail toggled) while the panel is open.
  useLayoutEffect(() => {
    if (!open) {
      setStyle(null);
      return;
    }
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, place]);

  // The first placement cannot know the panel's height (it is not drawn yet): place it once
  // more as soon as it is, before paint, so a flip never shows the panel in the wrong spot.
  const drawn = style !== null;
  useLayoutEffect(() => {
    if (open && drawn) place();
  }, [open, drawn, place]);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (anchorRef.current?.contains(target) === true) return;
      if (panel.current?.contains(target) === true) return;
      onClose();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [anchorRef, onClose, open]);

  if (!open || style === null) return null;
  return createPortal(
    <div
      ref={panel}
      className={className}
      role="group"
      aria-label={label}
      data-testid={testId}
      style={{ position: "fixed", top: style.top, left: style.left, width, maxHeight: style.maxHeight, zIndex: PANEL_LAYER }}
    >
      {children}
    </div>,
    document.body,
  );
}
