// The right rail's tab model. A session holds a small list of tabs seeded by the
// guide (the "Start" page); picking a door REPLACES the guide in place, and closing the
// last real tab reseeds it. Review and Files are singletons; Browser and Terminal allow many;
// a subagent's session is one tab per child (owner, 2026-10-13), opened from its row in the
// chat and never from the + menu. Designs is a singleton listing the session's design
// pages, and each design page opens in a tab of its own, one per file.
//
// The model is pure and localStorage-persisted per session (the rail's own precedence
// pattern, appHelpers keys). Terminals are deliberately never restored: the pipe-backed
// shell survives a tab switch server-side, but a reload has no reattach story, and a dead
// chip would lie — the reference design restores via host identity instead (deferred with remote PTY).

import { displayHost } from "./browserUrl";

export type RailTabKind = "guide" | "review" | "files" | "terminal" | "browser" | "subagent" | "designs" | "design" | "design-app";
/** The kinds a person opens from the strip. A subagent's tab is opened from its row, a design's
 *  from Designs, its row in the chat, or a link to its file. */
export type OpenableTabKind = Exclude<RailTabKind, "guide" | "subagent" | "design" | "design-app">;

/** Asked of the panel to open a design's review: the running app beside the locked design (ADR
 *  0195), from a `compare_design` row. `detail`: `{ path, url }`. */
export const OPEN_DESIGN_APP_EVENT = "reeve:open-design-app";

/** Asked of the panel to show a web page in its Browser: a link in the conversation, in the desktop
 *  app, whose Browser can show any site. `detail`: `{ url }`. */
export const OPEN_BROWSER_EVENT = "reeve:open-browser";

export interface RailTab {
  id: string;
  kind: RailTabKind;
  /** Browser tabs only: the committed address ("" = start page, no document). */
  url?: string;
  /** Browser tabs only: application-managed history, oldest first. */
  history?: string[];
  /** Browser tabs only: the index into `history` the current url came from (-1 = none). */
  historyIndex?: number;
  /** Terminal tabs only: the shell menu's pick (`cmd` / `bash` / `powershell`). */
  shell?: string;
  /** Terminal tabs only: the server-side id this tab owns (several per session, ). */
  terminalId?: string;
  /** Subagent tabs only: the child session this tab shows. */
  childId?: string;
  /** Subagent tabs only: the task the child was given, the tab's name. */
  title?: string;
  /** Subagent tabs only: the workflow run the child is an agent of, when it is one. */
  group?: string;
  /** Design and design-app tabs: the page's path, as the session names it. A design-app tab's
   *  `url` is the running app's address it is compared with. */
  path?: string;
}

export interface RailTabsState {
  tabs: RailTab[];
  activeId: string;
}

export const RAIL_TABS_KEY = "reeve:rail-tabs:v1";

export function seedRailTabs(): RailTab[] {
  return [{ id: "guide", kind: "guide" }];
}

export function railTabsStorageKey(sessionId: string): string {
  return `${RAIL_TABS_KEY}:${sessionId}`;
}

/** The session's own facts that decide the terminal door's shell list. */
export interface RailShellFacts {
  remote: boolean;
  host?: string | null;
}

function nextBrowserId(tabs: RailTab[]): string {
  let n = 1;
  while (tabs.some((tab) => tab.id === `browser-${n}`)) n += 1;
  return `browser-${n}`;
}

function nextTerminalNumber(tabs: RailTab[]): number {
  let n = 1;
  while (tabs.some((tab) => tab.id === `terminal-${n}`)) n += 1;
  return n;
}

/** Open a kind, replacing the guide seed on the first pick. Review is
 *  a singleton (opening it again activates it); Browser and Terminal allow many — one tab
 *  per address / per shell, each with its own server-side terminal id. */
