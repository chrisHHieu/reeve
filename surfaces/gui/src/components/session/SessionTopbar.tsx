// The session top bar: what this conversation IS (its name), what it is doing right now, and
// three controls (redesign of 2026-10-12, DESIGN.md "The session top bar").
//
// One 48px row. At the left: the way back to an automation when this is one of its runs, the
// session's name (a click renames it), where its files live when that is another machine,
// and its state in a few words: Working with the turn's clock, Needs you, Sleeps until. An
// idle session says nothing: there is no green "all is well" mark, silence is the mark. At
// the right, always the same three: the session's own menu, its connections, the side panel.
//
// What the session has running (subagents, jobs) is not here: that is said once, in the
// activity row above the composer. The bar was 76px with only the name in it, and wore the
// two run-report chips beside its controls.

import { useEffect, useRef, useState, type RefObject } from "react";
import { Icon, type IconName } from "../Icon";
import { StatusPopover } from "../StatusPopover";
import { AnchoredPopover } from "../primitives/AnchoredPopover";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { WhereCrumb, type WhereFacts } from "./WhereCrumb";
import { Elapsed } from "../../ui/Elapsed";

/**
 * True when a raw session title is really a filesystem path. The server can hand back the
 * workspace path — quoted, and with Windows separators — for a session that never got a
 * title of its own (a fresh temporary folder especially). That string is not a name.
 */
