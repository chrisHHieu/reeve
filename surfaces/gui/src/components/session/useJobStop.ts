// The two-press Stop of a background job, for both jobs surfaces (the header chip's list and
// the strip above the composer). One hook, because the two used to carry a copy each and a
// Stop that armed differently in two places would be two controls with one label.
//
// First press arms, and disarms by itself; the second press asks the engine. After that the
// button stays PENDING until the roster says the job is over (the pushed `jobs` frame):
// the answer to the request and the frame travel separately, and re-enabling the
// button on the answer could offer a second Stop for a row that still reads `running`.

import { useCallback, useEffect, useRef, useState } from "react";
import { stopSessionJob, type JobRow } from "../../api/jobs";
import type { KillPhase } from "../../jobView";

/** How long the armed Stop waits for its confirming press. */
export const KILL_ARM_MS = 3000;
/** How long a refused Stop says so before the button resets. */
export const KILL_FAILED_MS = 4000;
/** How long an accepted Stop waits for the roster to say the job is over. */
export const KILL_PENDING_MS = 8000;

export interface JobStop {
  /** This job's Stop state. */
  phaseOf: (id: string) => KillPhase;
  /** One press on this job's Stop. */
  press: (id: string) => void;
}

export function useJobStop(sessionId: string, jobs: readonly JobRow[]): JobStop {
  const [kill, setKill] = useState<{ id: string; phase: KillPhase }>({ id: "", phase: "idle" });
  const timer = useRef<number | null>(null);

  const later = useCallback((ms: number) => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setKill({ id: "", phase: "idle" }), ms);
  }, []);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  // A job that stopped being running has no Stop to describe any more.
  useEffect(() => {
    if (kill.id && !jobs.some((job) => job.id === kill.id && job.status === "running")) {
      setKill({ id: "", phase: "idle" });
    }
  }, [jobs, kill.id]);

  // Read through a ref, not inside a state updater: the request is a side effect, and React
  // may run an updater twice (the copy this replaced asked the engine from inside one).
  const current = useRef(kill);
  current.current = kill;
  const press = useCallback(
    (id: string) => {
      if (current.current.id !== id || current.current.phase !== "armed") {
        setKill({ id, phase: "armed" });
        later(KILL_ARM_MS);
        return;
      }
      setKill({ id, phase: "pending" });
      // The roster frame is what ends the wait. Should it never come (the socket dropped),
      // the button comes back rather than staying dead.
      later(KILL_PENDING_MS);
      void stopSessionJob(sessionId, id).then((result) => {
        if (result.ok) return;
        setKill({ id, phase: "failed" });
        later(KILL_FAILED_MS);
      });
    },
    [later, sessionId],
  );

  const phaseOf = useCallback(
    (id: string): KillPhase => (kill.id === id ? kill.phase : "idle"),
    [kill],
  );

  return { phaseOf, press };
}
