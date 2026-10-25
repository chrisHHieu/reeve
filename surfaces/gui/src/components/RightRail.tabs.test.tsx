// @vitest-environment jsdom
// The rail's two file surfaces answer different questions (2026-09-29 redesign): Review is
// what CHANGED (this session's recorded edits, or everything uncommitted) with its diff;
// Files is what is in the folders NOW. A file link always opens in Files.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RightRail } from "./RightRail";
import { OPEN_ARTIFACT_EVENT } from "./Markdown";

vi.mock("../api", async () => {
  const actual: any = await vi.importActual("../api");
  return {
    ...actual,
    getSessionChanges: vi.fn().mockResolvedValue({
      is_git: true,
      files: [
        { path: "src/tracked.txt", status: "M", add: 2, del: 1 },
        { path: "staged/mine.txt", status: "A", add: 4, del: 0, staged: true },
      ],
      workspace: "/ws",
    }),
    getChangeDiff: vi.fn().mockResolvedValue({
      ok: true,
      path: "src/tracked.txt",
      patch: "@@ -1 +1 @@\n-old\n+new",
      untracked: false,
    }),
    getRoots: vi.fn().mockResolvedValue([
      { path: "/ws/scratch", label: "scratch", writable: true, exists: true },
    ]),
    readArtifact: vi.fn(async (_sid: string, path: string) => {
      if (path === "/ws/scratch") {
        return { ok: true, path, kind: "folder", entries: [{ name: "notes.md", dir: false, size: 12 }] };
      }
      if (path.endsWith(".py")) {
        return { ok: true, path, kind: "code", content: "def f():\n  return 1" };
      }
      return { ok: true, path, kind: "markdown", content: "# Notes" };
    }),
    revealArtifact: vi.fn().mockResolvedValue({ ok: true }),
    getUndoableCalls: vi.fn().mockResolvedValue(["c-replayed"]),
  };
});

// The rail's Terminal tab mounts xterm.js; jsdom has no canvas/matchMedia, so the
// emulator is stubbed here exactly as TerminalPanel.test.tsx does for its own units.
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
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit() {}
  },
}));

// The rail's Terminal tab mounts the same socket the docked panel used; jsdom has no WS.
// The shell menu's discovery and the panel's create/attach are answered by the stub.
vi.mock("../api/terminal", () => ({
  closeUserTerminal: vi.fn().mockResolvedValue({ ok: true, closed: true }),
  openUserTerminal: vi.fn().mockResolvedValue({ ok: true, running: true, shell: "cmd.exe" }),
  listUserTerminalShells: vi.fn().mockResolvedValue({
    ok: true,
    shells: [{ id: "cmd", name: "cmd.exe", path: "C:\\Windows\\System32\\cmd.exe" }],
  }),
  openUserTerminalSocket: () => ({
    readyState: 0,
    send: vi.fn(),
    close: vi.fn(),
    onmessage: null,
    onclose: null,
  }),
}));

afterEach(() => {
  cleanup();
  // The rail persists its tabs per session; each test starts from the guide seed.
  localStorage.clear();
});

const rail = () => <RightRail active sessionId="s1" refreshKey={0} />;

// The rail seeds the Start guide; the review surfaces live behind its first door.
function openReview() {
  fireEvent.click(screen.getByTestId("rail-door-review"));
}

// Review opens on git's working tree when there is one: the scope menu, then every changed
// file stacked with its diff (the first ones already open).
function pickScope(id: "last-turn" | "session" | "uncommitted") {
  fireEvent.click(screen.getByTestId("rvw-scope"));
  fireEvent.click(screen.getByTestId(`rvw-scope-${id}`));
}

