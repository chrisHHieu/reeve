import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AUTOMATIONS_CHANGED, getAutomations, getSettings, type Automation, type WorkspaceWorktrees } from "../api";
import { childDescription, childSessionOnPath, type ChildSession } from "../childSessions";
import { NEEDS } from "../needs";
import { baseName } from "../paths";
import { clockTime, sessionAge } from "../sessionAge";
import { shortcut } from "../shortcut";
import type { SessionInfo } from "../types";
import { ConfirmDialog, Disclosure } from "../ui";
import { Icon } from "./Icon";
import { useLiveLines } from "./queue/liveLines";
import { QueueFacts } from "./queue/QueueFacts";
import { LIVE_GROUPS, buildQueue, groupOf, isLive, projectOf, queueByProject, type QueueGroup, type QueueProject } from "./queue/queueGroups";
import { ConnectorDot, LiveDot, RemoteIcon } from "./queue/RowMarks";
import { placeRowMenu, RowMenu, type RowMenuPlace } from "./queue/RowMenu";
import { SearchModal } from "./SearchModal";

// The app's frame (owner, 2026-10-13, "B frame, A inside"): an icon rail that is always there,
// and beside it the work queue: the sessions grouped by what each needs from the person (waiting
// on them, running, to review, done today, earlier) instead of a list of chats by folder or by
// time ("to review" included). Collapsing (⌘B) leaves the rail.

export interface SidebarProps {
  sessions: SessionInfo[];
  /** `/workspace`: the current folder's git worktrees. Without them the band is not drawn. */
  worktreesOpen?: boolean;
  worktrees?: WorkspaceWorktrees | null;
  worktreesLoading?: boolean;
  onToggleWorktrees?: () => void;
  onOpenWorktree?: (path: string) => void;
  activeSession: string;
  onNewSession: () => void;
  onSelectSession: (id: string, workspace: string) => void;
  onRenameSession: (id: string, title: string) => void;
  onDeleteSession: (id: string) => void;
  onArchiveSession: (id: string, archived: boolean) => void;
  onTogglePin: (id: string, pinned: boolean) => void;
  /** Branch the conversation into a new session that starts with its history. */
  onForkSession?: (id: string) => void;
  /** Settings. */
  onManage: () => void;
  onOpenScheduled: () => void;
  /** One automation's page, from a running run's row in the queue. */
  onOpenAutomation?: (id: string) => void;
  onOpenIntegrations: () => void;
  /** Ops: the harness's numbers across every session. */
  onOpenOps: () => void;
  scheduledActive: boolean;
  integrationsActive: boolean;
  opsActive: boolean;
  /** ⌘B: folded, the rail is all there is. */
  collapsed?: boolean;
  onCollapse?: () => void;
  /** Reeve is starting: the rail is drawn where it will be, and nothing in it works yet. */
  starting?: boolean;
  /** The child sessions the app knows of, the one on screen when it is a child, and
   *  how to open one. The child on the path to the one on screen is drawn under its parent, and
   *  gone once the path leaves it; a child is never a row of the list. */
  childSessions?: Record<string, ChildSession>;
  activeOnPath?: string;
  onOpenChild?: (sessionId: string) => void;
  /** Whether the list has been read: a skeleton until it has, and a way to try again when the
   *  first read failed. */
  sessionsState?: "loading" | "ready" | "error";
  onRetrySessions?: () => void;
}

const GROUP_LABEL: Record<QueueGroup, string> = {
  needs: "Needs you",
  running: "Running",
  review: "To review",
  today: "Done today",
  earlier: "Earlier",
};

/** The projects the person opened or closed under the queue, per viewer: `{ key: open }`. */
const OPEN_PROJECTS_KEY = "reeve:queue-projects:v1";

/** Active on today's local calendar day. */
function activeToday(iso: string): boolean {
  const then = iso ? new Date(iso) : null;
  if (!then || Number.isNaN(then.getTime())) return false;
  return then.toDateString() === new Date().toDateString();
}

