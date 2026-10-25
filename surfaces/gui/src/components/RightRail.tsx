import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { createPortal } from "react-dom";
import { DURATION, usePrefersReducedMotion } from "../ui/motion";
import {
  createSessionEntry,
  deleteSessionEntry,
  getRoots,
  getSessionChanges,
  getUndoableCalls,
  moveSessionEntry,
  readArtifact,
  renameSessionEntry,
  revealPath,
  searchSessionFiles,
  type ArtifactContent,
  type ArtifactInfo,
  type ChangeFile,
  type FileEditResult,
  type RootInfo,
  type SessionFile,
} from "../api";
import type { Item, ReviewComment } from "../types";
import { lastTurnFor, recordedChangesFromItems, relativeToRoots } from "../reviewChanges";
import { ReviewPane } from "./rail/ReviewPane";
import { statusType } from "./rail/changeStatus";
import type { DiffStyle } from "./rail/DiffView";
import {
  clampPanelWidth,
  clampTreeWidth,
  clearStoredSize,
  defaultPanelWidth,
  workPanelWidth,
  readStoredSize,
  TREE_WIDTH_DEFAULT,
  writeStoredSize,
} from "../app/railSizing";
import { FM_TREE_WIDTH_KEY, RAIL_WIDTH_KEY } from "../app/appHelpers";
import { FileIcon } from "./FileIcon";
import { Icon } from "./Icon";
import { OPEN_ARTIFACT_EVENT, OPEN_FILES_EVENT } from "./Markdown";
import { TerminalPanel, type TerminalTabState } from "./session/TerminalPanel";
import { closeUserTerminal } from "../api/terminal";
import { GuideView, type GuideDoor, type GuidePick, type GuideReview } from "./rail/GuideView";
import { RailTabs, type NewTabKind } from "./rail/RailTabs";
import { SubagentPanel } from "./rail/SubagentPanel";
import { runIdOf } from "./session/SubagentRow";
import type { DelegationNode } from "../delegations";
import type { ChildSession } from "../childSessions";
import { BrowserView } from "./rail/BrowserView";
import { readShellPref, writeShellPref } from "./rail/shellPref";
import { NewEntryRow, trashName, type TreeAction } from "./rail/filesParts";
import { FilePreview, type FilePreviewHandle } from "./rail/FilePreview";
import { kindOfPath, writeDraft } from "./rail/fileKinds";
import { treeChain } from "./rail/treeReveal";
import {
  browserCanStep,
  browserClearHistory,
  browserNavigate,
  browserStep,
  closeRailTab,
  isDesignPath,
  openDesignTab,
  openDesignAppTab,
  OPEN_DESIGN_APP_EVENT,
  OPEN_BROWSER_EVENT,
  openBrowserAt,
  samePage,
  openRailTab,
  openSubagentTab,
  reopenRailTab,
  reorderRailTabs,
  seedRailTabs,
  readStoredRailTabs,
  writeStoredRailTabs,
  type RailTab,
  type RailTabsState,
} from "./rail/tabs";
import { DesignView } from "./rail/DesignView";
import { DesignReview } from "./rail/DesignReview";
import { DesignsView } from "./rail/DesignsView";

/** The tabs a design write may take the panel's place from: nothing the person is reading. */
const DESIGN_QUIET_KINDS = new Set(["guide", "designs", "design"]);

/** A folder's own name, the Start page's title ("workspace" is the root's label, not its name). */
function folderName(path: string | undefined): string | undefined {
  return path?.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || undefined;
}

export interface RightRailProps {
  /** The panel is shown. Hidden, it reads nothing. */
  active: boolean;
  sessionId: string;
  /** Bumped when the conversation may have changed files: the panel reads them again. */
  refreshKey: number;
  /** Told when a file opened in Files starts or stops taking the navigation's room. */
  onPreviewChange?: (open: boolean) => void;
  /** Review's line comments. App holds them, because they go with the next message. */
  reviewComments?: ReviewComment[];
  onAddReviewComment?: (c: ReviewComment) => void;
  onDeleteReviewComment?: (id: string) => void;
  onEditReviewComment?: (id: string, text: string) => void;
  /** Sends the line comments to Reeve now (Review's "Send to Reeve"). */
  onSendReviewComments?: () => void;
  /** Bumped by `/terminal` and ⌃`: a new Terminal tab opens. */
  terminalRequestKey?: number;
  /** A file whose changes the transcript asked to see (a receipt's row): Review shows it. */
  seeRequest?: { path: string; key: number } | null;
  /** The conversation changed files: before a drag the pane takes the room the conversation
   *  does not need (the work is the stage, 2026-10-13); otherwise the moderate default. */
  hasWork?: boolean;
  onHidePanel?: () => void;
  /** The session's shell name for the guide's informational chip (`shellDisplayName`). */
  shellLabel?: string;
  /** The conversation so far — the Review tab folds its diff cards into recorded changes. */
  items?: Item[];
  /** A child session the chat asked to open (a row's Open): its tab is shown, or made. */
  childRequest?: { childId: string; title: string; group?: string; key: number } | null;
  /** What the chat knows of this conversation's children: a tab's mark and its head. */
  subagents?: readonly DelegationNode[];
  /** Every child the app has heard of, for the ones the chat has no row for. */
  childSessions?: Record<string, ChildSession>;
  onPromoteTask?: () => void;
  onOpenChild?: (run: { runId: string; title: string }) => void;
  /** The main agent's turn is running: said over a child that took the chat's place. */
  mainRunning?: boolean;
  /** The child whose tab is on screen, or null. The chat marks its row. */
  onShownChild?: (childId: string | null) => void;
}

// The panel's sizes are app/railSizing.ts's: the panel keeps at least 480px (800 for a split
// diff) and the conversation 450px. A width the person dragged is kept on this device and only
// clamped as the window draws; resizing the window never writes it.
const railSplit = (style: DiffStyle) => style === "split";
const viewportWidth = () => (typeof window === "undefined" ? 0 : window.innerWidth);

