// @vitest-environment jsdom
// The branch picker: the folder's branches, a search that doubles as "create",
// git's refusal shown where it happened, and — before a session's first message — a new
// worktree instead of a switch.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BRANCH_SWITCHED_EVENT, BranchPicker, validBranchName } from "./BranchPicker";
import { FolderActivityContext, otherWorkIn } from "./folderActivity";
import type { SessionInfo } from "../types";

vi.mock("../api/branches", () => ({
  getBranches: vi.fn().mockResolvedValue({
    is_git: true,
    current: "main",
    dirty: true,
    local: [
      { name: "main", current: true, upstream: "origin/main", ahead: 2, behind: 1, gone: false, when: "1 hour ago", subject: "x" },
      { name: "feature/a", current: false, upstream: null, ahead: 0, behind: 0, gone: false, when: "2 days ago", subject: "y" },
    ],
    remote: [{ name: "origin/release", branch: "release" }],
  }),
  switchBranch: vi.fn().mockResolvedValue({ ok: true, branch: "feature/a" }),
  newWorktree: vi.fn().mockResolvedValue({ ok: true, path: "/w/repo-spike", branch: "spike" }),
}));

afterEach(cleanup);

function picker(over: Partial<Parameters<typeof BranchPicker>[0]> = {}) {
  const props = { path: "/w/repo", onSwitched: vi.fn(), onClose: vi.fn(), ...over };
  render(<BranchPicker {...props} />);
  return props;
}

