import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import type { WhereFacts } from "./WhereCrumb";
import { SessionTopbar, sessionDisplayTitle, type SessionActions } from "./SessionTopbar";

const actions = (over: Partial<SessionActions> = {}): SessionActions => ({
  pinned: false,
  archived: false,
  onRename: vi.fn(),
  onTogglePin: vi.fn(),
  onToggleArchive: vi.fn(),
  onFork: vi.fn(),
  onExport: vi.fn(),
  onDelete: vi.fn(),
  ...over,
});

const base: ComponentProps<typeof SessionTopbar> = {
  railHidden: false,
  activeTitle: "Draft the launch note",
  hasHistory: true,
  workspaceName: "backend",
  onTogglePanel: vi.fn(),
  sidecarConnected: true,
  sidecarModel: "gpt-5.6-sol",
  onOpenConnectors: vi.fn(),
  onOpenSkills: vi.fn(),
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderTopbar(overrides: Partial<ComponentProps<typeof SessionTopbar>> = {}) {
  return render(<SessionTopbar {...base} {...overrides} />);
}

describe("SessionTopbar title row", () => {
  it("renders the session name and nothing else: no agent chip, there is one agent", () => {
    const { container } = renderTopbar();
    const main = container.querySelector(".main-title")!;
    expect(main.querySelector(".main-title-text")!.textContent).toBe("Draft the launch note");
    expect(screen.queryByTestId("topbar-agent")).toBeNull();
    // No chip, no folder/branch chip row, no facts subtitle.
    expect(main.querySelectorAll(".title-chip")).toHaveLength(0);
    expect(container.querySelector(".title-meta")).toBeNull();
    expect(screen.queryByTestId("session-subtitle")).toBeNull();
    expect(main.textContent).not.toContain("backend");
  });

  it("marks a fresh session's title as a draft", () => {
    const { container } = renderTopbar({ hasHistory: false, activeTitle: "New session" });
    expect(container.querySelector(".main-title-text")!.className).toBe(
      "main-title-text title-ghost",
    );
  });

  it("shows the friendly folder name for a raw quoted path title", () => {
    renderTopbar({
      activeTitle: '"C:\\Users\\DELL\\Downloads\\C…"',
      workspaceName: "Downloads",
    });
    expect(screen.getByText("Downloads")).toBeTruthy();
    expect(screen.queryByText(/"C:/)).toBeNull();
  });

  it("falls back to the standard new-session wording for a temporary folder", () => {
    renderTopbar({
      activeTitle: '"C:\\Users\\DELL\\Downloads\\C…"',
      workspaceName: null,
      hasHistory: false,
    });
    expect(screen.getByText("New session")).toBeTruthy();
    expect(screen.queryByText(/"C:/)).toBeNull();
  });

  // Where it works is a crumb before the name once the conversation has begun (owner,
  // 2026-10-13): said once, where the name is read. It was a mono chip beside the name for a
  // remote machine, and the strip under the composer said it again.
  const remote = { id: "h1", name: "Build server", user: "ubuntu", host: "dev.example.com", port: 2222 };
  const where = (over: Partial<WhereFacts> = {}): WhereFacts => ({
    folderName: "wealthlog",
    folderPath: "/Users/me/wealthlog",
    branch: "main",
    environment: null,
    remoteWorkspace: null,
    connected: true,
    problem: "",
    onBranchSwitched: vi.fn(),
    onRetry: vi.fn(),
    ...over,
  });

  it("says nothing of where before the first message: the strip under the composer does", () => {
    renderTopbar({ where: where(), started: false });
    expect(screen.queryByTestId("where-crumb")).toBeNull();
  });

  it("names the folder and its branch before the name, and opens them in full", () => {
    renderTopbar({ where: where(), started: true, sessionKey: "s1" });
    const crumb = screen.getByTestId("where-crumb");
    expect(crumb.textContent).toBe("wealthlogmain");
    // Before the name, not after it.
    const title = screen.getByTestId("topbar-title");
    expect(crumb.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(crumb);
    expect(screen.getByTestId("where-folder").textContent).toContain("/Users/me/wealthlog");
    expect(screen.getByTestId("where-show-files")).toBeTruthy();
    expect(screen.getByTestId("where-branch").textContent).toContain("main");
    expect(screen.getByTestId("where-machine").textContent).toBe("This computer");
    // The branch is still a control: Switch opens the picker in the same panel.
    fireEvent.click(screen.getByTestId("where-switch-branch"));
    expect(screen.getByTestId("branch-picker")).toBeTruthy();
  });

  it("names a remote machine by its name, its address and folder one press away", () => {
    renderTopbar({
      where: where({ folderName: null, folderPath: null, branch: null, environment: remote, remoteWorkspace: "/srv/app" }),
      started: true,
    });
    const crumb = screen.getByTestId("where-crumb");
    expect(crumb.textContent).toBe("Build server");
    expect(screen.queryByTestId("topbar-environment")).toBeNull();
    fireEvent.click(crumb);
    expect(screen.getByTestId("where-machine").textContent).toContain("ubuntu@dev.example.com:2222");
    expect(screen.getByTestId("where-machine").textContent).toContain("Connected");
    expect(screen.getByTestId("where-folder").textContent).toContain("/srv/app");
    // No branch row and no Files: those describe this computer.
    expect(screen.queryByTestId("where-branch")).toBeNull();
    expect(screen.queryByTestId("where-show-files")).toBeNull();
  });

  it("never shows a local path as the remote machine's folder", () => {
    renderTopbar({
      where: where({ folderName: null, folderPath: null, branch: null, environment: remote, remoteWorkspace: "C:\\Users\\DELI" }),
      started: true,
    });
    fireEvent.click(screen.getByTestId("where-crumb"));
    expect(screen.queryByTestId("where-folder")).toBeNull();
    expect(screen.getByTestId("where-pop").textContent).not.toContain("C:");
  });

  it("marks a remote machine that refused to start, says why, and can ask again", () => {
    const onRetry = vi.fn();
    renderTopbar({
      where: where({
        folderName: null,
        folderPath: null,
        branch: null,
        environment: remote,
        remoteWorkspace: "/srv/app",
        connected: false,
        problem: "ssh: connect to host dev.example.com port 2222: Connection timed out",
        onRetry,
      }),
      started: true,
    });
    const crumb = screen.getByTestId("where-crumb");
    expect(crumb.querySelector(".where-crumb-dot")).toBeTruthy();
    expect(crumb.getAttribute("aria-label")).toContain("cannot start");
    fireEvent.click(crumb);
    expect(screen.getByTestId("where-machine").textContent).toContain("Not started");
    expect(screen.getByTestId("where-problem").textContent).toContain("Connection timed out");
    fireEvent.click(screen.getByTestId("where-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe("SessionTopbar: what the session is doing", () => {
  it("says nothing for an idle session: silence is the all-is-well mark", () => {
    renderTopbar();
    expect(screen.queryByTestId("topbar-state")).toBeNull();
  });

  it("says Working with the turn's clock, counted from when the turn began", () => {
    renderTopbar({ state: { kind: "working", since: Date.now() - 134_000 } });
    const state = screen.getByTestId("topbar-state");
    expect(state.getAttribute("data-state")).toBe("working");
    expect(state.textContent).toMatch(/^Working2m 1\ds$/);
  });

  it("says Needs you in the warm ink when it waits on the person", () => {
    renderTopbar({ state: { kind: "needs" } });
    const state = screen.getByTestId("topbar-state");
    expect(state.textContent).toBe("Needs you");
    expect(state.className).toContain("need");
  });

  it("says when a sleeping session wakes, or only that it sleeps when it did not say", () => {
    renderTopbar({ state: { kind: "sleeping", until: "2026-10-12T14:30:00" } });
    expect(screen.getByTestId("topbar-state").textContent).toMatch(/^Sleeps until .*30/);
    cleanup();
    renderTopbar({ state: { kind: "sleeping", until: null } });
    expect(screen.getByTestId("topbar-state").textContent).toBe("Sleeping");
  });
});

describe("SessionTopbar: an automation's run", () => {
  it("names the automation before the session, and that name is the way back", () => {
    const onBack = vi.fn();
    renderTopbar({ run: { title: "Daily AI News", onBack } });
    const crumb = screen.getByTestId("run-crumb");
    expect(crumb.textContent).toBe("Daily AI News");
    fireEvent.click(crumb);
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("has no crumb for an ordinary session", () => {
    renderTopbar();
    expect(screen.queryByTestId("run-crumb")).toBeNull();
  });
});

describe("SessionTopbar: the session's own actions", () => {
  it("renames from a click on the name: Enter keeps it, Escape does not", () => {
    const a = actions();
    renderTopbar({ actions: a });
    fireEvent.click(screen.getByTestId("topbar-title"));
    const field = screen.getByLabelText("Session name") as HTMLInputElement;
    expect(field.value).toBe("Draft the launch note");
    fireEvent.change(field, { target: { value: "  Ship the launch note  " } });
    fireEvent.keyDown(field, { key: "Enter" });
    expect(a.onRename).toHaveBeenCalledWith("Ship the launch note");
    expect(screen.queryByLabelText("Session name")).toBeNull();

    fireEvent.click(screen.getByTestId("topbar-title"));
    fireEvent.change(screen.getByLabelText("Session name"), { target: { value: "Something else" } });
    fireEvent.keyDown(screen.getByLabelText("Session name"), { key: "Escape" });
    expect(a.onRename).toHaveBeenCalledTimes(1);
  });

  it("does not rename to an empty name or to the name it has", () => {
    const a = actions();
    renderTopbar({ actions: a });
    fireEvent.click(screen.getByTestId("topbar-title"));
    fireEvent.change(screen.getByLabelText("Session name"), { target: { value: "   " } });
    fireEvent.keyDown(screen.getByLabelText("Session name"), { key: "Enter" });
    fireEvent.click(screen.getByTestId("topbar-title"));
    fireEvent.keyDown(screen.getByLabelText("Session name"), { key: "Enter" });
    expect(a.onRename).not.toHaveBeenCalled();
  });

  it("a session with no record yet has a name that is not a button, and no menu", () => {
    renderTopbar({ hasHistory: false, activeTitle: "New session" });
    expect(screen.getByTestId("topbar-title").tagName).toBe("SPAN");
    expect(screen.queryByTestId("session-menu")).toBeNull();
  });

  it("offers the sidebar row's actions, named for the state the session is in", () => {
    const a = actions({ pinned: true });
    renderTopbar({ actions: a });
    fireEvent.click(screen.getByTestId("session-menu"));
    expect(screen.getByTestId("session-menu-pin").textContent).toBe("Unpin");
    expect(screen.getByTestId("session-menu-archive").textContent).toBe("Archive");
    fireEvent.click(screen.getByTestId("session-menu-pin"));
    expect(a.onTogglePin).toHaveBeenCalledTimes(1);
    // The menu closes on a pick.
    expect(screen.queryByTestId("session-menu-pin")).toBeNull();

    fireEvent.click(screen.getByTestId("session-menu"));
    fireEvent.click(screen.getByTestId("session-menu-export"));
    expect(a.onExport).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("session-menu"));
    fireEvent.click(screen.getByTestId("session-menu-fork"));
    expect(a.onFork).toHaveBeenCalledTimes(1);
  });

  it("Rename in the menu opens the same field the name does", () => {
    renderTopbar({ actions: actions() });
    fireEvent.click(screen.getByTestId("session-menu"));
    fireEvent.click(screen.getByTestId("session-menu-rename"));
    expect(screen.getByLabelText("Session name")).toBeTruthy();
  });

  it("Delete asks in a dialog before it deletes", () => {
    const a = actions();
    renderTopbar({ actions: a });
    fireEvent.click(screen.getByTestId("session-menu"));
    fireEvent.click(screen.getByTestId("session-menu-delete"));
    expect(a.onDelete).not.toHaveBeenCalled();
    expect(screen.getByTestId("confirm-dialog").textContent).toContain("Delete “Draft the launch note”?");
    fireEvent.click(screen.getByTestId("confirm-dialog-ok"));
    expect(a.onDelete).toHaveBeenCalledTimes(1);
  });
});

describe("SessionTopbar view", () => {
  it("has no conversation tab strip and no Session log action", () => {
    renderTopbar();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByTestId("conv-tab-chat")).toBeNull();
    expect(screen.queryByTestId("session-log")).toBeNull();
    expect(screen.queryByText("Session log")).toBeNull();
  });
});

describe("sessionDisplayTitle", () => {
  it("leaves real titles alone", () => {
    expect(sessionDisplayTitle("Draft the launch note", "backend")).toBe(
      "Draft the launch note",
    );
  });

  it("maps a path-shaped title to the folder name, then the standard wording", () => {
    expect(sessionDisplayTitle('"C:\\Users\\DELL\\Downloads\\C…"', "C…")).toBe("C…");
    expect(sessionDisplayTitle("/Users/me/code/backend", null)).toBe("New session");
  });

  it("does not mistake a slash inside prose for a path", () => {
    expect(sessionDisplayTitle("Ship client/server split", "backend")).toBe(
      "Ship client/server split",
    );
  });
});