export function openRailTab(
  tabs: RailTab[],
  kind: OpenableTabKind,
  opts: { url?: string; shell?: string } = {},
): RailTabsState {
  const base = tabs.filter((tab) => tab.kind !== "guide");
  if (kind === "review" || kind === "files" || kind === "designs") {
    const existing = tabs.find((tab) => tab.kind === kind);
    if (existing) return { tabs: base.length ? base : tabs, activeId: existing.id };
    return { tabs: [...base, { id: kind, kind }], activeId: kind };
  }
  if (kind === "terminal") {
    const n = nextTerminalNumber(tabs);
    const tab: RailTab = {
      id: `terminal-${n}`,
      kind: "terminal",
      shell: opts.shell,
      terminalId: `t${n}`,
    };
    return { tabs: [...base, tab], activeId: tab.id };
  }
  const url = opts.url ?? "";
  const tab: RailTab = {
    id: nextBrowserId(tabs),
    kind: "browser",
    url,
    history: url ? [url] : [],
    historyIndex: url ? 0 : -1,
  };
  return { tabs: [...base, tab], activeId: tab.id };
}

/** Open a child's session: its own tab, or the one it already has (opening is never a
 *  duplicate). A later open with a better name keeps the tab and takes the name. */
export function openSubagentTab(
  tabs: RailTab[],
  child: { childId: string; title?: string; group?: string },
): RailTabsState {
  const existing = tabs.find((tab) => tab.kind === "subagent" && tab.childId === child.childId);
  if (existing) {
    if (!child.title || child.title === existing.title) return { tabs, activeId: existing.id };
    return {
      tabs: tabs.map((tab) => (tab === existing ? { ...tab, title: child.title } : tab)),
      activeId: existing.id,
    };
  }
  const base = tabs.filter((tab) => tab.kind !== "guide");
  const tab: RailTab = {
    id: `subagent-${child.childId}`,
    kind: "subagent",
    childId: child.childId,
    ...(child.title ? { title: child.title } : {}),
    ...(child.group ? { group: child.group } : {}),
  };
  return { tabs: [...base, tab], activeId: tab.id };
}

/** A design page's path, one spelling of it: forward slashes, and case alone not a difference on
 *  Windows, where the server compares file names that way. */