describe("BranchPicker", () => {
  it("lists local branches with ahead/behind, then the remote ones; the current one is checked", async () => {
    picker();
    const main = await screen.findByTestId("branch-main");
    expect(main.textContent).toContain("↑2");
    expect(main.textContent).toContain("↓1");
    expect((main as HTMLButtonElement).disabled).toBe(true); // already on it
    expect(screen.getByTestId("branch-origin/release").textContent).toContain("check out");
    expect(screen.getByTestId("branch-dirty")).toBeTruthy();
  });

  it("switches, says so on the window, and closes", async () => {
    const { switchBranch } = await import("../api/branches");
    const heard = vi.fn();
    window.addEventListener(BRANCH_SWITCHED_EVENT, heard);
    const props = picker();
    fireEvent.click(await screen.findByTestId("branch-feature/a"));
    await waitFor(() => expect(switchBranch).toHaveBeenCalledWith("/w/repo", "feature/a", false));
    await waitFor(() => expect(props.onSwitched).toHaveBeenCalledWith("feature/a"));
    expect(props.onClose).toHaveBeenCalled();
    expect(heard).toHaveBeenCalled();
    window.removeEventListener(BRANCH_SWITCHED_EVENT, heard);
  });

  it("a new name creates a branch; a name git would misread is not offered", async () => {
    const { switchBranch } = await import("../api/branches");
    picker();
    await screen.findByTestId("branch-main");
    fireEvent.change(screen.getByTestId("branch-search"), { target: { value: "-D" } });
    expect(screen.queryByTestId("branch-create")).toBeNull();
    fireEvent.change(screen.getByTestId("branch-search"), { target: { value: "main" } });
    expect(screen.queryByTestId("branch-create")).toBeNull(); // exists: it is the row
    fireEvent.change(screen.getByTestId("branch-search"), { target: { value: "fix/retry" } });
    fireEvent.keyDown(screen.getByTestId("branch-search"), { key: "Enter" });
    await waitFor(() => expect(switchBranch).toHaveBeenCalledWith("/w/repo", "fix/retry", true));
    expect(validBranchName("a..b")).toBe(false);
    expect(validBranchName("feature/retry-2")).toBe(true);
  });

  it("shows git's refusal where it happened and stays open", async () => {
    const { switchBranch } = await import("../api/branches");
    vi.mocked(switchBranch).mockResolvedValueOnce({ ok: false, error: "error: Your local changes would be overwritten" });
    const props = picker();
    fireEvent.click(await screen.findByTestId("branch-feature/a"));
    expect((await screen.findByTestId("branch-error")).textContent).toContain("would be overwritten");
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("before the first message, can make a worktree instead of switching", async () => {
    const { newWorktree, switchBranch } = await import("../api/branches");
    vi.mocked(switchBranch).mockClear();
    const onWorktree = vi.fn();
    picker({ allowWorktree: true, onWorktree });
    await screen.findByTestId("branch-main");
    fireEvent.click(screen.getByTestId("branch-as-worktree"));
    fireEvent.change(screen.getByTestId("branch-search"), { target: { value: "spike" } });
    fireEvent.click(screen.getByTestId("branch-create"));
    await waitFor(() => expect(newWorktree).toHaveBeenCalledWith("/w/repo", "spike", true));
    expect(onWorktree).toHaveBeenCalledWith("/w/repo-spike", "spike");
    expect(switchBranch).not.toHaveBeenCalled();
    // The folder's current branch can be a worktree's too: it is not disabled then.
    expect((screen.queryByTestId("branch-main") as HTMLButtonElement | null)?.disabled ?? false).toBe(false);
  });

  it("when another session works in the folder, says so, and a new session starts on a worktree", async () => {
    const busy = (path: string) => (path === "/w/repo" ? "Fix the retry" : null);
    render(
      <FolderActivityContext.Provider value={busy}>
        <BranchPicker path="/w/repo" allowWorktree onWorktree={vi.fn()} onSwitched={vi.fn()} onClose={vi.fn()} />
      </FolderActivityContext.Provider>,
    );
    await screen.findByTestId("branch-main");
    expect((screen.getByTestId("branch-as-worktree") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByTestId("branch-busy").textContent).toContain("“Fix the retry” is working in this folder. A new worktree");
    fireEvent.click(screen.getByTestId("branch-as-worktree"));
    expect(screen.getByTestId("branch-busy").textContent).toContain("Switching the branch changes the files under it too.");
  });

  it("a session already under way is told what a switch does to the other one; a quiet folder says nothing", async () => {
    render(
      <FolderActivityContext.Provider value={() => "Fix the retry"}>
        <BranchPicker path="/w/repo" onSwitched={vi.fn()} onClose={vi.fn()} />
      </FolderActivityContext.Provider>,
    );
    await screen.findByTestId("branch-main");
    expect(screen.getByTestId("branch-busy").textContent).toContain("changes the files under it too");
    expect(screen.queryByTestId("branch-as-worktree")).toBeNull();
    cleanup();
    picker({ allowWorktree: true, onWorktree: vi.fn() });
    await screen.findByTestId("branch-main");
    expect(screen.queryByTestId("branch-busy")).toBeNull();
    expect((screen.getByTestId("branch-as-worktree") as HTMLInputElement).checked).toBe(false);
  });
});

describe("otherWorkIn", () => {
  const row = (over: Partial<SessionInfo>): SessionInfo =>
    ({ session_id: "s", title: "t", workspace: "C:\\w\\repo", agent: "reeve", model: "m", mode: "x", updated_at: "", messages: 1, ...over }) as SessionInfo;
  it("finds another working session in the same folder, however the path is spelled", () => {
    const sessions = [row({ session_id: "me", liveness: "working" }), row({ session_id: "b", title: "Other", liveness: "working", workspace: "c:/w/repo/" })];
    expect(otherWorkIn(sessions, "C:\\w\\repo", "me")).toBe("Other");
    expect(otherWorkIn(sessions, "C:\\w\\other", "me")).toBeNull();
    expect(otherWorkIn([row({ session_id: "b", liveness: "idle" })], "C:\\w\\repo", "me")).toBeNull();
    expect(otherWorkIn([row({ session_id: "b", liveness: "working", environment_id: "ssh1" })], "C:\\w\\repo", "me")).toBeNull();
  });
});
