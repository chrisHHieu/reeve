import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { liveRegistry } from "../live/liveSession";
import type { SessionInfo } from "../types";
import { Sidebar } from "./Sidebar";

// The frame (owner, 2026-10-13: "B frame, A inside"): a rail that is always there, and the work
// queue beside it, the sessions grouped by what each needs from the person.

type Sent = { url: string; method: string; body: any };
type Route = { match: string; method?: string; json: unknown };

/** /v1 answered from the first route whose path the URL contains and whose method, if it names
 *  one, matches; anything else answers `{}`. Returns every request, in order. */
function serve(routes: Route[]): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = (init?.method || "GET").toUpperCase();
      sent.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const hit = routes.find((r) => url.includes(r.match) && (!r.method || r.method === method));
      return { ok: true, json: async () => (hit ? hit.json : {}) } as Response;
    }),
  );
  return sent;
}

const now = () => new Date().toISOString();
const earlier = (days: number) => new Date(Date.now() - days * 86_400_000).toISOString();
/** `n` seconds before now, but never before today began: "Done today" must not depend on the
 *  hour the suite runs (a run just after midnight put "14 minutes ago" in yesterday). */
const todayAgo = (n: number) => {
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  return new Date(Math.max(midnight.getTime() + 1000, Date.now() - n * 1000)).toISOString();
};

const row = (session_id: string, title: string, over: Partial<SessionInfo> = {}): SessionInfo => ({
  session_id,
  title,
  workspace: "",
  agent: "reeve",
  model: "m",
  mode: "interactive",
  updated_at: now(),
  messages: 1,
  ...over,
});

// Newest first: the ops one, then the open one.
const SESSIONS: SessionInfo[] = [row("s-ops-1", "incident watch", { workspace: "/w", agent: "ops", messages: 2 }), row("s-reeve-1", "hi there", { updated_at: todayAgo(5) })];

const baseProps = {
  sessions: SESSIONS,
  activeSession: "s-reeve-1",
  scheduledActive: false,
  integrationsActive: false,
  opsActive: false,
  ...Object.fromEntries(
    ["onNewSession", "onSelectSession", "onRenameSession", "onDeleteSession", "onArchiveSession", "onTogglePin", "onForkSession", "onManage", "onOpenScheduled", "onOpenIntegrations", "onOpenOps"].map((name) => [name, vi.fn()]),
  ),
} as unknown as Parameters<typeof Sidebar>[0] & Record<string, ReturnType<typeof vi.fn>>;

const SETTINGS: Route[] = [{ match: "/v1/settings", method: "GET", json: {} }];

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const groupOf = (title: string) => screen.getByTitle(title).getAttribute("data-group");
/** The rows listed in one project under the queue ("" = the chats with no folder). */
const projectRows = (key: string) =>
  Array.from(
    screen
      .getAllByTestId("queue-project")
      .find((p) => p.getAttribute("data-project") === key)!
      .querySelectorAll('[data-testid="session-row"]'),
  ).map((r) => r.getAttribute("title"));