describe("RightRail review panel", () => {
  it("seeds the Start guide, then the Review door opens the diff-first Review", async () => {
    render(rail());
    await act(async () => {});
    expect(screen.getByTestId("rail-guide")).toBeTruthy();
    expect(screen.getByTestId("rail-door-review")).toBeTruthy();
    expect(screen.queryByTestId("review-pane")).toBeNull();

    openReview();
    expect(screen.getByTestId("review-pane")).toBeTruthy();
    expect(screen.getByTestId("rvw-scope").textContent).toContain("Uncommitted");
    // The changed files are cards in one column, the first open; the commit box is at the foot.
    expect(screen.getByTestId("rvw-count").textContent).toBe("2 files");
    expect(screen.getByTestId("rvw-card-staged/mine.txt")).toBeTruthy();
    expect(within(screen.getByTestId("rvw-card-src/tracked.txt")).getByTestId("rvw-stage-src/tracked.txt")).toBeTruthy();
    fireEvent.click(screen.getByTestId("rvw-toggle-staged/mine.txt"));
    const staged = screen.getByTestId("rvw-card-staged/mine.txt");
    expect(within(staged).getByText("Staged")).toBeTruthy();
    expect(within(staged).getByTestId("rvw-unstage-staged/mine.txt")).toBeTruthy();
    expect(screen.getByTestId("rvw-commit")).toBeTruthy();
  });

  it("shows the first file's diff without a click, in one column", async () => {
    render(rail());
    await act(async () => {});
    openReview();
    const card = screen.getByTestId("rvw-card-src/tracked.txt");
    const diff = await within(card).findByTestId("diffview");
    expect(within(diff).getByText("new")).toBeTruthy(); // the "+" line (marker stripped)
    expect(document.querySelector(".right-rail.review-mode")).toBeTruthy();
    // No list beside it: every file is a card.
    expect(screen.queryByTestId("rvw-list")).toBeNull();
    expect(screen.getByTestId("rvw-progress").textContent).toContain("Viewed 0 of 2");
  });

  it("the Review tab wears how many files changed", async () => {
    render(rail());
    await act(async () => {});
    openReview();
    expect((await screen.findByTestId("rail-tab-count-review")).textContent).toBe("2");
  });

  it("a Viewed mark counts in the list", async () => {
    render(rail());
    await act(async () => {});
    openReview();
    await within(screen.getByTestId("rvw-card-src/tracked.txt")).findByTestId("diffview");
    fireEvent.click(screen.getByTestId("rvw-viewed-src/tracked.txt"));
    expect((screen.getByTestId("rvw-viewed-src/tracked.txt") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByTestId("rvw-progress").textContent).toContain("Viewed 1 of 2");
    // The mark is this device's, per session, and only for this size of change.
    expect(JSON.parse(localStorage.getItem("reeve.review.viewed.s1") || "{}")["src/tracked.txt"]).toBe("M:2:1");
  });

  it("a reopened session's edit is undoable when the server still holds its copy", async () => {
    // Rows rebuilt from a transcript carry no `undoable`; Recorded said "Undo isn't
    // available" beside a receipt that offered Undo (review audit V4, traced 2026-09-26).
    const replayed = [
      {
        kind: "tool",
        id: "t1",
        callId: "c-replayed",
        name: "replace_in_file",
        args: {},
        status: "ok",
        presentation: {
          summary: "edit",
          card: { card: "diff", diffs: [{ path: "/ws/scratch/a.py", old_text: "a", new_text: "b" }] },
        },
      },
    ] as never;
    render(<RightRail active sessionId="s1" refreshKey={0} items={replayed} />);
    await act(async () => {});
    openReview();
    pickScope("session");
    expect(await screen.findByTestId("recorded-undo-/ws/scratch/a.py")).toBeTruthy();
    expect(screen.queryByTestId("recorded-undo-note")).toBeNull();
  });

  it("takes line comments in the split view too (V15)", async () => {
    // Review audit V15: comments existed only in the unified view; Split dropped them.
    const onAdd = vi.fn();
    render(
      <RightRail
        active
        sessionId="s1"
        refreshKey={0}
        reviewComments={[{ id: "rc-1", file: "src/tracked.txt", line: 1, text: "why new?", origin: "review" }]}
        onAddReviewComment={onAdd}
      />,
    );
    await act(async () => {});
    openReview();
    const card = screen.getByTestId("rvw-card-src/tracked.txt");
    await within(card).findByTestId("diffview");
    fireEvent.click(screen.getByTestId("rvw-options"));
    fireEvent.click(screen.getByRole("button", { name: "Split" }));
    const split = within(card).getByTestId("diffview");
    expect(split.className).toContain("split");
    expect(split.textContent).toContain("why new?");
    fireEvent.click(within(split).getAllByRole("button", { name: "Comment on line 1" })[0]);
    expect(within(split).getByRole("textbox")).toBeTruthy();
  });

  it("names its diff controls in words and shows the context lines by default", async () => {
    // Review audit V13: the toolbar was a row of glyphs with the meaning only in tooltips.
    // V9: the default hid every context line, so a hunk was bare -/+ rows with no code around.
    render(rail());
    await act(async () => {});
    openReview();
    fireEvent.click(screen.getByTestId("rvw-options"));
    const lines = screen.getByRole("group", { name: "Line display" });
    expect([...lines.querySelectorAll("button")].map((b) => [b.textContent, b.className])).toEqual([
      ["With context", "on"],
      ["Changes only", ""],
    ]);
    const view = screen.getByRole("group", { name: "Diff view" });
    expect([...view.querySelectorAll("button")].map((b) => b.textContent)).toEqual(["Unified", "Split"]);
  });

  it("Review has no file browser: its scopes are Reeve's work and git's views of the changes", async () => {
    render(rail());
    await act(async () => {});
    openReview();
    fireEvent.click(screen.getByTestId("rvw-scope"));
    const options = [...document.querySelectorAll("[data-testid^='rvw-scope-']")].map((el) =>
      el.getAttribute("data-testid"),
    );
    expect(options).toEqual([
      "rvw-scope-last-turn",
      "rvw-scope-session",
      "rvw-scope-uncommitted",
      "rvw-scope-staged",
      "rvw-scope-unstaged",
      "rvw-scope-branch",
      "rvw-scope-commits",
    ]);
    expect(screen.queryByTestId("review-scope-files")).toBeNull();
    expect(screen.queryByTestId("file-tree")).toBeNull();
  });

  it("outside git, Review offers only Reeve's own work", async () => {
    const api: any = await import("../api");
    api.getSessionChanges.mockResolvedValueOnce({ is_git: false, files: [], workspace: "/ws" });
    render(rail());
    await act(async () => {});
    openReview();
    expect(screen.getByTestId("rvw-scope").textContent).toContain("This session");
    fireEvent.click(screen.getByTestId("rvw-scope"));
    expect(screen.queryByTestId("rvw-scope-uncommitted")).toBeNull();
    expect(screen.getByTestId("recorded-empty")).toBeTruthy();
  });

  it("Files expands a folder in the tree and a file opens its preview", async () => {
    render(rail());
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rail-door-files"));
    // A folder only expands the tree — the right pane stays empty.
    fireEvent.click(await screen.findByTestId("files-root-row"));
    expect(await screen.findByTitle("/ws/scratch/notes.md")).toBeTruthy();
    expect(screen.getByTestId("fm-empty")).toBeTruthy();
    fireEvent.click(screen.getByTitle("/ws/scratch/notes.md"));
    await screen.findByTestId("artifact-body");
    expect(document.querySelector(".artifact-md")).toBeTruthy();
  });

  it("a transcript chip opens the file in the Files tab", async () => {
    render(rail());
    await act(async () => {});
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "/ws/scratch/notes.md" } }),
      );
    });
    expect(await screen.findByTestId("file-manager")).toBeTruthy();
    expect(screen.getByTestId("rail-tab-files")).toBeTruthy();
    await screen.findByTestId("artifact-body");
    expect(document.querySelector(".artifact-md")).toBeTruthy();
  });

  it("adds a line comment from the diff and shows existing ones", async () => {
    const onAddReviewComment = vi.fn();
    render(
      <RightRail
        active
        sessionId="s1"
        refreshKey={0}
        reviewComments={[{ id: "c1", file: "src/tracked.txt", line: 1, text: "please rename" }]}
        onAddReviewComment={onAddReviewComment}
        onDeleteReviewComment={vi.fn()}
      />,
    );
    await act(async () => {});
    openReview();
    await within(screen.getByTestId("rvw-card-src/tracked.txt")).findByTestId("diffview");
    // The existing comment renders under its line, and its line is tinted.
    expect(screen.getByTestId("diff-comment").textContent).toContain("please rename");
    expect(document.querySelector(".diffline.commented")).toBeTruthy();
    // Clicking the gutter + opens the editor; typing Comment hands the comment up.
    fireEvent.click(screen.getAllByLabelText(/Comment on line/)[0]);
    const editor = await screen.findByTestId("diff-comment-editor");
    fireEvent.change(editor.querySelector("textarea")!, { target: { value: "use a map" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Comment" }));
    expect(onAddReviewComment).toHaveBeenCalledWith(
      expect.objectContaining({ file: "src/tracked.txt", text: "use a map" }),
    );
  });

  it("edits an existing comment from its card and hands the new text up", async () => {
    const onEditReviewComment = vi.fn();
    render(
      <RightRail
        active
        sessionId="s1"
        refreshKey={0}
        reviewComments={[{ id: "c1", file: "src/tracked.txt", line: 1, text: "please rename" }]}
        onAddReviewComment={vi.fn()}
        onDeleteReviewComment={vi.fn()}
        onEditReviewComment={onEditReviewComment}
      />,
    );
    await act(async () => {});
    openReview();
    await within(screen.getByTestId("rvw-card-src/tracked.txt")).findByTestId("diffview");
    // The card's … menu offers Edit / Delete; Edit re-opens the editor pre-filled.
    fireEvent.click(screen.getByTestId("diff-comment-more"));
    fireEvent.click(await screen.findByTestId("diff-comment-edit"));
    const editor = await screen.findByTestId("diff-comment-editor");
    expect((editor.querySelector("textarea") as HTMLTextAreaElement).value).toBe("please rename");
    fireEvent.change(editor.querySelector("textarea")!, { target: { value: "rename to total" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Save" }));
    expect(onEditReviewComment).toHaveBeenCalledWith("c1", "rename to total");
  });

  it("deletes a comment from the card's … menu", async () => {
    const onDeleteReviewComment = vi.fn();
    render(
      <RightRail
        active
        sessionId="s1"
        refreshKey={0}
        reviewComments={[{ id: "c1", file: "src/tracked.txt", line: 1, text: "please rename" }]}
        onAddReviewComment={vi.fn()}
        onDeleteReviewComment={onDeleteReviewComment}
      />,
    );
    await act(async () => {});
    openReview();
    await within(screen.getByTestId("rvw-card-src/tracked.txt")).findByTestId("diffview");
    fireEvent.click(screen.getByTestId("diff-comment-more"));
    fireEvent.click(await screen.findByTestId("diff-comment-delete"));
    expect(onDeleteReviewComment).toHaveBeenCalledWith("c1");
  });

  it("adds a line comment from the Files tab preview (origin file)", async () => {
    const onAddReviewComment = vi.fn();
    render(
      <RightRail
        active
        sessionId="s1"
        refreshKey={0}
        onAddReviewComment={onAddReviewComment}
        onDeleteReviewComment={vi.fn()}
      />,
    );
    await act(async () => {});
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "/ws/scratch/app.py" } }),
      );
    });
    // The file view is commentable: hovering a line reveals the + gutter button.
    fireEvent.click(await screen.findByLabelText("Comment on line 2"));
    const editor = await screen.findByTestId("file-comment-editor");
    fireEvent.change(editor.querySelector("textarea")!, { target: { value: "return an int" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Comment" }));
    expect(onAddReviewComment).toHaveBeenCalledWith(
      expect.objectContaining({
        file: "/ws/scratch/app.py",
        line: 2,
        text: "return an int",
        origin: "file",
      }),
    );
  });

  it("tints the commented line in the Files tab preview", async () => {
    render(
      <RightRail
        active
        sessionId="s1"
        refreshKey={0}
        reviewComments={[{ id: "fc1", file: "/ws/scratch/app.py", line: 2, text: "hi", origin: "file" }]}
        onAddReviewComment={vi.fn()}
        onDeleteReviewComment={vi.fn()}
      />,
    );
    await act(async () => {});
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "/ws/scratch/app.py" } }),
      );
    });
    await screen.findByTestId("artifact-body");
    expect(document.querySelector(".rev-code-line.commented")).toBeTruthy();
  });

  it("renders markdown as the preview (no source toggle in the header)", async () => {
    render(rail());
    await act(async () => {});
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "/ws/scratch/notes.md" } }),
      );
    });
    await screen.findByTestId("artifact-body");
    expect(document.querySelector(".artifact-md")).toBeTruthy();
    expect(screen.queryByTestId("view-source")).toBeNull();
  });

  it("hides the file list with the tree button so the file gets the pane", async () => {
    render(rail());
    await act(async () => {});
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "/ws/scratch/notes.md" } }),
      );
    });
    // The list's toggle lives in the view options.
    fireEvent.click(await screen.findByTestId("fm-view"));
    expect(document.querySelector(".fm-body.tree-hidden")).toBeNull();
    fireEvent.click(screen.getByTestId("fm-toggle-tree"));
    expect(document.querySelector(".fm-body.tree-hidden")).toBeTruthy();
    fireEvent.click(screen.getByTestId("fm-view"));
    fireEvent.click(screen.getByTestId("fm-toggle-tree"));
    expect(document.querySelector(".fm-body.tree-hidden")).toBeNull();
  });

  it("the /terminal request opens the Terminal tab and closing it reseeds the guide", async () => {
    const { rerender } = render(
      <RightRail active sessionId="s1" refreshKey={0} terminalRequestKey={0} />,
    );
    await act(async () => {});
    expect(screen.getByTestId("rail-guide")).toBeTruthy();

    rerender(<RightRail active sessionId="s1" refreshKey={0} terminalRequestKey={1} />);
    expect(await screen.findByTestId("terminal-panel")).toBeTruthy();
    expect(screen.getByTestId("rail-tab-terminal-1")).toBeTruthy();

    // The chip fades for a beat before the tab goes.
    fireEvent.click(screen.getByTestId("rail-tab-close-terminal-1"));
    expect(await screen.findByTestId("rail-guide")).toBeTruthy();
    expect(screen.queryByTestId("terminal-panel")).toBeNull();
  });

  it("the Browser door opens the browser tab and the strip closes back to the guide", async () => {
    render(rail());
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rail-door-browser"));
    expect(screen.getByTestId("browser-start")).toBeTruthy();
    fireEvent.click(screen.getByTestId("rail-tab-close-browser-1"));
    expect(await screen.findByTestId("rail-guide")).toBeTruthy();
  });
});