export function RightRail({
  active,
  sessionId,
  refreshKey,
  onPreviewChange,
  reviewComments,
  onAddReviewComment,
  onDeleteReviewComment,
  onEditReviewComment,
  onSendReviewComments,
  terminalRequestKey,
  seeRequest,
  hasWork = false,
  onHidePanel,
  shellLabel,
  items,
  childRequest,
  subagents,
  childSessions,
  onPromoteTask,
  onOpenChild,
  mainRunning,
  onShownChild,
}: RightRailProps) {
  // Review answers ONE question — what changed, and do I keep it — diff first (rail/ReviewPane):
  // a scope menu, every changed file stacked, a commit box. Browsing what is in the folders NOW
  // is the Files tab's job (the old "All" scope made them look alike).
  // Hidden, the panel stays in the page for one close: it narrows to its right edge on the
  // beat the chat's margin follows, and only then leaves. The flag is set during the render
  // that sees `active` turn off, so no frame shows the page without the panel before it.
  const reduceMotion = usePrefersReducedMotion();
  const [wasActive, setWasActive] = useState(active);
  const [leaving, setLeaving] = useState(false);
  if (wasActive !== active) {
    setWasActive(active);
    setLeaving(!active && !reduceMotion);
  }
  useEffect(() => {
    if (!leaving) return;
    const timer = window.setTimeout(() => setLeaving(false), DURATION.base);
    return () => window.clearTimeout(timer);
  }, [leaving]);
  const [roots, setRoots] = useState<RootInfo[]>([]);
  const [changes, setChanges] = useState<ChangeFile[]>([]);
  const [isGit, setIsGit] = useState(true);
  // Whose working tree `changes` is. A read answered after the person moved on is dropped, and
  // until this conversation's first read is back Review says it is loading, not that nothing
  // changed. Both seen on the real app (2026-10-14): a `git status` that took twelve seconds
  // showed one conversation's files under the next, and "Everything is committed" over two
  // uncommitted files.
  const [changesFor, setChangesFor] = useState<string | null>(null);
  const shownSession = useRef(sessionId);
  shownSession.current = sessionId;
  // Cross-links between the two tabs: a file to open in Files (a transcript chip, Review's
  // file name) and a file to show in Review (Files' "See what changed"). The counter makes
  // the same path requested twice still a new request.
  const [filesRequest, setFilesRequest] = useState<{ path: string; line?: number; n: number } | null>(null);
  const [reviewFocus, setReviewFocus] = useState<{ path: string; n: number } | null>(null);
  const [filesPreviewOpen, setFilesPreviewOpen] = useState(false);
  const [diffStyle, setDiffStyle] = useState<DiffStyle>("unified");
  // The tab shell (rail/tabs.ts): seeded by the guide, persisted per session. Review and
  // Terminal are singletons; Browser allows many; the last close reseeds the guide.
  const [tabsState, setTabsState] = useState<RailTabsState>(
    () => readStoredRailTabs(sessionId) ?? { tabs: seedRailTabs(), activeId: "guide" },
  );
  const [fullscreen, setFullscreen] = useState(false);
  // Where the open tab draws its own actions in the tab row (a design's Export, Share…).
  const [actionsSlot, setActionsSlot] = useState<HTMLDivElement | null>(null);
  // What each browser tab's page calls itself (the desktop shell's browser reports it).
  const [paneTitles, setPaneTitles] = useState<Record<string, string>>({});
  // What each terminal tab says of itself (its command or folder, and whether it runs).
  const [termStates, setTermStates] = useState<Record<string, TerminalTabState>>({});
  // A terminal tab whose close would stop a running command: asked about first.
  const [stopAsk, setStopAsk] = useState<string | null>(null);
  // Closed tabs, most recent first (the tab overview's "Recently closed"). Session memory
  // only: a reload has no reattach story for a terminal, so it is not persisted.
  const [recentClosed, setRecentClosed] = useState<RailTab[]>([]);
  // The pane's authored width (a per-device preference) and the measured row width it is
  // clamped against. Window resizes and diff-style switches only clamp the RENDERED width —
  // the preference is kept, so a wider window restores the user's drag (the reference design does the
  // same with its render-time clampSessionPanelWidth).
  // `null` until the person drags: then the pane takes the room the conversation does not need
  // (railSizing `workPanelWidth`), as the window and the nav change.
  const [panelW, setPanelW] = useState<number | null>(() => readStoredSize(RAIL_WIDTH_KEY) ?? null);
  const [available, setAvailable] = useState<number | undefined>(undefined);
  // The panel lies over the chat (a window with no room for both, styles.css): what it
  // shows has taken the chat's place, and says how to get back.
  const [overlaying, setOverlaying] = useState(false);
  const dragW = useRef({ panel: 0, tree: 0 });
  const rootRef = useRef<HTMLElement | null>(null);

  const effectivePanelW = clampPanelWidth({
    width: panelW ?? (hasWork ? workPanelWidth(available, viewportWidth()) : defaultPanelWidth(viewportWidth())),
    available,
    split: railSplit(diffStyle),
  });
  const mainEl = () => (rootRef.current?.closest(".main") as HTMLElement | null) ?? null;

  const activeTab = tabsState.tabs.find((t) => t.id === tabsState.activeId) ?? tabsState.tabs[0];
  const reviewActive = activeTab?.kind === "review";
  const openTab = (kind: NewTabKind, opts?: { shell?: string; url?: string }) => {
    if (kind === "terminal") {
      // A new terminal tab carries the menu's pick (the guide's chevron) or the remembered
      // one; the reference design allows many, so this never reuses an existing tab.
      const shell = opts?.shell ?? readShellPref() ?? undefined;
      if (opts?.shell) writeShellPref(opts.shell);
      setTabsState((s) => openRailTab(s.tabs, "terminal", { shell }));
      return;
    }
    setTabsState((s) => openRailTab(s.tabs, kind, opts?.url ? { url: opts.url } : {}));
  };
  const removeTab = (id: string) => {
    // The tab overview offers "Recently closed", so a close is remembered here — capped,
    // most recent first, and only for real tabs (the guide is the seed, not a place).
    const closing = tabsState.tabs.find((tab) => tab.id === id);
    if (closing && closing.kind !== "guide") {
      setRecentClosed((list) => [closing, ...list.filter((t) => t.id !== closing.id)].slice(0, 8));
    }
    // A terminal tab is its shell: closing it stops the shell, which nothing could reach again.
    if (closing?.kind === "terminal") void closeUserTerminal(sessionId, closing.terminalId || "1").catch(() => undefined);
    setPaneTitles(({ [id]: _gone, ...rest }) => rest);
    setTermStates(({ [id]: _gone, ...rest }) => rest);
    setTabsState((s) => closeRailTab(s.tabs, id, s.activeId));
  };
  const closeTab = (id: string) => {
    // A command still running asks first: closing the tab stops it.
    if (termStates[id]?.running) {
      setStopAsk(id);
      return;
    }
    removeTab(id);
  };
  const selectTab = (id: string) => setTabsState((s) => ({ ...s, activeId: id }));
  const reorderTabs = (fromId: string, toId: string) =>
    setTabsState((s) => ({ ...s, tabs: reorderRailTabs(s.tabs, fromId, toId) }));
  const reopenTab = (tab: RailTab) => {
    setRecentClosed((list) => list.filter((t) => !(t.id === tab.id && t.kind === tab.kind)));
    setTabsState((s) => reopenRailTab(s.tabs, tab));
  };
  const pickDoor = (door: GuideDoor, pick?: GuidePick) =>
    openTab(door, pick ? { shell: pick.shell, url: pick.url } : undefined);
  // Below 768px the panel takes the frame.
  const measured = typeof available === "number" ? available : 0;
  const railFullscreen = fullscreen || (measured > 0 && measured < 768);

  const refreshAll = () => {
    const asked = sessionId;
    const current = () => shownSession.current === asked;
    getUndoableCalls(asked)
      .then((ids) => current() && setUndoableIds(new Set(ids)))
      .catch(() => current() && setUndoableIds(new Set()));
    getSessionChanges(asked)
      .then((c) => {
        if (!current()) return;
        setIsGit(c.is_git);
        setChanges(c.files || []);
        setChangesFor(asked);
      })
      .catch(() => {
        if (!current()) return;
        setIsGit(false);
        setChanges([]);
        setChangesFor(asked);
      });
    getRoots(asked)
      .then((list) => current() && setRoots(list))
      .catch(() => current() && setRoots([]));
  };

  // Shown, on another conversation, or told the files may have changed: read everything again.
  useEffect(() => {
    if (active) refreshAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, sessionId, refreshKey]);

  // A folder added or taken back elsewhere (the mode menu's projects, the start panel) is
  // announced, and the tree reads its roots again so it shows at once.
  useEffect(() => {
    if (!active) return;
    const onRoots = (event: Event) => {
      if ((event as CustomEvent).detail !== sessionId) return;
      getRoots(sessionId)
        .then(setRoots)
        .catch(() => {});
    };
    window.addEventListener("reeve:roots-changed", onRoots);
    return () => window.removeEventListener("reeve:roots-changed", onRoots);
  }, [active, sessionId]);

  // Coming back to the window reads the changes again: an edit made in an editor or a
  // commit made in a terminal shows up without a Refresh button to hunt for.
  useEffect(() => {
    if (!active) return;
    const onFocus = () => refreshAll();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, sessionId]);

  // Switching conversations drops the open tabs — they belong to the previous workspace.
  // The tab shell is restored from this session's own preference (terminals are never
  // restored; see rail/tabs.ts).
  useEffect(() => {
    setChanges([]);
    setFilesRequest(null);
    setReviewFocus(null);
    setTabsState(readStoredRailTabs(sessionId) ?? { tabs: seedRailTabs(), activeId: "guide" });
    // A page's name is the previous conversation's: its tabs have the same ids (`browser-1`), so a
    // blank Browser here wore the other session's page title (owner, 2026-10-20).
    setPaneTitles({});
    // A child's tab belongs to the conversation that started it.
    setRecentClosed((list) => list.filter((tab) => tab.kind !== "subagent"));
  }, [sessionId]);

  useEffect(() => {
    writeStoredRailTabs(sessionId, tabsState);
  }, [sessionId, tabsState]);

  // `/terminal` and ⌃` (App bumps the key): toggle the Terminal tab — active closes it,
  // otherwise open/activate it (the shell itself keeps running server-side).

  // A row's Open in the chat: the child's own tab, or the one it already has. The key is
  // remembered across a remount (Settings and back), so an old request opens nothing.
  const childKeySeen = useRef(childRequest?.key ?? 0);
  useEffect(() => {
    const requested = childRequest;
    if (!requested?.childId || requested.key === childKeySeen.current) return;
    childKeySeen.current = requested.key;
    setTabsState((state) => openSubagentTab(state.tabs, requested));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [childRequest?.key]);

  useEffect(() => {
    if (!terminalRequestKey) return;
    // `/terminal` and ⌃` open a NEW terminal tab with the remembered shell (the terminal
    // action is "new terminal", not a toggle: each shell keeps its own pty).
    const shell = readShellPref() ?? undefined;
    setTabsState((s) => openRailTab(s.tabs, "terminal", { shell }));
  }, [terminalRequestKey]);

  const filesActive = activeTab?.kind === "files";
  // Only a file opened in Files takes the room of the nav. Review's diffs fit the panel, and
  // the nav folding away the moment Review opened read as the app jumping.
  const previewOpen = filesActive && filesPreviewOpen;
  // Told on a change only: App draws a new callback whenever its navigation toggles, and telling
  // that one "open" again folded a sidebar the person had just opened (owner-hit 2026-08-21).
  const toldPreview = useRef(false);
  useEffect(() => {
    if (previewOpen === toldPreview.current) return;
    toldPreview.current = previewOpen;
    onPreviewChange?.(previewOpen);
  }, [previewOpen, onPreviewChange]);

  // Designs' Compare: the page to open with its variants side by side.
  const [compareRequest, setCompareRequest] = useState<{ path: string; n: number } | null>(null);
  // What the conversation asks the panel to show, by window events: a file, the folder, a design
  // beside its app, a web page.
  useEffect(() => {
    if (!sessionId) return;
    const detail = (e: Event): Record<string, unknown> => (e as CustomEvent).detail ?? {};
    const onOpen = (e: Event) => {
      const path = String(detail(e).path || "");
      if (!path) return;
      // A design page opens in its own tab, with the design's tools: one under
      // `design/`. Any other page (a flow drawn as HTML, a report) is a file and opens in Files: a
      // diagram the person never asked to have designed came up as a design with its tools
      // (owner, 2026-10-20).
      if (isDesignPath(path)) {
        setTabsState((s) => openDesignTab(s.tabs, path));
        return;
      }
      // A file link (a transcript chip, Review's "Open file") shows the file as it is now:
      // that is the Files tab's question, not Review's.
      setTabsState((s) => openRailTab(s.tabs, "files"));
      // `file.py:42` in an answer asks for the line too (markdown/folderFiles.ts).
      const line = Number(detail(e).line) || undefined;
      setFilesRequest((prev) => ({ path, line, n: (prev?.n ?? 0) + 1 }));
    };
    // The session's folder as a tree: the Files tab, nothing opened in it.
    const onFiles = () => setTabsState((s) => openRailTab(s.tabs, "files"));
    // A `compare_design` row's Review: the app beside its locked design.
    const onReview = (e: Event) => {
      const path = String(detail(e).path || "");
      const url = String(detail(e).url || "");
      if (path && url) setTabsState((s) => openDesignAppTab(s.tabs, path, url));
    };
    // A web link in the conversation (desktop): its page in the Browser (rail/browserLinks.ts).
    const onBrowse = (e: Event) => {
      const url = String(detail(e).url || "");
      if (url) setTabsState((s) => openBrowserAt(s.tabs, url));
    };
    window.addEventListener(OPEN_ARTIFACT_EVENT, onOpen);
    window.addEventListener(OPEN_FILES_EVENT, onFiles);
    window.addEventListener(OPEN_DESIGN_APP_EVENT, onReview);
    window.addEventListener(OPEN_BROWSER_EVENT, onBrowse);
    return () => {
      window.removeEventListener(OPEN_ARTIFACT_EVENT, onOpen);
      window.removeEventListener(OPEN_FILES_EVENT, onFiles);
      window.removeEventListener(OPEN_DESIGN_APP_EVENT, onReview);
      window.removeEventListener(OPEN_BROWSER_EVENT, onBrowse);
    };
  }, [sessionId]);

  // A design the agent writes while the panel is open comes up in its own tab, as OpenDesign opens
  // a file its agent wrote. Only then: the panel itself opens when the person opens it
  // (owner, 2026-10-16), and a tab the person is reading (Review, a terminal) is not taken from
  // them. The chat's row under the write has the page's picture and Open either way. A write is
  // new when its turn is running: a reopened conversation's history opens nothing.
  const designWrites = useMemo(
    () =>
      (items ?? []).flatMap((item) =>
        item.kind === "tool" && item.designCheck
          ? item.designCheck.flatMap((check) =>
              check.path && isDesignPath(check.path) ? [{ key: `${item.callId ?? item.id}:${check.path}`, path: check.path }] : [],
            )
          : [],
      ),
    [items],
  );
  const designSeen = useRef<{ session: string; keys: Set<string> } | null>(null);
  useEffect(() => {
    if (!designSeen.current || designSeen.current.session !== sessionId) {
      designSeen.current = { session: sessionId, keys: new Set(designWrites.map((write) => write.key)) };
      return;
    }
    const seen = designSeen.current.keys;
    const fresh = designWrites.filter((write) => !seen.has(write.key));
    fresh.forEach((write) => seen.add(write.key));
    const latest = fresh[fresh.length - 1];
    if (!latest || !active || !mainRunning) return;
    setTabsState((state) => {
      const current = state.tabs.find((tab) => tab.id === state.activeId);
      if (current && !DESIGN_QUIET_KINDS.has(current.kind)) return state;
      return openDesignTab(state.tabs, latest.path);
    });
  }, [designWrites, sessionId, active, mainRunning]);

  // Measure the row the pane lives in (the reference design observes the same row with a resize observer,
  // so a collapsing sidebar re-clamps too). jsdom has no ResizeObserver and measures 0 — the
  // width is then left untouched, exactly like the reference design before the layout is measured.
  useLayoutEffect(() => {
    if (!active) return;
    const main = mainEl();
    if (!main) return;
    const measure = () => {
      const w = main.getBoundingClientRect().width;
      if (w > 0) setAvailable(w);
      const chat = main.querySelector(".main-chat")?.getBoundingClientRect();
      const own = rootRef.current?.getBoundingClientRect();
      setOverlaying(!!chat && !!own && own.width > 0 && own.left < chat.right - 1);
    };
    measure();
    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(measure);
      observer.observe(main);
      // The panel opens by widening: whether it lies over the chat is only true to ask
      // once it has its width, and the row it lives in does not change size for that.
      if (rootRef.current) observer.observe(rootRef.current);
      return () => observer.disconnect();
    }
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [active, sessionId]);

  // Apply the CLAMPED width; `--review-rail-w` also drives the chat's margin and the topbar's
  // right edge, so the panel and the room it makes move on the same value.
  useLayoutEffect(() => {
    const main = mainEl();
    if (main) main.style.setProperty("--review-rail-w", `${effectivePanelW}px`);
  }, [effectivePanelW, active]);

  // Drag the PANEL's left edge to widen the whole rail (chat gives up the room). The CSS
  // var drives both the rail width and the chat margin, so they stay in lockstep. The drag
  // is clamped by the same pure rule as the render (the ResizeHandle does the same
  // min/max clamp): the chat keeps >= 450px and the pane keeps >= 480px (>= 800px split),
  // yielding to the chat when the window cannot fit both.
  const setPanelVar = (px: number) => mainEl()?.style.setProperty("--review-rail-w", `${px}px`);
  const startEdgeResize = (e: ReactPointerEvent) => {
    e.preventDefault();
    const main = mainEl();
    // While the divider is being dragged the width IS the pointer, so neither the panel nor
    // the chat may animate toward it (see the transition block in styles.css).
    document.body.dataset.railResize = "1";
    dragW.current.panel = effectivePanelW;
    const move = (ev: PointerEvent) => {
      const rect = main?.getBoundingClientRect();
      const right = rect?.right ?? window.innerWidth;
      const measured = rect && rect.width > 0 ? rect.width : available ?? viewportWidth();
      const next = clampPanelWidth({
        width: right - ev.clientX,
        available: measured,
        split: railSplit(diffStyle),
      });
      dragW.current.panel = next;
      setPanelVar(next);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      delete document.body.dataset.railResize;
      // The drag's final width becomes the remembered preference (the reference design persists the
      // panel width on every resize tick; we write once, on release).
      setPanelW(dragW.current.panel);
      writeStoredSize(RAIL_WIDTH_KEY, dragW.current.panel);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  // Double-clicking the divider forgets the preference and returns to the default.
  const resetEdgeResize = () => {
    clearStoredSize(RAIL_WIDTH_KEY);
    dragW.current.panel = hasWork ? workPanelWidth(available, viewportWidth()) : defaultPanelWidth(viewportWidth());
    setPanelW(null);
  };

  // Undoability comes from the server's undo store as well as the live frame: rows rebuilt
  // from a reopened transcript never carry it, and the list said "not undoable" for edits
  // the turn's receipt could still put back (review audit V4, traced 2026-09-26).
  const [undoableIds, setUndoableIds] = useState<Set<string>>(new Set());
  const recorded = useMemo(
    () =>
      recordedChangesFromItems(items ?? []).map((change) =>
        change.callId && undoableIds.has(change.callId) ? { ...change, undoable: true } : change,
      ),
    [items, undoableIds],
  );
  // A changed file's status for the All-files tree: exact path first, then a suffix match
  // (tree paths are absolute under their roots; `getSessionChanges` reports paths relative
  // to the workspace). A miss simply means no mark, never a wrong file.
  const changedPaths = useMemo(
    () => new Map(changes.map((f) => [f.path, f.status] as const)),
    [changes],
  );
  // The folder dot: a directory is "changed" when anything below it is.
  const changedBelow = (path: string): boolean => {
    const prefix = `${path.replace(/\\/g, "/").replace(/\/+$/, "")}/`;
    return changes.some((f) => f.path.replace(/\\/g, "/").startsWith(prefix));
  };
  // Files → Review: show this file's change. The pane picks the scope: Reeve's own edit
  // (This session, with Undo) or what git alone knows of (Uncommitted).
  const seeChanges = (path: string) => {
    setTabsState((s) => openRailTab(s.tabs, "review"));
    const rel = relativeToRoots(path, roots.map((root) => root.path));
    const normalized = path.replace(/\\/g, "/");
    const change = changes.find((f) => normalized === f.path || normalized.endsWith(`/${f.path.replace(/^\.\//, "")}`));
    const byTool = recordedTurnOf(path) !== undefined;
    setReviewFocus((prev) => ({ path: byTool || !change ? rel : change.path, n: (prev?.n ?? 0) + 1 }));
  };
  useEffect(() => {
    if (seeRequest?.path) seeChanges(seeRequest.path);
    // No file named: Review's list.
    else if (seeRequest) setTabsState((s) => openRailTab(s.tabs, "review"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seeRequest?.key]);
  // A Review card's file name: the file as it is now, in Files (a workspace-relative git
  // path is anchored on the session's primary folder).
  const openInFiles = (path: string) => {
    const primary = roots.find((root) => root.primary) ?? roots[0];
    const full = /^([A-Za-z]:|\/)/.test(path) || !primary ? path : `${primary.path.replace(/[\\/]+$/, "")}/${path}`;
    setTabsState((state) => openRailTab(state.tabs, "files"));
    setFilesRequest((prev) => ({ path: full, n: (prev?.n ?? 0) + 1 }));
  };
  // The Review tab wears how many files changed: git's working tree in a git folder,
  // otherwise the files Reeve's tools changed.
  const reviewCount = useMemo(
    () => new Set((isGit ? changes.map((c) => c.path) : recorded.map((c) => c.path))).size,
    [isGit, changes, recorded],
  );
  // The Start page's facts: what the Review row says changed, and the dev server a terminal
  // tab started.
  const guideReview = useMemo<GuideReview>(() => {
    if (!isGit) return { files: reviewCount, add: 0, del: 0 };
    return changes.reduce(
      (sum, file) => ({ files: sum.files, add: sum.add + (file.add || 0), del: sum.del + (file.del || 0) }),
      { files: reviewCount, add: 0, del: 0 },
    );
  }, [isGit, changes, reviewCount]);
  const recordedTurnOf = (path: string) => lastTurnFor(recorded, path, roots.map((root) => root.path));
  const changedStatus = (path: string): string | undefined => {
    const direct = changedPaths.get(path);
    if (direct) return direct;
    // A folder that is not a git repository has no working-tree status, but a file Reeve
    // changed there is still a change the tree must mark.
    if (!isGit && recordedTurnOf(path) !== undefined) return "M";
    const normalized = path.replace(/\\/g, "/");
    for (const [changePath, status] of changedPaths) {
      const needle = `/${changePath.replace(/^\.\//, "")}`;
      if (normalized === changePath || normalized.endsWith(needle)) return status;
    }
    return undefined;
  };

  // What the chat knows of each child that has a tab: the tab's mark, and the panel's head.
  const nodeOf = (childId: string | undefined) =>
    childId ? subagents?.find((node) => runIdOf(node) === childId) : undefined;
  const childBusy = (childId: string | undefined) => {
    const node = nodeOf(childId);
    return node ? node.outcome === "running" : !!childId && childSessions?.[childId]?.status === "busy";
  };
  const childTabs = tabsState.tabs.filter((tab) => tab.kind === "subagent");
  const shownChild = active && activeTab?.kind === "subagent" ? (activeTab.childId ?? null) : null;
  useEffect(() => {
    onShownChild?.(shownChild);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shownChild]);
  useEffect(() => () => onShownChild?.(null), []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!active && !leaving) return null;

  return (
    <aside
      className={"right-rail review-mode" + (railFullscreen ? " rail-fullscreen" : "") + (leaving ? " rail-leaving" : "")}
      aria-hidden={leaving || undefined}
      {...(leaving ? { inert: "" } : {})}
      ref={rootRef as any}
    >
      <div
        className="rev-edge"
        onPointerDown={startEdgeResize}
        onDoubleClick={resetEdgeResize}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize panel"
        data-testid="panel-resize"
      />
      <RailTabs
        tabs={tabsState.tabs}
        activeId={tabsState.activeId}
        onSelect={selectTab}
        onClose={closeTab}
        onNew={openTab}
        onReorder={reorderTabs}
        recentClosed={recentClosed}
        onReopen={reopenTab}
        onHide={() => onHidePanel?.()}
        fullscreen={railFullscreen}
        onToggleFullscreen={() => setFullscreen((v) => !v)}
        onActionsSlot={setActionsSlot}
        counts={{ review: reviewCount }}
        folderCount={roots.length}
        titles={{ ...paneTitles, ...Object.fromEntries(Object.entries(termStates).map(([id, state]) => [id, state.title])) }}
        busy={{
          ...Object.fromEntries(Object.entries(termStates).map(([id, state]) => [id, state.running])),
          ...Object.fromEntries(childTabs.map((tab) => [tab.id, childBusy(tab.childId)])),
        }}
        outcomes={Object.fromEntries(
          childTabs.flatMap((tab) => {
            const outcome = nodeOf(tab.childId)?.outcome;
            // `settled` is a child whose turn ended while its call still waits for the rest of
            // its batch: the tab wears the plain icon, not a check it has not earned.
            return outcome && outcome !== "running" && outcome !== "settled"
              ? [[tab.id, outcome]]
              : [];
          }),
        )}
      />
      <div className="rail-body">
        {activeTab?.kind === "guide" && (
          <GuideView
            sessionId={sessionId}
            shellLabel={shellLabel || "shell"}
            onPick={pickDoor}
            workspace={folderName((roots.find((root) => root.primary) ?? roots[0])?.path)}
            folders={roots.map((root) => root.label)}
            review={roots.length ? guideReview : null}
            recentClosed={recentClosed}
            onReopen={reopenTab}
          />
        )}
        {/* Every terminal tab stays attached while it exists, hidden when it is not on show,
            so a command that ends in a background tab still says so on its tab. */}
        {tabsState.tabs
          .filter((tab) => tab.kind === "terminal")
          .map((tab) => (
            <div key={tab.id} className="rail-term" hidden={tab.id !== activeTab?.id}>
              <TerminalPanel
                sessionId={sessionId}
                terminalId={tab.terminalId || "1"}
                shell={tab.shell}
                active={tab.id === activeTab?.id}
                onState={(state) => setTermStates((current) => ({ ...current, [tab.id]: state }))}
              />
            </div>
          ))}
        {stopAsk && (
          <div className="fm-dialog-backdrop" onClick={() => setStopAsk(null)}>
            <div
              className="fm-dialog"
              role="alertdialog"
              aria-modal="true"
              aria-labelledby="term-stop-title"
              data-testid="term-stop-dialog"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="fm-dialog-title" id="term-stop-title">
                Stop {termStates[stopAsk]?.title || "the running command"} and close?
              </div>
              <div className="fm-dialog-hint">It is still running. Closing the tab stops it.</div>
              <div className="fm-dialog-actions">
                <button type="button" className="btn sm" autoFocus onClick={() => setStopAsk(null)}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn sm danger-btn"
                  data-testid="term-stop-confirm"
                  onClick={() => {
                    const id = stopAsk;
                    setStopAsk(null);
                    removeTab(id);
                  }}
                >
                  Stop and close
                </button>
              </div>
            </div>
          </div>
        )}
        {activeTab?.kind === "subagent" && activeTab.childId && (
          <SubagentPanel
            key={activeTab.childId}
            sessionId={activeTab.childId}
            title={activeTab.title || nodeOf(activeTab.childId)?.description || ""}
            group={activeTab.group}
            node={nodeOf(activeTab.childId)}
            agent={childSessions?.[activeTab.childId]?.agent}
            busy={childBusy(activeTab.childId)}
            onPromote={onPromoteTask}
            onOpenChild={onOpenChild}
            back={
              railFullscreen || overlaying
                ? {
                    // A window too narrow for two columns: the panel is put away. One the
                    // reader made full screen: it goes back beside the chat.
                    onBack: fullscreen && !overlaying ? () => setFullscreen(false) : () => onHidePanel?.(),
                    mainRunning: !!mainRunning,
                  }
                : undefined
            }
          />
        )}
        {/* Every browser tab stays attached while it exists, hidden when it is not on show, so a
            page (a signed-in site, a form half filled) is where it was when the tab comes back. */}
        {tabsState.tabs
          .filter((tab) => tab.kind === "browser")
          .map((tab) => (
            // Keyed by the session too: tab ids repeat across sessions, and one session's page (a
            // signed-in site) must not stay on show in the next one's Browser of the same id.
            <div key={`${sessionId}:${tab.id}`} className="rail-term" hidden={tab.id !== activeTab?.id}>
              <BrowserView
                url={tab.url ?? ""}
                canGoBack={browserCanStep(tab, -1)}
                canGoForward={browserCanStep(tab, 1)}
                onNavigate={(url) => setTabsState((s) => ({ ...s, tabs: browserNavigate(s.tabs, tab.id, url) }))}
                onBack={() => setTabsState((s) => ({ ...s, tabs: browserStep(s.tabs, tab.id, -1) }))}
                onForward={() => setTabsState((s) => ({ ...s, tabs: browserStep(s.tabs, tab.id, 1) }))}
                onOpenTerminal={() => openTab("terminal")}
                onClearHistory={() => setTabsState((s) => ({ ...s, tabs: browserClearHistory(s.tabs, tab.id) }))}
                tabId={tab.id}
                onVisited={(url) => setTabsState((s) => ({ ...s, tabs: browserNavigate(s.tabs, tab.id, url) }))}
                onTitle={(title) => setPaneTitles((titles) => ({ ...titles, [tab.id]: title }))}
              />
            </div>
          ))}
        {activeTab?.kind === "designs" && (
          <DesignsView
            sessionId={sessionId}
            seen={designWrites.length}
            onOpen={(path) => setTabsState((state) => openDesignTab(state.tabs, path))}
            onCompare={(path) => {
              setTabsState((state) => openDesignTab(state.tabs, path));
              setCompareRequest((previous) => ({ path, n: (previous?.n ?? 0) + 1 }));
            }}
          />
        )}
        {activeTab?.kind === "design-app" && activeTab.path && activeTab.url && (
          <DesignReview key={activeTab.id} sessionId={sessionId} path={activeTab.path} url={activeTab.url} />
        )}
        {activeTab?.kind === "design" && activeTab.path && (
          <DesignView
            key={activeTab.id}
            sessionId={sessionId}
            path={activeTab.path}
            compareRequest={compareRequest && samePage(compareRequest.path, activeTab.path) ? compareRequest.n : undefined}
            actionsSlot={actionsSlot}
          />
        )}
        {activeTab?.kind === "files" && (
          <FileManagerView
            sessionId={sessionId}
            roots={roots}
            changedStatus={changedStatus}
            changedBelow={changedBelow}
            onChanged={refreshAll}
            readOnly={roots.length > 0 && roots.every((root) => !root.writable)}
            openRequest={filesRequest}
            onPreviewOpen={setFilesPreviewOpen}
            recordedTurnOf={recordedTurnOf}
            onSeeChanges={seeChanges}
            comments={reviewComments}
            onAddComment={onAddReviewComment}
            onDeleteComment={onDeleteReviewComment}
            onEditComment={onEditReviewComment}
          />
        )}
        {reviewActive && (
          <ReviewPane
            sessionId={sessionId}
            isGit={isGit}
            changes={changes}
            changesLoading={changesFor !== sessionId}
            recorded={recorded}
            items={items}
            roots={roots.map((root) => root.path)}
            workspacePath={(roots.find((root) => root.primary) ?? roots[0])?.path}
            agentTurn={recordedTurnOf}
            onRefresh={refreshAll}
            onOpenFile={openInFiles}
            focusPath={reviewFocus?.path}
            focusKey={reviewFocus?.n}
            comments={reviewComments}
            onAddComment={onAddReviewComment}
            onDeleteComment={onDeleteReviewComment}
            onEditComment={onEditReviewComment}
            onSendComments={onSendReviewComments}
            showCommentBar={railFullscreen}
            diffStyle={diffStyle}
            onDiffStyle={setDiffStyle}
          />
        )}
      </div>
    </aside>
  );
}


// The names the server's file search prunes (tools/search.py's ignore set plus dot entries).
// The tree lists what the reader returns; `hideIgnored` narrows it to what search would see.
const IGNORED_NAMES = new Set([
  ".git",
  "node_modules",
  "target",
  "dist",
  "build",
  ".venv",
  "venv",
  "__pycache__",
  ".next",
  ".mypy_cache",
  ".pytest_cache",
  ".ruff_cache",
  ".idea",
]);

function isIgnoredName(name: string): boolean {
  return name.startsWith(".") || IGNORED_NAMES.has(name);
}

export function FileTree({
  sessionId,
  roots,
  current,
  filter,
  onSelect,
  changedStatus,
  changedBelow,
  hideIgnored,
  onSeeChanges,
  onAction,
  creating,
  onCreate,
  onCancelCreate,
  stale,
  revealKey,
}: {
  sessionId: string;
  roots: RootInfo[];
  /** The file on show: its folders are opened, its row marked and scrolled to. */
  current?: string;
  filter?: string;
  onSelect: (info: ArtifactInfo) => void;
  changedStatus?: (path: string) => string | undefined;
  /** Whether anything below this folder changed. */
  changedBelow?: (path: string) => boolean;
  hideIgnored?: boolean;
  /** Review's "what changed" for a file — offered on a file that has a change. */
  onSeeChanges?: (path: string) => void;
  /** The row menu's writes (in a writable folder only): rename, move, delete, new here. */
  onAction?: (action: TreeAction, path: string, dir: boolean) => void;
  /** A new file or folder being named in place, under `dir`. */
  creating?: { dir: string; kind: "file" | "folder"; name?: string } | null;
  /** Create it; the error to show under the name, or null when it is made. */
  onCreate?: (name: string) => Promise<string | null>;
  onCancelCreate?: () => void;
  /** Folders whose listing changed (a create, rename or delete), or `*` for every folder read
   *  so far: read again in place, so the folders the person opened stay open. */
  stale?: { dirs: string[]; n: number } | null;
  /** Bumped when the same file is asked for again from outside: reveal it again. */
  revealKey?: number;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [kids, setKids] = useState<Record<string, { name: string; dir: boolean; size: number }[]>>({});
  // The row menu: what can be done with this path — open, show the change, copy, reveal, and
  // in a writable folder rename, move, delete and make something new inside a folder.
  const [menu, setMenu] = useState<{
    path: string;
    dir: boolean;
    x: number;
    y: number;
    writable: boolean;
    root: boolean;
  } | null>(null);
  const treeRef = useRef<HTMLDivElement | null>(null);
  // Where the file on show sits, in the tree's spelling, and the rows on the way to it (shown even
  // when hidden names are, so the file you are looking at is never inside a folder the tree hides).
  const chain = useMemo(() => (current ? treeChain(current, roots) : null), [current, roots]);
  const onChain = useMemo(() => new Set(chain ? [...chain.dirs, chain.file] : []), [chain]);
  const [scrollTo, setScrollTo] = useState<string | null>(null);

  const readDir = (path: string) =>
    readArtifact(sessionId, path)
      .then((c) => setKids((k) => ({ ...k, [path]: c.kind === "folder" ? c.entries || [] : [] })))
      .catch(() => setKids((k) => ({ ...k, [path]: [] })));

  // A folder being written into is opened, so the new name's row has somewhere to sit.
  useEffect(() => {
    if (!creating) return;
    setOpen((o) => ({ ...o, [creating.dir]: true }));
    if (!kids[creating.dir]) void readDir(creating.dir);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [creating?.dir]);

  // Opening a file from anywhere opens its folders here and scrolls its row into sight.
  useEffect(() => {
    if (!chain) return;
    setOpen((o) => ({ ...o, ...Object.fromEntries(chain.dirs.map((dir) => [dir, true])) }));
    for (const dir of chain.dirs) if (!kids[dir]) void readDir(dir);
    setScrollTo(chain.file);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chain?.file, revealKey]);

  // The row is there once its folders are read.
  useEffect(() => {
    if (!scrollTo) return;
    const row = Array.from(treeRef.current?.querySelectorAll<HTMLElement>("[data-tree-path]") ?? []).find(
      (el) => el.dataset.treePath === scrollTo,
    );
    if (!row) return;
    row.scrollIntoView?.({ block: "nearest" });
    setScrollTo(null);
  }, [scrollTo, kids, open]);

  useEffect(() => {
    if (!stale) return;
    // `*`: every folder read so far (a Refresh, or a move to a folder named by the person).
    const dirs = stale.dirs.includes("*") ? Object.keys(kids) : stale.dirs;
    for (const dir of dirs) if (kids[dir]) void readDir(dir);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stale?.n]);

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", close);
    };
  }, [menu]);

  const toggle = (path: string) => {
    const next = !open[path];
    setOpen((o) => ({ ...o, [path]: next }));
    if (next && !kids[path]) void readDir(path);
  };

  const node = (
    path: string,
    name: string,
    dir: boolean,
    size: number,
    depth: number,
    writable: boolean,
    meta?: { writable: boolean; missing: boolean },
  ) => {
    const childPath = (child: string) => `${path.replace(/\/+$/, "")}/${child}`;
    const kind = dir ? "folder" : kindOfPath(path);
    const status = dir ? undefined : changedStatus?.(path);
    // The vocabulary on a row: M/A/D/R/U in mono (git status, read at a glance), the name
    // tinted by the same status, and a folder carrying a 6px dot when anything below changed.
    const mark = status === "?" || status === "??" ? "U" : status;
    const dot = dir && changedBelow?.(path);
    return (
      <div className="tree-node" key={path}>
        <div
          className={"tree-row" + (dir ? " is-dir" : "") + (current === path || chain?.file === path ? " on" : "")}
          style={{ paddingLeft: 6 + depth * 13, "--tree-depth": depth } as React.CSSProperties}
        >
          {dir ? (
            <button className="tree-caret" onClick={() => toggle(path)} aria-label={open[path] ? `Collapse ${name}` : `Expand ${name}`}>
              <Icon name={open[path] ? "chevronDown" : "chevronRight"} size={12} />
            </button>
          ) : (
            <span className="tree-caret" />
          )}
          <button
            className="tree-label"
            data-testid={depth === 0 ? "files-root-row" : undefined}
            title={path}
            // The drag-to-chat: a FILE is a drag source carrying its workspace path (a
            // folder is not — dragging a directory into the composer has nothing to attach).
            draggable={!dir}
            onDragStart={
              dir
                ? undefined
                : (event) => {
                    event.dataTransfer.setData("application/x-reeve-file", path);
                    event.dataTransfer.setData("text/plain", path);
                    event.dataTransfer.effectAllowed = "copy";
                  }
            }
            onKeyDown={(event) => {
              // The tree contract: ↑↓ walk the visible rows, → opens a folder (or steps
              // into it), ← closes it, Enter follows the focus (the button's own click).
              const rows = Array.from(
                treeRef.current?.querySelectorAll<HTMLButtonElement>(".tree-label") ?? [],
              );
              const at = rows.indexOf(event.currentTarget);
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                const next = rows[at + (event.key === "ArrowDown" ? 1 : -1)];
                if (next) {
                  event.preventDefault();
                  next.focus();
                }
                return;
              }
              if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
              if (!dir) return;
              event.preventDefault();
              if (event.key === "ArrowRight") {
                if (!open[path]) toggle(path);
                else
                  treeRef.current
                    ?.querySelector<HTMLButtonElement>(`[data-tree-path="${CSS.escape(path)}/"]`)
                    ?.focus();
                return;
              }
              if (open[path]) toggle(path);
              else
                treeRef.current
                  ?.querySelector<HTMLButtonElement>(`[data-tree-path="${CSS.escape(path)}"]`)
                  ?.closest(".tree-node")
                  ?.parentElement?.querySelector<HTMLButtonElement>(".tree-label")
                  ?.focus();
            }}
            data-tree-path={dir ? `${path}/` : path}
            onContextMenu={(event) => {
              event.preventDefault();
              setMenu({ path, dir, x: event.clientX, y: event.clientY, writable, root: depth === 0 });
            }}
            // A FOLDER only expands the tree (the caret's job too) — it never opens the preview
            // pane: the right side is for FILE detail, not a second listing of the same tree.
            onClick={() => {
              if (dir) return toggle(path);
              onSelect({ path, abs_path: path, name, kind, size, modified_at: 0, origin: "files" });
            }}
          >
            {/* A file wears its language's logo, in colour, whether or not it is open (owner,
                2026-10-15: the colour only showed on the one clicked). Folders stay one ink: the
                yellow folders were half of what made the panel "màu mè" (owner, 2026-10-13). The
                open folder or file still wears its own mark, so the one you are on stands out. */}
            {dir && current !== path ? (
              <Icon name={open[path] ? "folderOpen" : "folder"} size={14} className="tree-ico" />
            ) : (
              <FileIcon path={path} type={dir ? "directory" : "file"} expanded={dir && !!open[path]} size={14} />
            )}
            <span className="tree-name" data-st={mark ? statusType(status || "M") : undefined}>{name}</span>
            {/* Only the exception is said: a folder the agent may write to says nothing (it said
                "Read & write" on every one, the normal case five times over). */}
            {meta && (!meta.writable || meta.missing) && (
              <span
                className={"tree-meta tree-access" + (meta.missing ? " is-missing" : " is-readonly")}
                data-testid={depth === 0 ? "files-root-access" : undefined}
              >
                {meta.missing ? (
                  "Not found"
                ) : (
                  <>
                    <Icon name="lock" size={11} />
                    Read only
                  </>
                )}
              </span>
            )}
            {dot && <span className="tree-dot" aria-hidden />}
            {mark && (
              <span
                className="tree-st"
                data-type={statusType(status || "M")}
                title={`Working tree: ${statusType(status || "M")}`}
                data-testid={`tree-st-${path}`}
              >
                {mark}
              </span>
            )}
          </button>
          <button
            type="button"
            className="tree-more"
            data-testid={`tree-more-${name}`}
            aria-label={`Actions for ${name}`}
            aria-haspopup="menu"
            title="Actions"
            onClick={(event) => {
              const box = event.currentTarget.getBoundingClientRect();
              setMenu({ path, dir, x: box.right - 200, y: box.bottom + 4, writable, root: depth === 0 });
            }}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <Icon name="moreHorizontal" size={14} />
          </button>
        </div>
        {dir && open[path] && creating?.dir === path && onCreate && (
          <NewEntryRow
            kind={creating.kind}
            initial={creating.name}
            depth={depth + 1}
            onCreate={onCreate}
            onCancel={() => onCancelCreate?.()}
          />
        )}
        {dir &&
          open[path] &&
          (kids[path] || [])
            .filter((e) => !hideIgnored || !isIgnoredName(e.name) || onChain.has(childPath(e.name)))
            .map((e) => node(childPath(e.name), e.name, e.dir, e.size, depth + 1, writable))}
      </div>
    );
  };

  const visible = filter ? roots.filter((r) => (r.label || r.path).toLowerCase().includes(filter)) : roots;
  return (
    <div className="file-tree" data-testid="file-tree" ref={treeRef}>
      {/* The folders the person chose. A project the session opened on its own (`reached`)
          is a permission it holds, listed in the composer's mode menu: in the tree they
          piled up, one per project the agent looked at (owner, 2026-10-18). */}
      {visible
        .filter((r) => !r.reached)
        .map((r) =>
          node(r.path, r.label || r.path.split("/").pop() || r.path, true, 0, 0, r.writable, {
            writable: r.writable,
            missing: !r.exists,
          }),
        )}
      {menu &&
        createPortal(
          <div
            className="tree-menu"
            role="menu"
            data-testid="tree-menu"
            style={{ left: Math.max(4, Math.min(menu.x, window.innerWidth - 190)), top: menu.y }}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                if (!menu.dir) {
                  onSelect({
                    path: menu.path,
                    abs_path: menu.path,
                    name: menu.path.split("/").pop() || menu.path,
                    kind: kindOfPath(menu.path),
                    size: 0,
                    modified_at: 0,
                    origin: "files",
                  });
                }
                setMenu(null);
              }}
            >
              Open{menu.dir ? " folder" : " file"}
            </button>
            {onSeeChanges && !menu.dir && changedStatus?.(menu.path) && (
              <button
                type="button"
                role="menuitem"
                data-testid="tree-menu-see-changes"
                onClick={() => {
                  onSeeChanges(menu.path);
                  setMenu(null);
                }}
              >
                See what changed
              </button>
            )}
            {onAction && menu.writable && !menu.root && (
              <>
                <button
                  type="button"
                  role="menuitem"
                  data-testid="tree-menu-rename"
                  onClick={() => {
                    onAction("rename", menu.path, menu.dir);
                    setMenu(null);
                  }}
                >
                  Rename…
                </button>
                <button
                  type="button"
                  role="menuitem"
                  data-testid="tree-menu-move"
                  onClick={() => {
                    onAction("move", menu.path, menu.dir);
                    setMenu(null);
                  }}
                >
                  Move to…
                </button>
              </>
            )}
            {onAction && menu.writable && menu.dir && (
              <>
                <button
                  type="button"
                  role="menuitem"
                  data-testid="tree-menu-new-file"
                  onClick={() => {
                    onAction("new-file", menu.path, true);
                    setMenu(null);
                  }}
                >
                  New file here
                </button>
                <button
                  type="button"
                  role="menuitem"
                  data-testid="tree-menu-new-folder"
                  onClick={() => {
                    onAction("new-folder", menu.path, true);
                    setMenu(null);
                  }}
                >
                  New folder here
                </button>
              </>
            )}
            <div className="tree-menu-sep" />
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                void navigator.clipboard?.writeText(menu.path);
                setMenu(null);
              }}
            >
              Copy path
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                void revealPath(sessionId, menu.path);
                setMenu(null);
              }}
            >
              Reveal in file manager
            </button>
            {onAction && menu.writable && !menu.root && (
              <>
                <div className="tree-menu-sep" />
                <button
                  type="button"
                  role="menuitem"
                  className="danger"
                  data-testid="tree-menu-delete"
                  onClick={() => {
                    onAction("delete", menu.path, menu.dir);
                    setMenu(null);
                  }}
                >
                  Delete…
                </button>
              </>
            )}
          </div>,
          document.body,
        )}
    </div>
  );
}

