// Leaving the desktop app without losing work.
//
// The shell closes to the tray so automations and work in progress go on, which used to happen
// silently: a person who pressed X believed Reeve had quit. Now the first close asks once
// ("Keep running in the tray?"), the answer is remembered (Settings ▸ General), and Quit asks
// before it would stop something that is running. The SPA is what knows what runs (its session
// list), so it reports that to the shell, which shows it in the tray and asks this component
// when a question is owed. In the browser build all of this is inert.

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Dialog } from "../ui/Dialog";
import type { SessionInfo } from "../types";
import {
  ackClose,
  closeToTray,
  isDesktop,
  onShellEvent,
  quitApp,
  setCloseBehavior,
  setQuitWhenDone,
  setTrayStatus,
  type CloseBehavior,
} from "../desktop";

export type RunningWork = {
  running: SessionInfo[];
  needsYou: number;
  /** The first of the running conversations, by name, for the tray's line. */
  titles: string;
};

/** What would stop if Reeve quit now: the conversations with a turn in flight, and how many
 *  wait on the person. */
export function runningWork(sessions: SessionInfo[]): RunningWork {
  const running = sessions.filter((s) => s.liveness === "working");
  const needsYou = sessions.filter((s) => (s.attention ?? 0) > 0).length;
  const titles = running
    .slice(0, 2)
    .map((s) => s.title || "Untitled")
    .join(" · ");
  return { running, needsYou, titles };
}

export function DesktopShellBridge({ sessions }: { sessions: SessionInfo[] }) {
  const work = useMemo(() => runningWork(sessions), [sessions]);
  const latest = useRef(work);
  latest.current = work;
  const [asking, setAsking] = useState<null | "close" | "quit">(null);

  const key = `${work.running.length}|${work.needsYou}|${work.titles}`;
  useEffect(() => {
    if (isDesktop()) void setTrayStatus(work.running.length, work.needsYou, work.titles);
    // `key` carries everything the shell is told.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    const quitOrAsk = () => {
      if (latest.current.running.length === 0) void quitApp();
      else setAsking("quit");
    };
    const offClose = onShellEvent<{ behavior: CloseBehavior }>("reeve:close-requested", (payload) => {
      void ackClose();
      if (payload?.behavior === "quit") quitOrAsk();
      else setAsking("close");
    });
    const offQuit = onShellEvent("reeve:quit-requested", () => setAsking("quit"));
    return () => {
      offClose();
      offQuit();
    };
  }, []);

  if (asking === "close")
    return (
      <FirstCloseDialog
        onTray={async () => {
          setAsking(null);
          await setCloseBehavior("tray");
          await closeToTray();
        }}
        onQuit={async () => {
          await setCloseBehavior("quit");
          if (latest.current.running.length === 0) await quitApp();
          else setAsking("quit");
        }}
        onCancel={() => setAsking(null)}
      />
    );
  if (asking === "quit")
    return (
      <QuitDialog
        work={work}
        onCancel={() => setAsking(null)}
        onQuitWhenDone={async () => {
          setAsking(null);
          await setQuitWhenDone(true);
          await closeToTray();
        }}
        onQuitNow={() => void quitApp()}
      />
    );
  return null;
}

/** The first time the window is closed: where Reeve goes, asked once. */
export function FirstCloseDialog({
  onTray,
  onQuit,
  onCancel,
}: {
  onTray: () => void;
  onQuit: () => void;
  onCancel: () => void;
}) {
  const heading = useId();
  return (
    <Dialog onClose={onCancel} labelledBy={heading} testId="first-close-dialog">
      <div className="ui-confirm">
        <h2 className="ui-confirm-title" id={heading}>
          Keep Reeve running in the tray?
        </h2>
        <p className="ui-confirm-body">
          Closing the window then hides it: work in progress and your automations go on, and the tray icon
          opens Reeve again or quits it. You can change this in Settings ▸ General.
        </p>
        <div className="ui-confirm-actions">
          <button type="button" className="ui-confirm-btn" data-testid="first-close-quit" onClick={onQuit}>
            Quit Reeve
          </button>
          <button
            type="button"
            className="ui-confirm-btn primary"
            data-autofocus
            data-testid="first-close-tray"
            onClick={onTray}
          >
            Keep running in the tray
          </button>
        </div>
      </div>
    </Dialog>
  );
}

/** Quitting while work is running: what would stop, and three answers. Cancel holds focus, so
 *  an Enter on a dialog that just appeared keeps what is there. */
export function QuitDialog({
  work,
  onCancel,
  onQuitWhenDone,
  onQuitNow,
}: {
  work: RunningWork;
  onCancel: () => void;
  onQuitWhenDone: () => void;
  onQuitNow: () => void;
}) {
  const heading = useId();
  const count = work.running.length;
  return (
    <Dialog onClose={onCancel} labelledBy={heading} testId="quit-dialog">
      <div className="ui-confirm">
        <h2 className="ui-confirm-title" id={heading}>
          Quit Reeve while {count} task{count === 1 ? " is" : "s are"} running?
        </h2>
        <ul className="ui-confirm-list" data-testid="quit-dialog-list">
          {work.running.slice(0, 5).map((s) => (
            <li key={s.session_id}>
              <span>{s.title || "Untitled"}</span>
              {(s.attention ?? 0) > 0 && <span className="fact">needs you</span>}
            </li>
          ))}
          {count > 5 && <li>{count - 5} more</li>}
        </ul>
        <p className="ui-confirm-body">
          They stop now and pick up where they left off the next time you open Reeve. A command that is running
          is stopped, and the agent is told. Automations do not run while Reeve is closed.
        </p>
        <div className="ui-confirm-actions">
          <button type="button" className="ui-confirm-btn" data-autofocus onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="ui-confirm-btn" data-testid="quit-when-done" onClick={onQuitWhenDone}>
            Quit when they finish
          </button>
          <button type="button" className="ui-confirm-btn danger" data-testid="quit-now" onClick={onQuitNow}>
            Quit now
          </button>
        </div>
      </div>
    </Dialog>
  );
}
