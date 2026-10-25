import { useEffect, useRef } from "react";

/** Closes something open — a menu, a popover — on a press outside the element the returned ref is
 *  on, or on Escape. The press is caught on the way down, so an inner handler that stops it cannot
 *  keep the menu open. */
export function useDismiss<T extends HTMLElement = HTMLDivElement>(open: boolean, close: () => void) {
  const ref = useRef<T | null>(null);
  useEffect(() => {
    if (!open) return;
    const press = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) close();
    };
    const key = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      close();
    };
    window.addEventListener("pointerdown", press, true);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("pointerdown", press, true);
      window.removeEventListener("keydown", key);
    };
  }, [open, close]);
  return ref;
}