describe("Review and Files link to each other", () => {
  const items = [
    { kind: "user", text: "tidy the notes" },
    {
      kind: "tool",
      id: "t1",
      callId: "c-notes",
      name: "replace_in_file",
      status: "ok",
      undoable: true,
      presentation: {
        card: { card: "diff", diffs: [{ path: "/ws/scratch/notes.md", old_text: "# N\n", new_text: "# Notes\n" }] },
      },
    },
    {
      kind: "tool",
      id: "t2",
      callId: "c-tracked",
      name: "replace_in_file",
      status: "ok",
      undoable: true,
      presentation: {
        card: { card: "diff", diffs: [{ path: "/ws/src/tracked.txt", old_text: "old\n", new_text: "new\n" }] },
      },
    },
  ] as any[];

  it("a git file Reeve changed sits under the request that changed it; the others are apart", async () => {
    render(<RightRail active sessionId="s1" refreshKey={0} items={items} />);
    await act(async () => {});
    openReview();
    expect(within(screen.getByTestId("rvw-ask-1")).getByTestId("rvw-card-src/tracked.txt")).toBeTruthy();
    expect(within(screen.getByTestId("rvw-outside")).getByTestId("rvw-card-staged/mine.txt")).toBeTruthy();
  });

  it("a file Reeve changed says so in Files, and See what changed opens its diff in Review", async () => {
    render(<RightRail active sessionId="s1" refreshKey={0} items={items} />);
    await act(async () => {});
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "/ws/scratch/notes.md" } }),
      );
    });
    const banner = await screen.findByTestId("fm-changed-banner");
    expect(banner.textContent).toContain("Changed in turn 1");
    fireEvent.click(screen.getByTestId("fm-see-changes"));
    // Review, This session (Reeve's own edit, with Undo), with that file's card open.
    expect(await screen.findByTestId("review-pane")).toBeTruthy();
    expect(screen.getByTestId("rvw-scope").textContent).toContain("This session");
    expect(await screen.findByTestId("recorded-diff-/ws/scratch/notes.md")).toBeTruthy();
  });

  it("says how many comments are waiting only when the composer is out of sight", async () => {
    render(
      <RightRail
        active
        sessionId="s1"
        refreshKey={0}
        reviewComments={[
          { id: "c1", file: "src/tracked.txt", line: 1, text: "a" },
          { id: "c2", file: "src/tracked.txt", line: 2, text: "b" },
        ]}
      />,
    );
    await act(async () => {});
    openReview();
    // The composer lists them as chips and sends them; the panel says nothing more...
    expect(screen.queryByTestId("review-comment-bar")).toBeNull();
    // ...until it takes the whole window and the composer is out of sight.
    fireEvent.click(screen.getByTestId("rail-fullscreen"));
    expect(screen.getByTestId("review-comment-bar").textContent).toContain("2 comments on the diff");
  });
});

