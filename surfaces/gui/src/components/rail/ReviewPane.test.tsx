// @vitest-environment jsdom
// Review in one column (2026-10-14, the owner's pick B): the changed files as cards that open in
// place, the working tree's grouped by the request that changed each; the git writes behind their
// buttons (stage, revert after a confirmation, commit), the commit box's default, the scope menu,
// and Send to Reeve.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReviewPane } from "./ReviewPane";
import type { ChangeFile } from "../../api";

vi.mock("../../api", async () => {
  const actual: any = await vi.importActual("../../api");
  return {
    ...actual,
    getScmLog: vi.fn().mockResolvedValue({
      is_git: true,
      branch: "main",
      ahead: 1,
      base: "main",
      remote: "origin",
      more: true,
      commits: [
        { hash: "a1b2c3d".padEnd(40, "0"), short: "a1b2c3d", author: "Me", when: "2 hours ago", subject: "Retry uploads", refs: ["HEAD -> main"] },
        { hash: "b7d03aa".padEnd(40, "0"), short: "b7d03aa", author: "Ann", when: "yesterday", subject: "Add the client", refs: ["origin/main"] },
      ],
    }),
    getSessionChanges: vi.fn().mockResolvedValue({ is_git: true, files: [], workspace: "/ws" }),
    scmHunk: vi.fn().mockResolvedValue({ ok: true }),
    scmPush: vi.fn().mockResolvedValue({ ok: true, remote: "origin", branch: "main" }),
    scmPull: vi.fn().mockResolvedValue({ ok: true }),
    getPullRequest: vi.fn().mockResolvedValue({ available: true, pr: null }),
    createPullRequest: vi.fn().mockResolvedValue({ ok: true, url: "https://github.com/o/r/pull/7" }),
    scmSuggestMessage: vi.fn().mockResolvedValue({ ok: true, message: "Retry uploads" }),
    scmReview: vi.fn().mockResolvedValue({
      ok: true,
      truncated: false,
      findings: [{ path: "src/app.py", line: 1, severity: "bug", body: "This divides by zero." }],
    }),
    getChangeDiff: vi.fn().mockResolvedValue({ ok: true, patch: "@@ -1 +1 @@\n-old\n+new", untracked: false }),
    scmStage: vi.fn().mockResolvedValue({ ok: true }),
    scmUnstage: vi.fn().mockResolvedValue({ ok: true }),
    scmDiscard: vi.fn().mockResolvedValue({ ok: true }),
    scmCommit: vi.fn().mockResolvedValue({ ok: true, commit: "d41f7c2" }),
    undoToolCall: vi.fn().mockResolvedValue({ ok: true }),
  };
});

beforeEach(() => localStorage.clear());
afterEach(cleanup);

const files: ChangeFile[] = [
  { path: "src/app.py", status: "M", add: 3, del: 1 },
  { path: "src/new.py", status: "A", add: 5, del: 0 },
  { path: "README.md", status: "M", add: 1, del: 0, staged: true },
];

/** Opens a file's card, unless it is open already. */
function show(path: string) {
  const card = screen.getByTestId(`rvw-card-${path}`);
  if (!card.classList.contains("open")) fireEvent.click(screen.getByTestId(`rvw-toggle-${path}`));
}
const isOpenCard = (path: string) => screen.getByTestId(`rvw-card-${path}`).classList.contains("open");

function pane(over: Partial<Parameters<typeof ReviewPane>[0]> = {}) {
  const props = {
    sessionId: "s1",
    isGit: true,
    changes: files,
    recorded: [],
    roots: ["/ws"],
    agentTurn: (path: string) => (path === "src/app.py" ? 2 : undefined),
    onRefresh: vi.fn(),
    onOpenFile: vi.fn(),
    diffStyle: "unified" as const,
    onDiffStyle: vi.fn(),
    ...over,
  };
  render(<ReviewPane {...props} />);
  return props;
}