// The File Manager (the reference design's `pi.file-manager` workbench, host-rendered): one pane with
// the project tree and a name search, one pane for the selected file. Search is the server's
// session-scoped name search; the tree reuses the review sidebar's lazy folders.
type FmAction = "rename" | "move";

const FM_ACTION_TITLE: Record<FmAction, string> = {
  rename: "Rename",
  move: "Move to",
};

/** A full path from the tree (a root's path, then names) rather than a search result's
 *  folder-relative one. */
const isTreePath = (path: string) => /^([A-Za-z]:|[\\/])/.test(path);

function FileManagerView({
  sessionId,
  roots,
  changedStatus,
  changedBelow,
  onChanged,
  readOnly,
  openRequest,
  onPreviewOpen,
  recordedTurnOf,
  onSeeChanges,
  comments,
  onAddComment,
  onDeleteComment,
  onEditComment,
}: {
  sessionId: string;
  roots: RootInfo[];
  changedStatus: (path: string) => string | undefined;
  changedBelow?: (path: string) => boolean;
  onChanged: () => void;
  readOnly?: boolean;
  /** A file to open from outside (a transcript chip, Review's "Open file"), at a line when
   *  the answer named one. */
  openRequest?: { path: string; line?: number; n: number } | null;
  /** Whether a file is open in the preview (the rail widens / the nav yields for it). */
  onPreviewOpen?: (open: boolean) => void;
  /** The newest turn in which Reeve changed a path, if it did. */
  recordedTurnOf?: (path: string) => number | undefined;
  /** Show a file's change in Review. */
  onSeeChanges?: (path: string) => void;
  comments?: ReviewComment[];
  onAddComment?: (c: ReviewComment) => void;
  onDeleteComment?: (id: string) => void;
  onEditComment?: (id: string, text: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SessionFile[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [truncated, setTruncated] = useState(false);
  const [selected, setSelected] = useState<ArtifactInfo | null>(null);
  const [content, setContent] = useState<ArtifactContent | null>(null);
  const [treeW, setTreeW] = useState(() =>
    clampTreeWidth(readStoredSize(FM_TREE_WIDTH_KEY) ?? TREE_WIDTH_DEFAULT),
  );
  const [treeHidden, setTreeHidden] = useState(false);
  // Hidden and ignored names show by default: the tree is the folder as it is (owner, 2026-10-21).
  const [showIgnored, setShowIgnored] = useState(true);
  // Rename / Move ask for a name in a small dialog; New is named in place in the tree, and
  // Delete asks first (it goes to the system's trash).
  const [action, setAction] = useState<{ kind: FmAction; path: string } | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [creating, setCreating] = useState<{ dir: string; kind: "file" | "folder"; name?: string } | null>(null);
  const [toDelete, setToDelete] = useState<{ path: string; dir: boolean } | null>(null);
  const [stale, setStale] = useState<{ dirs: string[]; n: number } | null>(null);
  const [newMenu, setNewMenu] = useState(false);
  const [viewMenu, setViewMenu] = useState(false);
  const newRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!newMenu && !viewMenu) return;
    const close = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!newRef.current?.contains(target)) setNewMenu(false);
      if (!viewRef.current?.contains(target)) setViewMenu(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setNewMenu(false);
      setViewMenu(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", escape);
    };
  }, [newMenu, viewMenu]);
  const drag = useRef<{ startX: number; startW: number } | null>(null);

  const preview = useRef<FilePreviewHandle | null>(null);
  /** Run `next` once the file on show can be left: it asks first when it holds unsaved edits. */
  const leave = (next: () => void) => (preview.current ? preview.current.requestLeave(next) : next());
  // The line an answer opened this file at: scrolled to and marked until another file opens.
  const [focusLine, setFocusLine] = useState<{ line: number; n: number } | null>(null);
  const open = (info: ArtifactInfo, focus?: { line: number; n: number }) =>
    leave(() => {
      setSelected(info);
      setFocusLine(focus ?? null);
      setContent(null);
      readArtifact(sessionId, info.path)
        .then(setContent)
        .catch(() => setContent(null));
    });

  useEffect(() => {
    if (!openRequest?.path) return;
    const path = openRequest.path;
    const focus = openRequest.line ? { line: openRequest.line, n: openRequest.n } : undefined;
    // The tree shows where it is: a name search would stand in its place.
    setQuery("");
    open({ path, name: path.split("/").pop() || path, kind: kindOfPath(path), size: 0, modified_at: 0, origin: "files" }, focus);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openRequest?.n]);

  useEffect(() => {
    onPreviewOpen?.(!!selected);
  }, [selected, onPreviewOpen]);
  useEffect(() => () => onPreviewOpen?.(false), [onPreviewOpen]);

  useEffect(() => {
    const needle = query.trim();
    if (!needle) {
      setResults(null);
      setSearching(false);
      setTruncated(false);
      return;
    }
    setSearching(true);
    let alive = true;
    const timer = window.setTimeout(() => {
      searchSessionFiles(sessionId, needle)
        .then((res) => {
          if (!alive) return;
          setResults(res.files ?? []);
          setTruncated(!!res.truncated);
          setSearching(false);
        })
        .catch(() => {
          if (!alive) return;
          setResults([]);
          setSearching(false);
        });
    }, 180);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [query, sessionId]);

  const root = roots.find((r) => r.primary) ?? roots[0];
  const projectName = root?.label || root?.path.split("/").pop() || "Project files";
  const reload = () => {
    setStale((prev) => ({ dirs: ["*"], n: (prev?.n ?? 0) + 1 }));
    onChanged();
    if (selected) open(selected);
  };

  const dirOf = (path: string) => path.split("/").slice(0, -1).join("/");
  const refreshDirs = (...dirs: string[]) => setStale((prev) => ({ dirs, n: (prev?.n ?? 0) + 1 }));
  const openPath = (path: string) =>
    open({ path, name: path.split("/").pop() || path, kind: kindOfPath(path), size: 0, modified_at: 0, origin: "files" });

  /** New file / folder: named in place under the open file's folder, else the primary folder. */
  const startCreate = (kind: "file" | "folder", dir?: string) => {
    setNewMenu(false);
    const near = selected && isTreePath(selected.path) ? dirOf(selected.path) : "";
    const target = dir || near || root?.path;
    if (!target) return;
    setQuery("");
    setCreating({ dir: target, kind });
  };
  const create = async (name: string): Promise<string | null> => {
    if (!creating) return null;
    const result = await createSessionEntry(sessionId, `${creating.dir}/${name}`, creating.kind).catch(
      (exc: unknown) => ({ ok: false, error: exc instanceof Error ? exc.message : String(exc) }) as FileEditResult,
    );
    if (!result.ok) return result.error || "Could not make that.";
    const made = `${creating.dir}/${name}`;
    setCreating(null);
    refreshDirs(creating.dir);
    onChanged();
    if (creating.kind === "file") openPath(made);
    return null;
  };

  const onTreeAction = (kind: TreeAction, path: string, dir: boolean) => {
    if (kind === "new-file" || kind === "new-folder") return startCreate(kind === "new-file" ? "file" : "folder", path);
    if (kind === "delete") {
      setActionError(null);
      setToDelete({ path, dir });
      return;
    }
    const ask = () => {
      setActionError(null);
      setDraft(kind === "rename" ? path.split("/").pop() || "" : "");
      setAction({ kind, path });
    };
    if (selected && (selected.path === path || selected.path.startsWith(`${path}/`))) leave(ask);
    else ask();
  };

  const submitAction = async () => {
    if (!action || busy) return;
    const value = draft.trim();
    if (!value && action.kind !== "move") return;
    setBusy(true);
    setActionError(null);
    const result: FileEditResult =
      action.kind === "rename"
        ? await renameSessionEntry(sessionId, action.path, value)
        : await moveSessionEntry(sessionId, action.path, value);
    setBusy(false);
    if (!result.ok || !result.path) {
      setActionError(result.error || "Could not do that.");
      return;
    }
    const was = action.path;
    setAction(null);
    onChanged();
    if (action.kind === "rename") {
      refreshDirs(dirOf(was));
      // The file on show follows its new name.
      if (selected?.path === was) openPath(`${dirOf(was)}/${value}`);
    } else {
      // A move lands in a folder the person named by its path: read every open folder again.
      refreshDirs("*");
      if (selected?.path === was) openPath(result.path);
    }
  };

  const confirmDelete = async () => {
    if (!toDelete || busy) return;
    setBusy(true);
    setActionError(null);
    const result = await deleteSessionEntry(sessionId, toDelete.path).catch(
      (exc: unknown) => ({ ok: false, error: exc instanceof Error ? exc.message : String(exc) }) as FileEditResult,
    );
    setBusy(false);
    if (!result.ok) {
      setActionError(result.error || "Could not delete that.");
      return;
    }
    const gone = toDelete.path;
    setToDelete(null);
    refreshDirs(dirOf(gone));
    onChanged();
    if (selected && (selected.path === gone || selected.path.startsWith(`${gone}/`))) {
      // The file is gone, and so is whatever was typed into it.
      writeDraft(sessionId, selected.path, null);
      setSelected(null);
      setContent(null);
    }
  };

  const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    drag.current = { startX: event.clientX, startW: treeW };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const moveResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const state = drag.current;
    if (!state) return;
    setTreeW(clampTreeWidth(state.startW + event.clientX - state.startX));
  };
  const endResize = () => {
    if (!drag.current) return;
    drag.current = null;
    writeStoredSize(FM_TREE_WIDTH_KEY, treeW);
  };

  return (
    <div className="fm" data-testid="file-manager">
      {/* One row: find by name, New (a file or a folder, named in place), and the view's
          options. What acts on one entry (rename, move, delete…) is on that entry's own menu. */}
      <div className="fm-toolbar">
        <label className="fm-find">
          <Icon name="search" size={13} />
          <input
            data-testid="fm-search"
            type="search"
            placeholder="Find a file by name"
            aria-label="Find a file by name"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button
              type="button"
              className="fm-search-clear"
              aria-label="Clear search"
              data-testid="fm-search-clear"
              onClick={() => setQuery("")}
            >
              <Icon name="x" size={12} />
            </button>
          )}
        </label>
        {!readOnly && (
          <div className="fm-menu-wrap" ref={newRef}>
            <button
              type="button"
              className="fm-new"
              data-testid="fm-new"
              aria-haspopup="menu"
              aria-expanded={newMenu}
              onClick={() => setNewMenu((v) => !v)}
            >
              <Icon name="plus" size={13} />
              New
            </button>
            {newMenu && (
              <div className="fm-menu" role="menu" aria-label="New">
                <button type="button" role="menuitem" data-testid="fm-new-file" onClick={() => startCreate("file")}>
                  New file
                </button>
                <button type="button" role="menuitem" data-testid="fm-new-folder" onClick={() => startCreate("folder")}>
                  New folder
                </button>
              </div>
            )}
          </div>
        )}
        <div className="fm-menu-wrap" ref={viewRef}>
          <button
            type="button"
            className="fm-icon-btn"
            data-testid="fm-view"
            aria-label="View options"
            aria-haspopup="menu"
            aria-expanded={viewMenu}
            title="View options"
            onClick={() => setViewMenu((v) => !v)}
          >
            <Icon name="moreHorizontal" size={15} />
          </button>
          {viewMenu && (
            <div className="fm-menu right" role="menu" aria-label="View options">
              <button
                type="button"
                role="menuitemcheckbox"
                aria-checked={showIgnored}
                data-testid="fm-ignored-toggle"
                onClick={() => {
                  setShowIgnored((shown) => !shown);
                  setViewMenu(false);
                }}
              >
                Show ignored files
                <span className="fm-menu-check" aria-hidden>
                  {showIgnored ? "✓" : ""}
                </span>
              </button>
              <button
                type="button"
                role="menuitem"
                data-testid="fm-toggle-tree"
                onClick={() => {
                  setTreeHidden((hidden) => !hidden);
                  setViewMenu(false);
                }}
              >
                {treeHidden ? "Show the file list" : "Hide the file list"}
              </button>
              <button
                type="button"
                role="menuitem"
                data-testid="fm-reload"
                onClick={() => {
                  reload();
                  setViewMenu(false);
                }}
              >
                Refresh
              </button>
            </div>
          )}
        </div>
      </div>
      <div
        className={"fm-body" + (treeHidden ? " tree-hidden" : "") + (selected ? " has-file" : "")}
        style={{ ["--fm-side" as any]: `${treeW}px` }}
      >
        <div className="fm-tree-pane">
          {results ? (
            <div className="fm-results" data-testid="fm-results">
              {searching ? (
                <div className="rev-empty-small">Searching…</div>
              ) : results.length === 0 ? (
                <div className="fm-no-matches" data-testid="fm-no-matches">
                  <span>No file is named like “{query.trim()}”.</span>
                  {!readOnly && root && (
                    <button
                      type="button"
                      className="fm-new"
                      data-testid="fm-create-from-query"
                      onClick={() => {
                        const name = query.trim();
                        setQuery("");
                        setCreating({ dir: root.path, kind: "file", name });
                      }}
                    >
                      Create {query.trim()}
                    </button>
                  )}
                </div>
              ) : (
                results.map((file) => (
                  <button
                    key={file.path}
                    type="button"
                    className={"fm-result" + (selected?.path === file.path ? " on" : "")}
                    data-testid={`fm-result-${file.path}`}
                    title={file.path}
                    onClick={() =>
                      open({
                        path: file.path,
                        abs_path: file.abs_path,
                        name: file.name,
                        kind: kindOfPath(file.path),
                        size: 0,
                        modified_at: 0,
                        origin: "files",
                      })
                    }
                  >
                    <FileIcon path={file.path} size={14} />
                    <span className="fm-result-name">{file.name}</span>
                    <span className="fm-result-dir">
                      {file.path.split("/").slice(0, -1).join("/")}
                    </span>
                  </button>
                ))
              )}
            </div>
          ) : roots.length === 0 ? (
            <div className="rev-empty-small">No folders in this session.</div>
          ) : (
            <div className="fm-tree">
              <FileTree
                sessionId={sessionId}
                roots={roots}
                current={selected?.path}
                revealKey={openRequest?.n}
                onSelect={open}
                changedStatus={changedStatus}
                changedBelow={changedBelow}
                hideIgnored={!showIgnored}
                onSeeChanges={onSeeChanges}
                onAction={readOnly ? undefined : onTreeAction}
                creating={creating}
                onCreate={create}
                onCancelCreate={() => setCreating(null)}
                stale={stale}
              />
            </div>
          )}
          {results && truncated && (
            <div className="fm-note fm-foot" data-testid="fm-more-matches">
              More matches than shown: type more of the name.
            </div>
          )}
        </div>

        <div
          className="fm-resize"
          onPointerDown={startResize}
          onPointerMove={moveResize}
          onPointerUp={endResize}
          onLostPointerCapture={endResize}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize file list"
        />

        <div className="fm-pane">
          {selected ? (
            <FilePreview
              // A quiet line under the file's name. It was a tinted banner above the name, the
              // loudest thing on the page.
              note={
                recordedTurnOf?.(selected.path) !== undefined ? (
                  <span className="fv-changed" data-testid="fm-changed-banner">
                    Changed in turn {recordedTurnOf(selected.path)}
                    {onSeeChanges && (
                      <>
                        {" · "}
                        <button type="button" className="fv-changed-link" data-testid="fm-see-changes" onClick={() => onSeeChanges(selected.path)}>
                          See what changed
                        </button>
                      </>
                    )}
                  </span>
                ) : undefined
              }
              ref={preview}
              sessionId={sessionId}
              info={selected}
              content={content}
              focusLine={focusLine ?? undefined}
              readOnly={readOnly}
              onAction={(kind) => onTreeAction(kind, selected.path, false)}
              comments={(comments || []).filter((c) => c.file === selected.path)}
              onAddComment={onAddComment}
              onDeleteComment={onDeleteComment}
              onEditComment={onEditComment}
              onReload={(encoding) => {
                setContent(null);
                return readArtifact(sessionId, selected.path, encoding)
                  .then(setContent)
                  .catch(() => setContent(null));
              }}
              onClose={() => {
                setSelected(null);
                setContent(null);
              }}
              onOpenEntry={(path) =>
                open({
                  path,
                  name: path.split("/").pop() || path,
                  kind: kindOfPath(path),
                  size: 0,
                  modified_at: 0,
                  origin: "files",
                })
              }
            />
          ) : (
            <div className="rev-empty" data-testid="fm-empty">
              <Icon name="folder" size={22} />
              <div className="rev-empty-title">What is in your folders right now</div>
              <div className="rev-empty-desc">
                Pick a file to open it, or type to find one by name. To see what changed, use Review.
              </div>
            </div>
          )}
        </div>
      </div>

      {action && (
        <div
          className="fm-dialog-backdrop"
          onClick={() => {
            if (!busy) setAction(null);
          }}
        >
          <div
            className="fm-dialog"
            role="dialog"
            aria-label={FM_ACTION_TITLE[action.kind]}
            data-testid="fm-dialog"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="fm-dialog-title">
              {FM_ACTION_TITLE[action.kind]} {action.path.split("/").pop()}
            </div>
            <div className="fm-dialog-hint">
              {action.kind === "move"
                ? `A folder in ${projectName}, by its path from there (empty: ${projectName} itself)`
                : action.path}
            </div>
            <input
              autoFocus
              className="fm-dialog-input"
              data-testid="fm-dialog-input"
              value={draft}
              spellCheck={false}
              autoComplete="off"
              placeholder={action.kind === "rename" ? "New name" : "docs"}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void submitAction();
                if (event.key === "Escape" && !busy) setAction(null);
              }}
            />
            {actionError && (
              <div className="fm-dialog-error" role="alert" data-testid="fm-dialog-error">
                {actionError}
              </div>
            )}
            <div className="fm-dialog-actions">
              <button type="button" className="btn sm" disabled={busy} onClick={() => setAction(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn sm primary"
                data-testid="fm-dialog-confirm"
                disabled={busy || (!draft.trim() && action.kind !== "move")}
                onClick={() => void submitAction()}
              >
                {busy ? "Working…" : action.kind === "rename" ? "Rename" : "Move"}
              </button>
            </div>
          </div>
        </div>
      )}

      {toDelete && (
        <div className="fm-dialog-backdrop" onClick={() => !busy && setToDelete(null)}>
          <div
            className="fm-dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="fm-delete-title"
            aria-describedby="fm-delete-hint"
            data-testid="fm-delete-dialog"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="fm-dialog-title" id="fm-delete-title">
              Delete {toDelete.path.split("/").pop()}?
            </div>
            <div className="fm-dialog-hint" id="fm-delete-hint">
              {toDelete.dir ? "The folder and everything in it go" : "It goes"} to the {trashName}, so you can
              restore it from there.
            </div>
            {actionError && (
              <div className="fm-dialog-error" role="alert" data-testid="fm-delete-error">
                {actionError}
              </div>
            )}
            <div className="fm-dialog-actions">
              <button type="button" className="btn sm" disabled={busy} autoFocus onClick={() => setToDelete(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn sm danger-btn"
                data-testid="fm-delete-confirm"
                disabled={busy}
                onClick={() => void confirmDelete()}
              >
                {busy ? "Deleting…" : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