export function Sidebar(props: SidebarProps) {
  const [searchModalOpen, setSearchModalOpen] = useState(false);
  // Folding the queue swaps it out at once, and what comes in fades in (opacity only).
  const [swapped, setSwapped] = useState(false);
  const firstCollapse = useRef(true);
  useEffect(() => {
    if (firstCollapse.current) firstCollapse.current = false;
    else setSwapped(true);
  }, [props.collapsed]);
  // The automations, for the rail button's count of runs not looked at yet and the queue's
  // running runs: read every 15s, and at once when a change is announced. Not while Reeve starts:
  // the rail is drawn then, and a read would fail.
  const [automations, setAutomations] = useState<Automation[]>([]);
  useEffect(() => {
    if (props.starting) return;
    const read = () =>
      getAutomations()
        .then(setAutomations)
        .catch(() => {});
    read();
    const timer = setInterval(read, 15_000);
    window.addEventListener(AUTOMATIONS_CHANGED, read);
    return () => {
      clearInterval(timer);
      window.removeEventListener(AUTOMATIONS_CHANGED, read);
    };
  }, [props.starting]);
  // The row being renamed, and the name as typed.
  const [renaming, setRenaming] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  // Delete asks in a dialog before it acts. Archive is the primary way to put a conversation
  // away: one click, reversible.
  const [toDelete, setToDelete] = useState<{ id: string; title: string } | null>(null);
  // The open ⋮ menu, one at a time.
  const [rowMenu, setRowMenu] = useState<(RowMenuPlace & { id: string }) | null>(null);
  const closeRowMenu = useCallback(() => setRowMenu(null), []);
  // J / K walk the queue and Enter opens the row (the layout's final mockup), as J / K walk the
  // files in Review. Only while the queue is shown and nothing else claims the key: not while
  // typing, not with a modifier, not inside a dialog, and not when a pane handled it first.
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (props.collapsed) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
      if (e.key !== "j" && e.key !== "k") return;
      const target = e.target instanceof Element ? e.target : null;
      if (target?.closest("input, textarea, select, [contenteditable='true'], [role=dialog], .right-rail")) return;
      if (document.querySelector("[aria-modal=true]")) return;
      const rows = [...(listRef.current?.querySelectorAll<HTMLButtonElement>(".q-row-main") ?? [])];
      if (rows.length === 0) return;
      const from = rows.indexOf(document.activeElement as HTMLButtonElement);
      const at = from >= 0 ? from : rows.findIndex((row) => row.getAttribute("aria-current") === "page");
      const step = e.key === "j" ? 1 : -1;
      const next = at < 0 ? (step > 0 ? 0 : rows.length - 1) : Math.min(rows.length - 1, Math.max(0, at + step));
      e.preventDefault();
      rows[next].focus();
      rows[next].scrollIntoView?.({ block: "nearest" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props.collapsed]);
  // ⌘K finds a session, folded or not; never inside the terminal, where Ctrl K is the shell's.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey || e.key.toLowerCase() !== "k") return;
      if (e.target instanceof Element && e.target.closest(".xterm")) return;
      e.preventDefault();
      setSearchModalOpen(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const [showArchived, setShowArchived] = useState(false);
  // An opened project shows `peek` sessions first (Settings, default 5), then "Show N more".
  const [peek, setPeek] = useState(5);
  useEffect(() => {
    if (props.starting) return;
    getSettings()
      .then((settings) => settings.sessions_peek && setPeek(settings.sessions_peek))
      .catch(() => {});
  }, [props.starting]);
  const queue = useMemo(() => buildQueue(props.sessions), [props.sessions]);
  const everyQueue = queue;
  const byProject = useMemo(() => queueByProject(props.sessions), [props.sessions]);
  // Which projects are open. Until the person opens or closes one, a project is open when it was
  // active today (what "Done today" used to show) or holds the open session, and folded when it
  // is quiet; their own choice is kept per viewer. The open session's project opens with it, so
  // the row on screen can always be seen in the list.
  const [projectChoice, setProjectChoice] = useState<Record<string, boolean>>(() => {
    try {
      const kept = JSON.parse(localStorage.getItem(OPEN_PROJECTS_KEY) || "{}");
      return kept && typeof kept === "object" && !Array.isArray(kept) ? kept : {};
    } catch {
      return {};
    }
  });
  const [allShown, setAllShown] = useState<Set<string>>(() => new Set());
  const chooseProject = (key: string, open: boolean) =>
    setProjectChoice((before) => {
      const next = { ...before, [key]: open };
      try {
        localStorage.setItem(OPEN_PROJECTS_KEY, JSON.stringify(next));
      } catch {
        /* a convenience: the list still works without it */
      }
      return next;
    });
  const activeRow = props.sessions.find((s) => s.session_id === props.activeSession);
  const activeProject = activeRow && !activeRow.archived && !isLive(groupOf(activeRow)) ? projectOf(activeRow) : null;
  const isOpen = (p: QueueProject) => projectChoice[p.key] ?? (activeToday(p.latest) || p.key === activeProject);
  useEffect(() => {
    if (activeProject !== null && projectChoice[activeProject] === false) chooseProject(activeProject, true);
    // Only when the open session changes: closing its project afterwards is the person's call.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.activeSession, activeProject]);
  const waitingCount = everyQueue.groups.needs.length;
  // What each working session is doing now, from the conversation's live state.
  const workingIds = useMemo(
    () => everyQueue.groups.running.filter((s) => s.liveness === "working").map((s) => s.session_id),
    [everyQueue],
  );
  const liveLines = useLiveLines(workingIds);
  const runningRuns = automations.filter((a) => a.latest_run?.status === "running" && !a.latest_run.attention);

  // The row's ⋮, and its menu when it is open.
  const rowActions = (s: SessionInfo, title: string) => {
    const menuOpen = rowMenu?.id === s.session_id;
    return (
      <>
        <span
          // Shown on hover and on keyboard focus, and kept while this row's menu is open (the
          // pointer may be on the menu, off the row). It takes the place of the row's time.
          // Placed by `top`, never by a transform: the menu inside is `position: fixed`, and a
          // transformed ancestor makes a fixed box answer to that ancestor (owner report
          // 2026-10-12; e2e/sidebar-rows.spec.ts).
          className={
            "q-row-menu transition-opacity duration-150 " +
            (menuOpen ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100")
          }
          onClick={(e) => e.stopPropagation()}
        >
          <button
            title="Session actions"
            aria-label="Session actions"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            data-testid="row-menu"
            className={"w-5 h-5 grid place-items-center rounded hover:text-ink " + (menuOpen ? "text-ink bg-chromeHover" : "text-faint hover:text-ink")}
            onClick={(e) => (menuOpen ? closeRowMenu() : setRowMenu({ id: s.session_id, ...placeRowMenu(e.currentTarget) }))}
          >
            {/* Vertical kebab = the horizontal glyph rotated — no extra icon needed. */}
            <Icon name="moreHorizontal" size={14} className="rotate-90" />
          </button>
        </span>
        {/* The menu is the ROW's child, beside the fading wrapper and never inside it: an
            element part-way through an opacity change is a stacking context of its own, which
            keeps a z-index inside it, so for the 150ms of the wrapper's fade the rows below
            were drawn over the menu's first items (e2e/sidebar-rows.spec.ts). */}
        {menuOpen && rowMenu && (
          <RowMenu
            session={s}
            place={rowMenu}
            onClose={closeRowMenu}
            onRename={() => {
              setRenaming(s.session_id);
              setNewName(title);
            }}
            onFork={props.onForkSession ? () => props.onForkSession?.(s.session_id) : undefined}
            onTogglePin={() => props.onTogglePin(s.session_id, !s.pinned)}
            onArchive={() => props.onArchiveSession(s.session_id, !s.archived)}
            onDelete={() => setToDelete({ id: s.session_id, title })}
          />
        )}
      </>
    );
  };

  // The nested child on the active path (P3): the direct child of `parentId` that the currently
  // open session sits under, then that child's own on-path child, one level per branch.
  const pathChild = (parentId: string, level = 0): ReactNode => {
    const child = childSessionOnPath(props.childSessions ?? {}, parentId, props.activeOnPath);
    if (!child) return null;
    const title = childDescription(child.title) || child.sessionId;
    return (
      <div key={`child-${child.sessionId}`} data-testid="sidebar-child" style={{ paddingLeft: 8 + (level + 1) * 16 }}>
        <div
          className="group flex items-center gap-2 pr-2 py-1.5 rounded-lg cursor-pointer text-left hover:text-ink"
          title={child.title}
          onClick={() => props.onOpenChild?.(child.sessionId)}
        >
          <span className="min-w-0 flex-1 truncate text-[13px] text-muted">{title}</span>
          <LiveDot state={child.status === "busy" ? "working" : "idle"} />
        </div>
        {pathChild(child.sessionId, level + 1)}
      </div>
    );
  };

  // The line under a row's title, when its state has something to say: what it waits on the
  // person for (the prompt's own words), what it is doing now, when it wakes, how much is not
  // committed yet.
  const rowLine = (s: SessionInfo, group: QueueGroup): { text: string; tone: string } | null => {
    if (group === "needs") return { text: s.attention_title || NEEDS[s.attention_kind || ""] || "Needs you", tone: "warn" };
    if (group === "running" && liveLines[s.session_id]) return { text: liveLines[s.session_id], tone: "now" };
    if (s.liveness === "sleeping") {
      const wakes = clockTime(s.sleeping_until);
      return { text: wakes ? `Wakes ${wakes}` : "Sleeping", tone: "" };
    }
    if (group === "review" && s.work?.open) {
      const n = s.work.open;
      return { text: `${n} file${n === 1 ? "" : "s"} not committed`, tone: "" };
    }
    return null;
  };

  // A queue row: its state's mark and title, the time it was last active (giving way to ⋮ on
  // hover or keyboard focus), the line its state says, and where its work lives. The title is a
  // real button, so the list is walked with Tab; hover changes the ink, never the ground. In its
  // project (`compact`) a past session is its title and time: its project is the heading above,
  // and what it changed is not news any more.
  const sessionRow = (s: SessionInfo, compact = false) => {
    const title = s.title || s.session_id;
    const editing = renaming === s.session_id;
    const active = s.session_id === props.activeSession;
    const menuOpen = rowMenu?.id === s.session_id;
    const group = groupOf(s);
    const line = rowLine(s, group);
    // When it was last active; a running one is now. A mention-spawned session says where it came
    // from beside its time.
    const when = s.liveness === "working" ? "" : sessionAge(s.updated_at);
    const age = s.origin === "slack" ? ["Slack", when].filter(Boolean).join(" · ") : when;
    // An empty or unchanged name renames nothing.
    const commitRename = () => {
      const name = newName.trim();
      if (name && name !== title) props.onRenameSession(s.session_id, name);
      setRenaming(null);
    };
    return (
      <Fragment key={s.session_id}>
        <div
          className={"group q-row" + (active ? " is-active" : "") + (compact ? " is-compact" : "")}
          data-testid="session-row"
          data-group={group}
          data-waiting={group === "needs" || undefined}
          // On the row, not on the button in it: the full name is the row's, wherever it is pointed at.
          title={editing ? undefined : title}
        >
          {editing ? (
            <input
              className="flex-1 min-w-0 mx-1 my-1 px-1.5 py-0.5 rounded-md bg-panel border border-accent text-[13px] text-ink outline-none"
              aria-label="Session name"
              value={newName}
              autoFocus
              onChange={(e) => setNewName(e.target.value)}
              onBlur={commitRename}
              onKeyDown={(e) => {
                e.stopPropagation();
                if (e.key === "Enter") commitRename();
                else if (e.key === "Escape") setRenaming(null);
              }}
            />
          ) : (
            <>
              <button
                type="button"
                className="q-row-main"
                aria-current={active ? "page" : undefined}
                onClick={() => props.onSelectSession(s.session_id, s.workspace)}
              >
                <span className="q-row-head">
                  <span className={"q-mark q-mark-" + group + (s.liveness === "sleeping" ? " sleeping" : "")} aria-hidden />
                  <span className="q-title">{title}</span>
                  {s.pinned && <Icon name="pin" size={11} className="q-pin" />}
                  <span className={"q-meta" + (menuOpen ? " is-hidden" : "")}>
                    <RemoteIcon s={s} />
                    <ConnectorDot subs={s.subscriptions} />
                    {age && (
                      <span className="q-age" data-testid="row-meta">
                        {age}
                      </span>
                    )}
                  </span>
                </span>
                {line && (
                  <span className={"q-line" + (line.tone ? " " + line.tone : "")} data-testid="queue-line">
                    {line.text}
                  </span>
                )}
                {!compact && <QueueFacts s={s} showProject />}
              </button>
              {/* A run is renamed, pinned and deleted with its automation, not from here. */}
              {!s.automation && rowActions(s, title)}
            </>
          )}
        </div>
        {pathChild(s.session_id)}
      </Fragment>
    );
  };

  // An automation's run that is running now, under Running. Its conversation is saved when it
  // ends, so the row opens the automation, where the run is listed live. A run that waits on
  // the person is its conversation's own row, under Needs you, and is not listed twice.
  const runRow = (a: Automation) => {
    const started = a.latest_run?.started_at ? new Date(a.latest_run.started_at * 1000).toISOString() : null;
    const when = clockTime(started);
    return (
      <div key={`run-${a.id}`} className="group q-row" data-testid="automation-run-row" data-group="running" title={a.title}>
        <button type="button" className="q-row-main" onClick={() => props.onOpenAutomation?.(a.id)}>
          <span className="q-row-head">
            <span className="q-mark q-mark-running" aria-hidden />
            <span className="q-title">{a.title}</span>
            <span className="q-meta">
              <Icon name="clock" size={11} className="text-faint" />
            </span>
          </span>
          <span className="q-line" data-testid="queue-line">
            {when ? `Automation run · started ${when}` : "Automation run"}
          </span>
        </button>
      </div>
    );
  };

  // One live group of the queue: its name and count, then its rows.
  const groupBlock = (key: QueueGroup) => {
    const rows = queue.groups[key];
    const runs = key === "running" ? runningRuns : [];
    if (rows.length + runs.length === 0) return null;
    return (
      <div key={key} data-testid={key === "needs" ? "needs-you" : `queue-group-${key}`} data-group={key}>
        <div className="q-group-head">
          <span className={"q-group-mark q-mark-" + key} aria-hidden />
          {GROUP_LABEL[key]}
          <span className="q-group-count">{rows.length + runs.length}</span>
        </div>
        <div className="q-rows">
          {rows.map((s) => sessionRow(s))}
          {runs.map(runRow)}
        </div>
      </div>
    );
  };

  // One project under the queue: a row that opens in place to its past sessions (the live ones
  // are above, in their groups), with a mark for each live group it has a session in, how many
  // sessions it has and when it was last active. Opened, `peek` sessions, then "Show N more".
  const projectBlock = (p: QueueProject, name: string) => {
    const open = isOpen(p);
    const all = allShown.has(p.key);
    // Today's sessions are never behind "Show more" (they were all in Done today); older ones
    // fill up to `peek`.
    const shown = Math.max(peek, p.past.filter((s) => activeToday(s.updated_at || "")).length);
    const hidden = p.past.length - shown;
    const id = `q-proj-${p.key || "loose"}`;
    return (
      <div key={p.key || "loose"} className="q-project-block" data-testid="queue-project" data-project={p.key}>
        <button
          type="button"
          className="q-proj"
          aria-expanded={open}
          aria-controls={id}
          title={p.key || "Conversations with no folder"}
          onClick={() => chooseProject(p.key, !open)}
        >
          <Icon name="chevronRight" size={12} className={"side-fold-chevron shrink-0 text-faint" + (open ? " open" : "")} />
          <Icon name="folder" size={13} className="shrink-0 text-faint" />
          <span className="q-proj-name">{name}</span>
          {p.live.length > 0 && (
            <span className="q-proj-live" aria-label={p.live.map((g) => GROUP_LABEL[g]).join(", ")}>
              {p.live.map((g) => (
                <span key={g} className={"q-mark q-mark-" + g} />
              ))}
            </span>
          )}
          <span className="q-proj-meta">
            {p.sessions.length} · {sessionAge(p.latest)}
          </span>
        </button>
        <Disclosure open={open} className="fold-fade">
          <div className="q-rows q-proj-rows" id={id}>
            {p.past.length === 0 ? (
              <div className="q-proj-empty">Everything here is in the queue above.</div>
            ) : (
              <>
                {p.past.slice(0, shown).map((s) => sessionRow(s, true))}
                <Disclosure open={all && hidden > 0} className="fold-fade">
                  <div className="q-rows">{p.past.slice(shown).map((s) => sessionRow(s, true))}</div>
                </Disclosure>
                {hidden > 0 && (
                  <button
                    type="button"
                    className="q-more"
                    onClick={() =>
                      setAllShown((before) => {
                        const next = new Set(before);
                        if (next.has(p.key)) next.delete(p.key);
                        else next.add(p.key);
                        return next;
                      })
                    }
                  >
                    {all ? "Show less" : `Show ${hidden} more`}
                  </button>
                )}
              </>
            )}
          </div>
        </Disclosure>
      </div>
    );
  };

  // `/workspace`: the current project's git worktrees, shown at the top of the queue. A click
  // opens a NEW session bound to that worktree path (the app's ordinary workspace switch).
  const worktreesBand = () =>
    props.worktreesOpen ? (
      <div data-testid="worktrees-band">
        <div className="flex items-center justify-between px-1.5 mb-1">
          <span className="text-[12px] font-medium text-muted">Worktrees</span>
          <button
            className="w-5 h-5 grid place-items-center rounded text-faint hover:text-ink"
            title="Hide worktrees"
            aria-label="Hide worktrees"
            data-testid="worktrees-close"
            onClick={() => props.onToggleWorktrees?.()}
          >
            <Icon name="x" size={12} />
          </button>
        </div>
        {props.worktreesLoading && !props.worktrees ? (
          <div className="px-2 py-1.5 text-[12px] text-faint">Reading worktrees…</div>
        ) : !props.worktrees?.is_git ? (
          <div className="px-2 py-1.5 text-[12px] text-faint leading-snug" data-testid="worktrees-empty">
            {props.worktrees?.error === "no workspace folder" ? "Open a project folder to see its worktrees." : "Not a git repository."}
          </div>
        ) : (
          <div className="space-y-0.5">
            {props.worktrees.worktrees.map((w) => (
              <button
                key={w.path}
                className="w-full flex items-center gap-2 px-1.5 py-1 rounded-lg text-left hover:text-ink"
                data-testid="worktree-row"
                title={w.path}
                onClick={() => props.onOpenWorktree?.(w.path)}
              >
                <Icon name="branch" size={13} className="shrink-0 text-muted" />
                <span className="flex-1 min-w-0">
                  <span className="block truncate text-[13px] text-ink">{w.name}</span>
                  <span className="block truncate font-mono text-[11px] text-faint">
                    {w.branch || (w.detached ? `detached @ ${(w.head || "").slice(0, 7)}` : (w.head || "").slice(0, 7))}
                    {w.primary ? " · primary" : ""}
                    {w.current ? " · current" : ""}
                    {w.locked ? " · locked" : ""}
                  </span>
                </span>
                <span className="shrink-0 text-[11px] text-faint">Open</span>
              </button>
            ))}
          </div>
        )}
      </div>
    ) : null;

  const unseenRuns = automations.reduce((sum, a) => sum + (a.unseen_runs || 0), 0);
  const settingsTitle = `Settings · ${shortcut(",")}`;
  // Folded, the rail says what the queue holds: one mark per session that is running or waits
  // for review, after the warm count of those that wait on the person.
  const railMarks = [...everyQueue.groups.running, ...everyQueue.groups.review].slice(0, 6);

  // The rail: always there, the queue's toggle at its top (the brand mark at rest, the panel
  // glyph on hover), then New session, Search, Automations, Metrics; Settings at the bottom.
  const rail = () => (
    <div className="flex flex-col items-center flex-1 min-h-0 pt-2.5" data-testid="sidebar-rail">
      <div className="flex flex-col items-center gap-1">
        <button
          className="rail-btn rail-brand"
          onClick={props.onCollapse}
          title={props.collapsed ? `Open sidebar · ${shortcut("B")}` : `Collapse sidebar · ${shortcut("B")}`}
          aria-label={props.collapsed ? "Open sidebar" : "Collapse sidebar"}
          aria-expanded={!props.collapsed}
          data-testid="rail-dock"
        >
          <span className="rail-brand-mark">
            <Icon name="logo" size={24} />
          </span>
          <span className="rail-brand-panel">
            <Icon name="sidebar" size={18} />
          </span>
        </button>
        <button className="rail-btn" onClick={() => props.onNewSession()} title="New session" aria-label="New session" data-testid="rail-new-session">
          <Icon name="plus" size={18} />
        </button>
        <button
          className="rail-btn"
          onClick={() => setSearchModalOpen(true)}
          title={`Search · ${shortcut("K")}`}
          aria-label="Search"
          aria-keyshortcuts="Control+K Meta+K"
          data-testid="rail-search"
        >
          <Icon name="search" size={18} />
        </button>
        <button
          className={"rail-btn" + (props.scheduledActive ? " rail-btn-on" : "")}
          onClick={props.onOpenScheduled}
          title={unseenRuns ? `Automations · ${unseenRuns} new run${unseenRuns > 1 ? "s" : ""}` : "Automations"}
          // The name stays "Automations"; the count is its description, as on the old nav row.
          aria-label="Automations"
          aria-description={unseenRuns ? `${unseenRuns} new run${unseenRuns > 1 ? "s" : ""}` : undefined}
          aria-current={props.scheduledActive ? "page" : undefined}
          data-testid="rail-automations"
        >
          <Icon name="clock" size={18} />
          {unseenRuns > 0 && (
            <span className="rail-unseen" aria-hidden>
              {unseenRuns > 9 ? "9+" : unseenRuns}
            </span>
          )}
        </button>
        <button
          className={"rail-btn" + (props.opsActive ? " rail-btn-on" : "")}
          onClick={props.onOpenOps}
          title="Metrics"
          aria-label="Metrics"
          aria-current={props.opsActive ? "page" : undefined}
          data-testid="rail-ops"
        >
          <Icon name="ops" size={18} />
        </button>
        {props.collapsed && waitingCount > 0 && (
          <button
            className="rail-btn rail-waiting"
            onClick={props.onCollapse}
            title={`${waitingCount} session${waitingCount > 1 ? "s" : ""} need${waitingCount > 1 ? "" : "s"} you`}
            aria-label={`${waitingCount} session${waitingCount > 1 ? "s" : ""} need${waitingCount > 1 ? "" : "s"} you. Open sidebar`}
            data-testid="rail-waiting"
          >
            {waitingCount > 99 ? "99+" : waitingCount}
          </button>
        )}
        {props.collapsed && railMarks.length > 0 && (
          <button
            className="rail-marks"
            onClick={props.onCollapse}
            title={railMarks.map((s) => `${GROUP_LABEL[groupOf(s)]}: ${s.title || s.session_id}`).join("\n")}
            aria-label={`${railMarks.length} running or to review. Open sidebar`}
            data-testid="rail-marks"
          >
            {railMarks.map((s) => (
              <span key={s.session_id} className={"q-mark q-mark-" + groupOf(s)} />
            ))}
          </button>
        )}
      </div>
      <div className="flex-1" />
      <div className="rail-foot w-full border-t border-line pt-2 pb-2 flex flex-col items-center gap-1">
        <button className="rail-btn" data-testid="rail-settings" onClick={props.onManage} aria-label="Open Settings" title={settingsTitle}>
          <Icon name="gear" size={18} />
        </button>
      </div>
    </div>
  );

  // The list has four states, and only one of them is the list.
  const hasSessions = props.sessions.some((s) => !s.session_id.startsWith("__"));
  const listState = hasSessions ? "ready" : props.sessionsState === "loading" ? "loading" : props.sessionsState === "error" ? "error" : "empty";
  const live = queue.groups.needs.length + queue.groups.running.length + runningRuns.length + queue.groups.review.length;

  // A project is named by its folder; two folders of one name say their parent too.
  const projectName = (key: string) => {
    const name = baseName(key.replace(/^[^:]+:(?=[\\/]|[A-Za-z]:)/, ""));
    const twins = byProject.projects.filter((p) => baseName(p.key) === baseName(key)).length > 1;
    return twins ? `${baseName(key.replace(/[\\/][^\\/]+[\\/]?$/, ""))}/${name}` : name;
  };

  const queuePanel = () => (
    <div className="queue-panel" data-testid="work-queue">
      <div className="queue-head" data-drag-region>
        <span className="queue-title" title="J / K to move through the queue, Enter to open">
          Work
        </span>
        <span className="flex-1" />
        <button type="button" className="queue-new" onClick={() => props.onNewSession()} aria-label="New session" title="New session">
          <Icon name="plus" size={14} />
          New
        </button>
        {/* Named apart from the rail's own toggle, which does the same: two buttons with one
            name are one name too many for a reader moving by name. */}
        <button
          type="button"
          className="queue-fold"
          title={`Hide the queue · ${shortcut("B")}`}
          aria-label="Hide the queue"
          onClick={props.onCollapse}
          data-testid="queue-collapse"
        >
          <Icon name="sidebar" size={15} />
        </button>
      </div>
      <div
        className="queue-list"
        ref={listRef}
        data-testid="sidebar-list"
        aria-keyshortcuts="J K"
        aria-busy={listState === "loading" || undefined}
      >
        {listState === "loading" ? (
          <div data-testid="sidebar-loading" role="status" aria-label="Loading sessions">
            {[62, 80, 48, 70, 56].map((wide) => (
              <div key={wide} className="flex items-center h-[44px] px-2">
                <span className="h-2 rounded bg-ink/[0.08]" style={{ width: `${wide}%` }} />
              </div>
            ))}
          </div>
        ) : listState === "error" ? (
          <div className="mx-1 rounded-lg bg-warnSoft px-3 py-2.5 text-[12.5px] leading-snug text-warnInk" role="alert" data-testid="sidebar-error">
            The session list could not be loaded.
            <div className="mt-2">
              <button
                type="button"
                className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-lg border border-line bg-panel text-[12.5px] text-ink"
                onClick={props.onRetrySessions}
              >
                <Icon name="refresh" size={13} /> Try again
              </button>
            </div>
          </div>
        ) : listState === "empty" ? (
          <div className="px-2 pt-1 flex flex-col items-start gap-2" data-testid="sidebar-empty">
            <div className="text-[13px] font-semibold text-ink">No work yet</div>
            <div className="text-[12.5px] leading-snug text-muted">What you start shows up here, by what it needs from you.</div>
            <button type="button" className="h-7 px-2.5 rounded-lg border border-line bg-panel text-[12.5px] text-ink" onClick={() => props.onNewSession()}>
              New session
            </button>
            {worktreesBand()}
          </div>
        ) : (
          <div className="queue-groups">
            {worktreesBand()}
            {live === 0 && <div className="q-quiet">Nothing running or waiting.</div>}
            {LIVE_GROUPS.map(groupBlock)}
            {/* Every project, the newest first, each with its own past work; then the
                conversations with no folder. */}
            {byProject.projects.length > 0 && (
              <div className="q-projects" data-testid="queue-projects">
                <div className="q-section">Projects</div>
                {byProject.projects.map((p) => projectBlock(p, projectName(p.key)))}
              </div>
            )}
            {byProject.loose && (
              <div className="q-projects">
                <div className="q-section">No folder</div>
                {projectBlock(byProject.loose, "Chats")}
              </div>
            )}
            {/* What was put away: closed until asked for, at the foot of the list. */}
            {queue.archived.length > 0 && (
              <div>
                <button
                  className="q-group-head is-button"
                  aria-expanded={showArchived}
                  data-testid="archived-toggle"
                  onClick={() => setShowArchived((v) => !v)}
                >
                  Archived
                  <span className="q-group-count">{queue.archived.length}</span>
                  {/* One chevron that turns, not two glyphs swapped. */}
                  <Icon name="chevronRight" size={12} className={"side-fold-chevron shrink-0 text-faint" + (showArchived ? " open" : "")} />
                </button>
                <Disclosure open={showArchived} className="fold-fade">
                  <div className="q-rows">{queue.archived.map((s) => sessionRow(s, true))}</div>
                </Disclosure>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div
      className={"sidebar flex min-h-0 bg-chrome border-r border-line" + (props.collapsed ? " sidebar-rail" : "") + (props.starting ? " is-starting" : "")}
      {...(props.starting ? { inert: "", "data-testid": "sidebar-starting" } : {})}
    >
      <div className="sidebar-rail-inner flex flex-col min-h-0">{rail()}</div>
      {!props.collapsed && <div className={"sidebar-wide flex flex-col flex-1 min-h-0" + (swapped ? " sidebar-swap" : "")}>{queuePanel()}</div>}

      {toDelete && (
        <ConfirmDialog
          title={`Delete “${toDelete.title}”?`}
          body="The conversation and its history are removed. This cannot be undone. To put it away and keep it, archive it instead."
          confirmLabel="Delete"
          onConfirm={() => {
            props.onDeleteSession(toDelete.id);
            setToDelete(null);
          }}
          onCancel={() => setToDelete(null)}
        />
      )}
      {/* ⌘K and the rail's Search: find a session by its name or its words. */}
      {searchModalOpen && (
        <SearchModal
          sessions={props.sessions}
          onSelect={(id, workspace) => {
            setSearchModalOpen(false);
            props.onSelectSession(id, workspace);
          }}
          onClose={() => setSearchModalOpen(false)}
        />
      )}
    </div>
  );
}
