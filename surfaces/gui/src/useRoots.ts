import { useCallback, useEffect, useState } from "react";
import { addRoot, getRoots, removeRoot, type RootInfo } from "./api";

// The folders one conversation holds, for every place that shows them: the session drawer's Working
// directories, the folder glance in the settings row, the start panel. Reading is live; a change goes
// through the manager, which applies it to the running engine and stores it. A new `reloadKey` reads
// again (the drawer passes one each time it opens).

const CHANGED = "reeve:roots-changed";
type Answer = { ok: boolean; error?: string; roots?: RootInfo[] };

export function useRoots(sessionId: string, reloadKey?: number) {
  const [roots, setRoots] = useState<RootInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const reload = useCallback(
    () =>
      getRoots(sessionId)
        .then(setRoots)
        .catch(() => setRoots([])),
    [sessionId],
  );
  useEffect(() => {
    reload();
  }, [reload, reloadKey]);

  // The composer chip and the start panel each hold their own copy of this hook, so a change made
  // in one is announced and the others read again.
  useEffect(() => {
    const onChanged = (event: Event) => {
      if ((event as CustomEvent).detail === sessionId) reload();
    };
    window.addEventListener(CHANGED, onChanged);
    return () => window.removeEventListener(CHANGED, onChanged);
  }, [sessionId, reload]);

  /** Run one change; true when the manager took it. */
  const change = async (request: Promise<Answer>): Promise<boolean> => {
    setBusy(true);
    const answer = await request;
    setBusy(false);
    if (answer.ok && answer.roots) {
      setRoots(answer.roots);
      setError("");
      window.dispatchEvent(new CustomEvent(CHANGED, { detail: sessionId }));
      return true;
    }
    setError(answer.error || "could not update directories");
    reload();
    return false;
  };

  /* eslint-disable react-hooks/exhaustive-deps */
  const add = useCallback((path: string, writable: boolean) => change(addRoot(sessionId, path, writable)), [sessionId]);
  // Adding a folder again with the other access changes its access where it is.
  const toggleAccess = useCallback(
    async (root: RootInfo) => {
      if (!root.primary) await change(addRoot(sessionId, root.path, !root.writable));
    },
    [sessionId],
  );
  const remove = useCallback(
    async (path: string) => {
      await change(removeRoot(sessionId, path));
    },
    [sessionId],
  );
  /* eslint-enable react-hooks/exhaustive-deps */

  return { roots, busy, error, reload, addRoot: add, toggleAccess, removeRoot: remove };
}
