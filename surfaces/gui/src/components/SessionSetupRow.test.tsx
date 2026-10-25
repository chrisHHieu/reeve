// The session-setup row's folder panel: search over the
// recent workspaces, the current folder checked, "Open folder…", and our "Just chat" — a
// temporary folder created through the existing /v1/workspaces/temp route.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { SessionSetupRow } from "./SessionSetupRow";
import type { RecentWorkspace } from "../api";

const RECENTS: RecentWorkspace[] = [
  { path: "/Users/me/exports", name: "exports", exists: true },
  { path: "/Users/me/recipes", name: "recipes", exists: true },
  { path: "/Users/me/gone", name: "gone", exists: false },
];

let openedPaths: string[] = [];
let openResult: (path: string) => Record<string, unknown> = (path) => ({
  ok: true,
  path,
  git_branch: "main",
});

function stubRoutes() {
  openedPaths = [];
  openResult = (path) => ({ ok: true, path, git_branch: "main" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/v1/workspaces/recent")) {
        return { ok: true, json: async () => ({ workspaces: RECENTS }) } as Response;
      }
      if (url.includes("/v1/workspaces/open")) {
        const body = JSON.parse(String(init?.body || "{}"));
        openedPaths.push(body.path);
        return { ok: true, json: async () => openResult(body.path) } as Response;
      }
      if (url.includes("/v1/workspaces/pick")) {
        return {
          ok: true,
          json: async () => ({ ok: true, path: "/tmp/picked-folder" }),
        } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const baseProps: ComponentProps<typeof SessionSetupRow> = {
  folderName: null,
  folderPath: null,
  tempFolder: false,
  environment: null,
  onPickFolder: vi.fn(),
  onUseTemp: vi.fn(async () => ({ ok: true })),
  onOpenRemoteSettings: vi.fn(),
};

function renderRow(over: Partial<ComponentProps<typeof SessionSetupRow>> = {}) {
  return render(<SessionSetupRow {...baseProps} {...over} />);
}

async function openFolderPanel() {
  fireEvent.click(screen.getByTestId("folder-chip"));
  await screen.findByTestId("folder-panel");
  await waitFor(() => expect(screen.queryAllByTestId("folder-option").length).toBeGreaterThan(0));
}

describe("SessionSetupRow chip labels", () => {
  it("labels the folder chip with the bound folder, or the invitation", () => {
    renderRow();
    expect(screen.getByTestId("folder-chip").textContent).toContain("Choose folder");
    cleanup();
    renderRow({ folderName: "exports", folderPath: "/Users/me/exports" });
    expect(screen.getByTestId("folder-chip").textContent).toContain("exports");
    cleanup();
    renderRow({ tempFolder: true, folderName: "Temporary folder" });
    expect(screen.getByTestId("folder-chip").textContent).toContain("Temporary folder");
  });
});

describe("SessionSetupRow folder panel", () => {
  it("checks the current folder and filters the recents from the search row", async () => {
    stubRoutes();
    renderRow({ folderName: "exports", folderPath: "/Users/me/exports" });
    await openFolderPanel();

    const exports = screen
      .getAllByTestId("folder-option")
      .find((el) => el.textContent?.includes("exports"))!;
    const recipes = screen
      .getAllByTestId("folder-option")
      .find((el) => el.textContent?.includes("recipes"))!;
    expect(exports.getAttribute("aria-checked")).toBe("true");
    expect(recipes.getAttribute("aria-checked")).toBe("false");
    // The missing folder is not offered.
    expect(screen.queryByText("gone")).toBeNull();

    fireEvent.change(screen.getByTestId("folder-search"), { target: { value: "rec" } });
    await waitFor(() => expect(screen.queryAllByTestId("folder-option")).toHaveLength(1));
    expect(screen.getByTestId("folder-option").textContent).toContain("recipes");
  });

  it("offers a typed absolute path and binds it", async () => {
    stubRoutes();
    const onPickFolder = vi.fn();
    renderRow({ onPickFolder });
    await openFolderPanel();

    fireEvent.change(screen.getByTestId("folder-search"), {
      target: { value: "/home/me/new-project" },
    });
    fireEvent.click(screen.getByTestId("folder-typed"));

    await waitFor(() => expect(onPickFolder).toHaveBeenCalledWith("/home/me/new-project", "main"));
    expect(openedPaths).toEqual(["/home/me/new-project"]);
  });

  it("surfaces an open failure instead of closing silently", async () => {
    stubRoutes();
    openResult = () => ({ ok: false, error: "could not open that folder" });
    const onPickFolder = vi.fn();
    renderRow({ onPickFolder });
    await openFolderPanel();

    fireEvent.click(
      screen.getAllByTestId("folder-option").find((el) => el.textContent?.includes("recipes"))!,
    );

    await screen.findByTestId("folder-error");
    expect(screen.getByTestId("folder-error").textContent).toContain("could not open that folder");
    expect(onPickFolder).not.toHaveBeenCalled();
  });

  it("Open folder… runs the native picker and binds the pick", async () => {
    stubRoutes();
    const onPickFolder = vi.fn();
    renderRow({ onPickFolder });
    await openFolderPanel();

    fireEvent.click(screen.getByTestId("folder-browse"));

    await waitFor(() => expect(onPickFolder).toHaveBeenCalledWith("/tmp/picked-folder", "main"));
  });

  it("Just chat: the temporary-folder row checks when bound and creates through the route", async () => {
    stubRoutes();
    const onUseTemp = vi.fn(async () => ({ ok: true }));
    renderRow({ tempFolder: true, folderName: "Temporary folder", onUseTemp });
    await openFolderPanel();

    const temp = screen.getByTestId("folder-temp");
    expect(temp.getAttribute("aria-checked")).toBe("true");

    fireEvent.click(temp);
    await waitFor(() => expect(onUseTemp).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.queryByTestId("folder-panel")).toBeNull());
  });

  it("shows the temporary-folder failure inline and keeps the panel open", async () => {
    stubRoutes();
    const onUseTemp = vi.fn(async () => ({
      ok: false,
      error: "Could not create a temporary folder.",
    }));
    renderRow({ onUseTemp });
    await openFolderPanel();

    fireEvent.click(screen.getByTestId("folder-temp"));

    await screen.findByTestId("folder-error");
    expect(screen.getByTestId("folder-error").textContent).toContain(
      "Could not create a temporary folder.",
    );
    expect(screen.getByTestId("folder-panel")).toBeTruthy();
  });

  it("Escape closes the panel and returns focus to the chip", async () => {
    stubRoutes();
    renderRow();
    const chip = screen.getByTestId("folder-chip");
    await openFolderPanel();

    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => expect(screen.queryByTestId("folder-panel")).toBeNull());
    expect(document.activeElement).toBe(chip);
  });

  it("arrow keys walk from the search row into the options", async () => {
    stubRoutes();
    renderRow();
    await openFolderPanel();

    fireEvent.keyDown(screen.getByTestId("folder-search"), { key: "ArrowDown" });
    const options = screen.getAllByTestId("folder-option");
    expect(document.activeElement).toBe(options[0]);
    fireEvent.keyDown(options[0], { key: "ArrowDown" });
    expect(document.activeElement).toBe(options[1]);
  });

  it("offers no agent to pick: the row is where the session works, and nothing else", () => {
    // The picker left the composer on 2026-10-12 (one agent, nothing to choose).
    renderRow();
    expect(screen.queryByTestId("agent-chip")).toBeNull();
    expect(screen.getByTestId("setup-row").querySelectorAll("button[aria-haspopup]")).toHaveLength(2);
  });
});