describe("the work queue", () => {
  const session = (over: Partial<SessionInfo>): SessionInfo => ({ ...SESSIONS[1], ...over });

  it("puts each session in the one group that says what it needs from the person", async () => {
    serve(SETTINGS);
    render(
      <Sidebar
        {...baseProps}
        sessions={[
          session({ session_id: "n", title: "waits on me", attention: 1, attention_kind: "approval", updated_at: earlier(3) }),
          session({ session_id: "r", title: "is working", liveness: "working" }),
          session({ session_id: "z", title: "is asleep", liveness: "sleeping" }),
          session({ session_id: "v", title: "left changes", work: { files: 2, added: 9, removed: 1, open: 2 }, updated_at: earlier(2) }),
          session({ session_id: "c", title: "all committed", work: { files: 2, added: 9, removed: 1, open: 0 } }),
          session({ session_id: "o", title: "last week", updated_at: earlier(6) }),
          session({ session_id: "a", title: "put away", archived: true, attention: 1 }),
        ]}
      />,
    );
    await screen.findByText("waits on me");
    expect(groupOf("waits on me")).toBe("needs");
    expect(groupOf("is working")).toBe("running");
    expect(groupOf("is asleep")).toBe("running");
    expect(groupOf("left changes")).toBe("review");
    expect(groupOf("all committed")).toBe("today");
    // The queue lists what is live, in the order of what is most pressing...
    const heads = Array.from(document.querySelectorAll("[data-group]"))
      .filter((n) => n.getAttribute("data-testid") !== "session-row")
      .map((n) => n.getAttribute("data-group"));
    expect(heads).toEqual(["needs", "running", "review"]);
    // ...and the rest are in their project under it (here the chats with no folder), newest first.
    expect(projectRows("")).toEqual(["all committed", "last week"]);
    expect(groupOf("last week")).toBe("earlier");
    // What was archived is in no group, closed at the foot, even while it waits.
    expect(screen.queryByText("put away")).toBeNull();
    fireEvent.click(screen.getByTestId("archived-toggle"));
    expect(screen.getByText("put away")).toBeTruthy();
  });

  it("says under the title what a session waits for, when it wakes, and what is not committed", async () => {
    serve(SETTINGS);
    render(
      <Sidebar
        {...baseProps}
        sessions={[
          session({ session_id: "w1", title: "tidy downloads", attention: 1, attention_kind: "approval", attention_title: "Delete 3 files in export/?" }),
          session({ session_id: "w2", title: "spending report", attention: 2, attention_kind: "question" }),
          session({ session_id: "w3", title: "old run", attention: 1 }),
          session({ session_id: "s1", title: "napping", liveness: "sleeping", sleeping_until: new Date(2026, 9, 11, 14, 30).toISOString() }),
          session({ session_id: "v1", title: "left changes", work: { files: 3, added: 31, removed: 7, open: 1 } }),
        ]}
      />,
    );
    await screen.findByText("tidy downloads");
    const line = (title: string) => screen.getByTitle(title).querySelector('[data-testid="queue-line"]')?.textContent;
    expect(line("tidy downloads")).toBe("Delete 3 files in export/?"); // the prompt's own words
    expect(line("spending report")).toBe("Question"); // else what kind it is
    expect(line("old run")).toBe("Needs you"); // a kind this does not know is still said
    expect(line("napping")).toMatch(/^Wakes .*30/);
    expect(line("left changes")).toBe("1 file not committed");
    expect(screen.getByTitle("tidy downloads").querySelector(".q-line")?.className).toContain("warn");
  });

  it("says where a session's work lives and what it amounts to", async () => {
    serve(SETTINGS);
    render(
      <Sidebar
        {...baseProps}
        sessions={[
          session({
            session_id: "f1",
            title: "fix retry",
            workspace: "/code/shop",
            branch: "fix/retry-after",
            worktree: true,
            work: { files: 3, added: 31, removed: 7, open: 3, check: { ok: true, summary: "12 passed", stale: false } },
          }),
          session({ session_id: "f2", title: "stale check", workspace: "/code/shop", work: { files: 1, added: 1, removed: 0, check: { ok: false, summary: "1 failed", stale: true } } }),
          session({ session_id: "f3", title: "plain chat", workspace: "/tmp/chat", temp_workspace: true }),
        ]}
      />,
    );
    await screen.findByText("fix retry");
    const facts = screen.getByTitle("fix retry").querySelector('[data-testid="queue-facts"]')!;
    expect(facts.querySelector(".q-branch")?.textContent).toBe("fix/retry-after");
    expect(facts.querySelector(".q-tag")?.textContent).toBe("worktree");
    expect(facts.querySelector(".q-project")?.textContent).toBe("shop");
    expect(facts.querySelector(".q-diff")?.textContent).toBe("+31−7");
    expect(facts.querySelector(".q-check")?.textContent).toBe("passed");
    // A check run before the last edits proves nothing about them: not shown.
    expect(screen.getByTitle("stale check").querySelector(".q-check")).toBeNull();
    // A plain chat has nothing to say there.
    expect(screen.getByTitle("plain chat").querySelector('[data-testid="queue-facts"]')).toBeNull();
  });

  it("lists every project under the queue, a quiet one too, open when it was active today", async () => {
    serve(SETTINGS);
    render(
      <Sidebar
        {...baseProps}
        sessions={[
          session({ session_id: "a", title: "shop work", workspace: "/code/shop", updated_at: todayAgo(2) }),
          session({ session_id: "b", title: "api work", workspace: "/code/api", liveness: "working", updated_at: now() }),
          session({ session_id: "c", title: "more shop", workspace: "/code/shop", attention: 1, updated_at: todayAgo(3) }),
          session({ session_id: "d", title: "old notes", workspace: "/code/notes", updated_at: earlier(9) }),
        ]}
      />,
    );
    await screen.findByText("shop work");
    const projects = screen.getAllByTestId("queue-project");
    // The newest first; each says how many sessions it has and marks what is live in it.
    expect(projects.map((p) => p.querySelector(".q-proj-name")?.textContent)).toEqual(["api", "shop", "notes"]);
    const shop = projects[1];
    expect(shop.querySelector(".q-proj-meta")?.textContent).toMatch(/^2 · /);
    expect(shop.querySelector(".q-proj-live")?.getAttribute("aria-label")).toBe("Needs you");
    // A live session is in the queue, not again in its project; there it says its folder.
    expect(projectRows("/code/shop")).toEqual(["shop work"]);
    expect(screen.getByTitle("more shop").querySelector(".q-project")?.textContent).toBe("shop");
    expect(within(projects[0]).getByText("Everything here is in the queue above.")).toBeTruthy();
    // A quiet project is folded, and opens in place; the choice is kept.
    const notes = projects[2].querySelector("button.q-proj") as HTMLButtonElement;
    expect(notes.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("old notes")).toBeNull();
    fireEvent.click(notes);
    expect(notes.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("old notes")).toBeTruthy();
    expect(JSON.parse(localStorage.getItem("reeve:queue-projects:v1") || "{}")).toEqual({ "/code/notes": true });
    // No folder chips any more: the projects list does their job.
    expect(screen.queryByTestId("queue-filter")).toBeNull();
  });

  it("opens the project of the session on screen, and shows as many of its sessions as the setting says", async () => {
    serve([{ match: "/v1/settings", method: "GET", json: { sessions_peek: 1 } }]);
    render(
      <Sidebar
        {...baseProps}
        activeSession="e2"
        sessions={[
          session({ session_id: "e1", title: "first", workspace: "/code/old", updated_at: earlier(3) }),
          session({ session_id: "e2", title: "second", workspace: "/code/old", updated_at: earlier(4) }),
        ]}
      />,
    );
    await waitFor(() => expect(screen.getByText("Show 1 more")).toBeTruthy());
    expect(screen.getByText("first")).toBeTruthy();
    expect(screen.queryByText("second")).toBeNull();
    fireEvent.click(screen.getByText("Show 1 more"));
    expect(screen.getByText("second")).toBeTruthy();
  });

  it("lists an automation's run while it waits, and no other internal conversation", async () => {
    // A run's conversation ("__run__…") is no row of the queue. The server sends one only while
    // it holds a prompt, marked with its automation: nobody has it open, so this is where its
    // prompt is found from.
    serve(SETTINGS);
    const onSelectSession = vi.fn();
    render(
      <Sidebar
        {...baseProps}
        onSelectSession={onSelectSession}
        sessions={[
          ...SESSIONS,
          session({ session_id: "__run__r9", title: "Daily AI News", attention: 1, attention_kind: "approval", automation: { id: "task-1", title: "Daily AI News" } }),
          session({ session_id: "__task__t1", title: "a task's own thread", attention: 1, attention_kind: "approval" }),
        ]}
      />,
    );
    const group = await screen.findByTestId("needs-you");
    const rows = group.querySelectorAll('[data-testid="session-row"]');
    expect(rows).toHaveLength(1);
    // It is its automation's to rename or delete, not the queue's.
    expect(rows[0].querySelector('[data-testid="row-menu"]')).toBeNull();
    fireEvent.click(within(rows[0] as HTMLElement).getAllByRole("button")[0]);
    expect(onSelectSession).toHaveBeenCalledWith("__run__r9", SESSIONS[1].workspace);
  });

  it("lists an automation's run under Running while it runs, and opens the automation", async () => {
    const run = (status: string, attention = 0) => ({ run_id: "r", task_id: "x", session_id: "__run__r", started_at: 1_760_000_000, status, attention });
    serve([
      ...SETTINGS,
      {
        match: "/v1/automations",
        method: "GET",
        json: {
          tasks: [
            { id: "a-run", title: "Nightly deps check", workspace: "/w", latest_run: run("running") },
            { id: "a-wait", title: "Inbox triage", workspace: "/w", latest_run: run("running", 1) },
            { id: "a-done", title: "Daily AI News", workspace: "/w", latest_run: run("ok") },
          ],
        },
      },
    ]);
    const onOpenAutomation = vi.fn();
    render(<Sidebar {...baseProps} onOpenAutomation={onOpenAutomation} />);
    const row = await screen.findByTestId("automation-run-row");
    // One row: a run that waits is its conversation's row under Needs you; a finished one is not running.
    expect(screen.getAllByTestId("automation-run-row")).toHaveLength(1);
    expect(screen.getByTestId("queue-group-running").contains(row)).toBe(true);
    expect(within(row).getByTestId("queue-line").textContent).toMatch(/^Automation run · started /);
    fireEvent.click(within(row).getByRole("button"));
    expect(onOpenAutomation).toHaveBeenCalledWith("a-run");
  });

  it("has no Needs you group when nothing waits", async () => {
    serve(SETTINGS);
    render(<Sidebar {...baseProps} />);
    await screen.findByText("incident watch");
    expect(screen.queryByTestId("needs-you")).toBeNull();
    expect(screen.getByText("Nothing running or waiting.")).toBeTruthy();
    expect(projectRows("/w")).toEqual(["incident watch"]);
    expect(projectRows("")).toEqual(["hi there"]);
  });

  it("says when a session was last active, where a mention came from, and nothing for a running one", async () => {
    serve(SETTINGS);
    render(
      <Sidebar
        {...baseProps}
        sessions={[
          session({ session_id: "r1", title: "running", liveness: "working" }),
          session({ session_id: "r4", title: "just now", updated_at: now() }),
          session({ session_id: "r6", title: "#general — check the deploy?", origin: "slack", origin_label: "#general · T0AB", updated_at: now() }),
        ]}
      />,
    );
    await screen.findByText("running");
    const meta = (title: string) => screen.getByTitle(title).querySelector('[data-testid="row-meta"]')?.textContent;
    expect(meta("running")).toBeUndefined();
    expect(meta("just now")).toBe("now");
    expect(meta("#general — check the deploy?")).toMatch(/^Slack · /);
    // A steady dot, never one that pulses.
    expect(document.querySelector(".animate-pulse")).toBeNull();
  });

  it("selects a session from its row, which is a button", async () => {
    serve(SETTINGS);
    render(<Sidebar {...baseProps} />);
    const row = (await screen.findByText("incident watch")).closest("button") as HTMLButtonElement;
    fireEvent.click(row);
    expect(baseProps.onSelectSession).toHaveBeenCalledWith("s-ops-1", "/w");
    expect(screen.getByTitle("hi there").querySelector("button")?.getAttribute("aria-current")).toBe("page");
    // The row carries the full name; the ⋮ sits in it, beside the button.
    expect(screen.getByTitle("incident watch").contains(row)).toBe(true);
    expect(screen.getByTitle("incident watch").querySelector('[data-testid="row-menu"]')).toBeTruthy();
  });

  it("says what a running session is doing now, from the conversation the app holds", async () => {
    serve(SETTINGS);
    const live = liveRegistry.ensure("s-run");
    live.setRunning(true);
    live.setItems([{ kind: "tool", id: "t1", name: "run_shell", args: { command: "pytest tests/api -q" }, status: "…" }]);
    try {
      render(
        <Sidebar
          {...baseProps}
          sessions={[
            session({ session_id: "s-run", title: "fix the retry", liveness: "working" }),
            session({ session_id: "s-far", title: "running elsewhere", liveness: "working" }),
          ]}
        />,
      );
      const line = await within(screen.getByTitle("fix the retry")).findByTestId("queue-line");
      expect(line.textContent).toBe("Running a command · pytest tests/api -q");
      // A run this window holds nothing of says nothing rather than a guess.
      expect(within(screen.getByTitle("running elsewhere")).queryByTestId("queue-line")).toBeNull();
      act(() => live.setItems([{ kind: "tool", id: "t2", name: "write_file", args: { path: "src/retry.py" }, status: "…" }]));
      expect(line.textContent).toBe("Editing files · retry.py");
    } finally {
      liveRegistry.clear();
    }
  });

  it("J and K walk the rows from the open one, and never while typing", async () => {
    serve(SETTINGS);
    render(<Sidebar {...baseProps} />);
    await screen.findByText("incident watch");
    const button = (title: string) => screen.getByTitle(title).querySelector("button") as HTMLButtonElement;
    // "incident watch" (now) is above "hi there" (the open one, earlier today) in Done today.
    fireEvent.keyDown(document.body, { key: "k" });
    expect(document.activeElement).toBe(button("incident watch"));
    fireEvent.keyDown(document.body, { key: "k" }); // the first row stays the first
    expect(document.activeElement).toBe(button("incident watch"));
    fireEvent.keyDown(document.activeElement!, { key: "j" });
    expect(document.activeElement).toBe(button("hi there"));
    // Typing a j is typing.
    const input = document.createElement("textarea");
    document.body.appendChild(input);
    input.focus();
    fireEvent.keyDown(input, { key: "k" });
    expect(document.activeElement).toBe(input);
    // With a modifier it is someone else's key.
    button("hi there").focus();
    fireEvent.keyDown(document.activeElement!, { key: "k", altKey: true });
    expect(document.activeElement).toBe(button("hi there"));
    input.remove();
  });

  it("J and K do nothing while the queue is folded", async () => {
    serve(SETTINGS);
    render(<Sidebar {...baseProps} collapsed onCollapse={vi.fn()} />);
    await screen.findByTestId("sidebar-rail");
    fireEvent.keyDown(document.body, { key: "j" });
    expect(document.activeElement).toBe(document.body);
  });

  it("is a skeleton before the list is read, says so when the read failed, and one line when there is none", async () => {
    serve(SETTINGS);
    const onRetrySessions = vi.fn();
    const view = render(<Sidebar {...baseProps} sessions={[]} sessionsState="loading" />);
    expect(screen.getByTestId("sidebar-loading")).toBeTruthy();
    expect(screen.getByTestId("sidebar-list").getAttribute("aria-busy")).toBe("true");

    view.rerender(<Sidebar {...baseProps} sessions={[]} sessionsState="error" onRetrySessions={onRetrySessions} />);
    expect(screen.getByTestId("sidebar-error").textContent).toContain("The session list could not be loaded.");
    fireEvent.click(screen.getByRole("button", { name: /Try again/ }));
    expect(onRetrySessions).toHaveBeenCalledTimes(1);

    view.rerender(<Sidebar {...baseProps} sessions={[]} sessionsState="ready" />);
    const empty = screen.getByTestId("sidebar-empty");
    expect(empty.textContent).toContain("No work yet");
    fireEvent.click(empty.querySelector("button") as HTMLButtonElement);
    expect(baseProps.onNewSession).toHaveBeenCalledTimes(1);

    // A list that is there is shown, whatever the last read said.
    view.rerender(<Sidebar {...baseProps} sessionsState="error" />);
    expect(screen.queryByTestId("sidebar-error")).toBeNull();
    expect(await screen.findByText("incident watch")).toBeTruthy();
  });
});

