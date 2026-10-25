// @vitest-environment jsdom
// The rail's tab model: seed/replace semantics, singleton reuse, browser history, and the
// per-session preference (terminals deliberately not restored).
import { afterEach, describe, expect, it } from "vitest";
import {
  browserCanStep,
  browserNavigate,
  browserStep,
  closeRailTab,
  openBrowserAt,
  openRailTab,
  openSubagentTab,
  railTabTitle,
  railTabsStorageKey,
  readStoredRailTabs,
  reopenRailTab,
  reorderRailTabs,
  seedRailTabs,
  writeStoredRailTabs,
  type RailTab,
} from "./tabs";

afterEach(() => localStorage.clear());

describe("rail tabs model", () => {
  it("seeds the guide and replaces it on the first door pick", () => {
    const seeded = seedRailTabs();
    expect(seeded).toEqual([{ id: "guide", kind: "guide" }]);
    const review = openRailTab(seeded, "review");
    expect(review.tabs.map((tab) => tab.kind)).toEqual(["review"]);
    expect(review.activeId).toBe("review");
  });

  it("reuses the review singleton, and opens one tab per browser and terminal", () => {
    let state = openRailTab(seedRailTabs(), "review");
    state = openRailTab(state.tabs, "review");
    expect(state.tabs).toHaveLength(1);

    state = openRailTab(state.tabs, "browser");
    state = openRailTab(state.tabs, "browser", { url: "http://localhost:1420/" });
    const browsers = state.tabs.filter((tab) => tab.kind === "browser");
    expect(browsers).toHaveLength(2);
    expect(new Set(browsers.map((tab) => tab.id)).size).toBe(2);
    expect(browsers[1].history).toEqual(["http://localhost:1420/"]);
    expect(browsers[1].historyIndex).toBe(0);

    // Terminals are many too: each gets its own server-side id.
    state = openRailTab(state.tabs, "terminal", { shell: "cmd" });
    state = openRailTab(state.tabs, "terminal", { shell: "powershell" });
    const terminals = state.tabs.filter((tab) => tab.kind === "terminal");
    expect(terminals.map((tab) => tab.id)).toEqual(["terminal-1", "terminal-2"]);
    expect(terminals.map((tab) => tab.terminalId)).toEqual(["t1", "t2"]);
    expect(terminals.map((tab) => tab.shell)).toEqual(["cmd", "powershell"]);
    expect(state.activeId).toBe("terminal-2");
  });

  it("focuses the neighbour on close and reseeds the guide when the last tab goes", () => {
    const opened = openRailTab(openRailTab(seedRailTabs(), "review").tabs, "browser");
    const browser = opened.tabs.find((tab) => tab.kind === "browser")!;
    const after = closeRailTab(opened.tabs, browser.id, opened.activeId);
    expect(after.tabs.map((tab) => tab.kind)).toEqual(["review"]);
    expect(after.activeId).toBe("review");
    const last = closeRailTab(after.tabs, "review", "review");
    expect(last.tabs[0].kind).toBe("guide");
    expect(last.activeId).toBe("guide");
  });

  it("navigates with forward-history truncation and clamped steps", () => {
    let tabs: RailTab[] = openRailTab(seedRailTabs(), "browser").tabs;
    const id = tabs[0].id;
    tabs = browserNavigate(tabs, id, "https://a.example/");
    tabs = browserNavigate(tabs, id, "https://b.example/");
    tabs = browserStep(tabs, id, -1);
    expect(tabs[0].url).toBe("https://a.example/");
    expect(browserCanStep(tabs[0], 1)).toBe(true);
    expect(browserCanStep(tabs[0], -1)).toBe(false);

    tabs = browserNavigate(tabs, id, "https://c.example/");
    expect(tabs[0].url).toBe("https://c.example/");
    expect(browserCanStep(tabs[0], 1)).toBe(false);
    expect(tabs[0].history).toEqual(["https://a.example/", "https://c.example/"]);
  });

  it("persists review/browser tabs per session and drops terminals", () => {
    const state = openRailTab(openRailTab(seedRailTabs(), "review").tabs, "terminal");
    writeStoredRailTabs("s1", state);
    // Terminals are not restorable: a reload forgets them (no reattach story).
    expect(readStoredRailTabs("s1")?.tabs.map((tab) => tab.kind)).toEqual(["review"]);
    expect(readStoredRailTabs("s2")).toBeNull();

    writeStoredRailTabs("s1", { tabs: seedRailTabs(), activeId: "guide" });
    expect(localStorage.getItem(railTabsStorageKey("s1"))).toBeNull();
  });

  it("ignores malformed storage", () => {
    localStorage.setItem(railTabsStorageKey("s1"), "{not json");
    expect(readStoredRailTabs("s1")).toBeNull();
    localStorage.setItem(
      railTabsStorageKey("s1"),
      JSON.stringify({ tabs: [{ kind: "terminal" }] }),
    );
    expect(readStoredRailTabs("s1")).toBeNull();
  });

  it("titles chips by kind, browser host and terminal shell", () => {
    expect(railTabTitle({ id: "guide", kind: "guide" })).toBe("Start");
    expect(railTabTitle({ id: "review", kind: "review" })).toBe("Review");
    expect(railTabTitle({ id: "terminal-1", kind: "terminal" })).toBe("Terminal");
    expect(railTabTitle({ id: "terminal-1", kind: "terminal", shell: "powershell" })).toBe(
      "powershell",
    );
    expect(railTabTitle({ id: "browser-1", kind: "browser" })).toBe("Browser");
    expect(railTabTitle({ id: "browser-1", kind: "browser", url: "http://127.0.0.1:1420/x" })).toBe(
      "127.0.0.1:1420",
    );
  });

  it("reorders by moving the dragged chip to the target's slot, ignoring stale ids", () => {
    const tabs: RailTab[] = [
      { id: "guide", kind: "guide" },
      { id: "review", kind: "review" },
      { id: "browser-1", kind: "browser", url: "http://a/" },
      { id: "browser-2", kind: "browser", url: "http://b/" },
    ];
    expect(reorderRailTabs(tabs, "browser-2", "review").map((t) => t.id)).toEqual([
      "guide",
      "browser-2",
      "review",
      "browser-1",
    ]);
    // Backwards, and the no-ops: same slot, unknown id on either side.
    expect(reorderRailTabs(tabs, "guide", "browser-1").map((t) => t.id)).toEqual([
      "review",
      "browser-1",
      "guide",
      "browser-2",
    ]);
    expect(reorderRailTabs(tabs, "review", "review")).toBe(tabs);
    expect(reorderRailTabs(tabs, "browser-9", "review")).toBe(tabs);
    expect(reorderRailTabs(tabs, "review", "browser-9")).toBe(tabs);
  });

  it("reopens a closed tab: singletons activate, browsers keep their history, terminals get a fresh shell", () => {
    const tabs: RailTab[] = [{ id: "guide", kind: "guide" }];

    const review = reopenRailTab(tabs, { id: "review", kind: "review" });
    expect(review.tabs.map((t) => t.id)).toEqual(["review"]);

    const browser = reopenRailTab(tabs, {
      id: "browser-4",
      kind: "browser",
      url: "http://b/page",
      history: ["http://a/", "http://b/page"],
      historyIndex: 1,
    });
    expect(browser.tabs[0]).toMatchObject({
      id: "browser-1",
      url: "http://b/page",
      history: ["http://a/", "http://b/page"],
      historyIndex: 1,
    });
    expect(browser.activeId).toBe("browser-1");

    const terminal = reopenRailTab(tabs, { id: "terminal-1", kind: "terminal", shell: "bash" });
    expect(terminal.tabs[0]).toMatchObject({ id: "terminal-1", shell: "bash", terminalId: "t1" });
    // A live terminal keeps its number, so the reopened one takes the next free id.
    const again = reopenRailTab(
      [{ id: "guide", kind: "guide" }, { id: "terminal-1", kind: "terminal", terminalId: "t1" }],
      { id: "terminal-1", kind: "terminal", shell: "cmd" },
    );
    expect(again.tabs.map((t) => t.id)).toEqual(["terminal-1", "terminal-2"]);
  });

  // A subagent's session is a tab per child (owner, 2026-10-13), opened from its row.
  it("gives each child its own tab, and never a second one for the same child", () => {
    let state = openSubagentTab(seedRailTabs(), { childId: "c1", title: "Probe A" });
    expect(state.tabs).toEqual([{ id: "subagent-c1", kind: "subagent", childId: "c1", title: "Probe A" }]);
    state = openSubagentTab(state.tabs, { childId: "c2", title: "Probe B" });
    expect(state.tabs.map((tab) => tab.id)).toEqual(["subagent-c1", "subagent-c2"]);
    expect(state.activeId).toBe("subagent-c2");
    // Opening the first again brings its tab forward.
    const again = openSubagentTab(state.tabs, { childId: "c1", title: "Probe A" });
    expect(again.tabs).toBe(state.tabs);
    expect(again.activeId).toBe("subagent-c1");
  });

  it("names a child's tab by its task, takes a better name later, and keeps the one it has otherwise", () => {
    const unnamed = openSubagentTab(seedRailTabs(), { childId: "c1" });
    expect(railTabTitle(unnamed.tabs[0])).toBe("Subagent");
    const named = openSubagentTab(unnamed.tabs, { childId: "c1", title: "Probe A" });
    expect(railTabTitle(named.tabs[0])).toBe("Probe A");
    // The sidebar's line opens a child with no title of its own: the name stays.
    expect(railTabTitle(openSubagentTab(named.tabs, { childId: "c1", title: "" }).tabs[0])).toBe("Probe A");
  });

  it("carries the workflow run an agent belongs to, and reopens a closed child as the same tab", () => {
    const opened = openSubagentTab(openRailTab(seedRailTabs(), "review").tabs, {
      childId: "a1",
      title: "shop/cart.py",
      group: "file-review",
    });
    const tab = opened.tabs.find((t) => t.kind === "subagent")!;
    expect(tab.group).toBe("file-review");
    const closed = closeRailTab(opened.tabs, tab.id, opened.activeId);
    expect(closed.tabs.map((t) => t.kind)).toEqual(["review"]);
    const back = reopenRailTab(closed.tabs, tab);
    expect(back.tabs.map((t) => t.id)).toEqual(["review", "subagent-a1"]);
    expect(back.activeId).toBe("subagent-a1");
  });

  it("does not keep a child's tab across a restart: the chat is the record", () => {
    const state = openSubagentTab(openRailTab(seedRailTabs(), "review").tabs, { childId: "c1", title: "Probe A" });
    writeStoredRailTabs("s1", state);
    expect(readStoredRailTabs("s1")?.tabs.map((tab) => tab.kind)).toEqual(["review"]);
    // The child was the open tab: what comes back opens on a tab that exists.
    expect(readStoredRailTabs("s1")?.activeId).toBe("review");
  });
});

describe("openBrowserAt", () => {
  it("shows a page in the tab already on it, else a blank Browser, else a new one", () => {
    const review: RailTab = { id: "review", kind: "review" };
    const docs: RailTab = { id: "browser-1", kind: "browser", url: "https://docs.example.com/", history: ["https://docs.example.com/"], historyIndex: 0 };
    const blank: RailTab = { id: "browser-2", kind: "browser", url: "", history: [], historyIndex: -1 };

    expect(openBrowserAt([review, docs], "https://docs.example.com/")).toEqual({ tabs: [review, docs], activeId: "browser-1" });

    const reused = openBrowserAt([review, docs, blank], "https://news.example.com/");
    expect(reused.activeId).toBe("browser-2");
    expect(reused.tabs).toHaveLength(3);
    expect(reused.tabs[2].url).toBe("https://news.example.com/");

    const added = openBrowserAt([{ id: "guide", kind: "guide" }], "https://news.example.com/");
    expect(added.tabs.map((tab) => tab.kind)).toEqual(["browser"]);
    expect(added.tabs[0].url).toBe("https://news.example.com/");
    expect(added.activeId).toBe(added.tabs[0].id);
  });
});