export function designKey(path: string): string {
  return path.replace(/\\/g, "/").replace(/^\.\//, "").toLowerCase();
}

/** Whether a path names a design page: a page under a `design/` folder, what Reeve
 *  checks after it writes one and what opens as a design tab. */
export function isDesignPath(path: string): boolean {
  const parts = path.replace(/\\/g, "/").split("/");
  return /\.html?$/i.test(parts[parts.length - 1] ?? "") && parts.slice(0, -1).some((part) => part.toLowerCase() === "design");
}

/** Whether two spellings name the same page: equal, or one the other's tail at a folder line (a
 *  link names `northwind/a.html` where the check named the full path, or the other way round). */
export function samePage(a: string, b: string): boolean {
  const x = designKey(a);
  const y = designKey(b);
  return x === y || x.endsWith(`/${y}`) || y.endsWith(`/${x}`);
}

/** Open a design page in its own tab, or the tab it already has. */
export function openDesignTab(tabs: RailTab[], path: string): RailTabsState {
  const key = designKey(path);
  const existing = tabs.find((tab) => tab.kind === "design" && tab.path && designKey(tab.path) === key);
  if (existing) return { tabs, activeId: existing.id };
  const base = tabs.filter((tab) => tab.kind !== "guide");
  const tab: RailTab = { id: `design:${key}`, kind: "design", path };
  return { tabs: [...base, tab], activeId: tab.id };
}

/** Open a design's review against the app at `url`, one per page; a later address replaces it. */
export function openDesignAppTab(tabs: RailTab[], path: string, url: string): RailTabsState {
  const key = designKey(path);
  const id = `design-app:${key}`;
  const existing = tabs.find((tab) => tab.id === id);
  if (existing) {
    return { tabs: tabs.map((tab) => (tab.id === id ? { ...tab, url } : tab)), activeId: id };
  }
  const base = tabs.filter((tab) => tab.kind !== "guide");
  return { tabs: [...base, { id, kind: "design-app", path, url }], activeId: id };
}

/** Show `url` in a Browser tab: the one already on it, else a blank one (a Browser opened and never
 *  pointed anywhere), else a new one. A link followed twice does not stack two tabs of one page. */
export function openBrowserAt(tabs: RailTab[], url: string): RailTabsState {
  const showing = tabs.find((tab) => tab.kind === "browser" && tab.url === url);
  if (showing) return { tabs, activeId: showing.id };
  const blank = tabs.find((tab) => tab.kind === "browser" && !tab.url);
  if (blank) return { tabs: browserNavigate(tabs, blank.id, url), activeId: blank.id };
  return openRailTab(tabs, "browser", { url });
}

/** Close a tab; the neighbour takes focus, and the last close reseeds the guide. */
export function closeRailTab(tabs: RailTab[], id: string, activeId: string): RailTabsState {
  const index = tabs.findIndex((tab) => tab.id === id);
  if (index === -1) return { tabs, activeId };
  const next = tabs.filter((tab) => tab.id !== id);
  if (next.length === 0) {
    const seeded = seedRailTabs();
    return { tabs: seeded, activeId: seeded[0].id };
  }
  const nextActive =
    id === activeId ? (next[Math.min(index, next.length - 1)] ?? next[0]).id : activeId;
  return { tabs: next, activeId: nextActive };
}

/** Move `fromId` to where `toId` sits. Unknown ids are a
 *  no-op, so a stale drop cannot scramble the strip. */
export function reorderRailTabs(tabs: RailTab[], fromId: string, toId: string): RailTab[] {
  const from = tabs.findIndex((tab) => tab.id === fromId);
  const to = tabs.findIndex((tab) => tab.id === toId);
  if (from === -1 || to === -1 || from === to) return tabs;
  const next = [...tabs];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

/** Put a closed tab back (the overview's "Recently closed"). Review/Files are singletons and
 *  only need activating; Browser and Terminal get a fresh id so the reopened tab cannot
 *  collide with a live one — a terminal comes back without its server id, i.e. as a fresh
 *  shell, because the pipe it owned is gone. */
export function reopenRailTab(tabs: RailTab[], tab: RailTab): RailTabsState {
  if (tab.kind === "review" || tab.kind === "files" || tab.kind === "designs") {
    return openRailTab(tabs, tab.kind);
  }
  if (tab.kind === "design") {
    return tab.path ? openDesignTab(tabs, tab.path) : { tabs, activeId: tabs[0]?.id ?? "guide" };
  }
  if (tab.kind === "design-app") {
    return tab.path && tab.url ? openDesignAppTab(tabs, tab.path, tab.url) : { tabs, activeId: tabs[0]?.id ?? "guide" };
  }
  if (tab.kind === "subagent") {
    if (!tab.childId) return { tabs, activeId: tabs[0]?.id ?? "guide" };
    return openSubagentTab(tabs, { childId: tab.childId, title: tab.title, group: tab.group });
  }
  const base = tabs.filter((t) => t.kind !== "guide");
  if (tab.kind === "terminal") {
    const n = nextTerminalNumber(tabs);
    const revived: RailTab = { id: `terminal-${n}`, kind: "terminal", shell: tab.shell, terminalId: `t${n}` };
    return { tabs: [...base, revived], activeId: revived.id };
  }
  const revived: RailTab = {
    id: nextBrowserId(tabs),
    kind: "browser",
    url: tab.url ?? "",
    history: tab.history?.length ? [...tab.history] : tab.url ? [tab.url] : [],
    historyIndex: tab.history?.length ? (tab.historyIndex ?? tab.history.length - 1) : tab.url ? 0 : -1,
  };
  return { tabs: [...base, revived], activeId: revived.id };
}

/** Commit an address in a browser tab: forward history is truncated, . */export function browserNavigate(tabs: RailTab[], id: string, url: string): RailTab[] {
  return tabs.map((tab) => {
    if (tab.id !== id || tab.kind !== "browser") return tab;
    const history = (tab.history ?? []).slice(0, (tab.historyIndex ?? -1) + 1);
    history.push(url);
    return { ...tab, url, history, historyIndex: history.length - 1 };
  });
}

export function browserStep(tabs: RailTab[], id: string, delta: -1 | 1): RailTab[] {
  return tabs.map((tab) => {
    if (tab.id !== id || tab.kind !== "browser") return tab;
    const history = tab.history ?? [];
    if (!history.length) return tab;
    const index = Math.min(Math.max((tab.historyIndex ?? 0) + delta, 0), history.length - 1);
    return { ...tab, historyIndex: index, url: history[index] ?? tab.url };
  });
}

/** Forget a browser tab's back and forward: the page it shows stays, as its only entry. */
export function browserClearHistory(tabs: RailTab[], id: string): RailTab[] {
  return tabs.map((tab) => {
    if (tab.id !== id || tab.kind !== "browser") return tab;
    return { ...tab, history: tab.url ? [tab.url] : [], historyIndex: tab.url ? 0 : -1 };
  });
}

export function browserCanStep(tab: RailTab | null | undefined, delta: -1 | 1): boolean {
  if (!tab || tab.kind !== "browser") return false;
  const index = tab.historyIndex ?? -1;
  if (delta === -1) return index > 0;
  return index >= 0 && index < (tab.history?.length ?? 0) - 1;
}

/** The chip's label: the guide is "Start", browsers show their host, terminals their shell. */
export function railTabTitle(tab: RailTab): string {
  if (tab.kind === "guide") return "Start";
  if (tab.kind === "review") return "Review";
  if (tab.kind === "files") return "Files";
  if (tab.kind === "terminal") return tab.shell || "Terminal";
  if (tab.kind === "subagent") return tab.title || "Subagent";
  if (tab.kind === "designs") return "Designs";
  if (tab.kind === "design") return tab.path?.split(/[\\/]/).pop() || "Design";
  if (tab.kind === "design-app") return `${tab.path?.split(/[\\/]/).pop() || "Design"} · app`;
  return tab.url ? displayHost(tab.url) : "Browser";
}

export function readStoredRailTabs(sessionId: string): RailTabsState | null {
  try {
    const raw = localStorage.getItem(railTabsStorageKey(sessionId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { tabs?: unknown; activeId?: unknown };
    if (!parsed || !Array.isArray(parsed.tabs)) return null;
    const tabs = parsed.tabs.filter(
      (tab): tab is RailTab =>
        !!tab &&
        typeof tab === "object" &&
        typeof (tab as RailTab).id === "string" &&
        ((tab as RailTab).kind === "review" ||
          (tab as RailTab).kind === "files" ||
          (tab as RailTab).kind === "browser" ||
          (tab as RailTab).kind === "designs" ||
          ((tab as RailTab).kind === "design" && typeof (tab as RailTab).path === "string") ||
          ((tab as RailTab).kind === "design-app" &&
            typeof (tab as RailTab).path === "string" &&
            typeof (tab as RailTab).url === "string")),
    );
    if (!tabs.length) return null;
    const activeId = tabs.some((tab) => tab.id === parsed.activeId)
      ? String(parsed.activeId)
      : tabs[0].id;
    return { tabs, activeId };
  } catch {
    return null;
  }
}

export function writeStoredRailTabs(sessionId: string, state: RailTabsState): void {
  try {
    const tabs = state.tabs.filter(
      (tab) =>
        tab.kind === "review" ||
        tab.kind === "files" ||
        tab.kind === "browser" ||
        tab.kind === "designs" ||
        tab.kind === "design" ||
        tab.kind === "design-app",
    );
    if (!tabs.length) {
      localStorage.removeItem(railTabsStorageKey(sessionId));
      return;
    }
    localStorage.setItem(railTabsStorageKey(sessionId), JSON.stringify({ tabs, activeId: state.activeId }));
  } catch {
    /* best effort */
  }
}