describe("row actions (⋮ menu)", () => {
  // The ops session is the newest, so its menu is the first.
  const menu = async (item?: string) => {
    fireEvent.click(screen.getAllByTestId("row-menu")[0]);
    if (item) fireEvent.click(screen.getByTestId(`row-menu-${item}`));
  };
  const opened = async () => {
    serve(SETTINGS);
    render(<Sidebar {...baseProps} />);
    await screen.findByText("incident watch");
  };

  it("renames from the menu", async () => {
    await opened();
    await menu("rename");
    const input = screen.getByDisplayValue("incident watch");
    fireEvent.change(input, { target: { value: "war room" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(baseProps.onRenameSession).toHaveBeenCalledWith("s-ops-1", "war room");
  });

  it.each([
    ["pin", "onTogglePin", ["s-ops-1", true]],
    ["archive", "onArchiveSession", ["s-ops-1", true]],
    ["fork", "onForkSession", ["s-ops-1"]],
  ])("%s calls %s", async (item, handler, args) => {
    await opened();
    await menu(item);
    expect(baseProps[handler]).toHaveBeenCalledWith(...args);
  });

  it("asks before deleting, in a dialog that names the session", async () => {
    await opened();
    await menu("delete");
    expect(screen.getByTestId("confirm-dialog").textContent).toContain("Delete “incident watch”?");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(baseProps.onDeleteSession).not.toHaveBeenCalled();
    await menu("delete");
    fireEvent.click(screen.getByTestId("confirm-dialog-ok"));
    await waitFor(() => expect(baseProps.onDeleteSession).toHaveBeenCalledWith("s-ops-1"));
  });

  it("never opens the row it is on, and closes on Escape", async () => {
    await opened();
    await menu("pin");
    expect(baseProps.onSelectSession).not.toHaveBeenCalled();
    await menu();
    expect(screen.getByTestId("row-menu-rename")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("row-menu-rename")).toBeNull();
  });

  it("puts a pinned session first in its project, marked", async () => {
    serve(SETTINGS);
    render(<Sidebar {...baseProps} sessions={[SESSIONS[0], { ...SESSIONS[1], workspace: "/w", pinned: true }]} />);
    await screen.findByText("incident watch");
    expect(projectRows("/w")).toEqual(["hi there", "incident watch"]);
    expect(screen.getByTitle("hi there").querySelector(".q-pin")).toBeTruthy();
  });
});

describe("the rail", () => {
  const renderRail = (over: Partial<Parameters<typeof Sidebar>[0]> = {}) => {
    serve(SETTINGS);
    return render(<Sidebar {...baseProps} collapsed onCollapse={vi.fn()} {...over} />);
  };

  it("is there folded and open, in one order, with Settings at the foot", async () => {
    const view = renderRail();
    await screen.findByTestId("sidebar-rail");
    expect(screen.queryByTestId("work-queue")).toBeNull();
    const order = () => Array.from(screen.getByTestId("sidebar-rail").querySelectorAll("[data-testid]")).map((node) => node.getAttribute("data-testid"));
    expect(order()).toEqual(["rail-dock", "rail-new-session", "rail-search", "rail-automations", "rail-ops", "rail-settings"]);
    expect(screen.getByTestId("rail-dock").getAttribute("aria-label")).toBe("Open sidebar");
    view.rerender(<Sidebar {...baseProps} onCollapse={vi.fn()} />);
    expect(screen.getByTestId("work-queue")).toBeTruthy();
    expect(order()).toEqual(["rail-dock", "rail-new-session", "rail-search", "rail-automations", "rail-ops", "rail-settings"]);
    expect(screen.getByTestId("rail-dock").getAttribute("aria-label")).toBe("Collapse sidebar");
  });

  it("wires every rail action to its handler", async () => {
    const onCollapse = vi.fn();
    const onNewSession = vi.fn();
    const onOpenScheduled = vi.fn();
    const onManage = vi.fn();
    renderRail({ onCollapse, onNewSession, onOpenScheduled, onManage });
    await screen.findByTestId("sidebar-rail");
    fireEvent.click(screen.getByTestId("rail-dock"));
    expect(onCollapse).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("rail-new-session"));
    expect(onNewSession).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("rail-automations"));
    expect(onOpenScheduled).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("rail-settings"));
    expect(onManage).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("rail-settings").getAttribute("title")).toMatch(/^Settings · (⌘|Ctrl\+),$/);
  });

  it("rail Search opens the command palette", async () => {
    renderRail();
    await screen.findByTestId("sidebar-rail");
    expect(screen.queryByPlaceholderText("Search chats")).toBeNull();
    fireEvent.click(screen.getByTestId("rail-search"));
    expect(screen.getByPlaceholderText("Search chats")).toBeTruthy();
  });

  it("⌘K / Ctrl K opens the same search, and says so on the button", async () => {
    renderRail();
    await screen.findByTestId("sidebar-rail");
    expect(screen.getByTestId("rail-search").getAttribute("title")).toMatch(/^Search · (⌘|Ctrl\+)K$/);
    fireEvent.keyDown(document.body, { key: "k", ctrlKey: true });
    expect(screen.getByPlaceholderText("Search chats")).toBeTruthy();
  });

  it("marks Automations live, and says when runs are not yet looked at", async () => {
    serve([...SETTINGS, { match: "/v1/automations", method: "GET", json: { tasks: [{ id: "t1", title: "Daily AI News", unseen_runs: 2 }] } }]);
    render(<Sidebar {...baseProps} scheduledActive collapsed onCollapse={vi.fn()} />);
    const button = await screen.findByTestId("rail-automations");
    expect(button.getAttribute("aria-current")).toBe("page");
    await waitFor(() => expect(button.getAttribute("aria-description")).toBe("2 new runs"));
    expect(button.getAttribute("aria-label")).toBe("Automations");
    expect(button.querySelector(".rail-unseen")?.textContent).toBe("2");
  });

  it("folded, it counts the sessions that wait and marks the running and the reviewable ones", async () => {
    const onCollapse = vi.fn();
    renderRail({
      onCollapse,
      sessions: [
        ...SESSIONS,
        { ...SESSIONS[0], session_id: "s-wait", title: "waiting", attention: 2, attention_kind: "approval" },
        { ...SESSIONS[0], session_id: "s-run", title: "running", liveness: "working" },
        { ...SESSIONS[0], session_id: "s-rev", title: "to review", work: { files: 1, added: 1, removed: 0, open: 1 } },
      ],
    });
    await screen.findByTestId("sidebar-rail");
    const count = screen.getByTestId("rail-waiting");
    expect(count.textContent).toBe("1"); // one session waits, however many prompts it holds
    expect(count.getAttribute("aria-label")).toBe("1 session needs you. Open sidebar");
    expect(screen.getByTestId("rail-marks").querySelectorAll(".q-mark")).toHaveLength(2);
    fireEvent.click(count);
    expect(onCollapse).toHaveBeenCalledTimes(1);
  });

  it("shows no count when nothing waits", async () => {
    renderRail();
    await screen.findByTestId("sidebar-rail");
    expect(screen.queryByTestId("rail-waiting")).toBeNull();
    expect(screen.queryByTestId("rail-marks")).toBeNull();
  });

  it("starts a session from the queue's New, and folds the queue from its own control", async () => {
    serve(SETTINGS);
    const onCollapse = vi.fn();
    render(<Sidebar {...baseProps} onCollapse={onCollapse} />);
    await screen.findByText("incident watch");
    const queue = screen.getByTestId("work-queue");
    fireEvent.click(within(queue).getByRole("button", { name: "New session" }));
    fireEvent.click(screen.getByTestId("queue-collapse"));
    expect(baseProps.onNewSession).toHaveBeenCalledTimes(1);
    expect(onCollapse).toHaveBeenCalledTimes(1);
  });
});

