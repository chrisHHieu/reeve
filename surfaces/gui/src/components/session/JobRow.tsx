// One background job's row — the unit the jobs panel of the activity row is made of
// (`JobsDock.tsx`), running or finished. One row for both, for the same reason the subagent
// row is one: a job that rendered two different ways would be two truths about the same
// process.
//
// A row says what its job is doing WITHOUT being opened: the command, a clock that
// ticks while it runs, and under them the last line it printed — which the engine pushes with
// the roster, so it moves whether or not anybody is following the job. A settled row keeps that
// line beside its verdict, because the last thing a build or a test run prints is usually the
// answer the reader came for.
//
// Opening the row opens the LOG, drawn by the same terminal card the transcript uses for a
// command (`cards/TerminalBlock.tsx`). The log is the observed stream, in
// this order:
//   1. it OBSERVES the job, which is what turns the engine's coalesced `job_output` frames on
//      for it — the full text of a job nobody opened is sent to nobody;
//   2. it ANCHORS with the tail route, the backlog of what was printed before it looked.
// What it shows is the anchor followed by whatever the socket delivered beyond it. A hole at
// either seam is said, never closed silently. Closing the row takes the observation back.

import { useEffect, useMemo, useState } from "react";
import { Icon } from "../Icon";
import { Elapsed, formatDuration } from "../../ui/Elapsed";
import { Disclosure } from "../../ui/Disclosure";
import { TerminalBlock } from "../../cards/TerminalBlock";
import {
  getJobOutput,
  observeSessionJob,
  type JobOutput,
  type JobRow as JobRowData,
} from "../../api/jobs";
import {
  jobElapsedMs,
  jobOutcome,
  jobTone,
  lastLine,
  logText,
  type JobTone,
  type KillPhase,
} from "../../jobView";
import type { JobOutputBuffer } from "../../live/jobOutput";
import type { ShellKind } from "../../shellKind";

/** How much backlog one anchor read asks for: the most the route serves. */
const ANCHOR_TAIL = 20_000;

/** The anchored tail of an OPEN row, and the observation that keeps its stream coming.
 *
 *  Observe first, then anchor: frames that start arriving before the anchor lands overlap it
 *  and are cut by position (`outputAfter`), while the other order would leave the text printed
 *  between the two requests in nobody's hands. A finished job is only anchored — it has
 *  nothing left to stream, and the engine says so if asked. When a running job finishes under
 *  an open row the effect runs again and re-anchors, so the row ends on the job's whole tail
 *  whether or not its last frame arrived. */
function useFollowedOutput(sessionId: string, job: JobRowData, open: boolean): JobOutput | null {
  const [anchor, setAnchor] = useState<JobOutput | null>(null);
  const running = job.status === "running";
  useEffect(() => {
    if (!open || !sessionId) return;
    let alive = true;
    const watching = running
      ? observeSessionJob(sessionId, job.id, true).catch(() => null)
      : Promise.resolve(null);
    void watching
      .then(() => getJobOutput(sessionId, job.id, ANCHOR_TAIL))
      .then((result) => {
        if (alive) setAnchor(result);
      });
    return () => {
      alive = false;
      if (running) void observeSessionJob(sessionId, job.id, false).catch(() => null);
    };
  }, [open, sessionId, job.id, running]);
  return anchor;
}

/** The dialect the job was written in, in the terminal card's vocabulary. */
function shellOf(job: JobRowData): ShellKind {
  const kind = (job.shell ?? "").toLowerCase();
  if (kind === "pwsh" || kind === "powershell") return "powershell";
  if (kind === "cmd") return "cmd";
  if (kind === "sh") return "sh";
  return "bash";
}

function JobLog({
  sessionId,
  job,
  buffer,
}: {
  sessionId: string;
  job: JobRowData;
  buffer?: JobOutputBuffer;
}) {
  const anchor = useFollowedOutput(sessionId, job, true);
  const log = useMemo(() => logText(anchor, buffer), [anchor, buffer]);
  const running = job.status === "running";
  return (
    <div className="job-log" data-testid="job-output">
      {log.state === "reading" ? (
        <div className="job-log-note">Reading…</div>
      ) : log.state === "failed" ? (
        <div className="job-log-note" data-tone="danger">
          {log.error}
        </div>
      ) : (
        <>
          {log.cut && (
            <div className="job-log-note" data-testid="job-output-cut">
              The end of the log. Earlier lines are not kept here.
            </div>
          )}
          <TerminalBlock
            command={job.command}
            cwd={job.cwd}
            shell={shellOf(job)}
            output={log.text}
            running={running}
            // A stopped job's code is the kill's, not the command's: the row says "stopped".
            exitCode={!running && job.killed !== true ? (job.exit_code ?? undefined) : undefined}
            labels={{ noOutput: "No output", running: "Running" }}
            // A job's log is read from its END, running or not: the verdict is the last line.
            follow
          />
          {running && log.text === "" && <div className="job-log-note">No output yet.</div>}
        </>
      )}
    </div>
  );
}