describe("ReviewPane", () => {
  it("draws every changed file as a card in one column, the first one open", async () => {
    pane();
    await act(async () => {});
    expect(screen.getByTestId("rvw-count").textContent).toBe("3 files");
    expect(screen.queryByTestId("rvw-list")).toBeNull();
    // A new file reads U, as git's own status does before it is staged.
    expect(within(screen.getByTestId("rvw-card-src/new.py")).getByText("U")).toBeTruthy();
    // The first file is open without a click, its diff loaded; the others wait for one.
    expect(await within(screen.getByTestId("rvw-card-README.md")).findByTestId("diffview")).toBeTruthy();
    expect(isOpenCard("src/app.py")).toBe(false);
    show("src/app.py");
    expect(await within(screen.getByTestId("rvw-card-src/app.py")).findByTestId("diffview")).toBeTruthy();
    // Opening one does not close another: they are read side by side, down one scroll.
    expect(isOpenCard("README.md")).toBe(true);
  });

  it("groups the working tree by the request that changed each file, and keeps the rest apart", async () => {
    const ask = (path: string, turn: number, text: string) => ({
      id: `${turn}:${path}`,
      callId: `c${turn}-${path}`,
      tool: "replace_in_file",
      path,
      status: "modified" as const,
      added: 1,
      removed: 0,
      diffs: [],
      undoable: true,
      turn,
      turnText: text,
    });
    pane({
      recorded: [ask("/ws/src/app.py", 1, "Add retry\nwith tests"), ask("/ws/src/new.py", 2, "Explain the retry")],
      items: [
        { kind: "user", text: "Add retry\nwith tests" },
        { kind: "changes", created: [], overwritten: ["/ws/src/app.py"], deleted: [], check: { ok: true, summary: "14 passed", command: "pytest", editsSince: 0 } },
        { kind: "user", text: "Explain the retry" },
        { kind: "changes", created: ["/ws/src/new.py"], overwritten: [], deleted: [] },
      ] as never,
    });
    await act(async () => {});
    // Newest request first, in the person's words, with what its turn checked.
    const asks = screen.getAllByTestId(/^rvw-ask-\d+$/);
    expect(asks.map((a) => a.getAttribute("data-testid"))).toEqual(["rvw-ask-2", "rvw-ask-1"]);
    expect(within(asks[1]).getByText("“Add retry”")).toBeTruthy();
    expect(asks[1].textContent).toContain("14 passed");
    expect(asks[0].textContent).toContain("not checked");
    expect(within(asks[1]).getByTestId("rvw-card-src/app.py")).toBeTruthy();
    // In its group a card does not name the turn again.
    expect(screen.queryByTestId("rvw-turn-src/app.py")).toBeNull();
    // A file no turn of this conversation changed is its own group.
    expect(within(screen.getByTestId("rvw-outside")).getByTestId("rvw-card-README.md")).toBeTruthy();
    // The last edit had no check after it: the line under the bar says so.
    expect(screen.getByTestId("rvw-unchecked").textContent).toContain("Not checked since the last edit");
  });

  it("Ask for a change hands the request's words to the composer; Undo asks first, then puts each change back", async () => {
    const { undoToolCall } = await import("../../api");
    const heard = vi.fn();
    window.addEventListener("reeve:ask-change", heard);
    const props = pane({
      recorded: [
        {
          id: "c1",
          callId: "c1",
          tool: "replace_in_file",
          path: "/ws/src/app.py",
          status: "modified",
          added: 1,
          removed: 0,
          diffs: [],
          undoable: true,
          turn: 1,
          turnText: "Add retry",
        },
      ] as never,
    });
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rvw-ask-change-1"));
    expect((heard.mock.calls[0][0] as CustomEvent).detail).toEqual({ text: "Add retry" });
    window.removeEventListener("reeve:ask-change", heard);
    fireEvent.click(screen.getByTestId("rvw-ask-undo-1"));
    expect(screen.getByTestId("scm-discard-dialog").textContent).toContain("Undo what “Add retry” changed?");
    expect(undoToolCall).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("scm-discard-confirm"));
    await waitFor(() => expect(undoToolCall).toHaveBeenCalledWith("s1", "c1"));
    await waitFor(() => expect(props.onRefresh).toHaveBeenCalled());
  });

  it("stages, unstages, and reverts only after a confirmation", async () => {
    const { scmStage, scmUnstage, scmDiscard } = await import("../../api");
    const props = pane();
    await act(async () => {});
    show("src/app.py");
    fireEvent.click(screen.getByTestId("rvw-stage-src/app.py"));
    await waitFor(() => expect(scmStage).toHaveBeenCalledWith("s1", ["src/app.py"]));
    show("README.md");
    fireEvent.click(screen.getByTestId("rvw-unstage-README.md"));
    await waitFor(() => expect(scmUnstage).toHaveBeenCalledWith("s1", ["README.md"]));
    expect(props.onRefresh).toHaveBeenCalled();

    show("src/new.py");
    fireEvent.click(screen.getByTestId("rvw-revert-src/new.py"));
    expect(screen.getByTestId("scm-discard-dialog").textContent).toContain("cannot be undone");
    expect(scmDiscard).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("scm-discard-confirm"));
    await waitFor(() => expect(scmDiscard).toHaveBeenCalledWith("s1", ["src/new.py"]));
  });

  it("shows git's refusal where it happened", async () => {
    const { scmStage } = await import("../../api");
    vi.mocked(scmStage).mockResolvedValueOnce({ ok: false, error: "pathspec did not match" });
    pane();
    await act(async () => {});
    show("src/app.py");
    fireEvent.click(screen.getByTestId("rvw-stage-src/app.py"));
    expect((await screen.findByTestId("scm-error")).textContent).toBe("pathspec did not match");
  });

  it("commits what is staged by default, or everything when nothing is", async () => {
    const { scmCommit } = await import("../../api");
    const props = pane();
    await act(async () => {});
    // The box is docked under the list; the line above says what the branch owes its remote.
    const box = screen.getByTestId("rvw-commit");
    expect(screen.getByTestId("rvw-sync").textContent).toContain("1 commit not pushed");
    // No message yet: Commit is off and says why, beside it.
    expect((screen.getByTestId("rvw-commit-go") as HTMLButtonElement).disabled).toBe(true);
    expect(box.textContent).toContain("Write a message first");
    fireEvent.change(screen.getByTestId("rvw-commit-message"), { target: { value: "Fix it" } });
    fireEvent.keyDown(screen.getByTestId("rvw-commit-message"), { key: "Enter", ctrlKey: true });
    // One file is staged: the default is "Staged only", so `all` is false.
    await waitFor(() => expect(scmCommit).toHaveBeenCalledWith("s1", "Fix it", false));
    expect((await screen.findByTestId("rvw-toast")).textContent).toContain("Committed d41f7c2 to main");
    expect(props.onRefresh).toHaveBeenCalled();

    cleanup();
    vi.mocked(scmCommit).mockClear();
    pane({ changes: files.filter((f) => !f.staged) });
    fireEvent.change(screen.getByTestId("rvw-commit-message"), { target: { value: "All of it" } });
    fireEvent.click(screen.getByTestId("rvw-commit-go"));
    await waitFor(() => expect(scmCommit).toHaveBeenCalledWith("s1", "All of it", true));
  });

  it("keeps the commit box to one line at rest and opens it while it is in use", async () => {
    pane();
    await act(async () => {});
    const box = screen.getByTestId("rvw-commit");
    const message = screen.getByTestId("rvw-commit-message");
    // At rest: the message and Write it for me; the line that commits is folded away.
    expect(box.dataset.open).toBe("false");
    expect(box.querySelector(".rvw-commit-fold")?.hasAttribute("inert")).toBe(true);
    act(() => message.focus());
    expect(box.dataset.open).toBe("true");
    expect(box.querySelector(".rvw-commit-fold")?.hasAttribute("inert")).toBe(false);
    // A click elsewhere with nothing written folds it again; a message keeps it open.
    fireEvent.pointerDown(document.body);
    expect(box.dataset.open).toBe("false");
    fireEvent.change(message, { target: { value: "Retry uploads" } });
    fireEvent.pointerDown(document.body);
    expect(box.dataset.open).toBe("true");
  });

  it("says Loading, not a clean tree, while the working tree is still being read", () => {
    pane({ changes: [], changesLoading: true });
    expect(screen.getByTestId("rvw-loading")).toBeTruthy();
    expect(screen.queryByTestId("rvw-clean")).toBeNull();
  });

  it("keeps the commit box open with git's error when the commit is refused", async () => {
    const { scmCommit } = await import("../../api");
    vi.mocked(scmCommit).mockResolvedValueOnce({ ok: false, error: "Author identity unknown" });
    pane();
    await act(async () => {});
    fireEvent.change(screen.getByTestId("rvw-commit-message"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("rvw-commit-go"));
    expect((await screen.findByTestId("rvw-commit-error")).textContent).toBe("Author identity unknown");
    expect(screen.getByTestId("rvw-commit")).toBeTruthy();
  });

  it("reads a file's diff only once it is opened; J and K move between files, V marks one viewed", async () => {
    const { getChangeDiff } = await import("../../api");
    pane({ changes: [...files, { path: "zdocs/big.md", status: "M", add: 900, del: 0 }] });
    await act(async () => {});
    expect(isOpenCard("zdocs/big.md")).toBe(false);
    expect(getChangeDiff).not.toHaveBeenCalledWith("s1", "zdocs/big.md", expect.anything());
    show("zdocs/big.md");
    expect(await within(screen.getByTestId("rvw-card-zdocs/big.md")).findByTestId("diffview")).toBeTruthy();
    expect(getChangeDiff).toHaveBeenCalledWith("s1", "zdocs/big.md", "uncommitted");
    // K goes back one file and opens it, V marks it viewed, J comes forward again.
    fireEvent.keyDown(screen.getByTestId("review-pane"), { key: "k" });
    expect(isOpenCard("src/new.py")).toBe(true);
    fireEvent.keyDown(screen.getByTestId("review-pane"), { key: "v" });
    expect((screen.getByTestId("rvw-viewed-src/new.py") as HTMLInputElement).checked).toBe(true);
    fireEvent.keyDown(screen.getByTestId("review-pane"), { key: "j" });
    expect(screen.getByTestId("rvw-card-zdocs/big.md").classList.contains("is-current")).toBe(true);
  });

  it("a file marked viewed folds, and the next one not viewed opens", async () => {
    pane();
    await act(async () => {});
    expect(screen.getByTestId("rvw-progress").textContent).toBe("Viewed 0 of 3");
    expect(isOpenCard("README.md")).toBe(true);
    fireEvent.click(screen.getByTestId("rvw-viewed-README.md"));
    expect(screen.getByTestId("rvw-progress").textContent).toBe("Viewed 1 of 3");
    expect(isOpenCard("README.md")).toBe(false);
    expect(isOpenCard("src/app.py")).toBe(true);
    // Said in words: no bar (owner, 2026-10-12).
    expect(document.querySelector(".rvw-progress-track")).toBeNull();
  });

  it("puts a tool-generated file last and never opens it first", async () => {
    pane({ changes: [{ path: "web/__pycache__/x.pyc", status: "A", add: 0, del: 0 }, ...files] });
    await act(async () => {});
    const cards = screen.getAllByTestId(/^rvw-card-/).map((card) => card.getAttribute("data-testid"));
    expect(cards[cards.length - 1]).toBe("rvw-card-web/__pycache__/x.pyc");
    expect(isOpenCard("web/__pycache__/x.pyc")).toBe(false);
    show("web/__pycache__/x.pyc");
    expect(screen.getByTestId("rvw-generated-web/__pycache__/x.pyc").textContent).toBe("Generated");
  });

  it("tells how many unchanged lines a hunk skips, unless only the changes are shown", async () => {
    const { getChangeDiff } = await import("../../api");
    vi.mocked(getChangeDiff).mockResolvedValue({
      ok: true,
      patch: "@@ -40,1 +40,1 @@\n-old\n+new\n@@ -90,1 +90,1 @@\n-a\n+b",
      untracked: false,
    });
    pane({ changes: [files[0]] });
    await act(async () => {});
    const card = screen.getByTestId("rvw-card-src/app.py");
    const diff = await within(card).findByTestId("diffview");
    expect(diff.textContent).toContain("39 unmodified lines");
    expect(diff.textContent).toContain("49 unmodified lines");
    fireEvent.click(screen.getByTestId("rvw-options"));
    fireEvent.click(screen.getByRole("button", { name: "Changes only" }));
    expect(within(card).getByTestId("diffview").textContent).not.toContain("unmodified");
    vi.mocked(getChangeDiff).mockResolvedValue({ ok: true, patch: "@@ -1 +1 @@\n-old\n+new", untracked: false });
  });

  it("opens a file in Files from its name", async () => {
    const props = pane();
    await act(async () => {});
    show("src/app.py");
    fireEvent.click(screen.getByTestId("rvw-open-src/app.py"));
    expect(props.onOpenFile).toHaveBeenCalledWith("src/app.py");
  });

  it("switches to Reeve's own work from the scope menu, and back", async () => {
    pane({
      recorded: [
        {
          id: "c1",
          tool: "replace_in_file",
          path: "/ws/src/app.py",
          status: "modified",
          turn: 2,
          callId: "c1",
          undoable: true,
          added: 1,
          removed: 1,
          diffs: [{ path: "/ws/src/app.py", old_text: "a\n", new_text: "b\n" }],
        },
      ] as never,
    });
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rvw-scope"));
    expect(screen.getByTestId("rvw-scope-session").textContent).toContain("1 file");
    fireEvent.click(screen.getByTestId("rvw-scope-session"));
    expect(screen.getByTestId("recorded-changes")).toBeTruthy();
    expect(screen.queryByTestId("rvw-commit")).toBeNull(); // Reeve's work has Undo, not Commit
    fireEvent.click(screen.getByText("See Uncommitted"));
    expect(screen.getByTestId("rvw-cards")).toBeTruthy();
  });

  it("says a clean tree is clean, what was last committed, and offers the one thing left to do", async () => {
    const { scmPush, getSessionChanges } = await import("../../api");
    pane({ changes: [] });
    await act(async () => {});
    const clean = screen.getByTestId("rvw-clean");
    expect(clean.textContent).toContain("No uncommitted changes");
    expect(clean.textContent).toContain("Last commit a1b2c3d, 2 hours ago.");
    expect(clean.textContent).toContain("1 commit on main is not on origin yet.");
    // No commit box with nothing to commit, and Push is offered here, not twice.
    expect(screen.queryByTestId("rvw-commit")).toBeNull();
    expect(screen.queryByTestId("rvw-push")).toBeNull();
    fireEvent.click(screen.getByTestId("rvw-clean-push"));
    await waitFor(() => expect(scmPush).toHaveBeenCalledWith("s1"));
    fireEvent.click(screen.getByTestId("rvw-clean-last"));
    await act(async () => {});
    expect(getSessionChanges).toHaveBeenLastCalledWith("s1", `commit:${"a1b2c3d".padEnd(40, "0")}`);
  });

  it("sends the reader's comments to Reeve together, or clears them", async () => {
    const onSend = vi.fn();
    const onDelete = vi.fn();
    pane({
      comments: [
        { id: "c1", file: "src/app.py", line: 1, text: "a" },
        { id: "c2", file: "src/app.py", line: 2, text: "b" },
      ],
      onSendComments: onSend,
      onDeleteComment: onDelete,
    });
    await act(async () => {});
    expect(screen.getByTestId("review-comment-bar").textContent).toContain("2 comments on the diff");
    // The list counts the open notes per file.
    expect(screen.getByTestId("rvw-notes-src/app.py").textContent).toBe("2 notes");
    fireEvent.click(screen.getByTestId("review-comments-send"));
    expect(onSend).toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("review-comments-clear"));
    expect(onDelete.mock.calls.map((c) => c[0])).toEqual(["c1", "c2"]);
  });

  it("reports whether it is showing diffs, so the app can give them room", async () => {
    const onDiffOpen = vi.fn();
    pane({ onDiffOpen });
    await act(async () => {});
    expect(onDiffOpen).toHaveBeenLastCalledWith(true);
    cleanup();
    expect(onDiffOpen).toHaveBeenLastCalledWith(false);
  });
});

