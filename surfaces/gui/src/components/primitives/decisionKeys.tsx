// The keyboard half of a decision dock — permission dialog semantics, applied to
// every decision card (components/decision/Decision.tsx):
//
//   1..9        answer with that option
//   ↑ / ↓, Tab  move the selection (Tab keeps the browser's own focus order working)
//   Enter       confirm the selected option
//   Escape      dismiss, when the surface has a dismissal
//
// The selection is a DOM attribute (`data-kbd`) rather than React state on purpose: the
// options are heterogeneous buttons owned by three different components, and a press of an
// arrow key must not re-render a card that is showing a 170-line preview. The hook focuses
// the container on mount, so the very first keypress works without a click first — the gap
// the old cards had (focus fell to <body> because the composer unmounts under a gate).

import { useEffect, type RefObject } from "react";

const OPTION = "button[data-option]:not([disabled])";

function options(container: HTMLElement): HTMLButtonElement[] {
  return Array.from(container.querySelectorAll<HTMLButtonElement>(OPTION));
}

function mark(container: HTMLElement, next: number): void {
  const items = options(container);
  items.forEach((item, index) => {
    if (index === next) item.setAttribute("data-kbd", "true");
    else item.removeAttribute("data-kbd");
  });
}

function selectedIndex(container: HTMLElement): number {
  return options(container).findIndex((item) => item.hasAttribute("data-kbd"));
}

export function useDecisionKeyboard(
  ref: RefObject<HTMLElement | null>,
  {
    onDismiss,
    enabled = true,
  }: {
    /** Escape. Omitted where the surface has no dismissal (a plan must be answered). */
    onDismiss?: () => void;
    enabled?: boolean;
  } = {},
): void {
  useEffect(() => {
    const container = ref.current;
    if (!container || !enabled) return;
    // The first option is the selection until a key moves it — the card must look answerable
    // without a pointer hovering it.
    if (selectedIndex(container) < 0) mark(container, 0);
    // Focus the container, not an option: a real focus ring on "Allow once" before anything
    // was pressed reads as a choice already made.
    if (!container.contains(document.activeElement)) {
      container.tabIndex = -1;
      container.focus({ preventScroll: true });
    }

    const onKey = (event: KeyboardEvent) => {
      const items = options(container);
      if (items.length === 0) return;
      // Typing belongs to the field: a "1" typed into a question's own answer box must not
      // pick option 1, and Enter there submits the field, not the highlighted option.
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
        return;
      }
      if (event.key === "Escape" && onDismiss) {
        event.preventDefault();
        onDismiss();
        return;
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const current = selectedIndex(container);
        const delta = event.key === "ArrowDown" ? 1 : -1;
        const next = current < 0 ? 0 : (current + delta + items.length) % items.length;
        mark(container, next);
        // The person is on the arrow keys now: the card may draw where the selection is.
        container.setAttribute("data-keys", "");
        return;
      }
      if (event.key === "Enter") {
        // A focused button already activates itself; only the container-focus case needs us.
        if (event.target instanceof HTMLElement && event.target.closest(OPTION)) return;
        const index = selectedIndex(container);
        if (index >= 0) {
          event.preventDefault();
          items[index].click();
        }
        return;
      }
      // Digits answer immediately: the number is printed on the option, and reaching for the
      // mouse to use it would be the whole cost the shortcut exists to remove.
      const digit = Number(event.key);
      if (Number.isInteger(digit) && digit >= 1 && digit <= items.length) {
        event.preventDefault();
        items[digit - 1].click();
      }
    };
    // A click or a pointer move means the person is on the mouse; the keyboard highlight is
    // then noise, so it is cleared and the hover styling takes over.
    const clearOnPointer = () => {
      options(container).forEach((item) => item.removeAttribute("data-kbd"));
      container.removeAttribute("data-keys");
    };
    container.addEventListener("keydown", onKey);
    container.addEventListener("mousemove", clearOnPointer);
    return () => {
      container.removeEventListener("keydown", onKey);
      container.removeEventListener("mousemove", clearOnPointer);
    };
  }, [ref, onDismiss, enabled]);
}