const TONE_WORD: Record<JobTone, string> = {
  running: "Running",
  ok: "Finished",
  failed: "Failed",
  stopped: "Stopped",
};

function Mark({ tone }: { tone: JobTone }) {
  // Running is the MOVING glyph in the live accent; a still one means the job is over: a
  // green check when it finished, a red cross when it failed, an amber stop when it was
  // stopped. Green while running would say "done" for the one state this panel exists to
  // make obvious.
  if (tone === "running") {
    return (
      <span className="job-mark" data-tone="running" data-testid="job-running-dot" aria-hidden>
        <Icon name="terminal" size={14} running />
      </span>
    );
  }
  return (
    <span className="job-mark" data-tone={tone} data-testid="job-dot" aria-hidden>
      <Icon name={tone === "ok" ? "check" : tone === "failed" ? "x" : "stop"} size={14} />
    </span>
  );
}

export interface JobRowProps {
  job: JobRowData;
  sessionId: string;
  /** This row's log is showing. One row at a time is open in a list; the list owns which. */
  open: boolean;
  onToggle: () => void;
  phase: KillPhase;
  onStop: () => void;
  /** This job's streamed output, when the session has received any. */
  buffer?: JobOutputBuffer;
}

export function JobRow({ job, sessionId, open, onToggle, phase, onStop, buffer }: JobRowProps) {
  const tone = jobTone(job);
  const live = tone === "running";
  const said = lastLine(job);
  const elapsed = jobElapsedMs(job, Date.now());
  const stopTitle =
    phase === "failed"
      ? "The job could not be stopped"
      : phase === "armed"
        ? "Click again to stop it"
        : `Stop ${job.command}`;
  return (
    <li className="job-item" data-testid="job-item" data-open={open || undefined}>
      <div
        className="job-row"
        data-status={job.status}
        data-tone={tone}
        data-killed={job.killed === true || undefined}
        data-testid="job-row"
      >
        {/* The whole row is the door to the log: a 12px chevron was the only target before. */}
        <button
          type="button"
          className="job-head"
          aria-expanded={open}
          aria-label={`${open ? "Hide" : "Show"} the output of ${job.command}`}
          data-testid="job-expand"
          onClick={onToggle}
        >
          <Mark tone={tone} />
          <span className="job-copy">
            <span className={"job-command" + (live ? " shimmer-text" : "")} title={job.command}>
              {job.command.replace(/\s+/g, " ").trim()}
            </span>
            <span className="job-sub">
              {!live && (
                <span className="job-outcome" data-testid="job-outcome" title={TONE_WORD[tone]}>
                  {jobOutcome(job)}
                </span>
              )}
              {said ? (
                <span className="job-said" data-testid="job-last-line" title={said}>
                  {said}
                </span>
              ) : (
                live && <span className="job-quiet">No output yet</span>
              )}
            </span>
          </span>
          {elapsed !== null &&
            (live ? (
              <Elapsed from={Date.now() - elapsed} className="job-time" />
            ) : (
              <span className="job-time">{formatDuration(elapsed)}</span>
            ))}
          <span className={"job-chevron" + (open ? " open" : "")} aria-hidden>
            <Icon name="chevronDown" size={12} />
          </span>
        </button>
        {live && (
          <button
            type="button"
            className={"job-stop" + (phase === "armed" ? " armed" : "") + (phase === "failed" ? " failed" : "")}
            data-kill-state={phase}
            disabled={phase === "pending"}
            aria-label={stopTitle}
            title={stopTitle}
            data-testid="job-stop"
            onClick={onStop}
          >
            <Icon name="stop" size={11} />
            {phase === "armed" && <span className="job-stop-label">Confirm stop</span>}
          </button>
        )}
      </div>
      {/* The log opens under its row by height, and closes the same way. */}
      <Disclosure open={open} className="fold-fade">
        <JobLog sessionId={sessionId} job={job} buffer={buffer} />
      </Disclosure>
    </li>
  );
}
