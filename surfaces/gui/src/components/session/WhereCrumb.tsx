// Where a conversation works, once it has begun: a crumb before its name in the top bar
// (owner, 2026-10-13). Before the first message the folder, branch and machine are picked in
// the strip under the composer; after it they were stated there again, in a row at the foot of
// the page for something nobody chooses any more, and a remote machine was said a second time
// in a chip beside the name. Now it is said once, where the name is read: the folder and its
// branch, or the remote machine by its name. Pressing it opens the facts in full, with the one
// thing still changed from here (the branch).

import { useLayoutEffect, useRef, useState } from "react";
import { AnchoredPopover } from "../primitives/AnchoredPopover";
import { BranchPicker } from "../BranchPicker";
import { Icon } from "../Icon";
import { openFilesTab } from "../Markdown";
import {
  formatRemoteEnvironmentDestination,
  type RemoteEnvironmentSummary,
} from "../../api/remote-environments";

export interface WhereFacts {
  /** The local folder's display name; null for a chat or a temporary folder. */
  folderName: string | null;
  folderPath: string | null;
  branch: string | null;
  /** The folder is a linked git worktree of its own (the session list's fact). */
  worktree?: boolean;
  /** The bound remote machine; null when the conversation runs on this computer. */
  environment: RemoteEnvironmentSummary | null;
  remoteWorkspace: string | null;
  connected: boolean;
  /** Why the machine refused the conversation (the server's own words); "" when it did not. */
  problem: string;
  onBranchSwitched?: (branch: string) => void;
  onRetry?: () => void;
}

/** Only an absolute POSIX path is a folder on the remote machine: anything else is a fact
 *  about this computer (formatRemoteEnvironmentReadout keeps the same rule). */
const remoteFolder = (path: string | null) => {
  const trimmed = (path ?? "").trim();
  return trimmed.startsWith("/") ? trimmed : "";
};