// P3: the queue nests the direct child ON THE PATH to the open session, indented, with the same
// busy dot. Leave the path and the row is gone; children never enter the list itself.
describe("the on-path child nests under its parent (P3)", () => {
  const CHILD = {
    sessionId: "explore-2",
    parentSession: "s-reeve-1",
    title: "Research subagents (@explore subagent)",
    agent: "explore",
    status: "busy" as const,
  };

  const renderSidebar = (over: Partial<Parameters<typeof Sidebar>[0]> = {}) => {
    serve(SETTINGS);
    return render(<Sidebar {...baseProps} childSessions={{ "explore-2": CHILD }} activeOnPath="explore-2" onOpenChild={vi.fn()} {...over} />);
  };

  it("indents the child under its parent, marks it busy and opens it on click", async () => {
    const onOpenChild = vi.fn();
    renderSidebar({ onOpenChild });
    await screen.findByText("incident watch");
    const nested = screen.getByTestId("sidebar-child");
    expect(nested.textContent).toContain("Research subagents");
    expect(nested.style.paddingLeft).toBe("24px");
    expect(nested.querySelector('[title="Working now"]')).toBeTruthy();
    fireEvent.click(nested.firstElementChild!);
    expect(onOpenChild).toHaveBeenCalledWith("explore-2");
    expect(screen.getAllByText("Research subagents")).toHaveLength(1);
  });

  it("renders no nested row when the open session is not inside the child", async () => {
    renderSidebar({ activeOnPath: "s-ops-1" });
    await screen.findByText("incident watch");
    expect(screen.queryByTestId("sidebar-child")).toBeNull();
  });

  it("walks a grandchild level by level", async () => {
    renderSidebar({
      activeOnPath: "grand-3",
      childSessions: {
        "explore-2": CHILD,
        "grand-3": { sessionId: "grand-3", parentSession: "explore-2", title: "Go deeper (@general subagent)", agent: "general", status: "idle" },
      },
    });
    await screen.findByText("incident watch");
    const rows = screen.getAllByTestId("sidebar-child");
    expect(rows).toHaveLength(2);
    expect(rows[1].textContent).toContain("Go deeper");
    expect(rows[1].style.paddingLeft).toBe("40px"); // 8 + 2*16
  });
});

