// The background jobs: the shell work the model started and left running, as a segment of
// the activity row above the composer (`ActivityRow.tsx`) and the list that opens under it.
//
// Running jobs are on top, each with the stop control the tool-only path never gave a person.
// Finished ones are behind "Finished N", closed, with a Clear that forgets them here (the
// engine keeps its own record). With nothing running the fold starts open.
//
// The roster is pushed: the panel draws the array it is given and never asks for
// the list. A row is `JobRow`: the command, the last line it printed, and its log on request.
// A log is watched only while its row is open AND the panel is: closing the panel over an
// open log gives the observation back.

import { memo, useMemo, useState } from "react";
import { Icon } from "../Icon";
import { Disclosure } from "../../ui/Disclosure";
import type { JobRow as JobRowData } from "../../api/jobs";
import { JobRow } from "./JobRow";
import { useJobStop } from "./useJobStop";
import { liveJobs, settledJobs } from "../../jobView";
import type { JobOutputBuffer } from "../../live/jobOutput";

export interface JobsPanelProps {
  sessionId: string;
  /** The session's roster, as the engine last pushed it, less what the person cleared. */
  jobs: readonly JobRowData[];
  /** The session's streamed job output, by job id (`live/jobOutput.ts`). */
  buffers?: Record<string, JobOutputBuffer>;
  /** The panel is open. A closed panel watches no log. */
  active: boolean;
  /** Forget the finished jobs. Absent where the surface keeps them. */
  onClear?: (ids: readonly string[]) => void;
}

export const JobsPanel = memo(function JobsPanel({ sessionId, jobs, buffers, active, onClear }: JobsPanelProps) {
  const [openRow, setOpenRow] = useState<string | null>(null);
  const [foldOpen, setFoldOpen] = useState<boolean | undefined>(undefined);
  const stop = useJobStop(sessionId, jobs);
  const live = useMemo(() => liveJobs(jobs), [jobs]);
  const settled = useMemo(() => settledJobs(jobs), [jobs]);
  const showSettled = foldOpen ?? live.length === 0;

  const row = (job: JobRowData, shown: boolean) => (
    <JobRow
      key={job.id}
      job={job}
      sessionId={sessionId}
      open={shown && openRow === job.id}
      onToggle={() => setOpenRow((current) => (current === job.id ? null : job.id))}
      phase={stop.phaseOf(job.id)}
      onStop={() => stop.press(job.id)}
      buffer={buffers?.[job.id]}
    />
  );

  return (
    <div className="act-roster jobs-dock">
      {live.length > 0 && (
        <>
          {settled.length > 0 && <div className="act-h">Running {live.length}</div>}
          <ul className="act-list">{live.map((job) => row(job, active))}</ul>
        </>
      )}
      {settled.length > 0 && (
        <>
          <div className="act-fold-row">
            <button
              type="button"
              className="act-fold"
              aria-expanded={showSettled}
              data-testid="jobs-finished-toggle"
              onClick={() => setFoldOpen(!showSettled)}
            >
              <Icon name="chevronRight" size={12} className={showSettled ? "open" : undefined} />
              Finished {settled.length}
            </button>
            {onClear && showSettled && (
              <button
                type="button"
                className="act-tiny"
                data-testid="jobs-clear"
                onClick={() => onClear(settled.map((job) => job.id))}
              >
                Clear
              </button>
            )}
          </div>
          <Disclosure open={showSettled}>
            <ul className="act-list">{settled.map((job) => row(job, active && showSettled))}</ul>
          </Disclosure>
        </>
      )}
    </div>
  );
});
