import { Suspense, lazy, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  createTempWorkspace,
  finalizeAutomationRun,
  getHealth,
  getSessionChildren,
  getSessionTranscript,
  getSessionMetrics,
  getSessionSummary,
  setSessionEnvironment,
  revertSession,
  unrevertSession,
  type RevertResult,
  type RemoteEnvironmentSummary,
  getSubagents,
  type SubagentAgent,
  getSessions,
  exportSession,
  sessionExportFilename,
  announceAutomationsChanged,
  METRICS_CHANGED,
  connectEvents,
  getSettings,
  getInbox,
  getConnectors,
  getSessionGoal,
  type GoalInfo,
  goalAction,
  resolveInboxItem,
  replyPermission,
  deleteSession,
  forkSession,
  promoteSessionTasks,
  renameSession,
  runAutomation,
  setSessionFlags,
  getWorkspaceWorktrees,
  getSessionShare,
  shareSession,
  unshareSession,
  type InboxItem,
  type PermissionReply,
  type PermissionRequestInfo,
  type SessionShareStatus,
  type WorkspaceCommandTrust,
  type WorkspaceWorktrees,
} from "./api";
import type {
  ApprovalDecision,
  Attachment,
  Item,
  ReviewComment,
  SentCommand,
  SessionInfo,
} from "./types";
import { baseName } from "./paths";
import { itemsFromMessages } from "./itemsFromMessages";
import { approvalItemFromInbox } from "./approvalItem";
import { canRedoMarker, canUndoItems, revertDraft } from "./revertFlow";
import { childDescription, type ChildSession } from "./childSessions";
import { delegationNodes, isDelegationCall, withKept, withReports } from "./delegations";
import { useKeptWorktrees } from "./live/useKeptWorktrees";
import type { ToolItem } from "./cards/cardModels";
import {
  buildConnectorToolIndex,
  EMPTY_CONNECTOR_TOOLS,
  type ConnectorToolIndex,
} from "./connectorTools";
import {
  buildAgentColors,
  EMPTY_AGENT_COLORS,
  type AgentColorIndex,
} from "./subagents";
import { planFromMessages } from "./todoPlan";
import { emptyUsage, formatTokens, usageFromMessages } from "./usage";
import { compactingWords as compactingWordsFor } from "./components/session/compactingWords";
import { compactNow, revealArtifact, undoToolCall } from "./api";
import { touchedFilesOf } from "./components/composer/atMenu";
import { SEE_CHANGES_EVENT } from "./seeChanges";
import { InboxItemCard } from "./components/InboxItemCard";
import { OPEN_ARTIFACT_EVENT, OPEN_FILES_EVENT } from "./components/Markdown";
import { SEND_PROMPT_EVENT } from "./components/visual/FormBlock";
import { takeVisualStates, withVisualStates } from "./components/visual/visualState";
import {
  isDesktop,
  onSidecarStatus,
  platformOS,
  restartSidecar,
  saveTextAnywhere,
  sidecarStatus,
  type SidecarState,
} from "./desktop";
import { GIVE_UP_MS, rememberBootTime } from "./bootTiming";
import { BootPane, type BootFailure } from "./components/BootPane";
import { SessionShellContext, type SessionShell } from "./sessionShell";
import { shellDisplayName, shellKind } from "./shellKind";
import { WindowControls } from "./components/TitleBar";
import { Sidebar } from "./components/Sidebar";
import { Composer } from "./components/Composer";
import { modeValue } from "./components/composer/modes";
import { BRANCH_SWITCHED_EVENT } from "./components/BranchPicker";
import { FolderActivityContext, otherWorkIn } from "./components/folderActivity";
import { ASK_CHANGE_EVENT, askChangeDraft } from "./components/rail/reviewAsks";
import { DESIGN_ATTACH_EVENT } from "./components/rail/designAsks";
import { OPEN_BROWSER_EVENT, OPEN_DESIGN_APP_EVENT } from "./components/rail/tabs";
import { SessionScroll } from "./components/session/SessionScroll";
import { IdleStart } from "./components/session/IdleHero";
import { ShownChildContext } from "./shownChild";
import { SessionDock, TaskHead } from "./components/session/SessionDock";
import { TaskFacts, taskFacts } from "./components/session/TaskFacts";
import { ConnectionNote } from "./components/session/ConnectionNote";
import { navLayout } from "./app/navLayout";
import { useStoredFlag } from "./app/useStoredFlag";
import { useViewportWidth } from "./app/useViewportWidth";
import { SessionOverlays } from "./components/session/SessionOverlays";
import { SessionTopbar } from "./components/session/SessionTopbar";
import type { QueuedPrompt } from "./components/QueuedPrompts";
import { useToast } from "./ui";
import { shouldStopOnEscape } from "./sessionKeys";
import { modeNoticeStep, withModeNotice, type ModeNoticeState } from "./modeNotice";
import { SessionSetupRow } from "./components/SessionSetupRow";
import { Onboarding } from "./components/Onboarding";
import { AutomationToast } from "./components/AutomationToast";
import { UpdateBanner } from "./components/UpdateBanner";
import { SidecarBanner } from "./components/SidecarBanner";
import { DesktopShellBridge } from "./components/DesktopShellBridge";
import { AutomationsView } from "./components/automations/AutomationsView";
import { RightRail } from "./components/RightRail";
import { getWorkspaceBranch } from "./api";
import { getSessionJobs } from "./api/jobs";
import { reloadSessionPlugins } from "./api/plugins";
import { ProjectPluginsBanner } from "./components/ProjectPluginsBanner";

import { useShellState } from "./live/useShellState";
import { liveRegistry, type LiveSession } from "./live/liveSession";
import { handleSessionEvent, pluginsReloadedNotice, type SessionEventHost } from "./live/sessionEvents";
import { activityLabel } from "./toolMeta";
import { IntegrationsView } from "./components/IntegrationsView";
import { SettingsView } from "./components/SettingsView";
import { AuditView } from "./components/AuditView";
// The Metrics page and its chart library load when the page is first opened: the chat does not carry them.
const OpsView = lazy(() => import("./components/OpsView").then((m) => ({ default: m.OpsView })));
import { ApprovalCard } from "./components/ApprovalCard";
import { ToolRequestCard } from "./components/ToolRequestCard";
import { PlanCard } from "./components/PlanCard";
import { PermissionDock } from "./components/PermissionDock";
import {
  newId,
  RAIL_HIDDEN_KEY,
  NAV_COLLAPSED_KEY,
  resolveLastApproval,
  resolveLastToolReq,
  resolveLastPlan,
  resolveLastQuestion,
  sessionComposerState,
  sessionRemoteWorkspace,
} from "./app/appHelpers";

/** Conversations whose scroll place is remembered after leaving them. */
const SCROLL_MEMORY = 24;