// A slow `git status` (twelve seconds on a real repo, 2026-10-14) must not be read as a clean
// tree, and an answer for a conversation the person left must not land on the next one.
describe("RightRail working tree reads", () => {
  function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((r) => (resolve = r));
    return { promise, resolve };
  }

  it("says it is loading until this conversation's working tree is read", async () => {
    const api: any = await import("../api");
    const pending = deferred<any>();
    api.getSessionChanges.mockReturnValueOnce(pending.promise);
    render(rail());
    await act(async () => {});
    openReview();
    expect(screen.getByTestId("rvw-loading")).toBeTruthy();
    expect(screen.queryByTestId("rvw-clean")).toBeNull();
    expect(screen.queryByTestId("rvw-count")).toBeNull();
    await act(async () => pending.resolve({ is_git: true, files: [{ path: "a.txt", status: "M", add: 1, del: 0 }], workspace: "/ws" }));
    expect(screen.queryByTestId("rvw-loading")).toBeNull();
    expect(screen.getByTestId("rvw-count").textContent).toBe("1 file");
  });

  it("drops an answer that comes back after the person moved to another conversation", async () => {
    const api: any = await import("../api");
    const first = deferred<any>();
    const second = deferred<any>();
    api.getSessionChanges.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { rerender } = render(<RightRail active sessionId="s1" refreshKey={0} />);
    await act(async () => {});
    rerender(<RightRail active sessionId="s2" refreshKey={0} />);
    await act(async () => {});
    openReview();
    // s2 answers first, then s1's late answer arrives: s2 keeps its own files.
    await act(async () => second.resolve({ is_git: true, files: [{ path: "mine.txt", status: "M", add: 1, del: 0 }], workspace: "/ws" }));
    await act(async () => first.resolve({ is_git: true, files: [{ path: "theirs-1.txt", status: "M", add: 1, del: 0 }, { path: "theirs-2.txt", status: "M", add: 1, del: 0 }], workspace: "/ws" }));
    expect(screen.getByTestId("rvw-count").textContent).toBe("1 file");
    expect(screen.getByTestId("rvw-card-mine.txt")).toBeTruthy();
    expect(screen.queryByTestId("rvw-card-theirs-1.txt")).toBeNull();
  });
});
