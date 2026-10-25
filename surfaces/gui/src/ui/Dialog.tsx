// A modal dialog: drawn on document.body, so no ancestor can clip or re-anchor it, with the
// keyboard contract a dialog owes (ui-skills fixing-accessibility §3).
//
// Why a portal: an ancestor with a transform (a page's enter animation, even one that has
// finished with `fill-mode: both`) becomes the containing block of every `position: fixed`
// child. The connect sheet in Settings ▸ Connectors was drawn inside the content column with its
// scrim covering only that column (owner screenshot, 2026-10-10). Rendering into body makes the
// overlay the window's, whatever the page around the trigger does.
//
// Focus: the first `[data-autofocus]` element (else the first focusable one) takes focus on
// open; Tab and Shift+Tab cycle inside; Escape closes; focus returns to whatever had it before.
// The keys are heard on the document (capture), not on the dialog: a button that disables itself
// while it works drops focus to the page, and Escape must still close the dialog then.

import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Dialog({
  onClose,
  label,
  labelledBy,
  testId,
  children,
}: {
  onClose: () => void;
  /** The dialog's accessible name, when no visible heading names it. */
  label?: string;
  /** The id of the visible heading that names it. */
  labelledBy?: string;
  testId?: string;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);

  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const node = panel.current;
      if (!node) return;
      if (event.key === "Escape") {
        // Stopped here so a page that also closes on Escape (Settings) stays open under it.
        event.stopPropagation();
        event.preventDefault();
        close.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = [...node.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
        (el) => el.getClientRects().length > 0,
      );
      const active = document.activeElement;
      if (items.length === 0) {
        event.preventDefault();
        node.focus({ preventScroll: true });
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (!node.contains(active)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && (active === first || active === node)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, []);

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const node = panel.current;
    const first =
      node?.querySelector<HTMLElement>("[data-autofocus]") ?? node?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? node)?.focus({ preventScroll: true });
    return () => {
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);

  return createPortal(
    <div className="ui-dialog-root" data-testid={testId}>
      <div className="ui-dialog-scrim" aria-hidden="true" onClick={onClose} />
      <div
        ref={panel}
        className="ui-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={labelledBy ? undefined : label}
        aria-labelledby={labelledBy}
        tabIndex={-1}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