describe("ReviewPane scopes", () => {
  async function scoped(scope: string, files: ChangeFile[], extra: Record<string, unknown> = {}) {
    const { getSessionChanges } = await import("../../api");
    vi.mocked(getSessionChanges).mockResolvedValue({ is_git: true, files, workspace: "/ws", ...extra } as never);
    const props = pane();
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rvw-scope"));
    fireEvent.click(screen.getByTestId(`rvw-scope-${scope}`));
    await act(async () => {});
    return props;
  }

  it("names the scopes and counts the files it can count from the working tree", async () => {
    pane();
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rvw-scope"));
    expect(screen.getByTestId("rvw-scope-staged").textContent).toContain("1 file");
    expect(screen.getByTestId("rvw-scope-unstaged").textContent).toContain("2 files");
    // The base branch is named before the scope is picked (from the log).
    expect(screen.getByTestId("rvw-scope-branch").textContent).toContain("Against main");
  });

  it("Staged reads the index: its files, and Unstage (not Stage) on each", async () => {
    const { getSessionChanges, getChangeDiff } = await import("../../api");
    await scoped("staged", [{ path: "README.md", status: "M", add: 1, del: 0, staged: true }]);
    expect(getSessionChanges).toHaveBeenCalledWith("s1", "staged");
    expect(screen.getByTestId("rvw-scope").textContent).toContain("Staged");
    const card = screen.getByTestId("rvw-card-README.md");
    expect(within(card).getByTestId("rvw-unstage-README.md")).toBeTruthy();
    expect(within(card).queryByTestId("rvw-stage-README.md")).toBeNull();
    await within(card).findByTestId("diffview");
    expect(getChangeDiff).toHaveBeenCalledWith("s1", "README.md", "staged");
  });

  it("stages and unstages one hunk, and reverts one only after a confirmation", async () => {
    const { getChangeDiff, scmHunk } = await import("../../api");
    vi.mocked(getChangeDiff).mockResolvedValue({
      ok: true,
      patch: "@@ -1 +1 @@\n-a\n+b\n@@ -20 +20 @@\n-c\n+d",
      untracked: false,
    });
    await scoped("unstaged", [{ path: "src/app.py", status: "M", add: 2, del: 2, staged: false }]);
    fireEvent.click(await screen.findByTestId("rvw-hunk-stage-src/app.py-1"));
    await waitFor(() =>
      expect(scmHunk).toHaveBeenCalledWith("s1", { path: "src/app.py", index: 1, header: "@@ -20 +20 @@", action: "stage" }),
    );
    vi.mocked(scmHunk).mockClear();
    fireEvent.click(screen.getByTestId("rvw-hunk-revert-src/app.py-0"));
    expect(screen.getByTestId("scm-discard-dialog").textContent).toContain("cannot be undone");
    expect(scmHunk).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("scm-discard-confirm"));
    await waitFor(() =>
      expect(scmHunk).toHaveBeenCalledWith("s1", { path: "src/app.py", index: 0, header: "@@ -1 +1 @@", action: "revert" }),
    );
    vi.mocked(getChangeDiff).mockResolvedValue({ ok: true, patch: "@@ -1 +1 @@\n-old\n+new", untracked: false });
  });

  it("offers Unstage hunk in Staged, and no hunk buttons where the diff is not the index's or the edits'", async () => {
    const { getChangeDiff } = await import("../../api");
    vi.mocked(getChangeDiff).mockResolvedValue({ ok: true, patch: "@@ -1 +1 @@\n-a\n+b\n@@ -30 +30 @@\n-c\n+d", untracked: false });
    await scoped("staged", [{ path: "README.md", status: "M", add: 2, del: 2, staged: true }]);
    expect(await screen.findByTestId("rvw-hunk-unstage-README.md-0")).toBeTruthy();
    expect(screen.queryByTestId("rvw-hunk-stage-README.md-0")).toBeNull();
    cleanup();
    // Uncommitted: a fully staged file's diff against HEAD is the index's, not the edits' —
    // no hunk buttons; the unstaged one has them.
    pane({ changes: [
      { path: "src/app.py", status: "M", add: 1, del: 1 },
      { path: "README.md", status: "M", add: 1, del: 1, staged: true },
    ] });
    await act(async () => {});
    show("src/app.py");
    await within(screen.getByTestId("rvw-card-src/app.py")).findByTestId("diffview");
    expect(screen.getByTestId("rvw-hunk-stage-src/app.py-0")).toBeTruthy();
    show("README.md");
    await within(screen.getByTestId("rvw-card-README.md")).findByTestId("diffview");
    expect(screen.queryByTestId("rvw-hunk-stage-README.md-0")).toBeNull();
    expect(screen.queryByTestId("rvw-hunk-unstage-README.md-0")).toBeNull();
    vi.mocked(getChangeDiff).mockResolvedValue({ ok: true, patch: "@@ -1 +1 @@\n-old\n+new", untracked: false });
  });

  it("stages, unstages and reverts every file from the options menu", async () => {
    const { scmStage, scmUnstage, scmDiscard } = await import("../../api");
    pane();
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rvw-options"));
    fireEvent.click(screen.getByTestId("rvw-stage-all"));
    // README.md is already staged, so only the other two go.
    await waitFor(() => expect(scmStage).toHaveBeenCalledWith("s1", ["src/app.py", "src/new.py"]));
    fireEvent.click(screen.getByTestId("rvw-options"));
    fireEvent.click(screen.getByTestId("rvw-unstage-all"));
    await waitFor(() => expect(scmUnstage).toHaveBeenCalledWith("s1", ["README.md"]));
    fireEvent.click(screen.getByTestId("rvw-options"));
    fireEvent.click(screen.getByTestId("rvw-revert-all"));
    expect(screen.getByTestId("scm-discard-dialog").textContent).toContain("3 files");
    fireEvent.click(screen.getByTestId("scm-discard-confirm"));
    await waitFor(() => expect(scmDiscard).toHaveBeenCalledWith("s1", ["README.md", "src/app.py", "src/new.py"]));
  });

  it("Against the base branch is read-only and says what it holds", async () => {
    await scoped("branch", [{ path: "src/app.py", status: "M", add: 3, del: 0 }], { base: "main" });
    expect(screen.getByTestId("rvw-scope-note").textContent).toContain("main");
    const card = screen.getByTestId("rvw-card-src/app.py");
    // History is looked at: no stage, no revert, no commit, and no hunk buttons.
    expect(within(card).queryByTestId("rvw-stage-src/app.py")).toBeNull();
    expect(within(card).queryByTestId("rvw-revert-src/app.py")).toBeNull();
    expect(screen.queryByTestId("rvw-commit")).toBeNull();
    await within(card).findByTestId("diffview");
    expect(screen.queryByTestId("rvw-hunk-stage-src/app.py-0")).toBeNull();
  });

  it("on the base branch itself, says there is nothing to compare", async () => {
    await scoped("branch", [], { base: "main", note: "you are on the base branch" });
    expect(screen.getByTestId("rvw-clean").textContent).toContain("This is the base branch");
  });

  it("picks a commit from the history and shows that commit's own change", async () => {
    const { getSessionChanges, getScmLog } = await import("../../api");
    vi.mocked(getSessionChanges).mockResolvedValue({
      is_git: true,
      files: [{ path: "src/client.py", status: "A", add: 9, del: 0 }],
      workspace: "/ws",
    } as never);
    pane();
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rvw-scope"));
    fireEvent.click(screen.getByTestId("rvw-scope-commits"));
    const history = screen.getByTestId("rvw-history");
    // The commits newest first, the branch's tip marked, what is unpushed said once.
    expect(history.textContent).toContain("Retry uploads");
    expect(within(history).getByTestId("rvw-history-unpushed").textContent).toBe("Not pushed · 1");
    expect(within(history).getByText("origin/main")).toBeTruthy();
    fireEvent.click(screen.getByTestId("rvw-history-more"));
    await waitFor(() => expect(getScmLog).toHaveBeenLastCalledWith("s1", 100));
    fireEvent.click(screen.getByTestId("rvw-commit-b7d03aa"));
    await act(async () => {});
    expect(getSessionChanges).toHaveBeenLastCalledWith("s1", `commit:${"b7d03aa".padEnd(40, "0")}`);
    expect(screen.getByTestId("rvw-scope").textContent).toContain("Commit b7d03aa");
    expect(screen.getByTestId("rvw-scope-note").textContent).toContain("Add the client");
    expect(screen.getByTestId("rvw-card-src/client.py")).toBeTruthy();
    expect(screen.queryByTestId("rvw-commit")).toBeNull();
  });

  it("a Viewed mark made in one scope is not made in another", async () => {
    pane();
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rvw-viewed-src/app.py"));
    expect(Object.keys(JSON.parse(localStorage.getItem("reeve.review.viewed.s1") || "{}"))).toEqual(["src/app.py"]);
    cleanup();
    await scoped("staged", [{ path: "src/app.py", status: "M", add: 3, del: 1, staged: true }]);
    expect((screen.getByTestId("rvw-viewed-src/app.py") as HTMLInputElement).checked).toBe(false);
  });
});