describe("Worktrees section (/workspace)", () => {
  const WORKTREES = {
    is_git: true,
    root: "/repo/main",
    worktrees: [
      { path: "/repo/main", name: "main", head: "1111111aaaa", branch: "main", primary: true, current: true },
      { path: "/repo/feature", name: "feature", head: "2222222bbbb", branch: "feature/x", primary: false, current: false },
      { path: "/repo/pinned", name: "pinned", head: "3333333cccc", detached: true, primary: false, current: false },
    ],
  };

  it("lists worktrees at the top of the queue and opens a session on click", async () => {
    serve(SETTINGS);
    const onOpenWorktree = vi.fn();
    render(<Sidebar {...baseProps} worktreesOpen worktrees={WORKTREES} onOpenWorktree={onOpenWorktree} />);
    await screen.findByText("incident watch");
    const rows = screen.getAllByTestId("worktree-row");
    expect(rows).toHaveLength(3);
    expect(rows[0].textContent).toContain("primary");
    expect(rows[1].textContent).toContain("feature/x");
    expect(rows[2].textContent).toContain("detached @ 3333333");
    fireEvent.click(rows[1]);
    expect(onOpenWorktree).toHaveBeenCalledWith("/repo/feature");
  });

  it("a non-repo says so; a closed section renders nothing; its close control toggles it", async () => {
    serve(SETTINGS);
    const onToggleWorktrees = vi.fn();
    const { unmount } = render(
      <Sidebar {...baseProps} worktreesOpen worktrees={{ is_git: false, root: "/plain", worktrees: [] }} onToggleWorktrees={onToggleWorktrees} />,
    );
    await screen.findByText("incident watch");
    expect(screen.getByTestId("worktrees-empty").textContent).toContain("Not a git repository.");
    fireEvent.click(screen.getByTestId("worktrees-close"));
    expect(onToggleWorktrees).toHaveBeenCalledTimes(1);
    unmount();
    serve(SETTINGS);
    render(<Sidebar {...baseProps} worktrees={WORKTREES} />);
    await screen.findByText("incident watch");
    expect(screen.queryByTestId("worktrees-band")).toBeNull();
  });
});