export function looksLikePath(raw: string): boolean {
  const value = raw
    .trim()
    .replace(/^["'“”‘’]+/, "")
    .replace(/["'“”‘’]+$/, "");
  if (!value) return false;
  return /^[a-zA-Z]:[\\/]/.test(value) || value.startsWith("/") || value.includes("\\");
}

/** The name the topbar shows for a session: a path-shaped raw title never renders as one
 * (owner ask 2026-09-18 — the quoted temp-folder path read as noise). Falls back to the
 * bound folder's display name, then to the standard new-session wording. */
export function sessionDisplayTitle(raw: string, workspaceName: string | null): string {
  return looksLikePath(raw) ? workspaceName || "New session" : raw;
}

/** What the session is doing right now. Null: nothing to say. */
export type SessionState =
  /** A turn is running; `since` is when it began (ms), when the transcript knows. */
  | { kind: "working"; since?: number | null }
  /** A `/compact` runs between turns. */
  | { kind: "compacting"; since?: number | null }
  /** It waits on the person: an approval, a question, a plan, a subagent's ask. */
  | { kind: "needs" }
  /** It scheduled its own wake-up. */
  | { kind: "sleeping"; until?: string | null };

/** The session's own actions: the sidebar row's menu, reachable with the sidebar collapsed. */
export interface SessionActions {
  pinned: boolean;
  archived: boolean;
  onRename: (title: string) => void;
  onTogglePin: () => void;
  onToggleArchive: () => void;
  onFork?: () => void;
  onExport?: () => void;
  onDelete: () => void;
}

function StateWords({ state }: { state: SessionState }) {
  // A turn whose first stamp has not arrived counts from when the bar first saw it running.
  const seen = useRef(0);
  if (state.kind === "working" || state.kind === "compacting") {
    if (!seen.current) seen.current = Date.now();
    return (
      <span className="topbar-state" data-state={state.kind} data-testid="topbar-state">
        <span className="turn-dots" aria-hidden>
          <i />
          <i />
        </span>
        {state.kind === "working" ? "Working" : "Compacting"}
        <Elapsed from={state.since ?? seen.current} className="topbar-state-time" />
      </span>
    );
  }
  seen.current = 0;
  if (state.kind === "needs") {
    return (
      <span className="topbar-state need" data-state="needs" data-testid="topbar-state">
        <span className="topbar-state-dot" aria-hidden />
        Needs you
      </span>
    );
  }
  const until = state.until ? new Date(state.until) : null;
  return (
    <span className="topbar-state" data-state="sleeping" data-testid="topbar-state">
      <Icon name="clock" size={13} />
      {until && !Number.isNaN(until.getTime())
        ? `Sleeps until ${until.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
        : "Sleeping"}
    </span>
  );
}

function SessionMenu({ title, actions, onRename }: { title: string; actions: SessionActions; onRename: () => void }) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const item = (key: string, icon: IconName, label: string, run: () => void) => (
    <button
      type="button"
      className="topbar-menu-item"
      role="menuitem"
      data-testid={`session-menu-${key}`}
      onClick={() => {
        setOpen(false);
        run();
      }}
    >
      <Icon name={icon} size={13} />
      <span>{label}</span>
    </button>
  );
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={"topbar-icon-btn" + (open ? " on" : "")}
        aria-label="Session actions"
        title="Session actions"
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid="session-menu"
        onMouseDown={(e) => e.stopPropagation()}
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name="moreHorizontal" size={16} />
      </button>
      <AnchoredPopover
        anchorRef={trigger}
        open={open}
        onClose={() => setOpen(false)}
        width={184}
        label="Session actions"
        testId="session-menu-pop"
        className="topbar-menu"
      >
        <div role="menu">
          {item("rename", "pencil", "Rename", onRename)}
          {item("pin", "pin", actions.pinned ? "Unpin" : "Pin", actions.onTogglePin)}
          {actions.onFork && item("fork", "gitFork", "Fork", actions.onFork)}
          {actions.onExport && item("export", "save", "Export", actions.onExport)}
          {item("archive", "archive", actions.archived ? "Unarchive" : "Archive", actions.onToggleArchive)}
          <div className="topbar-menu-sep" />
          <button
            type="button"
            className="topbar-menu-item danger"
            role="menuitem"
            data-testid="session-menu-delete"
            onClick={() => {
              setOpen(false);
              setConfirming(true);
            }}
          >
            <Icon name="trash" size={13} />
            <span>Delete…</span>
          </button>
        </div>
      </AnchoredPopover>
      {confirming && (
        <ConfirmDialog
          title={`Delete “${title}”?`}
          body="The conversation and its history are removed. This cannot be undone. To put it away and keep it, archive it instead."
          confirmLabel="Delete"
          onConfirm={() => {
            actions.onDelete();
            setConfirming(false);
          }}
          onCancel={() => setConfirming(false)}
        />
      )}
    </>
  );
}

export function SessionTopbar({
  railHidden,
  activeTitle,
  hasHistory,
  workspaceName,
  where = null,
  sessionKey = "",
  started = false,
  sentHere = false,
  onTogglePanel,
  sidecarConnected,
  sidecarModel,
  onOpenConnectors,
  onOpenSkills,
  state = null,
  run = null,
  actions = null,
  starting = null,
}: {
  railHidden: boolean;
  /** The session record's raw title — path-shaped values are never rendered (see
   * sessionDisplayTitle). */
  activeTitle: string;
  hasHistory: boolean;
  /** The bound folder's display name; null for Chat and temporary folders. */
  workspaceName: string | null;
  /** Where the conversation works (folder and branch, or the remote machine): a crumb before
   *  the name once the first message is in (session/WhereCrumb.tsx). */
  where?: WhereFacts | null;
  /** The conversation on screen, so the crumb arrives with a motion only when its first
   *  message went in here. */
  sessionKey?: string;
  /** The person has sent a message in this conversation. */
  started?: boolean;
  /** …and sent it from this window, now: the crumb then arrives with a motion. */
  sentHere?: boolean;
  onTogglePanel: () => void;
  /** Keeps the bar draggable on the frameless build without making the buttons drag. */
  /** Connections popover inputs: the WS state + active model (its header line) and where
   *  its footer/row links land. */
  sidecarConnected: boolean;
  sidecarModel: string;
  onOpenConnectors: () => void;
  onOpenSkills: () => void;
  /** What the session is doing right now; null when it is idle (the bar says nothing). */
  state?: SessionState | null;
  /** This conversation is a run of an automation: its name, and the way back to it. */
  run?: { title: string; onBack: () => void } | null;
  /** The session's own actions. Null while there is no session record yet (a draft). */
  actions?: SessionActions | null;
  /** Reeve is starting: the name of the conversation being opened once it is known, and none of
   *  the bar's live readouts, which have nothing true to say before the engine answers. */
  starting?: { title: string | null } | null;
}) {
  const title = sessionDisplayTitle(activeTitle, workspaceName);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const field = useRef<HTMLInputElement>(null);
  // Another session came on screen: a half-typed name belongs to the one that left.
  useEffect(() => setEditing(false), [activeTitle]);
  useEffect(() => {
    if (editing) field.current?.select();
  }, [editing]);
  const startRename = () => {
    setDraft(title);
    setEditing(true);
  };
  const commit = () => {
    const next = draft.trim();
    if (next && next !== title) actions?.onRename(next);
    setEditing(false);
  };
  const titleClass = "main-title-text" + (hasHistory ? "" : " title-ghost");

  return (
    // The window's controls float over the bar's right end when nothing else is under them; the
    // room it keeps for them is the stylesheet's (`--bar-reserve`), which knows where the panel is.
    <div className="main-topbar">
      <div className="main-topbar-row">
        <div className="main-title">
          {/* An automation's run names its automation before its own name, and that name is
              the way back to the automation's runs. It was a banner of its own in the
              conversation's column. */}
          {run && (
            <>
              <button
                type="button"
                className="topbar-crumb"
                data-testid="run-crumb"
                title={run.title ? `Back to “${run.title}”` : "Back to the automation"}
                onClick={run.onBack}
              >
                <Icon name="arrowLeft" size={13} />
                <span>{run.title || "Automation"}</span>
              </button>
              <span className="topbar-sep" aria-hidden>
                /
              </span>
            </>
          )}
          {starting ? (
            starting.title ? (
              <span className="main-title-text" title={sessionDisplayTitle(starting.title, workspaceName)} data-testid="topbar-title">
                {sessionDisplayTitle(starting.title, workspaceName)}
              </span>
            ) : (
              <span className="topbar-title-pending" aria-hidden="true" data-testid="topbar-title-pending" />
            )
          ) : (
          <>
          {where && <WhereCrumb where={where} sessionKey={sessionKey} started={started} sentHere={sentHere} />}
          {editing ? (
            <input
              ref={field}
              className="topbar-title-field"
              aria-label="Session name"
              data-testid="topbar-title-field"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commit}
              onKeyDown={(e) => {
                // The keys end here: Escape in this field cancels the rename and nothing else
                // (it stops a running turn when it reaches the composer).
                if (e.key === "Enter" || e.key === "Escape") e.stopPropagation();
                if (e.key === "Enter") commit();
                if (e.key === "Escape") setEditing(false);
              }}
            />
          ) : actions ? (
            <button type="button" className={titleClass + " is-button"} title="Rename" data-testid="topbar-title" onClick={startRename}>
              {title}
            </button>
          ) : (
            <span className={titleClass} title={title} data-testid="topbar-title">
              {title}
            </span>
          )}
          {state && <StateWords state={state} />}
          </>
          )}
        </div>
        <div className="main-topbar-actions">
          {/* No run report here: what the session has running (subagents, jobs) is said once,
              in the activity row above the composer. It was said here a second time. */}
          {actions && !starting && <SessionMenu title={title} actions={actions} onRename={startRename} />}
          {/* Connections: what the agent can reach, and whether it can. A pip only when
              something needs a look. */}
          {!starting && (
            <StatusPopover
              connected={sidecarConnected}
              model={sidecarModel}
              onOpenConnectors={onOpenConnectors}
              onOpenSkills={onOpenSkills}
            />
          )}
          {/* The panel toggle is the ONE session-panel entry. */}
          <button
            className="topbar-icon-btn"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={onTogglePanel}
            aria-label={railHidden ? "Show side panel" : "Hide side panel"}
            title={railHidden ? "Show side panel" : "Hide side panel"}
          >
            <Icon name="sidebarRight" size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

/** Kept so a caller can type a ref it owns for the topbar (window-drag hit testing). */
export type TopbarRef = RefObject<HTMLDivElement>;