describe("ReviewPane, as a reader sees it", () => {
  it("a file with one hunk has no hunk buttons: they would repeat the file's own", async () => {
    const { getChangeDiff } = await import("../../api");
    vi.mocked(getChangeDiff).mockResolvedValue({ ok: true, patch: "@@ -1 +1 @@\n-a\n+b", untracked: false });
    pane({ changes: [{ path: "src/app.py", status: "M", add: 1, del: 1 }] });
    await act(async () => {});
    const card = screen.getByTestId("rvw-card-src/app.py");
    await within(card).findByTestId("diffview");
    expect(within(card).queryByTestId("rvw-hunk-stage-src/app.py-0")).toBeNull();
    expect(within(card).getByTestId("rvw-stage-src/app.py")).toBeTruthy();
    // Nothing to say before a hunk at the top of the file: no raw `@@` row either.
    expect(card.textContent).not.toContain("@@");
    vi.mocked(getChangeDiff).mockResolvedValue({ ok: true, patch: "@@ -1 +1 @@\n-old\n+new", untracked: false });
  });

  it("says where a hunk sits in words, never git's @@ header", async () => {
    const { getChangeDiff } = await import("../../api");
    vi.mocked(getChangeDiff).mockResolvedValue({
      ok: true,
      patch: "@@ -12,1 +12,1 @@ class Upload:\n-a\n+b\n@@ -40,1 +40,1 @@ def build(config):\n-c\n+d",
      untracked: false,
    });
    pane({ changes: [{ path: "src/app.py", status: "M", add: 2, del: 2 }] });
    await act(async () => {});
    const diff = await within(screen.getByTestId("rvw-card-src/app.py")).findByTestId("diffview");
    expect(diff.textContent).toContain("11 unmodified lines");
    expect(diff.textContent).toContain("class Upload:");
    expect(diff.textContent).toContain("def build(config):");
    expect(diff.textContent).not.toContain("@@");
    vi.mocked(getChangeDiff).mockResolvedValue({ ok: true, patch: "@@ -1 +1 @@\n-old\n+new", untracked: false });
  });

  it("keeps Refresh in the options menu, not on the bar, for git's scopes", async () => {
    const props = pane();
    await act(async () => {});
    expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull();
    fireEvent.click(screen.getByTestId("rvw-options"));
    fireEvent.click(screen.getByTestId("rvw-refresh"));
    expect(props.onRefresh).toHaveBeenCalled();
  });

  it("leaves the comments to the composer unless it is out of sight", async () => {
    pane({ comments: [{ id: "c1", file: "src/app.py", line: 1, text: "a" }], showCommentBar: false });
    await act(async () => {});
    expect(screen.queryByTestId("review-comment-bar")).toBeNull();
  });

  it("each card's Viewed control names its file, so the column needs no legend", async () => {
    pane();
    await act(async () => {});
    const boxes = screen.getAllByRole("checkbox", { name: /^Viewed: / });
    expect(boxes.map((b) => b.getAttribute("aria-label"))).toEqual(["Viewed: README.md", "Viewed: app.py", "Viewed: new.py"]);
  });
});

