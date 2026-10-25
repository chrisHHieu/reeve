// @vitest-environment jsdom
// The Files tab (the reference design's file-manager workbench): one pane with the project tree and a
// name search, one pane for the selected file.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RightRail } from "./RightRail";

const searchSessionFiles = vi.fn(async (_sid: string, query: string) => ({
  ok: true,
  files: query
    ? [
        { path: "src/notes.md", abs_path: "/ws/src/notes.md", name: "notes.md" },
        { path: "docs/other.md", abs_path: "/ws/docs/other.md", name: "other.md" },
      ]
    : [],
  truncated: false,
}));

vi.mock("../api", async () => {
  const actual: any = await vi.importActual("../api");
  return {
    ...actual,
    getSessionChanges: vi.fn().mockResolvedValue({ is_git: true, files: [], workspace: "/ws" }),
    getChangeDiff: vi.fn().mockResolvedValue({ ok: true, path: "x", patch: "", untracked: false }),
    getRoots: vi.fn().mockResolvedValue([
      { path: "/ws", label: "reeve", writable: true, exists: true, primary: true },
    ]),
    searchSessionFiles: (sid: string, query: string) => searchSessionFiles(sid, query),
    readArtifact: vi.fn(async (_sid: string, path: string) => {
      if (path === "/ws") {
        return { ok: true, path, kind: "folder", entries: [{ name: "src", dir: true, size: 0 }] };
      }
      return { ok: true, path, kind: "markdown", content: "# Notes from the manager" };
    }),
    revealArtifact: vi.fn().mockResolvedValue({ ok: true }),
    writeSessionEntry: vi.fn(async (_sid: string, path: string, content: string) => ({
      ok: true,
      path,
      size: content.length,
      mtime_ms: 1234,
    })),
    createSessionEntry: vi.fn(async (_sid: string, path: string) => ({ ok: true, path })),
    renameSessionEntry: vi.fn(async (_sid: string, path: string, name: string) => ({
      ok: true,
      path: path.split("/").slice(0, -1).concat(name).join("/"),
    })),
    moveSessionEntry: vi.fn(async (_sid: string, path: string, to: string) => ({
      ok: true,
      path: to ? `${to}/${path.split("/").pop()}` : path.split("/").pop(),
    })),
    deleteSessionEntry: vi.fn(async (_sid: string, path: string) => ({ ok: true, path })),
  };
});

/** Opens the tree's root folder and the row menu of `name` under it. */
async function rowMenu(name: string) {
  fireEvent.click(await screen.findByRole("button", { name: "Expand reeve" }));
  fireEvent.click(await screen.findByRole("button", { name: `Actions for ${name}` }));
  return screen.getByTestId("tree-menu");
}

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    cols = 100;
    rows = 30;
    loadAddon() {}
    open() {}
    onData() {
      return { dispose() {} };
    }
    onResize() {
      return { dispose() {} };
    }
    write() {}
    clear() {}
    reset() {}
    dispose() {}
  },
}));
vi.mock("@xterm/addon-search", () => ({
  SearchAddon: class {
    onDidChangeResults() {
      return { dispose() {} };
    }
  },
}));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit() {} } }));
vi.mock("../api/terminal", () => ({
  closeUserTerminal: vi.fn().mockResolvedValue({ ok: true, closed: true }),
  openUserTerminal: vi.fn().mockResolvedValue({ ok: true }),
  listUserTerminalShells: vi.fn().mockResolvedValue({ ok: true, shells: [] }),
  openUserTerminalSocket: () => ({ readyState: 0, send: vi.fn(), close: vi.fn(), onmessage: null, onclose: null }),
}));

afterEach(() => {
  cleanup();
  localStorage.clear();
  searchSessionFiles.mockClear();
});