export function App() {
  const [workspace, setWorkspace] = useState<string | null>(null);
  // The session works in a temporary folder, whose path is never shown: the top bar
  // keeps a new session's name. Set here when a send makes one; every `ready` says it again.
  const [tempWorkspace, setTempWorkspace] = useState(false);
  const [workspaceTrustRequest, setWorkspaceTrustRequest] = useState<WorkspaceCommandTrust | null>(null);
  const [model, setModel] = useState("gpt-5.6-sol");
  const [models, setModels] = useState<string[]>([]);
  const [modelLabels, setModelLabels] = useState<Record<string, string>>({});
  // Each verified model's context window, for the usage chip's ring.
  const [modelContextWindows, setModelContextWindows] = useState<Record<string, number>>({});
  // Settings: show the composer's context-window fill bar. ON unless the server says
  // otherwise (reviews.py `context_bar`, default ON since 2026-09-25).
  const [contextBar, setContextBar] = useState(true);
  // The `task` agent definitions (`GET /v1/subagents`) for the @mention menu and picker.
  const [subagents, setSubagents] = useState<SubagentAgent[]>([]);
  const [mode, setMode] = useState("interactive");
  // The Plan overlay (backend `ready.plan` / `plan_changed`): ON hard-denies
  // consequential tools while the agent explores and proposes. Orthogonal to `mode`.
  const [plan, setPlan] = useState(false);
  // The session's thinking level ("" = the model's own default). Server-owned; the
  // composer sends a change and the server echoes it back.
  const [thinking, setThinking] = useState("");
  // Review comments on diff lines (the rail's comment boxes). Held here because they ride
  // the NEXT message as context, then clear.
  const [reviewComments, setReviewComments] = useState<ReviewComment[]>([]);
  /**
   * Prompts written while the agent was working, oldest first (see `QueuedPrompts`).
   *
   * The engine takes one turn at a time, so a mid-turn message cannot be sent when it is
   * typed — it is parked here and sent by the drain effect once the turn ends. Session-scoped:
   * a queued sentence belongs to the conversation it was written in.
   */
  const [queuedPrompts, setQueuedPrompts] = useState<QueuedPrompt[]>([]);
  // The conversation on screen is a `LiveSession` (live/liveSession.ts): its transcript, the
  // live step, run state, usage, plan, goal and revert marker, plus its own socket. They live
  // outside React so a conversation left in the background keeps receiving its events; the
  // declarations that read them are further down, once `sessionId` is known.

  // Mode markers in the transcript: what mode the transcript last recorded (so a switch
  // can be told apart from a re-render or a session change). See `modeNoticeStep`.
  const modeNoticeState = useRef<ModeNoticeState>({ mark: null });
  // Which session the current `mode` value is CONFIRMED for. On a session switch, `mode`
  // still holds the previous session's value until the server's `ready` event delivers the
  // real one — announcing anything in that window posts the old session's marker into the
  // new transcript (seen 2026-08-22: a fresh session opened with a stale notice, then a
  // stray "Workspace write is on." marker when `ready` landed).
  const [modeConfirmedFor, setModeConfirmedFor] = useState("");
  // The session facts the user has explicitly picked in-session (model / mode /
  // thinking), keyed by session. Hydration — the record read at load and the `ready`
  // frame — must not clobber such a pick: on a slow connect the pick is queued before
  // `ready` lands, and reverting the pill to the server's old value would be a lie.
  const composerPickRef = useRef<{
    session: string;
    model?: boolean;
    mode?: boolean;
    thinking?: boolean;
  }>({ session: "" });

  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  // Whether the list has been read at all: until it has the sidebar draws a skeleton, and a
  // first read that failed says so. A later poll that fails keeps the list it has.
  const [sessionsState, setSessionsState] = useState<"loading" | "ready" | "error">("loading");
  // A new conversation starts as a hydrated draft: there is no history to wait for.
  const [sessionId, setSessionId] = useState<string>(() => {
    const id = newId();
    liveRegistry.ensure(id).startDraft();
    return id;
  });
  // The conversation on screen. `ensure` is idempotent, so rendering is free of side
  // effects; the pool's recency bookkeeping runs in an effect (`liveRegistry.activate`, below).
  const live = liveRegistry.ensure(sessionId);
  // Everything but the live step's text: a streamed token does not re-render App
  // (live/useShellState.ts); SessionScroll subscribes to the text itself.
  const liveState = useShellState(live);
  const {
    items,
    streamOpen,
    liveKind,
    liveTool,
    running,
    todo,
    usage,
    goal,
    compacting,
    compactingSince,
    compactingWhy,
    compactNote,
    connected,
    revert: revertMarker,
    loading: hydrating,
  } = liveState;
  // Same names the rest of this component has always used; they now write to the session's
  // own store, so a late callback for a conversation that is no longer on screen lands in
  // THAT conversation instead of the one in front of the reader.
  const { setItems, setRunning, setGoal } = live;
  // The newest state for callbacks that outlive a render (the stream pin runs from observers).
  const liveRef = useRef(liveState);
  liveRef.current = liveState;
  // What the open conversations know about themselves right now. The polled list is up to
  // five seconds behind: a conversation that just started working, or just asked the reader
  // something, should say so in the sidebar the moment it happens.
  const liveVersion = useSyncExternalStore(liveRegistry.subscribe, liveRegistry.getVersion);
  const sidebarSessions = useMemo(() => {
    const activity = liveRegistry.activity();
    if (Object.keys(activity).length === 0) return sessions;
    return sessions.map((s) => {
      const now = activity[s.session_id];
      if (!now) return s;
      return {
        ...s,
        liveness: now.running ? ("working" as const) : s.liveness,
        // A prompt in the conversation the reader is already looking at is not news.
        attention: Math.max(s.attention || 0, now.needsAttention && s.session_id !== sessionId ? 1 : 0),
      };
    });
    // `liveVersion` is the dependency that matters: the registry changes outside React state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions, liveVersion, sessionId]);
  // Which folders another session is working in, for the branch picker wherever it opens.
  const folderActivity = useCallback((path: string) => otherWorkIn(sidebarSessions, path, sessionId), [sidebarSessions, sessionId]);
  // What the running header should name: the stream's CURRENT block. A call being
  // written is not a tool item yet, so without this the header could only say "Working".
  const liveActivity =
    liveKind === "tool_call"
      ? liveTool
        ? activityLabel(liveTool)
        : undefined
      : liveKind === "reasoning"
        ? "Thinking"
        : undefined;
  // The active session's bound SSH environment (null = Local) and
  // the typed start failure for it (the engine-build probe's error, shown in the setup
  // row rather than only as a transcript line). `connectNonce` re-opens the socket for
  // the SAME session after a bind switch — `workspace` is deliberately not a connect
  // dependency, so a world swap needs its own trigger.
  const [activeEnvironment, setActiveEnvironment] = useState<RemoteEnvironmentSummary | null>(null);
  // The shell the session on screen runs a command in when it names none: a Setting pinned at
  // build, so the OS alone does not say it. From the `ready` frame.
  const [defaultShell, setDefaultShell] = useState<string | undefined>(undefined);
  // The workspace ON THE BOUND HOST — the path half of the
  // `user@host:path` readout. Server truth (the session record's remote workspace),
  // kept apart from the local `workspace` state so a stale local folder spelling can
  // never be appended to the remote readout. Null = local or not known (destination-only).
  const [remoteWorkspace, setRemoteWorkspace] = useState<string | null>(null);
  const [environmentError, setEnvironmentError] = useState("");
  const [connectNonce, setConnectNonce] = useState(0);
  // Ref mirrors for the socket handler, which is built once per connection.
  const activeEnvironmentRef = useRef<RemoteEnvironmentSummary | null>(null);
  activeEnvironmentRef.current = activeEnvironment;
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;
  useEffect(() => { setReviewComments([]); }, [sessionId]);
  // A queue is a promise about THIS conversation: carrying a sentence into the next session
  // would send it to an agent that never saw the context it was written for — so the queue is
  // keyed by session, restored when the user comes back, and written back as it changes.
  // Attachments stay memory-only (a pasted image can exceed the storage quota); the entry
  // keeps working for this page's lifetime, it just does not survive a reload.
  const queueSessionRef = useRef<string | null>(null);
  const queueReadyRef = useRef(false);
  // The conversation the window opens on before Reeve has said which one to resume: a stand-in.
  // What is typed and queued in it while Reeve starts belongs to the conversation that opens
  // (the person sees that one's name in the bar as soon as it is known), so it goes with it.
  const provisionalSessionRef = useRef<string | null>(sessionId);
  useEffect(() => {
    const key = `reeve.queue.${sessionId}`;
    if (queueSessionRef.current !== sessionId) {
      let restored: QueuedPrompt[] = [];
      try {
        const raw = localStorage.getItem(key);
        if (raw) restored = JSON.parse(raw) as QueuedPrompt[];
      } catch {
        restored = [];
      }
      if (queueSessionRef.current !== null && queueSessionRef.current === provisionalSessionRef.current) {
        restored = [...restored, ...queuedPrompts];
        provisionalSessionRef.current = null;
      }
      queueSessionRef.current = sessionId;
      // Not ready to SAVE until the restored value has rendered: the save branch
      // below would otherwise write this render's stale queue over the saved one
      // (and StrictMode runs this effect twice on mount).
      queueReadyRef.current = false;
      setQueuedPrompts(restored);
      return;
    }
    if (!queueReadyRef.current) {
      queueReadyRef.current = true;
      return;
    }
    const entries = queuedPrompts
      .filter((q) => !q.attachments?.length)
      .map(({ id, text: t, skill, mentions }) => ({ id, text: t, skill, mentions }));
    try {
      if (entries.length) localStorage.setItem(key, JSON.stringify(entries));
      else localStorage.removeItem(key);
    } catch {
      /* private mode / quota: the queue still works for this page's lifetime */
    }
  }, [queuedPrompts, sessionId]);
  // The goal strip's reload path: opening (or switching to) a session reads its goal. The
  // effect owns the fetch so every entry point (resume, sidebar pick, new conversation,
  // archive/delete) gets it without a call site each — and so a slow answer for a session
  // the user already left cannot write into the new one's strip.
  useEffect(() => {
    const target = liveRegistry.ensure(sessionId);
    const asked = target.goalVersion;
    let alive = true;
    // A `goal` frame that lands while this read is in flight is newer than the answer.
    const land = (g: GoalInfo | null) => {
      if (alive && target.goalVersion === asked) target.setGoal(g);
    };
    getSessionGoal(sessionId)
      .then(land)
      .catch(() => land(null));
    return () => {
      alive = false;
    };
  }, [sessionId]);
  // An automation run's conversation (owner, 2026-07-04): which automation it belongs to, for its
  // banner and "Back to runs". A run without it still gets a plain banner, known by its __run__ id.
  const [runContext, setRunContext] = useState<{ id: string; title: string } | null>(null);
  // The automation the Automations page opens on (from a run's banner or the sidebar). Forgotten
  // on leaving the page: kept, an automation deleted meanwhile reopened as "Loading…" for good
  // (owner-hit 2026-07-20). Coming back lands on the list.
  const [scheduledOpenId, setScheduledOpenId] = useState<string | null>(null);
  type SettingsPage = "general" | "appearance" | "models" | "skills" | "plugins" | "subagents" | "voice" | "memory" | "remote" | "connectors";
  const [settingsTab, setSettingsTab] = useState<SettingsPage>("general");
  const openSettings = (tab: SettingsPage = "general") => {
    setSettingsTab(tab);
    setSurface("settings");
  };
  // Whether any provider can answer for the default model, for the composer's "No model
  // connected". True until settings say otherwise, so the line does not flash as the app opens.
  const [modelReady, setModelReady] = useState(true);
  const [surface, setSurface] = useState<"session" | "scheduled" | "integrations" | "audit" | "settings" | "ops">("session");
  useEffect(() => {
    if (surface !== "scheduled") setScheduledOpenId(null);
  }, [surface]);
  const [browserRefreshKey, setBrowserRefreshKey] = useState(0);
  // The side panel starts hidden, and its toggle is kept on this device (owner, 2026-08-21). A link
  // that needs it (a file chip, Access) shows it for now without changing what is kept. ⌘⌥B
  // toggles it from a listener registered once, so the toggle reads the current value itself.
  const rail = useStoredFlag(RAIL_HIDDEN_KEY, true);
  const railHidden = rail.value;
  const setRailHidden = rail.setForNow;
  const setRailHiddenPersist = rail.set;
  const toggleRail = rail.toggle;
  // ⌘B folds the navigation to its 56px rail: its actions at the top, Settings at the foot. Kept
  // on this device. A file preview folds it for as long as it is open (#3), and puts back what was
  // there before, unless the person opened it again meanwhile.
  const nav = useStoredFlag(NAV_COLLAPSED_KEY, false);
  const navCollapsed = nav.value;
  const setNavCollapsed = nav.setForNow;
  const setNavCollapsedPersist = nav.set;
  const navBeforePreview = useRef<boolean | null>(null);
  // How wide the navigation may be in THIS window (app/navLayout.ts): a tight window shows
  // the rail and lets the reader peek the full nav; the saved preference above is for the
  // widths where there is room for either.
  const viewportWidth = useViewportWidth();
  const [navPeek, setNavPeek] = useState(false);
  const navView = navLayout({
    viewport: viewportWidth,
    preferCollapsed: navCollapsed,
    peek: navPeek,
    panelOpen: surface === "session" && !railHidden,
  });
  // An expanded queue over the page leaves the way it came: it stays drawn, sliding out, for
  // the length of the motion after it is closed (show/hide animates both ways, owner 2026-10-10).
  const [navLeaving, setNavLeaving] = useState(false);
  const overlayBefore = useRef(navView.overlay);
  useLayoutEffect(() => {
    const was = overlayBefore.current;
    overlayBefore.current = navView.overlay;
    const closed = was && !navView.overlay && navView.collapsed;
    if (!closed || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      setNavLeaving(false);
      return;
    }
    setNavLeaving(true);
    const t = window.setTimeout(() => setNavLeaving(false), 240);
    return () => window.clearTimeout(t);
  }, [navView.overlay, navView.collapsed]);
  const navAutoRef = useRef(navView.auto);
  navAutoRef.current = navView.auto;
  // Crossing into (or out of) a tight window starts from the default for that width.
  useEffect(() => setNavPeek(false), [navView.auto]);
  // Picking something in the overlaid navigation is leaving it.
  useEffect(() => {
    if (navAutoRef.current) setNavPeek(false);
  }, [sessionId, surface]);
  const toggleNav = useCallback(() => {
    navBeforePreview.current = null; // a manual toggle takes control from the artifact auto-collapse
    // In a tight window the toggle is the peek; the saved preference is not the reader's to
    // change by resizing, nor by pressing the key in a window where it cannot show.
    if (navAutoRef.current) setNavPeek((open) => !open);
    else setNavCollapsedPersist(!navCollapsed);
  }, [navCollapsed, setNavCollapsedPersist]);
  // The preview's fold, never kept. One callback for good: a new one on every toggle of the nav
  // made the panel tell it "open" again with the preview still up, folding the nav the moment the
  // person opened it (owner-hit 2026-08-21); the nav's state is read in the updater instead.
  const onArtifactPreview = useCallback(
    (open: boolean) => {
      if (open) {
        setNavCollapsed((now) => {
          if (navBeforePreview.current === null) navBeforePreview.current = now;
          return true;
        });
      } else if (navBeforePreview.current !== null) {
        setNavCollapsed(navBeforePreview.current);
        navBeforePreview.current = null;
      }
    },
    [setNavCollapsed],
  );
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Not with ⌥: ⌘⌥B is the side pane's key (below), and it used to fold or peek the queue
      // as well. Unseen while a peek sat beside the page; once a peek covered it, pressing
      // ⌘⌥B left a scrim over the window (e2e/run-view.spec.ts, 2026-10-14).
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "b") {
        e.preventDefault();
        toggleNav();
      }
      // ⌘, opens Settings, as on every platform (the account menu says so).
      if ((e.metaKey || e.ctrlKey) && e.key === ",") {
        e.preventDefault();
        setSurface("settings");
      }
      // ⌘⌥B — the side-pane toggle (`toggleSidePane`): show/hide the right rail.
      if ((e.metaKey || e.ctrlKey) && e.altKey && e.key.toLowerCase() === "b") {
        e.preventDefault();
        toggleRail();
      }
      // ⌃` — the terminal toggle keybind (`terminal.toggle`). The slash row is the
      // discoverable doorway; this is the muscle-memory one.
      if (e.ctrlKey && e.key === "`") {
        e.preventDefault();
        openTerminal();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // `openTerminal` is defined below (the handler reads it at key time, not at registration);
    // `toggleRail` is above, so it is a real dependency.
  }, [toggleNav, toggleRail]);
  // Whatever the conversation opens in the panel (a file, the folder, a design, a page) lands
  // where it can be seen: the panel opens it, and this shows the panel.
  useEffect(() => {
    const show = () => setRailHidden(false);
    window.addEventListener("reeve-open-artifact", show);
    window.addEventListener(OPEN_FILES_EVENT, show);
    window.addEventListener(OPEN_DESIGN_APP_EVENT, show);
    window.addEventListener(OPEN_BROWSER_EVENT, show);
    return () => {
      window.removeEventListener(OPEN_BROWSER_EVENT, show);
      window.removeEventListener("reeve-open-artifact", show);
      window.removeEventListener(OPEN_FILES_EVENT, show);
      window.removeEventListener(OPEN_DESIGN_APP_EVENT, show);
    };
  }, []);
  // A receipt's row asks to see what changed in a file: the panel opens on Review with it.
  const [seeRequest, setSeeRequest] = useState<{ path: string; key: number } | null>(null);
  useEffect(() => {
    const see = (event: Event) => {
      // No path: Review's list (openReview).
      const path = String((event as CustomEvent).detail?.path || "");
      setRailHidden(false);
      setSeeRequest((previous) => ({ path, key: (previous?.key ?? 0) + 1 }));
    };
    window.addEventListener(SEE_CHANGES_EVENT, see);
    return () => window.removeEventListener(SEE_CHANGES_EVENT, see);
  }, []);
  // The side panel opens only when the person opens it (owner, 2026-10-16). A turn that ended
  // having changed files used to open it on Review by itself, and a "To review" session opened
  // from the queue did too (layout step 3, 2026-10-13); both were taken out. What stays of
  // "the work is the stage" is the width: in a conversation that changed files, a panel the
  // person opens takes the room the conversation does not need (`hasWork` → `railSizing.ts`).
  const sessionHasWork = useMemo(() => taskFacts(items).files > 0, [items]);
  // `/open`: the workspace file picker's visibility. The dialog
  // searches the session's roots and opens the pick in the RightRail viewer through the
  // same `reeve-open-artifact` event the transcript's chips dispatch.
  const [filePickerOpen, setFilePickerOpen] = useState(false);
  // `/terminal` and ⌃`
  // open the RAIL'S Terminal tab (the shell), not a dock under the composer.
  // Session-scoped: switching conversations re-attaches the tab to the new session's own
  // shell server-side. A key bump toggles an already-active tab (RightRail decides).
  const [terminalRequest, setTerminalRequest] = useState(0);
  const openTerminal = useCallback(() => {
    setRailHidden(false);
    setTerminalRequest((k) => k + 1);
  }, []);
  // `/workspace`: the current
  // folder's git worktrees, listed in the sidebar. Read-only v1: the section lists and
  // "open session here" reuses the ordinary workspace switch; create/prune are deferred.
  const [worktreesOpen, setWorktreesOpen] = useState(false);
  const [worktrees, setWorktrees] = useState<WorkspaceWorktrees | null>(null);
  const [worktreesLoading, setWorktreesLoading] = useState(false);
  const loadWorktrees = async (path: string) => {
    setWorktreesLoading(true);
    try {
      setWorktrees(await getWorkspaceWorktrees(path));
    } catch {
      setWorktrees({
        is_git: false,
        root: path,
        worktrees: [],
        error: "Could not read worktrees.",
      });
    } finally {
      setWorktreesLoading(false);
    }
  };
  const toggleWorktrees = () => {
    const next = !worktreesOpen;
    setWorktreesOpen(next);
    if (next) void loadWorktrees(workspace || "");
  };
  useEffect(() => {
    if (worktreesOpen) void loadWorktrees(workspace || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace, worktreesOpen]);
  // "Open session here": the ordinary workspace switch (a fresh session bound to the
  // worktree path), so existing sessions/window machinery do the rest.
  const openWorktree = (path: string) => {
    if (!path) return;
    chooseWorkspace(path);
    toast({ title: "New session in worktree", description: path, tone: "success" });
  };
  // `/share`: there is no hosting service, so the row is driven entirely by
  // the harness's `[share] endpoint` configuration. The status is fetched per session —
  // the stored URL is what `/unshare` would revoke, so it is session state, not a pref.
  const [shareStatus, setShareStatus] = useState<SessionShareStatus | null>(null);
  useEffect(() => {
    let alive = true;
    setShareStatus(null);
    getSessionShare(sessionId)
      .then((status) => {
        if (alive) setShareStatus(status);
      })
      .catch(() => {
        if (alive) setShareStatus({ configured: false, can_revoke: false, share: null });
      });
    return () => {
      alive = false;
    };
  }, [sessionId]);
  const copyText = async (text: string): Promise<boolean> => {
    try {
      await navigator.clipboard?.writeText(text);
      return true;
    } catch {
      return false;
    }
  };
  const shareConversation = async () => {
    // An existing link is copied, never re-uploaded: sharing twice must not put two
    // copies of the transcript on the endpoint.
    const existing = shareStatus?.share?.url;
    if (existing) {
      const copied = await copyText(existing);
      toast({
        title: copied ? "Share link copied" : "Share link",
        description: existing,
        tone: copied ? "success" : "info",
      });
      return;
    }
    if (shareStatus && !shareStatus.configured) {
      toast({
        title: "Sharing needs a configured endpoint",
        description: "Add [share] endpoint to config.toml to enable it.",
        tone: "error",
      });
      return;
    }
    try {
      const res = await shareSession(sessionId);
      if (!res.ok || !res.url) {
        toast({
          title: "Could not share this session",
          description: res.error || "The share endpoint did not return a URL.",
          tone: "error",
        });
        return;
      }
      const copied = await copyText(res.url);
      toast({
        title: copied ? "Session shared — link copied" : "Session shared",
        description: res.url,
        tone: copied ? "success" : "info",
      });
      setShareStatus(await getSessionShare(sessionId).catch(() => null));
    } catch (error) {
      toast({
        title: "Could not share this session",
        description: String(error),
        tone: "error",
      });
    }
  };
  const unshareConversation = async () => {
    try {
      const res = await unshareSession(sessionId);
      if (!res.ok) {
        toast({
          title: "Could not unshare this session",
          description: res.error || "The endpoint refused the revoke.",
          tone: "error",
        });
        return;
      }
      setShareStatus(await getSessionShare(sessionId).catch(() => null));
      toast({ title: "Session unshared", tone: "success" });
    } catch (error) {
      toast({ title: "Could not unshare this session", description: String(error), tone: "error" });
    }
  };
  const openFileInViewer = (path: string) => {
    if (!path) return;
    // Forward slashes for the viewer: the tab label and the kind check split on "/",
    // and the server resolves either separator on Windows.
    window.dispatchEvent(
      new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: path.replace(/\\/g, "/") } }),
    );
  };
  // A pending composer prefill (text + attachments) pushed from the session start panel.
  const [composerPrefill, setComposerPrefill] = useState<{ text: string; attachments?: Attachment[]; nonce: number; keepText?: boolean }>();

  // The prompts of the ACTIVE session that the server holds parked: shown above the composer
  // when no live card is up, which is the case after a reload, and for a prompt a run raised
  // while nobody had its conversation open.
  const [sessionInbox, setSessionInbox] = useState<InboxItem[]>([]);
  const readSessionInbox = () =>
    getInbox(sessionId, "pending")
      .then(setSessionInbox)
      .catch(() => setSessionInbox([]));
  // Answered: read again, and the session list too, so its "Needs you" mark goes at once.
  const resolveSessionInbox = async (id: string, resolution: string) => {
    await resolveInboxItem(id, resolution);
    void readSessionInbox();
    refreshSessions();
  };
  // The desktop tray's "Settings" item dispatches this on the window; a loaded-skill card
  // (cards/SkillBlock.tsx) dispatches it with `detail.section` to deep-link to Skills.
  useEffect(() => {
    const open = (event: Event) => {
      const section = (event as CustomEvent<{ section?: string }>).detail?.section;
      openSettings(section === "skills" ? "skills" : "general");
    };
    window.addEventListener("reeve:open-settings", open);
    return () => window.removeEventListener("reeve:open-settings", open);
  }, []);

  // Settings' "Run setup again".
  useEffect(() => {
    const open = () => setOnboarding(true);
    window.addEventListener("reeve:open-onboarding", open);
    return () => window.removeEventListener("reeve:open-onboarding", open);
  }, []);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  // The scroller as STATE too, set by a callback ref: every effect that attaches to it keys
  // on the element itself. Keyed on session/surface, they ran while the scroller was not
  // mounted yet (its ref still null) and never again once it was — a long session opened
  // at its TOP, the resize pin and the wheel-up listener were never attached, and the
  // view still believed it was following (no "Back to bottom").
  const [scrollNode, setScrollNode] = useState<HTMLDivElement | null>(null);
  const attachScroller = useCallback((el: HTMLDivElement | null) => {
    scrollRef.current = el;
    setScrollNode(el);
  }, []);
  // A message that goes as soon as the next session connects: an automation's "Run now", or a first
  // message whose folder was made as it was sent. `notice` is said after it ("Temporary
  // folder created · git initialized").
  const pendingPromptRef = useRef<{ text: string; attachments?: Attachment[]; skill?: string; mentions?: string[]; model?: string; notice?: string } | null>(null);
  // A "Run now" under way, finished on the server after its first turn.
  const activeRunRef = useRef<{ taskId: string; runId: string; sessionId: string } | null>(null);

  // Fetch every session for the sidebar's list.
  const refreshSessions = useCallback(() => {
    getSessions()
      .then((list) => {
        setSessions(list);
        setSessionsState("ready");
      })
      .catch(() => setSessionsState((now) => (now === "ready" ? now : "error")));
  }, []);

  // The start: the engine is asked whether it is up until it answers (the desktop shell starts it
  // alongside the window, so the first second or two may go unanswered), then the last session
  // is opened.
  const [booting, setBooting] = useState(true);
  const [onboarding, setOnboarding] = useState(false);
  // Latched: the start is not over until the restored session is actually CONNECTED (not just
  // until `booting` clears), so an early click can't land on a session that's still settling.
  const [uiReady, setUiReady] = useState(false);
  // What the start knows, for the boot pane: when it began, how long the engine took to answer,
  // which conversation is being opened, what the shell's supervisor last said, and why it failed.
  const starting = booting || !uiReady;
  const bootStartedAt = useRef(Date.now());
  const [engineMs, setEngineMs] = useState<number | null>(null);
  const [bootTitle, setBootTitle] = useState<string | null>(null);
  const [bootSidecar, setBootSidecar] = useState<SidecarState | null>(null);
  const [bootFailure, setBootFailure] = useState<BootFailure | null>(null);
  const [bootTry, setBootTry] = useState(0);
  // The start just ended: the boot pane fades out while the conversation arrives under it.
  const [bootLeaving, setBootLeaving] = useState(false);
  const wasStarting = useRef(true);
  useEffect(() => {
    if (wasStarting.current && !starting) {
      wasStarting.current = false;
      provisionalSessionRef.current = null;
      setBootLeaving(true);
      const t = window.setTimeout(() => setBootLeaving(false), 220);
      return () => window.clearTimeout(t);
    }
  }, [starting]);

  // `/v1/connectors` publishes each connector's logo, brand color, AND its tools. Index it so
  // a connector tool row (`gmail_get_message`) can wear its connector's mark and fold with its
  // siblings instead of reading as an anonymous "Tool". Global, refetched when a session opens
  // so a connector connected mid-run shows up without an app restart.
  const [connectorToolIndex, setConnectorToolIndex] =
    useState<ConnectorToolIndex>(EMPTY_CONNECTOR_TOOLS);
  useEffect(() => {
    let alive = true;
    getConnectors()
      .then((list) => {
        if (alive) setConnectorToolIndex(buildConnectorToolIndex(list));
      })
      .catch(() => {
        /* branding is cosmetic: a failed list leaves every row on its old rendering */
      });
    return () => {
      alive = false;
    };
  }, [sessionId]);

  // The `task` agent definitions (`GET /v1/subagents`, ledger 2.2/2.10/5.10): the task card
  // draws the agent's own `color` (P2), and the composer's @mention menu + agent picker
  // (P4) read the SAME list — `mode`/`hidden`/`default` included, so the two surfaces can
  // never disagree about which agents exist. Refetched when a session opens so a config
  // edit lands without an app restart; a failed list is cosmetic — every agent still
  // paints from the palette hash and the mention menu simply does not open.
  const [agentColors, setAgentColors] = useState<AgentColorIndex>(EMPTY_AGENT_COLORS);
  useEffect(() => {
    let alive = true;
    getSubagents()
      .then((list) => {
        if (!alive) return;
        setAgentColors(buildAgentColors(list));
        setSubagents(list);
      })
      .catch(() => {
        /* cosmetic: the palette hash is the fallback */
      });
    return () => {
      alive = false;
    };
  }, [sessionId]);

  // The session's delegated runs, for the subagents roster above the composer
  // (components/session/SubagentsDock.tsx). The same rows the transcript's delegation card
  // draws, in the same order: `task` spawn calls and background workflow runs,
  // each already carrying the manager's live
  // frames and the settle frame's timing, so the roster and the card can never disagree.
  const delegated = useMemo(
    () =>
      delegationNodes(
        items.filter((it): it is ToolItem => it.kind === "tool" && isDelegationCall(it)),
        agentColors,
      ),
    [items, agentColors],
  );
  // Which children still hold work in their own copy: the server's fact, asked
  // when a child settles and when one was handed back, and drawn on that child's row.
  const keptWorktrees = useKeptWorktrees(
    sessionId,
    delegated.length,
    delegated.filter((node) => node.outcome !== "running").length,
  );
  // How each background child reported back (its wake marker): in a reopened session that is
  // the one record of a child that failed after its spawn call returned.
  const childReports = useMemo(() => {
    const reports = new Map<string, "completed" | "failed">();
    for (const it of items) {
      const facts = it.kind === "notice" ? it.wakeFacts : undefined;
      if (facts?.kind === "task" && facts.sessionId) reports.set(facts.sessionId, facts.failed ? "failed" : "completed");
    }
    return reports;
  }, [items]);
  const subagentNodes = useMemo(
    () => withKept(withReports(delegated, childReports), keptWorktrees),
    [delegated, childReports, keptWorktrees],
  );

  // Hydrate the composer's session facts (model / mode) from the session
  // record the list already holds. The WS `ready` frame carries the same facts a beat
  // later; reading the record here keeps a boot, resume or sidebar pick from showing —
  // and sending under — the app defaults in the gap. Never applied to the session that
  // is already active: a pick the user just made has not reached the record yet and
  // must win over this hydration (the `ready` handler holds the same line).
  const hydrateSessionState = (info: SessionInfo | null | undefined) => {
    if (!info || info.session_id === sessionId) return;
    const state = sessionComposerState(info);
    if (state.model) setModel(state.model);
    if (state.mode) setMode(state.mode);
  };

  // On boot, reopen the last thing the user had: the most recent conversation (its folder
  // and transcript). With nothing to resume the app opens on a new session, which starts
  // on a scratch folder until a folder is picked.
  const resumeLast = async () => {
    try {
      // An automation run's conversation (`__run__…`) is never the one to reopen.
      const listed = (await getSessions()).filter((s) => s.session_id && !s.session_id.startsWith("__"));
      setSessions(listed);
      setSessionsState("ready");
      const when = (s: SessionInfo) => Date.parse(s.updated_at || "") || Number(s.updated_at) || 0;
      const last = [...listed].sort((a, b) => when(b) - when(a))[0];
      if (last) {
        // Said in the bar while its transcript loads: the person knows where they are going.
        setBootTitle(last.title || null);
        if (last.workspace) {
          setWorkspace(last.workspace);
        }
        // The resumed session's own SSH binding: the topbar/composer readout and the
        // setup row must know where the workspace actually lives.
        setActiveEnvironment(last.environment ?? null);
        setRemoteWorkspace(sessionRemoteWorkspace(last));
        setEnvironmentError("");
        // The resumed session's own model and mode — the same facts the sidebar row
        // shows. The `ready` frame will confirm them; this is what the composer paints
        // in the meantime.
        hydrateSessionState(last);
        await loadSession(last.session_id);
        setSessionId(last.session_id);
      }
    } catch {
      // Nothing to reopen: the app opens on a new session.
    }
  };

  useEffect(() => {
    let cancelled = false;
    const attempt = () => {
      getHealth()
        .then(async (h) => {
          if (cancelled) return;
          // How long this computer's engine takes to answer: the boot pane's usual next time.
          const took = Date.now() - bootStartedAt.current;
          setEngineMs(took);
          if (bootTry === 0) rememberBootTime(took);
          setModel(h.model);
          // The desktop app's first-run setup, until it is finished or put aside.
          if (isDesktop()) {
            getSettings()
              .then((settings) => !cancelled && !settings.onboarded && setOnboarding(true))
              .catch(() => {});
          }
          // The session to open is settled before `booting` clears, since clearing it lets the
          // socket connect: the placeholder session would connect first, and the server would make
          // a scratch folder for it before the real session took its place.
          await resumeLast();
          // Again, now that the engine answers: the read made as the page loaded races the start
          // and loses quietly, which left "Loading models…" until Settings was visited (owner-hit
          // 2026-07-23).
          loadSettings();
          if (!cancelled) setBooting(false);
        })
        .catch(() => {
          if (cancelled) return;
          const waited = Date.now() - bootStartedAt.current;
          if (waited >= GIVE_UP_MS) {
            // The engine never answered: the boot pane says so, with what can be done about it,
            // instead of opening the app on a connection that is not there.
            setBootFailure({ reason: "The engine did not answer.", waitedMs: waited });
          } else {
            setTimeout(attempt, 500);
          }
        });
    };
    attempt();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bootTry]);

  // The shell's supervisor, while Reeve starts: a crash and its restart are said in the boot pane,
  // and a stop ends the wait at once with its reason (it will not answer however long we wait).
  useEffect(() => {
    if (!isDesktop()) return;
    let live = true;
    sidecarStatus()
      .then((s) => live && s && setBootSidecar(s))
      .catch(() => {});
    const off = onSidecarStatus((s) => live && setBootSidecar(s));
    return () => {
      live = false;
      off();
    };
  }, []);
  useEffect(() => {
    if (!starting || bootFailure || bootSidecar?.state !== "stopped") return;
    setBootFailure({
      reason: bootSidecar.reason || "The engine stopped.",
      waitedMs: Date.now() - bootStartedAt.current,
    });
  }, [starting, bootFailure, bootSidecar]);
  const retryBoot = () => {
    bootStartedAt.current = Date.now();
    setBootFailure(null);
    // A stopped engine is started again by the shell; a browser has only the polling to redo.
    if (bootSidecar?.state === "stopped") void restartSidecar();
    setBootTry((n) => n + 1);
  };

  // The app is shown once the start is over and the session it opened is connected, and stays shown:
  // a reconnect later never brings the start screen back. If the session never says it connected,
  // the app is shown anyway after a moment; the engine has answered by then, so a connect that is
  // working takes well under a second.
  useEffect(() => {
    if (uiReady || booting) return;
    if (connected) return setUiReady(true);
    const timer = setTimeout(() => setUiReady(true), 1500);
    return () => clearTimeout(timer);
  }, [uiReady, booting, connected]);

  // The models the composer offers, their names and windows, and whether one can answer.
  const loadSettings = () =>
    getSettings()
      .then((settings) => {
        setModels(settings.models || []);
        setModelLabels(settings.model_labels || {});
        setModelContextWindows(settings.model_context_windows || {});
        setContextBar(settings.context_bar !== false);
        setModelReady(settings.model_ready);
      })
      .catch(() => {});

  // The composer's "No model connected" goes to Settings ▸ Models.
  const openModelSetup = () => openSettings("models");

  // Read again on leaving Settings, where models may have been added or removed.
  useEffect(() => {
    if (surface !== "settings") loadSettings();
  }, [surface]);

  useEffect(() => {
    refreshSessions();
    loadSettings();
  }, [refreshSessions]);

  // The list is read every 5s, so its marks stay current and sessions started elsewhere (background
  // work, a message, an automation) show up without a refresh.
  useEffect(() => {
    const timer = setInterval(refreshSessions, 5000);
    return () => clearInterval(timer);
  }, [refreshSessions]);

  // -- the conversation's socket ----------------------------------------------------------
  // The pool keeps one socket per open conversation, so this effect only has to make sure
  // the one on screen HAS one: a healthy socket is kept (switching away and back never
  // reconnects), and a changed nonce replaces it. Events for every conversation go
  // through the same handler; `host` is how it reaches this component, and it is read through
  // a ref so a socket opened a minute ago still calls today's closures.
  const hostRef = useRef<SessionEventHost | null>(null);
  const loadSessionRef = useRef<(id: string, opts?: { quiet?: boolean }) => Promise<void>>(async () => {});
  const factsApplier = useRef<(target: LiveSession, kind: "ready" | "patch" | "activate") => void>(
    () => {},
  );
  useEffect(() => {
    if (booting) return; // wait until boot/resume settles the session before connecting
    // History first, socket second: events that stream in while the transcript is still being
    // read would be overwritten by it (or duplicate what it already holds).
    if (hydrating) return;
    liveRegistry.ensure(sessionId).connect({
      params: { workspace: workspace || "" },
      nonce: connectNonce,
      onEvent: (target, ev) => {
        if (hostRef.current) handleSessionEvent(target, ev, hostRef.current);
      },
      onOpen: (target, reconnected) => {
        // The jobs roster as it stands: the frames that carry it went out to
        // whoever was connected, and this socket was not. Read once per open, for every
        // conversation — a background one may be the one with a build running.
        target.seedJobs(getSessionJobs(target.id));
        // Auto-send the pending message once the session connects ("Run now" prompts and
        // Deferred first send). Only the conversation on screen may claim it: a
        // background socket reconnecting must not take a message meant for another one.
        const p = pendingPromptRef.current;
        if (p && sessionIdRef.current === target.id) {
          pendingPromptRef.current = null;
          const shown = p.skill ? `/${p.skill}${p.text ? ` ${p.text}` : ""}` : p.text;
          target.setItems((prev) => [
            ...prev,
            { kind: "user", text: shown, attachments: p.attachments, ts: Date.now() / 1000 },
            ...(p.notice
              ? [{ kind: "notice", tone: "info", text: p.notice } as Item]
              : []),
          ]);
          target.userMessage(p.text, p.attachments, p.model, p.skill, p.mentions);
        }
        // A reconnect means events were missed in the gap: the server's transcript is the
        // truth about what happened meanwhile. Only the conversation on screen is re-read now;
        // the others wait until they are opened (a restarted server drops every socket at once).
        if (reconnected) {
          if (sessionIdRef.current === target.id) void loadSession(target.id, { quiet: true });
          else target.stale = true;
        }
      },
    });
    // Not on `workspace`: every real change of folder (picking one, opening or starting a
    // session) comes with a new `sessionId`, which reconnects. The one change of folder alone is
    // `ready` taking up the scratch folder the server made, and reconnecting on it tore the
    // socket down right after it opened and lost the first message (the "send twice" bug). The
    // server makes that folder from the session id, so nothing is missed by not reconnecting.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [booting, sessionId, connectNonce, hydrating]);

  // The reader is now looking at this conversation: it is the pool's most recent, and a
  // conversation they walked away from without ever saying anything goes (its socket would
  // otherwise linger, holding a server-side scratch folder for nothing).
  const lastViewedRef = useRef<string>("");
  useEffect(() => {
    const previous = lastViewedRef.current;
    lastViewedRef.current = sessionId;
    liveRegistry.activate(sessionId);
    if (previous && previous !== sessionId) {
      const left = liveRegistry.get(previous);
      if (left && left.draft && left.current.items.length === 0 && !left.current.running) {
        liveRegistry.drop(previous);
      }
    }
    // The facts the server told this conversation while it was elsewhere: apply them now.
    const target = liveRegistry.get(sessionId);
    if (target?.facts.ready) factsApplier.current(target, "activate");
    if (target?.stale) {
      target.stale = false;
      void loadSessionRef.current(sessionId, { quiet: true });
    }
  }, [sessionId]);

  // Following the stream: the view keeps to the bottom only while the reader is there, so
  // scrolling up to read in a running turn stays put. `atBottomRef` is what is so now, read on
  // every scroll without a render; `following` is its copy in state, for "Back to bottom". A
  // smooth scroll the app starts fires scroll events too, and while one runs (`autoScrollingRef`)
  // they are not the reader leaving, or each step of the stream would end its own follow. It only
  // moves down, so the view moving up while it runs can only be the reader.
  const atBottomRef = useRef(true);
  const autoScrollingRef = useRef(false);
  const lastScrollTopRef = useRef(0);
  /** The scroll box's size as last seen by the scroll handler (see `handleScroll`). */
  const lastLayoutRef = useRef({ height: 0, client: 0 });
  // Where the reader was in a conversation they left: the line they were on, or "at the
  // bottom, following". The transcript itself needs no snapshot any more — every open
  // conversation keeps its own live state (live/liveSession.ts) — so coming back is a
  // re-subscribe plus this scroll position.
  const scrollMemory = useRef(new Map<string, { following: boolean; scrollTop: number }>());
  const pendingScrollRef = useRef<number | null>(null);
  const rememberSession = (id: string) => {
    if (!id || items.length === 0) return;
    const map = scrollMemory.current;
    map.delete(id);
    map.set(id, { following: atBottomRef.current, scrollTop: lastScrollTopRef.current });
    while (map.size > SCROLL_MEMORY) map.delete(map.keys().next().value as string);
  };
  const [following, setFollowing] = useState(true);
  // Where this session compacts (server truth: the model's window under the context cap,
  // minus the answer's reserve). The gauge's denominator: the catalogue window alone read
  // "381k of 1M" in a session about to compact at 380k.
  const [compactAt, setCompactAt] = useState<number | undefined>(undefined);
  // "Back to bottom": follow again, and go there smoothly.
  const followLatest = () => {
    atBottomRef.current = true;
    setFollowing(true);
    const box = scrollRef.current;
    if (!box) return;
    autoScrollingRef.current = true;
    box.scrollTo({ top: box.scrollHeight, behavior: "smooth" });
  };
  const handleScroll = () => {
    const box = scrollRef.current;
    // Hidden behind another page, the view measures nothing; reading it would lose the place.
    if (!box || box.clientHeight === 0) return;
    const top = box.scrollTop;
    const atBottom = box.scrollHeight - top - box.clientHeight < 48;
    if (autoScrollingRef.current) {
      // The app's own scroll: over when it lands, or when the view moves up under it.
      if (atBottom || top < lastScrollTopRef.current) autoScrollingRef.current = false;
      else {
        lastScrollTopRef.current = top;
        return;
      }
    }
    const el = box;
    // A gap to the bottom that LAYOUT opened is not the reader leaving: content growing under
    // a follower, or the view shrinking (the To-dos dock arriving), fires a scroll event that
    // can land before the resize pin closes the gap — read as "the user scrolled away", it left
    // the status line under the composer with nobody touching the page. So the follow stops
    // when the reader moved up, or when the view moved while the layout stood still.
    const movedUp = top < lastScrollTopRef.current - 1;
    const layoutMoved =
      el.scrollHeight !== lastLayoutRef.current.height ||
      el.clientHeight !== lastLayoutRef.current.client;
    lastScrollTopRef.current = top;
    lastLayoutRef.current = { height: el.scrollHeight, client: el.clientHeight };
    if (atBottom || movedUp || !layoutMoved) {
      atBottomRef.current = atBottom;
      setFollowing(atBottom);
    }
  };
  // Another session starts at its bottom, following, or at the line the reader left it on
  // (`scrollMemory`); it never takes the last one's place. Before the pin below: a switch and its
  // items can land in one render, and the pin must see the new state.
  useEffect(() => {
    const restore = pendingScrollRef.current;
    atBottomRef.current = restore === null;
    setFollowing(restore === null);
  }, [sessionId]);
  useLayoutEffect(() => {
    const restore = pendingScrollRef.current;
    const el = scrollRef.current;
    if (restore === null || !el) return;
    pendingScrollRef.current = null;
    el.scrollTop = restore;
    lastScrollTopRef.current = restore;
  }, [sessionId, items]);
  // Back from another page: the view kept its content, and its place is put back — the
  // bottom when it was following, the reader's line when it was not.
  useLayoutEffect(() => {
    if (surface !== "session") return;
    const el = scrollRef.current;
    if (!el) return;
    if (atBottomRef.current) el.scrollTop = el.scrollHeight;
    else el.scrollTop = lastScrollTopRef.current;
  }, [surface]);
  // A wheel-up over the transcript is the user saying "I am reading something else now", and
  // it must win even while the viewport is still within the bottom threshold — otherwise a
  // growing answer keeps yanking the text away from someone who stopped following ten pixels
  // from the end, which reads as the app fighting the mouse. Position alone cannot tell that
  // apart from "already at the bottom, keep following", so the gesture is what disengages.
  useEffect(() => {
    const el = scrollNode;
    if (!el) return;
    const onWheel = (event: WheelEvent) => {
      if (event.deltaY >= 0) return;
      atBottomRef.current = false;
      autoScrollingRef.current = false;
      setFollowing(false);
    };
    el.addEventListener("wheel", onWheel, { passive: true });
    return () => el.removeEventListener("wheel", onWheel);
  }, [scrollNode]);

  // A change of mode is said in the transcript (modeNoticeStep), inside the running turn or as a
  // line of its own between turns. Nothing is said until `ready` has said whose mode it is.
  useEffect(() => {
    const { item, state } = modeNoticeStep(modeNoticeState.current, mode, sessionId, modeConfirmedFor, running);
    modeNoticeState.current = state;
    if (item) setItems((now) => withModeNotice(now, item));
  }, [mode, sessionId, modeConfirmedFor, running]);
  // Following means PINNED to the bottom, whatever made the content taller — and most of
  // what grows it during a turn is not a new item: the status line's live thought, a call
  // being written, a command's streamed output, a row opening itself, the To-dos dock
  // shrinking the viewport. Only `items`/`streaming` used to re-scroll, and that scroll was
  // a smooth one aimed at the height at its start, so it landed short while the content
  // kept growing, inside the 48px "at bottom" slack: the status line sat half under the
  // composer (owner, 2026-09-28). The pin is instant and re-runs on every size change.
  const pinToBottom = () => {
    const el = scrollRef.current;
    if (!el || !atBottomRef.current || el.clientHeight === 0) return;
    // An empty conversation shows the start page, which reads from the top. On a short window
    // it is taller than the view, and pinning to its bottom cut the heading off under the bar.
    if (liveRef.current.items.length === 0 && liveRef.current.streamEmpty) {
      el.scrollTop = 0;
      return;
    }
    autoScrollingRef.current = false;
    el.scrollTop = el.scrollHeight;
    lastScrollTopRef.current = el.scrollTop;
  };
  useEffect(() => {
    pinToBottom();
    // Text growing inside the live step is caught by the ResizeObserver below, not here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, streamOpen]);
  useEffect(() => {
    const el = scrollNode;
    if (!el || typeof ResizeObserver === "undefined") return;
    const resize = new ResizeObserver(() => pinToBottom());
    const watch = () => {
      resize.disconnect();
      resize.observe(el);
      for (const child of Array.from(el.children)) resize.observe(child);
    };
    watch();
    const children = new MutationObserver(watch);
    children.observe(el, { childList: true });
    return () => {
      resize.disconnect();
      children.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrollNode]);

  // The open session's held prompts: read on opening it and after each turn, and every 4s, so a
  // question raised while nobody was looking comes up.
  useEffect(() => {
    if (surface !== "session") return;
    void readSessionInbox();
    const timer = setInterval(readSessionInbox, 4000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [surface, sessionId, browserRefreshKey]);

  // The conversation a message was last sent in from this window: the top bar's crumb arrives
  // with a motion for a first message sent, not for history that loaded.
  const sentIn = useRef<string | null>(null);
  const send = (
    text: string,
    attachments?: Attachment[],
    skill?: string,
    mentions?: string[],
    command?: SentCommand,
  ) => {
    // The line comments go with the message, saying what to change and where, and are cleared once
    // sent. A skill's bubble is what the person typed, "/name rest", which has to match the
    // server's `display` sidecar for `turn_start` to know the bubble as this message.
    const notes = reviewComments.map((c) => `The user made the following comment regarding line ${c.line} of ${c.file}: ${c.text}`).join("\n");
    const commented = notes ? `${text}${text ? "\n\n" : ""}${notes}` : text;
    // Where the person left a visual they moved: the model reads it with the message.
    const withComments = withVisualStates(commented, takeVisualStates());
    const shown = skill ? `/${skill}${withComments ? ` ${withComments}` : ""}` : withComments;
    sentIn.current = sessionIdRef.current;
    setItems((p) => [...p, { kind: "user", text: shown, attachments, ts: Date.now() / 1000, ...(command ? { command } : {}) }]);
    // The clock starts as the message leaves (owner, 2026-09-04), not when `turn_start` comes
    // back; `turn_start` confirms it and `input_rejected` takes it back.
    if (connected) setRunning(true);
    // The model on screen goes with the message: it is the turn's model.
    live.userMessage(withComments, attachments, model, skill, mentions, undefined, command);
    if (reviewComments.length) setReviewComments([]);
    // Sending follows the stream again, wherever the reader had scrolled.
    followLatest();
  };
  // Says what the user meant while the agent was busy. Cleared when the message it holds is
  // actually sent (see the drain effect), so an empty banner and an empty queue are one fact.
  const queueMessage = (text: string, attachments?: Attachment[], skill?: string, mentions?: string[], command?: SentCommand) =>
    setQueuedPrompts((cur) => [...cur, { id: `q${Date.now().toString(36)}-${cur.length}`, text, attachments, skill, mentions, ...(command ? { command } : {}) }]);
  // Into the running turn, at its next step. No bubble until the server's `steering_queued`, so a
  // refused one leaves none.
  const steerMessage = (text: string) => live.userMessage(text, [], model, undefined, undefined, true);
  // `send` closes over this render's state, so the drain effect below reads it through a ref
  // rather than depending on it — a dependency would re-fire the effect on every keystroke.
  const sendRef = useRef(send);
  useEffect(() => {
    sendRef.current = send;
  });
  // A visual's form sends its answers as the person's next message: straight away
  // when the session is idle, queued behind the running turn otherwise — what the composer
  // does with a message typed mid-turn.
  const promptSink = useRef<(text: string) => void>(() => {});
  promptSink.current = (text: string) => (running || compacting ? queueMessage(text) : sendRef.current(text));
  useEffect(() => {
    const onPrompt = (event: Event) => {
      const text = String((event as CustomEvent).detail?.text || "").trim();
      if (text) promptSink.current(text);
    };
    window.addEventListener(SEND_PROMPT_EVENT, onPrompt);
    return () => window.removeEventListener(SEND_PROMPT_EVENT, onPrompt);
  }, []);
  // Answering a live prompt answers its held copy on the server too, but the copy read here stays
  // pending until the next read, long enough for the card to ask the same thing again right after
  // it was answered (tester, 2026-07-12: a Slack send "asked twice"). So the copy goes here at
  // once; the next read brings back anything really still pending.
  const dropSessionInbox = (kind: string) => setSessionInbox((cur) => cur.filter((it) => it.kind !== kind));
  const approve = (decision: ApprovalDecision, edited?: Record<string, unknown>) => {
    setItems((p) => resolveLastApproval(p, decision));
    dropSessionInbox("approval");
    live.approve(decision, edited);
  };
  const respondPlan = (approved: boolean, mode?: string, feedback?: string) => {
    setItems((p) => resolveLastPlan(p, approved ? "approved" : "rejected"));
    dropSessionInbox("plan");
    live.respondPlan(approved, mode, feedback);
    if (approved) {
      // Approving leaves the Plan overlay; the server clears it too (`plan_changed`).
      setPlan(false);
      // "Run without asking" passes `danger-full-access`; plain Approve passes no mode,
      // so the session keeps the preset it is already in.
      if (mode) {
        markComposerPick("mode"); // the server flips the live engine to this mode
        setMode(mode);
      }
    }
  };
  const respondTool = (approved: boolean) => {
    setItems((p) => resolveLastToolReq(p, approved ? "installed" : "skipped"));
    dropSessionInbox("tool");
    live.respondTool(approved);
  };
  const answerQuestion = (answer: string) => {
    setItems((p) => resolveLastQuestion(p, answer));
    dropSessionInbox("question");
    live.respondQuestion(answer);
  };
  // Record that the user has taken a session fact into their own hands. Hydration (the
  // record read at load, the `ready` frame) checks this and leaves their choice alone.
  const markComposerPick = (field: "model" | "mode" | "thinking") => {
    const cur = composerPickRef.current;
    composerPickRef.current =
      cur.session === sessionId
        ? { ...cur, [field]: true }
        : { session: sessionId, [field]: true };
  };
  const prefillComposer = (text: string, attachments?: Attachment[]) =>
    setComposerPrefill((p) => ({ text, attachments, nonce: (p?.nonce ?? 0) + 1 }));
  // Review's "Ask for a change" on one request: its words go into the composer, for the person
  // to say what to change and send.
  useEffect(() => {
    const ask = (event: Event) => {
      const text = (event as CustomEvent<{ text?: string }>).detail?.text ?? "";
      const draft = askChangeDraft(text);
      if (draft) setComposerPrefill((p) => ({ text: draft, nonce: (p?.nonce ?? 0) + 1 }));
    };
    window.addEventListener(ASK_CHANGE_EVENT, ask);
    return () => window.removeEventListener(ASK_CHANGE_EVENT, ask);
  }, []);
  // A design preview's pointer, pinned comments or retyped text: added to the message
  // being written, never replacing it.
  useEffect(() => {
    const attach = (event: Event) => {
      const detail = (event as CustomEvent<{ attachments?: Attachment[]; text?: string }>).detail;
      if (!detail?.attachments?.length && !detail?.text) return;
      setComposerPrefill((p) => ({
        text: detail.text ?? "",
        attachments: detail.attachments,
        keepText: true,
        nonce: (p?.nonce ?? 0) + 1,
      }));
    };
    window.addEventListener(DESIGN_ATTACH_EVENT, attach);
    return () => window.removeEventListener(DESIGN_ATTACH_EVENT, attach);
  }, []);
  const interrupt = () => live.interrupt();
  const retry = () => {
    // Optimistic running: turn_start confirms; a rejected retry still ends in turn_done. A
    // retry that could not be sent (no socket right now) must not leave the header running.
    setRunning(true);
    if (!live.retry()) setRunning(false);
  };
  const changeMode = (m: string) => {
    markComposerPick("mode");
    setMode(m);
    live.setMode(m);
  };
  // Plan is an overlay, not a preset: it rides its own frame and its own state. Optimistic
  // (the switch must move under the finger); `plan_changed` confirms or corrects it.
  const changePlan = (on: boolean) => {
    setPlan(on);
    live.setPlan(on);
  };
  const changeModel = (m: string) => {
    if (running) return; // the server refuses mid-turn rebinds — don't let the header lie
    markComposerPick("model");
    setModel(m);
    live.setModel(m);
  };
  const changeThinking = (level: string) => {
    if (running) return; // same guard as the model: no rebind mid-turn
    markComposerPick("thinking");
    setThinking(level);
    live.setThinking(level);
  };

  // A new conversation starts fresh: no folder, so the server provisions a new scratch
  // folder for the new session id, and the strip under the composer offers a real one.
  const startNewSession = () => {
    setSurface("session"); // return to the conversation view if we were on a sub-view
    setWorkspace(null);
    setTempWorkspace(false);
    setActiveEnvironment(null);
    setRemoteWorkspace(null);
    setEnvironmentError("");
    openDraft();
  };
  // The setup row's folder chip — bind the draft to a folder before the first
  // message. A fresh id re-triggers the connection effect with the folder attached.
  const pickDraftFolder = (path: string) => {
    setWorkspace(path);
    setTempWorkspace(false);
    reopenDraft();
  };
  // "Just chat": bind the draft to its per-conversation temporary folder
  // now (idempotent, git-ready), so the composer can send immediately. The path stays
  // keyed to this draft's session id.
  const pickDraftTemp = async (): Promise<{ ok: boolean; error?: string }> => {
    const res = await createTempWorkspace(sessionId, true);
    if (!res.ok || !res.path) {
      return { ok: false, error: res.error || "Could not create a temporary folder." };
    }
    setWorkspace(res.path);
    setTempWorkspace(true);
    // The draft's record says so too: it was written at connect, before this folder existed, and
    // coming back to the conversation re-applies it.
    live.facts.ready = { ...(live.facts.ready ?? {}), workspace: res.path, temp_workspace: true };
    return { ok: true };
  };
  // A scheduled run that starts is said for 5s in the corner, from the app-wide event
  // stream, with View run to open its conversation. Not for "Run now": the person is watching it.
  const [runToast, setRunToast] = useState<{ title: string; sessionId: string; workspace: string; time: string } | null>(null);
  // The children announced on the app-wide stream (step C). They are NOT
  // top-level sessions — the sidebar keeps its roots-only list — but the child-session
  // view needs them for Prev/Next and its busy/idle poll, so the app keeps the sibling
  // state here. Keyed by child session id; insertion order is spawn order.
  const [childSessions, setChildSessions] = useState<Record<string, ChildSession>>({});
  // Parked permission asks (P1), keyed by request_id and insertion-ordered: a child's
  // tool ask or a `task` spawn ask the dock can answer. The app-wide stream carries
  // both `permission_asked` (add) and `permission_replied` (remove) — the session
  // stream carries the same frames for the viewed ancestor, so the map's keying is what
  // keeps a second delivery from drawing two docks.
  const [permissionRequests, setPermissionRequests] = useState<
    Record<string, PermissionRequestInfo>
  >({});
  const [permissionResponding, setPermissionResponding] = useState(false);
  useEffect(() => {
    const stop = connectEvents((msg) => {
      // A step of some session was folded into the Metrics store: tell the page if it is open.
      if (msg.type === "metrics_changed") {
        window.dispatchEvent(new CustomEvent(METRICS_CHANGED));
        return;
      }
      // Automation toast — unchanged.
      if (msg.type === "automation_run_started") {
        const d = (msg.data ?? {}) as Record<string, string>;
        setRunToast({
          title: d.task_title || "Automation",
          sessionId: d.session_id || "",
          workspace: d.workspace || "",
          time: new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
        });
        announceAutomationsChanged(); // the sidebar's count of new runs is now stale
        return;
      }
      // A permission ask (P1): a child tool call or a `task` spawn parked on the
      // manager's broker. Normalize the wire shape once here so every reader below
      // (dock, toast, navigation) speaks the same camelCase object.
      if (msg.type === "permission_asked") {
        const d = (msg.data ?? {}) as Record<string, any>;
        if (typeof d.request_id !== "string" || !d.request_id) return;
        setPermissionRequests((p) =>
          p[d.request_id]
            ? p
            : {
                ...p,
                [d.request_id]: {
                  requestId: d.request_id,
                  sessionId: String(d.session_id || ""),
                  parentSession: String(d.parent_session || ""),
                  permission: String(d.permission || ""),
                  patterns: Array.isArray(d.patterns) ? d.patterns.map(String) : [],
                  metadata:
                    d.metadata && typeof d.metadata === "object" ? d.metadata : {},
                  always: Array.isArray(d.always) ? d.always.map(String) : [],
                  tool: d.tool && typeof d.tool === "object" ? d.tool : null,
                } satisfies PermissionRequestInfo,
              },
        );
        return;
      }
      // Answered elsewhere (the dock in another window, an API client, a Stop that
      // cancelled the child): the request is gone wherever it was drawn.
      if (msg.type === "permission_replied") {
        const d = (msg.data ?? {}) as Record<string, any>;
        if (typeof d.request_id !== "string") return;
        setPermissionRequests((p) => {
          if (!p[d.request_id]) return p;
          const next = { ...p };
          delete next[d.request_id];
          return next;
        });
        return;
      }
      // A new subagent child exists (the manager announces it before its first turn).
      if (msg.type === "session_created") {
        const d = (msg.data ?? {}) as Record<string, any>;
        if (d.kind !== "subagent" || typeof d.session_id !== "string" || !d.session_id) return;
        setChildSessions((p) =>
          p[d.session_id]
            ? p
            : {
                ...p,
                [d.session_id]: {
                  sessionId: d.session_id,
                  parentSession: String(d.parent_session || ""),
                  title: String(d.title || ""),
                  agent: String(d.agent || ""),
                  // A spawn is announced as it starts its turn; `session_status` will
                  // correct this to idle when the child finishes.
                  status: "busy",
                },
              },
        );
        return;
      }
      // The child's turn claimed/released: what drives the child view's poll.
      if (msg.type === "session_status") {
        const d = (msg.data ?? {}) as Record<string, any>;
        if (typeof d.session_id !== "string" || !d.session_id) return;
        const status: "busy" | "idle" = d.status === "busy" ? "busy" : "idle";
        setChildSessions((p) => {
          const cur = p[d.session_id];
          if (!cur || cur.status === status) return p;
          return { ...p, [d.session_id]: { ...cur, status } };
        });
      }
    });
    return stop;
  }, []);
  useEffect(() => {
    if (!runToast) return;
    const timer = window.setTimeout(() => setRunToast(null), 5000);
    return () => window.clearTimeout(timer);
  }, [runToast]);

  /**
   * Load one conversation's transcript, and the facts derived from it (plan, usage).
   *
   * Three entry points used to carry their own copy of the same four calls — resume at boot,
   * picking a session in the nav, switching agent — and the copies had drifted (one cleared
   * the transcript first, the others did not), so a failed fetch could leave the PREVIOUS
   * conversation on screen under the new title.
   *
   * It also owns `hydrating`, which is what keeps the empty-state hero from claiming a
   * conversation is empty while its history is still in flight.
   */
  const loadSession = async (id: string, opts: { quiet?: boolean } = {}) => {
    // The conversation's own state is the target, whichever one is on screen: the person may
    // have moved on while this read was in flight, and a late answer belongs to ITS
    // conversation, never to the one in front of them. A QUIET load is a background re-read of
    // a conversation already on screen (a reconnect): nothing is cleared, no skeleton, and a
    // failure keeps what is shown.
    const target = liveRegistry.ensure(id);
    // Only the latest read for this conversation may land.
    const load = ++target.loadSeq;
    if (!opts.quiet) target.patch({ loading: true });
    try {
      const { messages, revert } = await getSessionTranscript(id);
      if (load !== target.loadSeq) return;
      target.hydrate({
        items: itemsFromMessages(messages, revert),
        revert,
        todo: planFromMessages(messages),
        usage: usageFromMessages(messages),
      });
    } catch {
      if (opts.quiet || load !== target.loadSeq) return;
      // Not hydrated: the next time the reader opens it, it is read again.
      target.patch({ items: [], revert: null, todo: [], usage: emptyUsage(), loading: false });
    }
  };
  loadSessionRef.current = loadSession;
  // The revert routes' own follow-up: same read, minus the hydrate/usage work a session
  // switch needs.
  const reloadTranscript = async (id: string) => {
    try {
      const { messages, revert } = await getSessionTranscript(id);
      const target = liveRegistry.ensure(id);
      target.setItems(itemsFromMessages(messages, revert));
      target.setRevert(revert);
    } catch {
      /* the frame/route response already moved the marker; a failed re-read is transient */
    }
  };
  // A new, empty conversation: nothing to wait for, so it is hydrated from the start.
  const openDraft = (id: string = newId()) => {
    liveRegistry.ensure(id).startDraft();
    setSessionId(id);
  };
  // The same draft moved to another folder: a fresh id (the connection follows the folder), and
  // the model, mode and thinking the person already picked on it go along. Without this the new
  // id's `ready` put the pickers back on the defaults (owner-hit 2026-10-12).
  const reopenDraft = () => {
    const id = newId();
    const picks = composerPickRef.current;
    openDraft(id);
    if (picks.session !== sessionId) return;
    composerPickRef.current = { ...picks, session: id };
    const target = liveRegistry.ensure(id);
    if (picks.model) target.setModel(model);
    if (picks.mode) target.setMode(mode);
    if (picks.thinking) target.setThinking(thinking);
  };
  // -- the active conversation's facts --------------------------------------------------------
  // What the server said about the conversation on screen (mode, model, folder, ...), applied
  // to the composer's pickers. The event handler records the facts for EVERY conversation and
  // calls back here; only the one on screen is applied, the rest wait for activation.
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const applyFacts = (target: LiveSession, kind: "ready" | "patch" | "activate") => {
    if (sessionIdRef.current !== target.id) return;
    // A `ready` clears it: the machine that refused has started since.
    if (target.facts.environmentError || kind === "ready") setEnvironmentError(target.facts.environmentError);
    const d = target.facts.ready;
    if (!d) return;
    setCompactAt(typeof d.compact_at === "number" && d.compact_at > 0 ? d.compact_at : undefined);
    // Hydration, not a live change: a pick the user already made for this session wins over the
    // values the engine reports on connect (their `set_*` frame is already queued behind the
    // connect). Later moves (`patch`) are the server confirming or correcting, and always apply.
    const picked =
      kind === "ready" && composerPickRef.current.session === target.id ? composerPickRef.current : null;
    if (d.model && !picked?.model) setModel(d.model);
    if (d.mode && !picked?.mode) setMode(d.mode);
    if (typeof d.thinking === "string" && !picked?.thinking) setThinking(d.thinking);
    // The Plan overlay is server truth; `plan_changed` keeps it live after this.
    if (typeof d.plan === "boolean") setPlan(d.plan);
    // Even when d.mode is absent (older servers), this is the best truth we'll get — unblock
    // the mode-notice effect for this session either way.
    setModeConfirmedFor(target.id);
    // A patch moves one picker (model, mode, thinking, plan) and nothing else. The folder facts
    // below are the connect-time record: re-applied on a patch they undid what the person did on
    // the draft since, so a model picked after "Just chat" turned "Temporary folder" back into
    // the scratch folder's id (owner-hit 2026-10-14).
    if (kind === "patch") return;
    if (d.command_trust?.required) setWorkspaceTrustRequest(d.command_trust);
    // Adopt the server-provisioned scratch dir (only when we don't already have one).
    if (d.workspace) setWorkspace((cur) => cur || d.workspace);
    // The bound host's workspace is server truth and NEVER comes from the local `workspace`
    // state: a missing field (older server) leaves the last known value alone; an explicit
    // null means this world is local.
    if (d.remote_workspace === null || typeof d.remote_workspace === "string") {
      setRemoteWorkspace(d.remote_workspace || null);
    }
    // Server truth on whether this session runs in a temporary folder.
    if (typeof d.temp_workspace === "boolean") setTempWorkspace(d.temp_workspace);
    // The shell its commands run in when they name none; null from an older server.
    setDefaultShell(typeof d.shell === "string" && d.shell ? d.shell : undefined);
    // The pick outranks the connect-time value on screen, but the ENGINE still reports what it
    // holds. When they differ the person is looking at a permission the backend does not have
    // (a `set_mode` lost with a dropped socket), so say it again: the picker may only claim
    // what the engine will actually do.
    if (picked?.mode && d.mode && modeValue(d.mode) !== modeValue(modeRef.current)) {
      target.setMode(modeRef.current);
    }
  };
  factsApplier.current = applyFacts;
  hostRef.current = {
    factsChanged: (target, kind) => applyFacts(target, kind),
    railStale: (target) => {
      if (sessionIdRef.current === target.id) setBrowserRefreshKey((k) => k + 1);
    },
    reloadTranscript: (target) => void reloadTranscript(target.id),
    // The engine behind the socket was replaced: reconnect so the next turn runs on the new
    // one. Done for the conversation even when it is in the background.
    engineReplaced: (target) => target.reconnect(),
    turnEnded: (target) => {
      refreshSessions();
      // Catch-all artifact refresh: files created via shell or on a brand-new session (whose
      // record only exists after the first save) appear once the turn completes.
      if (sessionIdRef.current === target.id) setBrowserRefreshKey((k) => k + 1);
      // Finalize a manual run after its first turn completes (mark it ok in history).
      const ar = activeRunRef.current;
      if (ar && ar.sessionId === target.id) {
        activeRunRef.current = null;
        finalizeAutomationRun(ar.taskId, ar.runId).catch(() => {});
      }
    },
    showsEnvironmentError: (target) =>
      sessionIdRef.current === target.id && !!activeEnvironmentRef.current,
  };
  // Ops' "Open trace": the payload carries the session id, the sessions list carries the
  // workspace — selecting with both is the same path the sidebar takes, so the session opens
  // on the right conversation.
  const openOpsTrace = (sid: string) => {
    const row = sessions.find((item) => item.session_id === sid);
    void selectSession(sid, row?.workspace ?? "");
  };
  const selectSession = async (id: string, ws: string) => {
    setSurface("session"); // selecting a conversation always returns to the conversation view
    // The conversation already open: coming back to it is navigation, not a reload. Its
    // socket never closed, so its transcript, stream, running turn and approvals are all
    // live — resetting them here showed "loading" and a turn that had stopped (while the
    // server was still running it) every time someone returned from another page.
    if (id === sessionId) return;
    // Leaving this conversation for another: remember where the reader was in it. Nothing is
    // cleared and nothing is copied — the conversation keeps its own live state and socket,
    // so whatever the agent does while the reader is elsewhere is already being recorded.
    rememberSession(sessionId);
    // `ready` says again whether the session works in a temporary folder.
    setTempWorkspace(false);
    if (ws && ws !== workspace) setWorkspace(ws);
    // The picked session's own composer facts, from the same record the sidebar row
    // shows. The `ready` frame confirms them; nothing paints (or sends under) defaults.
    hydrateSessionState(sessions.find((s) => s.session_id === id) ?? null);
    // The row already carries the binding; the detail read is the authoritative shape
    // (and covers a fork/session created after the list was last refreshed). Only the
    // still-current session's answer lands.
    const info = sessions.find((s) => s.session_id === id);
    setActiveEnvironment(info?.environment ?? null);
    setRemoteWorkspace(sessionRemoteWorkspace(info));
    setEnvironmentError("");
    void getSessionSummary(id)
      .then((res) => {
        if (res.ok && res.session_id === id && sessionIdRef.current === id) {
          setActiveEnvironment(res.environment ?? null);
          // The detail read's authoritative remote workspace (falls back to the
          // summary's `workspace`, which for a bound session IS the host's path).
          setRemoteWorkspace(
            res.remote_workspace ??
              (res.environment ? res.workspace || null : null),
          );
        }
      })
      .catch(() => {});
    const target = liveRegistry.ensure(id);
    const place = scrollMemory.current.get(id);
    pendingScrollRef.current = place && !place.following ? place.scrollTop : null;
    setSessionId(id);
    // A conversation the pool already holds is shown as it is — it has been receiving events
    // the whole time. Only one never read (or evicted, or whose last read failed) is loaded.
    if (!target.current.hydrated) void loadSession(id);
  };
  // -- the environment pick ------------------------------
  // Bind the CURRENT draft/current session to a saved host + absolute workspace on it
  // (PATCH /v1/sessions/{id}), then re-open the socket so the engine rebuilds against
  // the remote world. The response is the server's own answer; the setup row renders a
  // refusal inline. The host is not probed here — the session start is, and its typed
  // error lands on `environmentError` below.
  const pickRemoteEnvironment = async (
    environment: RemoteEnvironmentSummary,
    remoteWorkspace: string,
  ): Promise<{ ok: boolean; error?: string }> => {
    try {
      const res = await setSessionEnvironment(sessionId, environment.id, remoteWorkspace);
      if (!res.ok) {
        return { ok: false, error: res.error || "could not bind that remote workspace" };
      }
      setActiveEnvironment(res.environment ?? environment);
      // The remote workspace the server recorded (the pick's own path is the fallback
      // for an older answer) — never the local `workspace` state.
      setRemoteWorkspace(res.remote_workspace ?? remoteWorkspace);
      setEnvironmentError("");
      setWorkspace(res.workspace ?? remoteWorkspace);
      setTempWorkspace(false);
      setConnectNonce((k) => k + 1);
      return { ok: true };
    } catch (error) {
      return {
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      };
    }
  };
  /** LOCAL: clear the binding (PATCH with `environment_id: null`). The server lands the
   *  session on the default local workspace when one exists; its refusal is inline. */
  const clearRemoteEnvironment = async (): Promise<{ ok: boolean; error?: string }> => {
    try {
      const res = await setSessionEnvironment(sessionId, null);
      if (!res.ok) {
        return { ok: false, error: res.error || "could not switch to this computer" };
      }
      setActiveEnvironment(null);
      setRemoteWorkspace(null);
      setEnvironmentError("");
      if (res.workspace) setWorkspace(res.workspace);
      setTempWorkspace(false);
      setConnectNonce((k) => k + 1);
      return { ok: true };
    } catch (error) {
      return {
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      };
    }
  };
  const chooseWorkspace = (path: string) => {
    setWorkspace(path);
    setActiveEnvironment(null);
    setRemoteWorkspace(null);
    setEnvironmentError("");
    reopenDraft();
  };
  const renameConversation = async (id: string, title: string) => {
    if ((await renameSession(id, title)).ok) refreshSessions();
  };
  // The GoalBar's verbs. The route answers the mutated goal (or `{goal: null}` for a
  // clear); the live `goal` frame arrives right behind it, so this only needs to move
  // the strip immediately and hand a refusal back to the component for its inline line.
  const handleGoalAction = async (    action: "pause" | "resume" | "edit" | "clear",
    fields?: { objective?: string; max_rounds?: number },
  ): Promise<{ ok: boolean; error?: string }> => {
    try {
      const res = await goalAction(sessionId, action, fields);
      if (res.ok) setGoal(res.goal ?? null);
      return { ok: res.ok, error: res.error };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : String(err) };
    }
  };
  const forkConversation = async (id: string) => {
    const res = await forkSession(id);
    if (!res.ok || !res.session_id) {
      toast({ title: "Could not fork that conversation", description: res.error, tone: "error" });
      return;
    }
    refreshSessions();
    // Open the branch right away: forking is a move ("try it differently HERE"), and a
    // silent new row in the sidebar would make the user hunt for it.
    const parent = sessions.find((s) => s.session_id === id);
    selectSession(res.session_id, parent?.workspace || workspace || "");
  };
  // `/export`: the whole transcript as one JSON file, saved
  // through `saveTextAnywhere` (native save dialog in the desktop shell, an ordinary
  // download in the browser). Success and failure are toasts in the app's own style — the
  // command has no room for a transcript line.
  const exportConversation = async () => {
    try {
      const data = await exportSession(sessionId);
      const filename = sessionExportFilename(data.info ?? { session_id: sessionId });
      const where = await saveTextAnywhere(filename, JSON.stringify(data, null, 2));
      if (where === null) return; // cancelled at the save dialog — not a failure
      toast({
        title: "Session exported",
        description: `Saved session to ${filename}`,
        tone: "success",
      });
    } catch (error) {
      toast({
        title: "Failed to export session",
        description:
          error instanceof Error
            ? error.message
            : "An error occurred while exporting the session",
        tone: "error",
      });
    }
  };
  const togglePinned = async (id: string, pinned: boolean) => {
    await setSessionFlags(id, { pinned });
    refreshSessions();
  };

  // `/undo` / `/redo`: message-level revert. The server stages a marker and
  // returns the reverted message; the transcript redraws with the boundary line and the
  // composer gets the text back. Failures are
  // toasts — a command that bought nothing must not pretend otherwise (the `/compact`
  // rule). A running turn is interrupted server-side by the revert route itself.
  const applyRevert = async (res: RevertResult, failure: string): Promise<boolean> => {
    if (!res.ok) {
      toast({ title: failure, description: res.error, tone: "error" });
      return false;
    }
    await reloadTranscript(sessionId);
    return true;
  };
  // `/reload-plugins`. The server drops this conversation's engine and
  // tells every view of it (`plugins_reloaded`), which reconnects and says so. When no view
  // was listening yet (this socket still building its engine), the answer says it here.
  const reloadPlugins = async () => {
    try {
      const res = await reloadSessionPlugins(sessionId);
      if (!res.ok) setItems((p) => [...p, { kind: "notice", tone: "warn", text: res.error || "Could not reload the plugins." }]);
      else if (res.views === 0) setItems((p) => [...p, pluginsReloadedNotice(res.plugins, res.errors)]);
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      setItems((p) => [...p, { kind: "notice", tone: "warn", text: `Could not reload the plugins: ${text}` }]);
    }
  };

  const undoConversation = async () => {
    try {
      const res = await revertSession(sessionId);
      if (!(await applyRevert(res, "Could not undo the last message"))) return;
      const draft = revertDraft(res);
      if (draft) prefillComposer(draft.text, draft.attachments);
    } catch (error) {
      toast({ title: "Could not undo the last message", description: String(error), tone: "error" });
    }
  };
  const redoConversation = async () => {
    try {
      await applyRevert(await unrevertSession(sessionId), "Could not redo");
    } catch (error) {
      toast({ title: "Could not redo", description: String(error), tone: "error" });
    }
  };
  // Archived, the open conversation is left for a new one; it is under Archived now.
  const toggleArchived = async (id: string, archived: boolean) => {
    await setSessionFlags(id, { archived });
    refreshSessions();
    if (archived && id === sessionId) openDraft();
  };
  const deleteConversation = async (id: string) => {
    if (!(await deleteSession(id)).ok) return;
    // Its socket goes too: one left open would reconnect and have the server make it again.
    liveRegistry.drop(id);
    refreshSessions();
    if (id === sessionId) openDraft();
  };

  // "Run now": the server prepares a run, its conversation opens, and the task is sent, so the run
  // happens live on screen; it is recorded as finished after its first turn.
  const openRunSession = (runSession: string, ws: string, task?: { id: string; title: string }) => {
    setRunContext(task ?? null);
    setSurface("session");
    selectSession(runSession, ws);
  };
  const runTaskNow = async (taskId: string, title?: string) => {
    const run = await runAutomation(taskId);
    if (!run?.ok) return;
    pendingPromptRef.current = { text: run.prompt };
    activeRunRef.current = { taskId, runId: run.run_id, sessionId: run.session_id };
    openRunSession(run.session_id, run.workspace, { id: taskId, title: title || "" });
  };

  const idle = items.length === 0 && !streamOpen;
  // The composer's `/undo` / `/redo` gates (the reference design: `visibleUserMessages().length === 0`
  // and `!info()?.revert?.messageID`). The boundary item is where the model's view stops,
  // so an undoable message is a user bubble ABOVE it (or anywhere, with no marker).
  const canUndo = useMemo(() => canUndoItems(items), [items]);
  const canRedo = canRedoMarker(revertMarker);
  // Announce actions whose outcome is not on screen (see ui/Toast.tsx). `undoToolCall` used to
  // return `{ok, restored, failed, error}` to a caller that dropped it, so a refused undo and
  // a successful one looked identical.
  const toast = useToast();
  // A conversation the reader is NOT looking at asks for them, or finishes: say so once, with
  // a way there. This is the other half of letting it run in the background — without it
  // "your agent is blocked on an approval" is something found by wandering back.
  const activityRef = useRef({ sessions, selectSession, toast });
  activityRef.current = { sessions, selectSession, toast };
  useEffect(
    () =>
      liveRegistry.onActivity((id, kind) => {
        if (id === sessionIdRef.current) return;
        const { sessions: known, selectSession: open, toast: show } = activityRef.current;
        const row = known.find((s) => s.session_id === id);
        const title = row?.title || "A conversation";
        const action = { label: "Open", onClick: () => void open(id, row?.workspace ?? "") };
        if (kind === "needs-attention") {
          show({ title: `${title} is waiting for you`, description: "It needs an approval or an answer to go on.", tone: "info", action, durationMs: 12000 });
        } else {
          show({ title: `${title} finished`, tone: "success", action, durationMs: 6000 });
        }
      }),
    [],
  );

  // `/compact` (slash command or the popover's button): the busy phase is an IN-CHAT row —
  // the same transient the automatic path uses — so the prompt bar stays a prompt bar
  // (design review 2026-09-16: a sentence above the composer is where the eye is not, and
  // it vanished before it could be read). Failures and refusals go to a toast: the command
  // bought nothing, and nothing in the transcript should pretend otherwise.
  // A subagent's session opens beside the chat, in its own tab of the workspace panel
  // (owner, 2026-10-13): the request names the child, the panel shows it, and says back which
  // child is on screen so its row in the chat can be marked.
  const [childRequest, setChildRequest] = useState<{
    childId: string;
    title: string;
    group?: string;
    key: number;
  } | null>(null);
  const [shownChild, setShownChild] = useState<string | null>(null);
  // The child id on the wire is `presentation.run_id` / the first `tool_metadata` frame —
  // the child session id itself (step B). A row with no title of its own (the
  // sidebar's) takes the description from the session the app has heard of.
  const openChild = (run: { runId: string; title: string; group?: string }) => {
    if (!run.runId) return;
    const known = childSessions[run.runId];
    setChildRequest((previous) => ({
      childId: run.runId,
      title: run.title || (known ? childDescription(known.title) : ""),
      ...(run.group ? { group: run.group } : {}),
      key: (previous?.key ?? 0) + 1,
    }));
    // Opening a child shows the panel it opens in.
    if (railHidden) setRailHiddenPersist(false);
  };
  // P6: promote this session's running foreground `task` child(ren) to background. The
  // route promotes ALL of the session's running spawns,
  // and the row updates from the server's own `tool_metadata` frame (`background: true`),
  // not from this call. `ok: false` is the normal race when the child just finished, so a
  // refusal stays silent — the affordance disappears with the call's settlement anyway.
  const promoteTask = () => {
    void promoteSessionTasks(sessionId).catch(() => {});
  };
  // P3: `GET /v1/sessions/{id}/children` seeds the registry a RELOADED client never heard
  // `session_created` for (a task row replayed from history is the only reference to its
  // child), and refreshes the busy/idle status the sidebar's nested row draws. Frames stay
  // the live authority for newer children; this only fills what the socket missed.
  useEffect(() => {
    if (!shownChild) return;
    const parent = sessionId;
    let alive = true;
    getSessionChildren(parent)
      .then((rows) => {
        if (!alive || !rows.length) return;
        setChildSessions((p) => {
          const next = { ...p };
          for (const row of rows) {
            const known = next[row.session_id];
            next[row.session_id] = {
              sessionId: row.session_id,
              parentSession: parent,
              title: row.title || known?.title || "",
              agent: row.agent || known?.agent || "",
              status: row.status,
            };
          }
          return next;
        });
      })
      .catch(() => {
        /* the live frames are the primary source; a failed seed changes nothing */
      });
    return () => {
      alive = false;
    };
  }, [shownChild, sessionId]);
  // -- permission dock selection (P1) -----------------------------------------
  // The session whose turn is on screen: the selected conversation (a child is read in
  // the panel beside it, and never has the dock). An ask belongs to the dock when its session IS that one or
  // sits in its subtree (the child registry is the parent links we already track).
  const activeSessionOnPath = sessionId;
  const permissionOnPath = useCallback(
    (req: PermissionRequestInfo) => {
      let cur: string | undefined = req.sessionId;
      const seen = new Set<string>();
      while (cur && !seen.has(cur)) {
        if (cur === activeSessionOnPath) return true;
        seen.add(cur);
        cur = childSessions[cur]?.parentSession;
      }
      return false;
    },
    [activeSessionOnPath, childSessions],
  );
  // One dock at a time: the active session's own ask first (you are looking right at it),
  // else the oldest descendant's — the same "one prompt" rule the dock follows.
  const activePermission = useMemo(() => {
    const all = Object.values(permissionRequests);
    return (
      all.find((r) => r.sessionId === activeSessionOnPath) ??
      all.find((r) => permissionOnPath(r)) ??
      null
    );
  }, [permissionRequests, activeSessionOnPath, permissionOnPath]);
  // Who asks, when it is not the session on screen: the child's own description.
  const permissionFrom =
    activePermission && activePermission.sessionId !== activeSessionOnPath
      ? childDescription(childSessions[activePermission.sessionId]?.title || "") || undefined
      : undefined;
  // Navigating to an ask: a root is opened directly; a child's request opens its ROOT
  // (the child is read-only and its dock lives on the viewed ancestor), where the
  // subtree rule above makes the dock appear.
  const goToPermission = (req: PermissionRequestInfo) => {
    let root = req.sessionId;
    let cur = childSessions[root]?.parentSession || req.parentSession || "";
    const seen = new Set<string>();
    while (cur && !seen.has(cur)) {
      seen.add(cur);
      root = cur;
      cur = childSessions[cur]?.parentSession || "";
    }
    selectSession(root, "");
  };
  const respondPermission = async (req: PermissionRequestInfo, reply: PermissionReply) => {
    setPermissionResponding(true);
    try {
      const res = await replyPermission(req.requestId, reply);
      if (res.ok) {
        // Remove locally as well as on the frame: a reply answered from this window is
        // settled before the broadcast completes, and a dock that lingers invites a
        // second click the server would refuse.
        setPermissionRequests((p) => {
          if (!p[req.requestId]) return p;
          const next = { ...p };
          delete next[req.requestId];
          return next;
        });
      } else {
        toast({
          title: "Could not answer the permission request",
          description: res.error,
          tone: "error",
        });
      }
    } catch (error) {
      toast({
        title: "Could not answer the permission request",
        description: String(error),
        tone: "error",
      });
    } finally {
      setPermissionResponding(false);
    }
  };
  // A request for a session that is NOT on screen: a dismissible toast with the way
  // over.
  // Long-lived on purpose — a permission prompt is worth more than a 4-second glance —
  // and keyed so a frame redelivered on both sockets toasts once.
  const announcedPermissions = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const req of Object.values(permissionRequests)) {
      if (announcedPermissions.current.has(req.requestId)) continue;
      announcedPermissions.current.add(req.requestId);
      if (permissionOnPath(req)) continue; // the dock is already on screen
      toast({
        title: "Permission required",
        description:
          req.permission === "task" ? "Launch sub-agents" : req.permission,
        durationMs: 15_000,
        action: { label: "Go to session", onClick: () => goToPermission(req) },
      });
    }
    // `toast`/`goToPermission` are stable enough per render; the request map is the signal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [permissionRequests, permissionOnPath]);
  // `/compact` between turns: its running line, its marker and how it failed come from the
  // server's frames, for every view of the session. What is left here is a refusal the server
  // never got to announce ("wait for the current task", a session that is not open).
  // The files the agent read or changed in this conversation, newest first: "@" offers them
  // before the folder.
  const touchedFiles = useMemo(() => touchedFilesOf(items, workspace ?? undefined), [items, workspace]);
  // What a compaction works through, said the same on the turn's line and the one between turns.
  const compactingWords = compactingWordsFor(compactingWhy, usage.context);
  // How full the conversation is, for "Nothing to compact yet".
  const contextWords =
    usage.context > 0
      ? modelContextWindows[model]
        ? `${formatTokens(usage.context)} of ${formatTokens(modelContextWindows[model])} tokens`
        : `${formatTokens(usage.context)} tokens`
      : "";
  const onCompactStatus = (
    _busy: boolean,
    problem?: string,
    tone: "info" | "error" = "error",
  ) => {
    if (!problem) return;
    if (tone === "info") toast({ title: problem, tone });
    else live.setCompactNote({ nothing: /^nothing to compact/i.test(problem), text: problem });
  };
  const compactAgain = () => {
    void compactNow(sessionId)
      .then((out) => {
        if (!out.ok && !out.announced) onCompactStatus(false, out.error || "Could not compact.");
      })
      .catch(() => onCompactStatus(false, "Could not compact."));
  };
  // `/goal` (the composer's human command): every status, result
  // and refusal is a quiet transcript block where the person who typed it is reading —
  // no alert, no confirm. The GoalDock is not touched here: the route broadcasts the
  // whole goal view (plus the activation edge), and the session socket already patches
  // it, so the dock moves from the same frames every other goal change uses.
  const onGoalNotice = (text: string, tone: "info" | "warn" = "info", title?: string) => {
    setItems((p) => [
      ...p,
      { kind: "notice", tone, text, command: true, ...(title ? { title } : {}) },
    ]);
  };
  const undoCall = async (callId: string) => {
    try {
      const res = await undoToolCall(sessionId, callId);
      if (res.ok) {
        const count = res.restored?.length ?? 0;
        toast({
          title: count === 1 ? "Put 1 file back" : `Put ${count} files back`,
          description: res.restored?.join(", "),
          tone: "success",
        });
      } else {
        toast({
          title: "Could not undo that step",
          description: res.error ?? res.failed?.join(", ") ?? "the server refused the request",
          tone: "error",
        });
      }
      // Returned as well as announced: the receipt that offered the button still renders the
      // refusal next to itself, and a caller reading the result must not see a success.
      return res;
    } catch (error) {
      toast({
        title: "Could not undo that step",
        description: String(error),
        tone: "error",
      });
      return { ok: false, error: String(error) };
    }
  };
  // The newest unanswered ask of each kind.
  const waiting = (kind: Item["kind"]) => items.findLast((i) => i.kind === kind && !(i as { resolved?: unknown }).resolved);
  const pendingApproval = waiting("approval");
  const pendingToolReq = waiting("toolreq");
  const pendingPlan = waiting("planreq");
  const pendingQuestion = waiting("question");
  // A pending DECISION replaces the composer (owner, 2026-09-10; mockup #10): while an
  // approval / question / request waits for an answer, that card is the ONLY input — no
  // chat box stacked underneath it.
  //
  // The parked/Inbox copy is a fallback for exactly one case: NO live item is present
  // (e.g. a reload landed on a server-side decision, or a run asked with no window). Without
  // that gate the same decision rendered twice — once live (this card) and once from the
  // 4-second Inbox poll — so the user was asked to grant the same thing again.
  const hasLiveDecision =
    !!(
      pendingApproval ||
      pendingPlan ||
      pendingQuestion ||
      pendingToolReq
    );
  const parkedApproval = sessionInbox[0] ? approvalItemFromInbox(sessionInbox[0]) : null;
  // What the turn is waiting for, said on its status line. The pending card itself renders
  // over the composer, so the transcript used to keep saying "Running a command" while the
  // agent was in fact blocked on the person: the one state that most needs to be named.
  const waitingOn = running
    ? pendingApproval || parkedApproval
      ? "Waiting for your approval"
      : pendingQuestion
        ? "Waiting for your answer"
        : hasLiveDecision
          ? "Waiting for your decision"
          : undefined
    : undefined;
  const blockedCard =
    pendingApproval?.kind === "approval" ? (
      <ApprovalCard
        item={pendingApproval}
        onApprove={approve}
        runTask={runContext}
      />
    ) : pendingPlan?.kind === "planreq" ? (
      <PlanCard item={pendingPlan} onRespond={respondPlan} fullAccess={mode === "danger-full-access"} />
    ) : pendingQuestion?.kind === "question" ? (
      <InboxItemCard
        item={{
          id: "live-question",
          session_id: sessionId,
          kind: "question",
          title: pendingQuestion.question,
          body: "",
          state: "pending",
          resolution: null,
          inbox: "default",
          created_at: "",
          resolved_at: null,
          options: pendingQuestion.options,
          allow_text: pendingQuestion.allow_text,
          multi: pendingQuestion.multi,
          header: pendingQuestion.header,
          questions: pendingQuestion.questions,
        }}
        onResolve={(_id, answer) => answerQuestion(answer)}
      />
    ) : pendingToolReq?.kind === "toolreq" ? (
      <ToolRequestCard item={pendingToolReq} onRespond={respondTool} />
    ) : !hasLiveDecision && sessionInbox[0]?.kind === "plan" ? (
      // A plan the server holds parked (asked with no window, or after a reload) resolves through the SAME
      // dock as the live one — one dialect for the plan decision, and the composer looks the
      // same whether the plan arrived live or was replayed. The plan CONTENT is on the
      // transcript's `propose_plan` step either way.
      <PlanCard
        fullAccess={mode === "danger-full-access"}
        item={{ kind: "planreq", plan: "" } as any}
        onRespond={(approved, _mode, feedback) =>
          void resolveSessionInbox(
            sessionInbox[0].id,
            JSON.stringify(
              approved
                ? { approved: true }
                : { approved: false, ...(feedback ? { feedback } : {}) },
            ),
          )
        }
      />
    ) : !hasLiveDecision && parkedApproval ? (
      // A parked approval is the live card again (U2): same item, same buttons, and the same
      // answer path — the WS `approval` frame resolves the session's pending item whether the
      // prompt arrived live or was parked before a reconnect or a restart.
      <ApprovalCard
        item={parkedApproval}
        onApprove={approve}
        runTask={
          runContext ??
          (sessionInbox[0].data?.task_id
            ? { id: String(sessionInbox[0].data.task_id), title: String(sessionInbox[0].data.task_title || "") }
            : null)
        }
      />
    ) : !hasLiveDecision && sessionInbox[0] ? (
      // No live item: the session is blocked on a server-parked Inbox item — answer it
      // in context. This is a run that asked with no window, and the post-reload fallback.
      <InboxItemCard item={sessionInbox[0]} onResolve={resolveSessionInbox} />
    ) : undefined;
  // The start page: a session on screen with no history, nothing loading in, and no decision
  // that has taken the composer's place.
  const startPage = surface === "session" && idle && !hydrating && !blockedCard;
  // Drain the queue the moment the agent goes idle again. The queue exists for the WAIT, so
  // the trigger is "nothing is running and no gate owns the composer" — not "the turn ended
  // successfully": a user who queued a follow-up wants it sent whether the last turn finished,
  // failed or was interrupted. One message per pass; the effect re-runs on the shorter queue.
  //
  // `send` is read through a ref because it is rebuilt every render (it closes over items,
  // mode, model…). Depending on it directly would re-fire the effect on every keystroke.
  // A `/compact` between turns holds the queue too: the server refuses a message while it
  // runs, and the queue is where a message typed meanwhile waits (owner, 2026-10-13).
  useEffect(() => {
    if (starting || !connected || running || compacting || blockedCard || queuedPrompts.length === 0) return;
    const [next, ...rest] = queuedPrompts;
    setQueuedPrompts(rest);
    sendRef.current(next.text, next.attachments, next.skill, next.mentions, next.command);
  }, [starting, connected, running, compacting, blockedCard, queuedPrompts]);
  /**
   * Escape stops the run — the fastest way out of a command that is obviously going nowhere,
   * and the same key the reference design binds to its global cancel.
   *
   * Two guards, both deliberate. It never fires while an input has focus: there Escape is the
   * user's OWN key (it closes the slash popup, clears a draft), and stealing it to kill work
   * would be the app guessing which of the two they meant. And it never fires under a gate:
   * the engine is suspended waiting for an answer, so there is nothing to stop — the card's
   * own buttons are the decision.
   *
   * Declared here, after `blockedCard`, because the dependency array is evaluated during
   * render and that const does not exist earlier in the body.
   */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const el = document.activeElement;
      const active =
        el instanceof HTMLElement
          ? { tag: el.tagName, contentEditable: el.isContentEditable }
          : null;
      if (!shouldStopOnEscape(event, { running, gateOpen: Boolean(blockedCard) }, active)) return;
      interrupt();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [running, blockedCard]);
  // The title line's ghost styling: until the first turn the name is a draft, not a fact.
  const hasHistory = items.length > 0;
  // When the turn on screen began: its user message's stamp. The bar's "Working" clock and
  // the transcript's status line count from the same instant.
  const turnSince = useMemo(() => {
    for (let i = items.length - 1; i >= 0; i--) {
      const it = items[i];
      if (it.kind === "user") return typeof it.ts === "number" && it.ts > 0 ? it.ts * 1000 : null;
    }
    return null;
  }, [items]);
  const activeInfo = sessions.find((s) => s.session_id === sessionId);
  // The bound folder's display name — null for Chat and for temporary folders, whose
  // server-generated scratch names are noise. Feeds the composer's readout chips and the
  // topbar's path-shaped-title fallback (SessionTopbar.sessionDisplayTitle). A bound
  // session names the HOST's folder: the local workspace may be stale and is the wrong
  // machine's name for a path-shaped title.
  const workspaceName = workspace && !tempWorkspace ? baseName(workspace) : null;
  const topbarWorkspaceName = activeEnvironment
    ? remoteWorkspace
      ? baseName(remoteWorkspace)
      : null
    : workspaceName;
  // The composer footer's checked-out branch, resolved once per session/workspace pair.
  // A non-repo answers null and the branch just doesn't show.
  const [gitBranch, setGitBranch] = useState<string | null>(null);
  useEffect(() => {
    setGitBranch(null);
    // A remote workspace is a POSIX path on the other host: the local git read would
    // either miss or, worse, answer for a same-named local directory.
    if (!workspace || tempWorkspace || activeEnvironment) return;
    let alive = true;
    getWorkspaceBranch(workspace).then((b) => {
      if (alive) setGitBranch(b);
    });
    // Review's branch picker switches the same folder: read the branch again when it says so.
    const onSwitched = (event: Event) => {
      const branch = String((event as CustomEvent).detail?.branch || "");
      if (branch) setGitBranch(branch);
    };
    window.addEventListener(BRANCH_SWITCHED_EVENT, onSwitched);
    return () => {
      alive = false;
      window.removeEventListener(BRANCH_SWITCHED_EVENT, onSwitched);
    };
  }, [sessionId, workspace, tempWorkspace, activeEnvironment]);
  // A turn can move the branch too (Reeve ran `git switch`): read it again after each one,
  // without blanking the chip in between.
  useEffect(() => {
    if (!browserRefreshKey || !workspace || tempWorkspace || activeEnvironment) return;
    let alive = true;
    getWorkspaceBranch(workspace).then((b) => {
      if (alive) setGitBranch(b);
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [browserRefreshKey]);
  const activeTitle = activeInfo?.title || "New session";

  const desktop = isDesktop();
  // Windows runs the shell frameless (decorations(false) in lib.rs): the app draws its
  // own title strip with the window controls. macOS keeps the overlay traffic lights;
  // the browser never had a title bar to replace.
  const framelessWindow = desktop && platformOS() !== "macos";
  // In a browser, `?overlay=1` draws the Mac window's top-left (its class, and stand-in traffic
  // lights where the real ones are), to tune that corner without building the app. Never in the app.
  const simOverlay = !desktop && new URLSearchParams(window.location.search).has("overlay");
  // The overlaid title bar is the Mac's alone: Windows and Linux keep their own bar, and the Mac's
  // insets drawn under it were out of line (caught 2026-07-21).
  const overlay = (desktop && platformOS() === "macos") || simOverlay;
  // The session's shell facts, for every tool card under the conversation. Stable across
  // streaming renders (it only changes when the session's environment binding does), so the
  // provider never re-renders a transcript row by itself.
  const sessionShell = useMemo<SessionShell>(
    () => ({
      environment: activeEnvironment,
      platform: platformOS(),
      sessionId: sessionId || undefined,
      ...(defaultShell ? { shell: defaultShell } : {}),
    }),
    [activeEnvironment, sessionId, defaultShell],
  );
  // The rail's guide shows which shell the terminal door launches (informational: the
  // backend starts one fixed interpreter per session).
  const railShellLabel = shellDisplayName(shellKind(sessionShell));

  return (
    <FolderActivityContext.Provider value={folderActivity}>
    <div
      className={
        "app" +
        (framelessWindow ? " has-titlebar" : "") +
        (overlay ? " tauri-overlay" : "") +
        (navView.collapsed ? " nav-collapsed" : "") +
        (navView.overlay || navLeaving ? " nav-overlay" : "") +
        (navLeaving ? " nav-leaving" : "") +
        (surface === "settings" ? " settings-takeover" : "")
      }
    >
      {/* The frameless window's – □ × float at the corner; drag lives on each surface's
          own top bar (session topbar, page shells) — no dead strip like a native bar. */}
      <WindowControls />
      {/* A phone-width window shows the expanded navigation over the page, not beside it:
          anywhere on the page outside it closes it again. */}
      {(navView.overlay || navLeaving) && (
        <div
          className="nav-scrim"
          data-testid="nav-scrim"
          onClick={() => setNavPeek(false)}
          aria-hidden="true"
        />
      )}
      {/* `?overlay=1`'s stand-in traffic lights. */}
      {simOverlay && (
        <div className="sim-traffic-lights" aria-hidden="true">
          <span /><span /><span />
        </div>
      )}
      {/* The desktop app's own lines, none of them drawn in a browser: an update to install (15s
          after the start, then every 30 min); the engine restarted after a crash on the same port,
          said rather than left as a spinner (the boot pane says it while Reeve starts); and what
          closing and quitting do, with the tray's count of what runs. */}
      <UpdateBanner />
      {!starting && <SidecarBanner />}
      <DesktopShellBridge sessions={sessions} />
      {runToast && (
        <AutomationToast
          title={runToast.title}
          time={runToast.time}
          framed={framelessWindow}
          onView={() => {
            selectSession(runToast.sessionId, runToast.workspace);
            setRunToast(null);
          }}
          onDismiss={() => setRunToast(null)}
        />
      )}
      {/* Setup's end: read the model it may have connected, then go where it pointed — the
          Automations quickstart for "Create your first automation", a new session for
          "Start working". */}
      {onboarding && (
        <Onboarding
          onDone={(next) => {
            setOnboarding(false);
            getHealth()
              .then((health) => setModel(health.model))
              .catch(() => {});
            loadSettings();
            if (next === "automations") setSurface("scheduled");
            else if (next === "work") startNewSession();
          }}
        />
      )}
      {surface !== "settings" && (
      <Sidebar
        sessions={sidebarSessions}
        worktreesOpen={worktreesOpen}
        worktrees={worktrees}
        worktreesLoading={worktreesLoading}
        onToggleWorktrees={toggleWorktrees}
        onOpenWorktree={openWorktree}
        activeSession={sessionId}
        onNewSession={startNewSession}
        onSelectSession={(id, ws) => {
          // A run waiting on the person opens as a run, its banner naming the automation.
          const row = sessions.find((s) => s.session_id === id);
          if (row?.automation) openRunSession(id, ws, row.automation);
          else selectSession(id, ws);
        }}
        onRenameSession={renameConversation}
        onDeleteSession={deleteConversation}
        onArchiveSession={toggleArchived}
        onTogglePin={togglePinned}
        onForkSession={forkConversation}
        onManage={() => openSettings("general")}
        onOpenScheduled={() => setSurface("scheduled")}
        onOpenAutomation={(id) => {
          setScheduledOpenId(id);
          setSurface("scheduled");
        }}
        onOpenIntegrations={() => setSurface("integrations")}
        onOpenOps={() => setSurface("ops")}
        scheduledActive={surface === "scheduled"}
        integrationsActive={surface === "integrations"}
        sessionsState={sessionsState}
        onRetrySessions={refreshSessions}
        opsActive={surface === "ops"}
        childSessions={childSessions}
        activeOnPath={shownChild ?? activeSessionOnPath}
        onOpenChild={(id) => openChild({ runId: id, title: "" })}
        collapsed={navView.collapsed && !navLeaving}
        onCollapse={toggleNav}
        starting={starting}
      />
      )}
      {surface === "scheduled" ? (
        <AutomationsView
          onOpenRun={openRunSession}
          onRunNow={runTaskNow}
          initialOpenId={scheduledOpenId}
        />
      ) : surface === "ops" ? (
        <Suspense fallback={null}>
          <OpsView onOpenTrace={openOpsTrace} />
        </Suspense>
      ) : surface === "integrations" ? (
        <IntegrationsView />
      ) : surface === "settings" ? (
        <SettingsView
          key={settingsTab}
          initialTab={settingsTab}
          model={model}
          workspace={workspace ?? undefined}
          onBack={() => setSurface("session")}
          onCreateSkill={(description) => {
            // Making a skill is a conversation: a new session, the request in
            // the composer for the person to read and send, or to finish when it has no description.
            startNewSession();
            prefillComposer(`Build a new skill for me: ${description || "(describe what the skill should do)"}`);
          }}
        />
      ) : surface === "audit" ? (
        <AuditView />
      ) : null}
      {/* The session is hidden, never unmounted, while another page is up (Settings,
          Automations): coming back finds it as it was — its place, the draft, the panel, a turn
          still streaming — rather than built again from the server. */}
      <div
        className={"main" + (surface === "session" && !railHidden ? " rail-open" : "")}
        hidden={surface !== "session"}
        data-testid="session-surface"
      >
        <SessionTopbar
          railHidden={railHidden}
          activeTitle={activeTitle}
          hasHistory={hasHistory}
          workspaceName={topbarWorkspaceName}
          // Where it works, said once and in the bar once the conversation has begun; before
          // that the strip under the composer holds the pickers that set it.
          where={{
            folderName: activeEnvironment ? null : workspaceName,
            folderPath: activeEnvironment || !workspace ? null : workspace,
            branch: activeEnvironment ? null : gitBranch,
            worktree: !activeEnvironment && !!activeInfo?.worktree,
            environment: activeEnvironment,
            remoteWorkspace: activeEnvironment ? remoteWorkspace : null,
            connected,
            problem: environmentError,
            onBranchSwitched: (branch) => {
              setGitBranch(branch);
              setBrowserRefreshKey((k) => k + 1);
            },
            onRetry: () => live.reconnect(),
          }}
          sessionKey={sessionId}
          starting={starting ? { title: bootTitle } : null}
          started={items.some((item) => item.kind === "user")}
          sentHere={sentIn.current === sessionId}
          onTogglePanel={() => setRailHiddenPersist(!railHidden)}
          sidecarConnected={connected}
          sidecarModel={model}
          onOpenConnectors={() => setSurface("integrations")}
          onOpenSkills={() => openSettings("skills")}
          // What the session is doing, in the bar's few words. Waiting on the person wins
          // over working: a turn that is parked on an approval is not making progress.
          state={
            blockedCard || activePermission
              ? { kind: "needs" }
              : running
                ? { kind: "working", since: turnSince }
                : compacting
                  ? { kind: "compacting", since: compactingSince }
                  : activeInfo?.liveness === "sleeping"
                  ? { kind: "sleeping", until: activeInfo.sleeping_until }
                  : null
          }
          // Automation-run context (owner ask 2026-07-04): a __run__ session looked like any
          // other chat with no way back to the runs list. The way back is in the bar, before
          // the name; it was a banner in the conversation's column.
          run={
            sessionId.startsWith("__run__")
              ? {
                  title: runContext?.title || "",
                  onBack: () => {
                    if (runContext) setScheduledOpenId(runContext.id);
                    setSurface("scheduled");
                  },
                }
              : null
          }
          actions={
            activeInfo && !sessionId.startsWith("__run__")
              ? {
                  pinned: !!activeInfo.pinned,
                  archived: !!activeInfo.archived,
                  onRename: (title) => void renameConversation(sessionId, title),
                  onTogglePin: () => void togglePinned(sessionId, !activeInfo.pinned),
                  onToggleArchive: () => void toggleArchived(sessionId, !activeInfo.archived),
                  onFork: () => void forkConversation(sessionId),
                  onExport: () => void exportConversation(),
                  onDelete: () => void deleteConversation(sessionId),
                }
              : null
          }
        />
        <div className={"main-workspace" + (railHidden ? " rail-hidden" : "")}>
          {/* A session with no history is a start page: its heading sits in the middle with
              the composer under it and things to try under that (`data-idle`), where a
              conversation pins the composer to the foot of the column. */}
          <div
            className="main-chat"
            data-idle={startPage && !starting ? "" : undefined}
            data-arrived={bootLeaving ? "" : undefined}
          >
            <SessionShellContext.Provider value={sessionShell}>
            <ShownChildContext.Provider value={shownChild}>
            {/* Reeve starting: what it is doing, in this column, in place of the conversation. */}
            {(starting || bootLeaving) && (
              <BootPane
                opening={engineMs !== null}
                title={bootTitle}
                startedAt={bootStartedAt.current}
                engineMs={engineMs}
                sidecar={bootSidecar}
                failure={bootFailure}
                onRetry={retryBoot}
                leaving={!starting}
              />
            )}
            {!starting && (
            <>
            {surface === "session" && (
              <ProjectPluginsBanner sessionId={sessionId} workspace={workspace} onReview={() => openSettings("plugins")} />
            )}
            {/* The task's head (layout step 2): where the task stands, read before the
                conversation. A new session's start page has nothing to say yet. */}
            {!startPage && (
              <TaskHead
                running={running}
                todo={todo}
                goal={goal}
                onGoalAction={handleGoalAction}
                subagents={subagentNodes}
                onOpenChild={openChild}
                sessionId={sessionId}
                jobs={liveState.jobs}
                buffers={liveState.jobOutput}
                onClearJobOutput={live.dropJobOutput}
                facts={<TaskFacts items={items} usage={usage} />}
              />
            )}
            <SessionScroll
              idle={idle}
              hydrating={hydrating}
              sessionId={sessionId}
              items={items}
              running={running}
              // Compaction is an activity like any other (nothing streams while the summarizer runs,
              // 35-100 s in the real logs), so the one status line names it instead of a second
              // spinner standing under "Working".
              liveActivity={
                waitingOn ??
                (running && compacting
                  ? // What is happening and how much there is to read: the reason the agent stands still.
                    compactingWords
                  : liveActivity)
              }
              live={live}
              streaming={streamOpen}
              compacting={compacting}
              compactingSince={compactingSince}
              compactingWords={compactingWords}
              compactNote={compactNote}
              contextWords={contextWords}
              onCompactAgain={compactAgain}
              following={following}
              connectorToolIndex={connectorToolIndex}
              agentColors={agentColors}
              scrollRef={scrollRef}
              attachScroller={attachScroller}
              handleScroll={handleScroll}
              approve={approve}
              retry={retry}
              revealArtifact={revealArtifact}
              undoCall={undoCall}
              setSurface={setSurface}
              followLatest={followLatest}
              openChild={openChild}
              promoteTask={promoteTask}
              // A message row's "Fork session" action: the same
              // whole-prefix fork the `/fork` command and the slash menu already run.
              onForkSession={() => void forkConversation(sessionId)}
            />
            </>
            )}
            {/* Per-session setup (folder, machine) is the strip under the composer
                card on a fresh session. One-time pick: after the first message the same
                strip states what was picked. */}
            {!starting && (
              <ConnectionNote
                connected={connected}
                gaveUp={liveState.gaveUp}
                onRetry={() => live.reconnect()}
                // A remote machine that refused to start is named: it is the cause.
                machine={activeEnvironment && environmentError ? activeEnvironment.name || activeEnvironment.host : undefined}
              />
            )}
            {/* Over the input: only the line that says the agent is asleep. What the task is
                doing and has done is its head, at the top (TaskHead). */}
            <SessionDock activeInfo={activeInfo} running={running} send={send} todo={todo} />
            {/* A parked child/spawn ask (P1): the decision dock for the active session's
                subtree, right above the input it belongs to. */}
            {activePermission && (
              <PermissionDock
                request={activePermission}
                responding={permissionResponding}
                from={permissionFrom}
                onReply={(reply) => void respondPermission(activePermission, reply)}
              />
            )}
            {blockedCard ? (
              // The decision sits where the composer sat: same column, same width.
              <div className="decision-wrap" data-testid="decision-wrap">
                {blockedCard}
              </div>
            ) : (
              <Composer
                mode={mode}
                plan={plan}
                model={model}
                models={models}
                modelLabels={modelLabels}
                setupSlot={
                  // Not while Reeve starts: the stand-in's folder pickers are no conversation's.
                  idle && !starting && !sessionId.startsWith("__run__") ? (
                    <SessionSetupRow
                      folderName={tempWorkspace ? "Temporary folder" : workspaceName}
                      folderPath={workspace}
                      tempFolder={tempWorkspace}
                      environment={activeEnvironment}
                      remoteWorkspace={activeEnvironment ? remoteWorkspace : null}
                      onPickRemote={pickRemoteEnvironment}
                      onClearEnvironment={clearRemoteEnvironment}
                      remoteError={environmentError}
                      onPickFolder={pickDraftFolder}
                      onUseTemp={pickDraftTemp}
                      onOpenRemoteSettings={() => openSettings("remote")}
                    />
                  ) : undefined
                }
                running={running}
                // Reeve starting holds a sent message the way a compaction does: queued, and sent
                // once the conversation is open and connected.
                holding={(compacting && !running) || starting}
                holdFor={starting ? "Reeve is ready" : undefined}
                provisional={starting && sessionId === provisionalSessionRef.current}
                touchedFiles={touchedFiles}
                connected={connected}
                modelReady={modelReady}
                onConnectModel={openModelSetup}
                onOpenMemory={() => openSettings("memory")}
                onConfigureVoiceInput={() => openSettings("voice")}
                onSend={send}
                onQueue={queueMessage}
                onSteer={steerMessage}
                queued={queuedPrompts}
                onUnqueue={(id) => setQueuedPrompts((p) => p.filter((x) => x.id !== id))}
                onInterrupt={interrupt}
                onModeChange={changeMode}
                onPlanChange={changePlan}
                onModelChange={changeModel}
                onModelsChanged={loadSettings}
                onCompactStatus={onCompactStatus}
                onGoalNotice={onGoalNotice}
                thinking={thinking}
                onThinkingChange={changeThinking}
                comments={reviewComments}
                onRemoveComment={(id) => setReviewComments((p) => p.filter((x) => x.id !== id))}
                onEditComment={(id, text) => setReviewComments((p) => p.map((x) => (x.id === id ? { ...x, text } : x)))}
                sessionId={sessionId}
                workspace={workspace || ""}
                // The composer's readout chips (fresh) / footer readout (in-session):
                // the branch the topbar already resolves, and the folder's display name.
                // A temporary folder says nothing — its scratch name is noise, not a fact.
                // A REMOTE session replaces both with `user@host:path`:
                // the local folder/branch facts describe the wrong machine.
                gitBranch={activeEnvironment ? null : gitBranch}
                // The chip switches the folder's branch; before the first message
                // it can start the session in a new worktree instead (a fresh draft there).
                onBranchSwitched={(branch) => {
                  setGitBranch(branch);
                  setBrowserRefreshKey((k) => k + 1);
                }}
                onBranchWorktree={items.some((item) => item.kind === "user") ? undefined : (path) => pickDraftFolder(path)}
                workspaceName={activeEnvironment ? null : workspaceName}
                environment={activeEnvironment}
                remoteWorkspace={activeEnvironment ? remoteWorkspace : null}
                // P4: the @mention menu reads the same `GET /v1/subagents` list the task
                // card colors come from.
                agents={subagents}
                // Built-in session commands: a slash row is a second doorway onto the
                // handlers the app already uses, never a second implementation.
                onNewSession={() => startNewSession()}
                onExportSession={() => void exportConversation()}
                onForkSession={() => void forkConversation(sessionId)}
                onOpenConnectors={() => setSurface("integrations")}
                // While Reeve starts, no strip of folder pickers: they set up a new conversation, and
                // which one opens is not known yet (the last one, almost always, which has begun).
                hasMessages={starting || items.some((item) => item.kind === "user")}
                // `/undo` / `/redo`: the same routes the transcript's boundary
                // reflects; the draft comes back from the revert response.
                onUndoSession={() => void undoConversation()}
                onReloadPlugins={() => void reloadPlugins()}
                onRedoSession={() => void redoConversation()}
                canUndo={canUndo}
                canRedo={canRedo}
                // `/open`: the file picker over this session's
                // roots; the pick lands in the RightRail viewer.
                onOpenFile={() => setFilePickerOpen(true)}
                // `/terminal`: the panel's visibility.
                onToggleTerminal={() => openTerminal()}
                // `/workspace`: the sidebar section.
                onToggleWorktrees={() => toggleWorktrees()}
                // `/share` / `/unshare`: over the configured endpoint only.
                onShareSession={() => void shareConversation()}
                canShare={
                  shareStatus
                    ? shareStatus.configured || !!shareStatus.share?.url
                    : undefined
                }
                shareReason={
                  shareStatus && !shareStatus.configured && !shareStatus.share?.url
                    ? "Sharing needs a configured endpoint"
                    : undefined
                }
                onUnshareSession={() => void unshareConversation()}
                canUnshare={!!shareStatus?.configured && !!shareStatus?.can_revoke}
                unshareReason={
                  shareStatus && !shareStatus.configured
                    ? "Sharing needs a configured endpoint"
                    : shareStatus && !shareStatus.share
                      ? "Not shared"
                      : shareStatus && !shareStatus.can_revoke
                        ? "The configured endpoint has no revoke"
                        : undefined
                }
                prefill={composerPrefill}
                resetKey={sessionId}
                usage={usage}
                contextWindow={modelContextWindows[model]}
                compactAt={compactAt}
                contextBar={contextBar}
                compacting={compacting}
                loadMetrics={() => getSessionMetrics(sessionId).catch(() => null)}
              />
            )}
            {startPage && !starting && (
              <div className="start-below">
                <IdleStart
                  sessionId={sessionId}
                  onPrefill={prefillComposer}
                  sessions={sessions}
                  folder={workspace && !tempWorkspace ? workspace : null}
                  onOpenSession={(id, ws) => {
                    const run = sessions.find((s) => s.session_id === id)?.automation;
                    if (run) openRunSession(id, ws, run);
                    else void selectSession(id, ws);
                  }}
                />
              </div>
            )}
            {/* `/terminal` now opens the rail's Terminal TAB (RightRail), so no dock under
                the composer: one surface, one shell (the reference design's sidebar-right shape). */}
            </ShownChildContext.Provider>
            </SessionShellContext.Provider>
                  </div>
          <RightRail
            active={surface === "session" && !railHidden && !starting}
            sessionId={sessionId}
            refreshKey={browserRefreshKey}
            onPreviewChange={onArtifactPreview}
            terminalRequestKey={terminalRequest}
            seeRequest={seeRequest}
            // The work is the stage only where there is work: a conversation that changed files.
            hasWork={sessionHasWork}
            onHidePanel={() => setRailHiddenPersist(true)}
            shellLabel={railShellLabel}
            reviewComments={reviewComments}
            onAddReviewComment={(c) => setReviewComments((p) => [...p, c])}
            onDeleteReviewComment={(id) => setReviewComments((p) => p.filter((x) => x.id !== id))}
            onEditReviewComment={(id, text) => setReviewComments((p) => p.map((x) => (x.id === id ? { ...x, text } : x)))}
            onSendReviewComments={() => send("")}
            items={items}
            childRequest={childRequest}
            subagents={subagentNodes}
            childSessions={childSessions}
            onPromoteTask={promoteTask}
            onOpenChild={openChild}
            mainRunning={running}
            onShownChild={setShownChild}
          />
        </div>
      </div>

            <SessionOverlays
              workspaceTrustRequest={workspaceTrustRequest}
              setWorkspaceTrustRequest={setWorkspaceTrustRequest}
              filePickerOpen={filePickerOpen}
              filePickerSessionId={sessionId}
              openFileInViewer={openFileInViewer}
              setFilePickerOpen={setFilePickerOpen}
            />
    </div>
    </FolderActivityContext.Provider>
  );
}