describe("ReviewPane: push, write it for me, review", () => {
  it("pushes from the bar when commits wait, and says where they went", async () => {
    const { scmPush } = await import("../../api");
    const props = pane();
    await act(async () => {});
    fireEvent.click(await screen.findByTestId("rvw-push"));
    await waitFor(() => expect(scmPush).toHaveBeenCalledWith("s1"));
    expect((await screen.findByTestId("rvw-toast")).textContent).toContain("Pushed main to origin");
    expect(props.onRefresh).toHaveBeenCalled();
  });

  it("offers no push without a remote or with nothing to push", async () => {
    const { getScmLog } = await import("../../api");
    vi.mocked(getScmLog).mockResolvedValueOnce({ is_git: true, branch: "main", ahead: 3, remote: null, commits: [] });
    pane();
    await act(async () => {});
    expect(screen.queryByTestId("rvw-push")).toBeNull();
    expect(screen.queryByTestId("rvw-commit-push")).toBeNull();
  });

  it("drafts the message into the box for the person to edit, never commits it", async () => {
    const { scmSuggestMessage, scmCommit } = await import("../../api");
    vi.mocked(scmCommit).mockClear();
    pane();
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rvw-commit-write"));
    // One file is staged: the draft describes what "Staged only" would commit.
    await waitFor(() => expect(scmSuggestMessage).toHaveBeenCalledWith("s1", false));
    expect((screen.getByTestId("rvw-commit-message") as HTMLTextAreaElement).value).toBe("Retry uploads");
    expect(scmCommit).not.toHaveBeenCalled();
  });

  it("commits and pushes in one go, and a failed push is said, not swallowed", async () => {
    const { scmPush, scmCommit } = await import("../../api");
    vi.mocked(scmPush).mockResolvedValueOnce({ ok: false, error: "Authentication failed" });
    pane();
    await act(async () => {});
    fireEvent.change(screen.getByTestId("rvw-commit-message"), { target: { value: "Fix it" } });
    fireEvent.click(screen.getByTestId("rvw-commit-push"));
    expect(screen.getByTestId("rvw-commit-go").textContent).toBe("Commit & push");
    fireEvent.click(screen.getByTestId("rvw-commit-go"));
    await waitFor(() => expect(scmCommit).toHaveBeenCalledWith("s1", "Fix it", false));
    await waitFor(() => expect(scmPush).toHaveBeenCalled());
    expect((await screen.findByTestId("scm-error")).textContent).toContain("the push failed: Authentication failed");
  });

  it("puts Reeve's findings under their lines; Fix this becomes the person's comment", async () => {
    const { scmReview } = await import("../../api");
    const onAdd = vi.fn();
    pane({ onAddComment: onAdd });
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rvw-review-code"));
    await waitFor(() => expect(scmReview).toHaveBeenCalledWith("s1", "uncommitted"));
    // The list counts it with the comments; the note is under its line in the file.
    expect((await screen.findByTestId("rvw-notes-src/app.py")).textContent).toBe("1 note");
    expect(screen.getByTestId("rvw-toast").textContent).toContain("Reeve raised 1 thing");
    show("src/app.py");
    const card = screen.getByTestId("rvw-card-src/app.py");
    const note = await within(card).findByText("This divides by zero.");
    expect(note).toBeTruthy();
    fireEvent.click(within(card).getByText("Fix this"));
    expect(onAdd).toHaveBeenCalledWith(
      expect.objectContaining({ file: "src/app.py", line: 1, text: expect.stringContaining("This divides by zero.") }),
    );
    expect(within(card).queryByText("This divides by zero.")).toBeNull();
  });

  it("a dismissed finding goes, and a review that found nothing says so", async () => {
    const { scmReview } = await import("../../api");
    pane();
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rvw-review-code"));
    await screen.findByTestId("rvw-notes-src/app.py");
    show("src/app.py");
    const card = screen.getByTestId("rvw-card-src/app.py");
    await within(card).findByText("This divides by zero.");
    fireEvent.click(within(card).getByText("Dismiss"));
    expect(within(card).queryByText("This divides by zero.")).toBeNull();

    vi.mocked(scmReview).mockResolvedValueOnce({ ok: true, findings: [], truncated: true });
    fireEvent.click(screen.getByTestId("rvw-review-code"));
    await waitFor(() =>
      expect(screen.getByTestId("rvw-toast").textContent).toContain(
        "Reeve found nothing to raise in the first part of the change",
      ),
    );
  });
});

