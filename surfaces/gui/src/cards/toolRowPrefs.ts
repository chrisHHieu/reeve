// Whether a tool call's row opens when the call finishes (Settings ▸ General ▸ Open tools when
// they finish). Off, the default: a call is one folded line, while it runs and after, so no
// row opens and folds back again. On: a call still running after half a second opens to
// stream its output, and a finished call opens to show what it did. Either way a failure
// opens and a row the person opened or closed stays as they left it.
// Per device, like the map background beside it (`visual/mapPrefs.ts`).

import { useEffect, useState } from "react";

const KEY = "reeve:open-finished-tools";
const EVENT = "reeve:open-finished-tools-changed";

export function getOpenFinishedTools(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function setOpenFinishedTools(value: boolean) {
  try {
    if (value) localStorage.setItem(KEY, "1");
    else localStorage.removeItem(KEY);
  } catch {
    /* private mode: still applies until the page reloads */
  }
  window.dispatchEvent(new CustomEvent(EVENT));
}

export function useOpenFinishedTools(): [boolean, (value: boolean) => void] {
  const [value, setValue] = useState<boolean>(getOpenFinishedTools);
  useEffect(() => {
    const sync = () => setValue(getOpenFinishedTools());
    window.addEventListener(EVENT, sync);
    return () => window.removeEventListener(EVENT, sync);
  }, []);
  return [value, setOpenFinishedTools];
}
