import { useCallback, useState } from "react";

/** A yes/no the person sets that this device keeps ("1" or "0" under `key`; anything else, or
 *  storage that cannot be read, is `fallback`). `set` keeps what it sets; `setForNow` changes what
 *  shows without keeping it, for a link that opens a pane or a preview that folds one. */
export function useStoredFlag(key: string, fallback: boolean) {
  const [value, setForNow] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === "1" ? true : raw === "0" ? false : fallback;
    } catch {
      return fallback;
    }
  });
  const keep = useCallback(
    (next: boolean) => {
      try {
        localStorage.setItem(key, next ? "1" : "0");
      } catch {
        /* shown, not kept */
      }
    },
    [key],
  );
  const set = useCallback(
    (next: boolean) => {
      setForNow(next);
      keep(next);
    },
    [keep],
  );
  // A functional update, so a key handler registered once still flips the current value.
  const toggle = useCallback(
    () =>
      setForNow((now) => {
        keep(!now);
        return !now;
      }),
    [keep],
  );
  return { value, set, setForNow, toggle };
}