export function WhereCrumb({
  where,
  sessionKey,
  started,
  sentHere = false,
}: {
  where: WhereFacts;
  sessionKey: string;
  started: boolean;
  /** The person sent this conversation's message from here: history that arrived after the
   *  conversation was opened is not a first message. */
  sentHere?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<"facts" | "branches">("facts");
  const trigger = useRef<HTMLButtonElement>(null);
  // It arrives with a motion only when the first message went in THIS conversation; one opened
  // with its history already there shows it at once.
  // Read in a layout effect, before the frame is painted: a render can run twice.
  const [arrive, setArrive] = useState(false);
  const seen = useRef({ key: sessionKey, started });
  useLayoutEffect(() => {
    const before = seen.current;
    setArrive(before.key === sessionKey && !before.started && started && sentHere);
    seen.current = { key: sessionKey, started };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- read at the moment it starts
  }, [sessionKey, started]);

  const { environment, folderName, branch } = where;
  if (!started || (!environment && !folderName)) return null;

  const close = () => {
    setOpen(false);
    setView("facts");
  };
  const folder = remoteFolder(where.remoteWorkspace);
  const trouble = !!environment && !!where.problem && !where.connected;
  const name = environment ? environment.name || environment.host : folderName!;
  const label = environment
    ? `Where this conversation works: ${name}${trouble ? ", which Reeve cannot start on" : ""}`
    : `Where this conversation works: ${name}${branch ? `, on ${branch}` : ""}`;

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="where-crumb"
        data-testid="where-crumb"
        data-arrive={arrive || undefined}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={label}
        title={label}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <Icon name={environment ? "server" : "folder"} size={13} />
        <span className="where-crumb-name">{name}</span>
        {!environment && branch && (
          <span className="where-crumb-branch" data-testid="where-crumb-branch">
            <Icon name="branch" size={12} />
            <span>{branch}</span>
          </span>
        )}
        {!environment && where.worktree && (
          <span className="where-crumb-tag" data-testid="where-crumb-worktree" title="This folder is a git worktree of its own">
            worktree
          </span>
        )}
        {trouble && <span className="where-crumb-dot" aria-hidden />}
      </button>
      <span className="topbar-sep" data-arrive={arrive || undefined} aria-hidden>
        /
      </span>
      <AnchoredPopover
        anchorRef={trigger}
        open={open}
        onClose={close}
        width={view === "branches" ? 380 : 360}
        align="start"
        label="Where this conversation works"
        testId="where-pop"
        className="topbar-menu where-pop pop-in-down"
      >
        {view === "branches" && where.folderPath ? (
          <BranchPicker
            path={where.folderPath}
            onSwitched={(next) => where.onBranchSwitched?.(next)}
            onClose={close}
          />
        ) : (
          <>
            <div className="topbar-menu-label">Where this conversation works</div>
            {environment ? (
              <>
                <div className="where-row" data-testid="where-machine">
                  <span className="where-row-mark">
                    <Icon name="server" size={14} />
                  </span>
                  <span className="where-row-text">
                    <span className="where-row-name">{name}</span>
                    <span className="where-row-sub mono">{formatRemoteEnvironmentDestination(environment)}</span>
                  </span>
                  <span className="where-state" data-tone={trouble ? "warn" : where.connected ? "ok" : undefined}>
                    <i aria-hidden />
                    {trouble ? "Not started" : where.connected ? "Connected" : "Connecting"}
                  </span>
                </div>
                {folder && (
                  <div className="where-row" data-testid="where-folder">
                    <span className="where-row-mark">
                      <Icon name="folder" size={14} />
                    </span>
                    <span className="where-row-text">
                      <span className="where-row-name mono">{folder}</span>
                      <span className="where-row-sub">On that machine</span>
                    </span>
                  </div>
                )}
                {trouble && (
                  <>
                    <div className="topbar-menu-sep" />
                    <div className="where-row where-problem" data-testid="where-problem">
                      <span className="where-row-mark warn">
                        <Icon name="warning" size={14} />
                      </span>
                      <span className="where-row-text">
                        <span className="where-row-why">{where.problem}</span>
                      </span>
                      {where.onRetry && (
                        <button
                          type="button"
                          className="where-act"
                          data-testid="where-retry"
                          onClick={() => {
                            close();
                            where.onRetry?.();
                          }}
                        >
                          Try again
                        </button>
                      )}
                    </div>
                  </>
                )}
              </>
            ) : (
              <>
                <div className="where-row" data-testid="where-folder">
                  <span className="where-row-mark">
                    <Icon name="folder" size={14} />
                  </span>
                  <span className="where-row-text">
                    <span className="where-row-name">{folderName}</span>
                    {where.folderPath && <span className="where-row-sub mono">{where.folderPath}</span>}
                  </span>
                  <button
                    type="button"
                    className="where-act"
                    data-testid="where-show-files"
                    onClick={() => {
                      close();
                      openFilesTab();
                    }}
                  >
                    Show in Files
                  </button>
                </div>
                {branch && (
                  <div className="where-row" data-testid="where-branch">
                    <span className="where-row-mark">
                      <Icon name="branch" size={14} />
                    </span>
                    <span className="where-row-text">
                      <span className="where-row-name">{branch}</span>
                      <span className="where-row-sub">Checked out in the folder</span>
                    </span>
                    {where.onBranchSwitched && where.folderPath && (
                      <button
                        type="button"
                        className="where-act"
                        data-testid="where-switch-branch"
                        onClick={() => setView("branches")}
                      >
                        Switch…
                      </button>
                    )}
                  </div>
                )}
                <div className="where-row" data-testid="where-machine">
                  <span className="where-row-mark">
                    <Icon name="laptop" size={14} />
                  </span>
                  <span className="where-row-text">
                    <span className="where-row-name">This computer</span>
                  </span>
                </div>
              </>
            )}
          </>
        )}
      </AnchoredPopover>
    </>
  );
}