describe("Remote session badge", () => {
  const remoteSession: SessionInfo = {
    session_id: "s-remote-1",
    title: "build watch",
    workspace: "/srv/app",
    environment_id: "h1",
    environment: { id: "h1", name: "Build server", user: "ubuntu", host: "dev.example.com", port: 2222 },
    agent: "reeve",
    model: "m",
    mode: "interactive",
    updated_at: now(),
    messages: 1,
  };

  it("marks a remote row with a monitor badge and the destination tooltip", async () => {
    serve(SETTINGS);
    render(<Sidebar {...baseProps} sessions={[remoteSession]} />);
    await screen.findByText("build watch");
    expect(screen.getByTestId("session-remote").getAttribute("title")).toContain("ubuntu@dev.example.com:2222:/srv/app");
  });

  it("leaves local rows badge-less", async () => {
    serve(SETTINGS);
    render(<Sidebar {...baseProps} sessions={SESSIONS} />);
    await screen.findByText("incident watch");
    expect(screen.queryByTestId("session-remote")).toBeNull();
  });

  it("never composes a local workspace into the remote tooltip", async () => {
    serve(SETTINGS);
    render(<Sidebar {...baseProps} sessions={[{ ...remoteSession, workspace: "C:\\Users\\DELI" }]} />);
    await screen.findByText("build watch");
    expect(screen.getByTestId("session-remote").getAttribute("title")).toBe("Remote · ubuntu@dev.example.com:2222");
  });
});