vi.mock("../../desktop", () => ({ openExternal: vi.fn() }));

describe("ReviewPane: pull and the pull request", () => {
  const featureLog = {
    is_git: true,
    branch: "fix/retry",
    ahead: 0,
    behind: 0,
    upstream: "origin/fix/retry",
    base: "main",
    remote: "origin",
    commits: [{ hash: "a".repeat(40), short: "aaaaaaa", author: "Me", when: "now", subject: "Retry uploads", refs: [] }],
  };

  it("pulls from the bar when the branch is behind, and from the menu at any time", async () => {
    const { getScmLog, scmPull } = await import("../../api");
    vi.mocked(getScmLog).mockResolvedValue({ ...featureLog, branch: "main", behind: 2, upstream: "origin/main" });
    const props = pane();
    await act(async () => {});
    fireEvent.click(await screen.findByTestId("rvw-pull"));
    await waitFor(() => expect(scmPull).toHaveBeenCalledWith("s1"));
    expect((await screen.findByTestId("rvw-toast")).textContent).toContain("up to date with origin/main");
    expect(props.onRefresh).toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("rvw-options"));
    expect(screen.getByTestId("rvw-pull-menu").textContent).toContain("Pull from origin/main");
  });

  it("a pull git refuses (it would need a merge) is said, nothing else happens", async () => {
    const { getScmLog, scmPull } = await import("../../api");
    vi.mocked(getScmLog).mockResolvedValue({ ...featureLog, branch: "main", behind: 1, upstream: "origin/main" });
    vi.mocked(scmPull).mockResolvedValueOnce({ ok: false, error: "fatal: Not possible to fast-forward, aborting." });
    pane();
    await act(async () => {});
    fireEvent.click(await screen.findByTestId("rvw-pull"));
    expect((await screen.findByTestId("scm-error")).textContent).toContain("Not possible to fast-forward");
  });

  it("on a feature branch with no PR, offers to open one with the last commit's subject", async () => {
    const { getScmLog, createPullRequest } = await import("../../api");
    const { openExternal } = await import("../../desktop");
    vi.mocked(getScmLog).mockResolvedValue(featureLog);
    pane();
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rvw-options"));
    fireEvent.click(await screen.findByTestId("rvw-open-pr"));
    expect((screen.getByTestId("pr-title") as HTMLInputElement).value).toBe("Retry uploads");
    expect((screen.getByTestId("pr-base") as HTMLInputElement).value).toBe("main");
    fireEvent.change(screen.getByTestId("pr-body"), { target: { value: "Why." } });
    fireEvent.click(screen.getByTestId("pr-draft"));
    fireEvent.click(screen.getByTestId("pr-create"));
    await waitFor(() =>
      expect(createPullRequest).toHaveBeenCalledWith("s1", { title: "Retry uploads", body: "Why.", base: "main", draft: true }),
    );
    await waitFor(() => expect(openExternal).toHaveBeenCalledWith("https://github.com/o/r/pull/7"));
  });

  it("shows the PR's number and checks on the bar, and each check in its pop-over", async () => {
    const { getScmLog, getPullRequest } = await import("../../api");
    vi.mocked(getScmLog).mockResolvedValue(featureLog);
    vi.mocked(getPullRequest).mockResolvedValue({
      available: true,
      pr: {
        number: 7, title: "Retry uploads", url: "https://github.com/o/r/pull/7", state: "OPEN", draft: false,
        head: "fix/retry", base: "main", review: "APPROVED",
        checks: { passed: 1, failed: 1, running: 0, verdict: "failed", items: [
          { name: "lint", state: "passed", url: null },
          { name: "tests", state: "failed", url: "https://ci/tests" },
        ] },
      },
    });
    pane();
    await act(async () => {});
    const badge = await screen.findByTestId("pr-badge");
    expect(badge.textContent).toContain("#7");
    expect(badge.getAttribute("data-verdict")).toBe("failed");
    fireEvent.click(badge);
    const pop = screen.getByTestId("pr-pop");
    expect(pop.textContent).toContain("Checks failed");
    expect(pop.textContent).toContain("Approved");
    expect(pop.textContent).toContain("tests");
    // With a PR open, the menu does not offer a second one.
    fireEvent.click(screen.getByTestId("rvw-options"));
    expect(screen.queryByTestId("rvw-open-pr")).toBeNull();
  });

  it("says why there are no pull requests when the GitHub CLI is missing, and nothing on the base branch", async () => {
    const { getScmLog, getPullRequest } = await import("../../api");
    vi.mocked(getScmLog).mockResolvedValue(featureLog);
    vi.mocked(getPullRequest).mockResolvedValue({ available: false, reason: "the GitHub CLI (gh) is not installed" });
    pane();
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rvw-options"));
    expect((await screen.findByTestId("rvw-pr-unavailable")).textContent).toContain("gh");
    cleanup();
    vi.mocked(getPullRequest).mockClear();
    vi.mocked(getScmLog).mockResolvedValue({ ...featureLog, branch: "main" });
    pane();
    await act(async () => {});
    expect(getPullRequest).not.toHaveBeenCalled();
  });
});