describe("RightRail file manager", () => {
  it("the Files door opens the two-pane workbench with the project tree and an empty pane", async () => {
    render(<RightRail active sessionId="s1" refreshKey={0} />);
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rail-door-files"));

    expect(await screen.findByTestId("file-manager")).toBeTruthy();
    expect(screen.getByTestId("rail-tab-files")).toBeTruthy();
    expect(screen.getByTestId("file-tree")).toBeTruthy();
    // The empty pane says what this tab is for — and where the other question is answered.
    expect(screen.getByTestId("fm-empty").textContent).toContain("What is in your folders right now");
    expect(screen.getByTestId("fm-empty").textContent).toContain("To see what changed, use Review.");
  });

  it("searching by name lists matches and opens one in the preview", async () => {
    render(<RightRail active sessionId="s1" refreshKey={0} />);
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rail-door-files"));
    await screen.findByTestId("file-manager");

    fireEvent.change(screen.getByTestId("fm-search"), { target: { value: "notes" } });
    const hit = await screen.findByTestId("fm-result-src/notes.md");
    expect(searchSessionFiles).toHaveBeenCalledWith("s1", "notes");
    expect(screen.getByTestId("fm-result-docs/other.md")).toBeTruthy();

    fireEvent.click(hit);
    expect(await screen.findByText("Notes from the manager")).toBeTruthy();
  });

  it("edits a text file in place: Edit mode, dirty state, save, back to Preview", async () => {
    const { writeSessionEntry } = await import("../api");
    render(<RightRail active sessionId="s1" refreshKey={0} />);
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rail-door-files"));
    await screen.findByTestId("file-manager");

    fireEvent.change(screen.getByTestId("fm-search"), { target: { value: "notes" } });
    fireEvent.click(await screen.findByTestId("fm-result-src/notes.md"));

    // Markdown opens in Preview (the reading view) with the Edit|Preview toggle.
    await screen.findByTestId("fm-mode-preview");
    await waitFor(() => expect(document.querySelector(".artifact-md")).toBeTruthy());
    expect(screen.getByTestId("fm-mode-preview").className).toContain("on");
    // Nothing to save, nothing said about saving.
    expect(screen.queryByTestId("fm-save")).toBeNull();
    expect(screen.queryByTestId("fm-file-state")).toBeNull();

    fireEvent.click(screen.getByTestId("fm-mode-edit"));
    const editor = (await screen.findByTestId("fm-editor")) as HTMLTextAreaElement;
    expect(editor.value).toContain("Notes from the manager");

    fireEvent.change(editor, { target: { value: "# Edited" } });
    expect(screen.getByTestId("fm-file-state").textContent).toContain("Unsaved changes");
    expect((screen.getByTestId("fm-save") as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(screen.getByTestId("fm-save"));
    await waitFor(() =>
      expect(writeSessionEntry).toHaveBeenCalledWith("s1", "src/notes.md", "# Edited", undefined, undefined),
    );
    await waitFor(() => expect(screen.getByTestId("fm-file-state").textContent).toContain("Saved"));
  });

  it("New file is named in place in the tree, made through the server, and opened", async () => {
    const { createSessionEntry } = await import("../api");
    render(<RightRail active sessionId="s1" refreshKey={0} />);
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rail-door-files"));
    await screen.findByTestId("file-manager");

    fireEvent.click(screen.getByTestId("fm-new"));
    fireEvent.click(screen.getByTestId("fm-new-file"));
    // Nothing open: it goes in the primary folder, whose row opens to hold the name.
    const name = await screen.findByTestId("fm-new-name");
    fireEvent.change(name, { target: { value: "draft.md" } });
    fireEvent.keyDown(name, { key: "Enter" });

    await waitFor(() => expect(createSessionEntry).toHaveBeenCalledWith("s1", "/ws/draft.md", "file"));
    await waitFor(() => expect(screen.queryByTestId("fm-new-row")).toBeNull());
    expect(await screen.findByText("Notes from the manager")).toBeTruthy();
  });

  it("a refused name is said under it, and the name stays to fix", async () => {
    const { createSessionEntry } = await import("../api");
    vi.mocked(createSessionEntry).mockResolvedValueOnce({ ok: false, error: "that path is protected" });
    render(<RightRail active sessionId="s1" refreshKey={0} />);
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rail-door-files"));
    await screen.findByTestId("file-manager");

    fireEvent.click(screen.getByTestId("fm-new"));
    fireEvent.click(screen.getByTestId("fm-new-folder"));
    const name = await screen.findByTestId("fm-new-name");
    fireEvent.change(name, { target: { value: ".env" } });
    fireEvent.keyDown(name, { key: "Enter" });

    expect((await screen.findByTestId("fm-new-error")).textContent).toContain("that path is protected");
    expect((screen.getByTestId("fm-new-name") as HTMLInputElement).value).toBe(".env");
    fireEvent.keyDown(screen.getByTestId("fm-new-name"), { key: "Escape" });
    expect(screen.queryByTestId("fm-new-row")).toBeNull();
  });

  it("a row's own menu renames and moves that entry", async () => {
    const { renameSessionEntry, moveSessionEntry } = await import("../api");
    render(<RightRail active sessionId="s1" refreshKey={0} />);
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rail-door-files"));
    await screen.findByTestId("file-manager");

    fireEvent.click(within(await rowMenu("src")).getByTestId("tree-menu-rename"));
    expect((screen.getByTestId("fm-dialog-input") as HTMLInputElement).value).toBe("src");
    fireEvent.change(screen.getByTestId("fm-dialog-input"), { target: { value: "lib" } });
    fireEvent.click(screen.getByTestId("fm-dialog-confirm"));
    await waitFor(() => expect(renameSessionEntry).toHaveBeenCalledWith("s1", "/ws/src", "lib"));
    await waitFor(() => expect(screen.queryByTestId("fm-dialog")).toBeNull());

    fireEvent.click(await screen.findByRole("button", { name: "Actions for src" }));
    fireEvent.click(screen.getByTestId("tree-menu-move"));
    fireEvent.change(screen.getByTestId("fm-dialog-input"), { target: { value: "docs" } });
    fireEvent.click(screen.getByTestId("fm-dialog-confirm"));
    await waitFor(() => expect(moveSessionEntry).toHaveBeenCalledWith("s1", "/ws/src", "docs"));
  });

  it("Delete asks first, says where it goes, and only then sends it there", async () => {
    const { deleteSessionEntry } = await import("../api");
    render(<RightRail active sessionId="s1" refreshKey={0} />);
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rail-door-files"));
    await screen.findByTestId("file-manager");

    fireEvent.click(within(await rowMenu("src")).getByTestId("tree-menu-delete"));
    const ask = screen.getByTestId("fm-delete-dialog");
    expect(ask.textContent).toContain("Delete src?");
    expect(ask.textContent).toMatch(/The folder and everything in it go to the (Recycle Bin|Trash)/);
    expect(deleteSessionEntry).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("fm-delete-confirm"));
    await waitFor(() => expect(deleteSessionEntry).toHaveBeenCalledWith("s1", "/ws/src"));
    await waitFor(() => expect(screen.queryByTestId("fm-delete-dialog")).toBeNull());
  });

  it("a session folder itself offers no rename, move or delete", async () => {
    render(<RightRail active sessionId="s1" refreshKey={0} />);
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rail-door-files"));
    await screen.findByTestId("file-manager");

    fireEvent.click(await screen.findByRole("button", { name: "Actions for reeve" }));
    const menu = screen.getByTestId("tree-menu");
    expect(within(menu).queryByTestId("tree-menu-rename")).toBeNull();
    expect(within(menu).queryByTestId("tree-menu-delete")).toBeNull();
    expect(within(menu).getByTestId("tree-menu-new-file")).toBeTruthy();
  });

  it("clearing the search returns to the tree", async () => {
    render(<RightRail active sessionId="s1" refreshKey={0} />);
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rail-door-files"));
    await screen.findByTestId("file-manager");

    fireEvent.change(screen.getByTestId("fm-search"), { target: { value: "notes" } });
    await screen.findByTestId("fm-results");
    fireEvent.click(screen.getByTestId("fm-search-clear"));
    expect(await screen.findByTestId("file-tree")).toBeTruthy();
    expect(screen.queryByTestId("fm-results")).toBeNull();
  });
});
