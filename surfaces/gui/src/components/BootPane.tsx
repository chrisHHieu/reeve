// What the window says while Reeve starts, in the conversation's column (the boot screen redesign,
// 2026-10-23). The frame around it (the rail, the top bar, the composer) is the app's own from the
// first frame, so nothing is swapped when the start is done: only this column's content changes.
//
// It says only what the window knows: whether the engine has answered (its health), what the shell's
// supervisor reported (a crash, a restart, a stop, with its reason), which conversation is being
// opened, and how long this start has taken against this computer's own usual (bootTiming.ts). A
// start that fails says why and what can be done, instead of opening the app on a dead connection.

import { useEffect, useState } from "react";
import { GIVE_UP_MS, isSlow, readBootTimes, seconds, usualBootMs } from "../bootTiming";
import { isDesktop, openLogs, platformOS, sidecarLogTail, type SidecarState } from "../desktop";
import { Icon } from "./Icon";

export type BootFailure = {
  /** What went wrong, in the supervisor's own words where it gave some. */
  reason: string;
  /** How long the window waited. */
  waitedMs: number;
};

type Props = {
  /** The engine has answered: the conversation is being opened. */
  opening: boolean;
  /** The conversation being opened, once it is known. */
  title: string | null;
  /** When this start began (Date.now()). */
  startedAt: number;
  /** How long the engine took to answer, once it has. */
  engineMs: number | null;
  /** The shell's supervisor's last word (desktop only). */
  sidecar: SidecarState | null;
  failure: BootFailure | null;
  onRetry: () => void;
  /** The start is over: the column fades out while the conversation arrives under it. */
  leaving?: boolean;
};

export function BootPane(props: Props) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (props.failure || props.leaving) return;
    const t = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(t);
  }, [props.failure, props.leaving]);
  const [times] = useState(readBootTimes);
  const elapsed = now - props.startedAt;

  return (
    <div className={"boot-pane" + (props.leaving ? " is-leaving" : "")} data-testid="boot-pane" aria-hidden={props.leaving || undefined}>
      {props.failure ? (
        <BootFailureCard failure={props.failure} sidecar={props.sidecar} onRetry={props.onRetry} />
      ) : (
        <div className="boot-status">
          {!props.opening && (
            <span className="boot-mark" aria-hidden="true">
              <Icon name="logo" size={40} />
            </span>
          )}
          {!props.opening && <div className="boot-phase">Starting Reeve</div>}
          <ul className="boot-steps" aria-live="polite">
            {props.opening ? (
              <>
                <li className="boot-step is-done">
                  <Icon name="check" size={14} className="boot-tick" />
                  Engine started
                  {props.engineMs !== null && <span className="boot-elapsed">{seconds(props.engineMs)}</span>}
                </li>
                <li className="boot-step is-now" data-testid="boot-step-now">
                  <span className="boot-dot" aria-hidden="true" />
                  {props.title ? `Opening “${props.title}”` : "Opening your last conversation"}
                </li>
              </>
            ) : (
              <>
                <li className="boot-step is-now" data-testid="boot-step-now">
                  <span className="boot-dot" aria-hidden="true" />
                  {engineWords(props.sidecar)}
                  {elapsed >= 3_000 && <span className="boot-elapsed">{seconds(elapsed)}</span>}
                </li>
                <li className="boot-step is-later">
                  <span className="boot-dot is-hollow" aria-hidden="true" />
                  Opening your last conversation
                </li>
              </>
            )}
          </ul>
          {!props.opening && isSlow(elapsed, times) && (
            <p className="boot-note" data-testid="boot-slow">
              {slowWords(times)} It keeps trying for {Math.round(GIVE_UP_MS / 1000)} s, then says what went wrong.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** The engine's step, in the supervisor's words while it is crashing and coming back. */
export function engineWords(sidecar: SidecarState | null): string {
  if (sidecar?.state === "crashed" || sidecar?.state === "restarting") {
    const why = sidecar.reason ? ` (${sidecar.reason})` : "";
    const tries = sidecar.attempt > 0 ? `, try ${sidecar.attempt} of ${sidecar.max_attempts}` : "";
    return `The engine stopped${why}. Starting it again${tries}`;
  }
  return "Starting the engine";
}

/** Why a slow start is called slow: against this computer's usual when it has one. */
export function slowWords(times: readonly number[]): string {
  const usual = usualBootMs(times);
  return usual === null ? "Still starting." : `Slower than usual. It took ${seconds(usual)} the last few times on this computer.`;
}

function BootFailureCard({ failure, sidecar, onRetry }: { failure: BootFailure; sidecar: SidecarState | null; onRetry: () => void }) {
  const [copied, setCopied] = useState(false);
  const stopped = sidecar?.state === "stopped";
  const copy = async () => {
    const tail = await sidecarLogTail();
    const lines = [
      `Reeve did not start (${platformOS() || "browser"})`,
      `What happened: ${failure.reason}`,
      `Waited: ${seconds(failure.waitedMs)}`,
      ...(sidecar ? [`Engine supervisor: ${sidecar.state}, attempt ${sidecar.attempt} of ${sidecar.max_attempts}`] : []),
      ...(tail ? ["", "The end of the engine's log:", tail] : []),
    ];
    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* the clipboard refused: the button simply does not say Copied */
    }
  };
  return (
    <div className="boot-card" role="alert" data-testid="boot-failure">
      <h2 className="boot-card-title">Reeve’s engine didn’t start</h2>
      <p className="boot-card-body">
        {stopped
          ? "It stopped while starting, so Reeve stopped trying."
          : `It didn’t answer within ${Math.round(failure.waitedMs / 1000)} s.`}{" "}
        Your conversations and files are safe on this computer.
      </p>
      <div className="boot-reason" data-testid="boot-reason">
        {failure.reason}
      </div>
      <div className="boot-actions">
        <button type="button" className="boot-btn is-primary" onClick={onRetry} data-testid="boot-retry">
          Try again
        </button>
        {isDesktop() && (
          <button type="button" className="boot-btn" onClick={() => void openLogs()} data-testid="boot-logs">
            Open the log folder
          </button>
        )}
        <button type="button" className="boot-btn" onClick={() => void copy()} data-testid="boot-copy">
          {copied ? "Copied" : "Copy details"}
        </button>
      </div>
    </div>
  );
}
