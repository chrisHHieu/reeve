// A child's work waiting in its own copy (WorktreeHandback.tsx), against a stubbed
// server that keeps the copy the way the real one does: until it is applied or discarded.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WORKTREE_CHANGED, WorktreeHandback, handbackSummary } from "./WorktreeHandback";
import { withKept, type DelegationNode } from "../../delegations";

afterEach(cleanup);

type Copy = { files: unknown[]; busy: boolean } | null;
let copy: Copy;
let answer: { ok: boolean; error?: string };
let posted: string[];

beforeEach(() => {
  copy = {
    busy: false,
    files: [
      { path: "src/app.py", change: "modified", added: 2, removed: 1 },
      { path: "src/new.py", change: "created", added: 9, removed: 0 },
    ],
  };
  answer = { ok: true };
  posted = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        posted.push(url.split("/worktree/")[1]);
        if (answer.ok) copy = null;
        return { json: async () => answer } as Response;
      }
      return {
        json: async () => ({ worktree: copy && { path: "/r/.worktrees/subagent-c1", branch: "reeve-sub/c1", ...copy } }),
      } as Response;
    }),
  );
});

describe("WorktreeHandback", () => {
  it("says what is waiting and lists the files on request", async () => {
    render(<WorktreeHandback childId="c1" />);
    expect((await screen.findByTestId("handback-summary")).textContent).toBe(
      "2 files changed in its own copy — not in your folder yet",
    );
    expect(screen.queryByTestId("handback-files")).toBeNull();
    fireEvent.click(screen.getByTestId("handback-files-toggle"));
    const rows = screen.getByTestId("handback-files").querySelectorAll("li");
    expect([...rows].map((row) => row.textContent)).toEqual(["src/app.pymodified+2−1", "src/new.pycreated+9"]);
  });

  it("applies with one press, and tells the roster a copy was handed back", async () => {
    const heard = vi.fn();
    window.addEventListener(WORKTREE_CHANGED, heard);
    render(<WorktreeHandback childId="c1" />);
    fireEvent.click(await screen.findByTestId("handback-apply"));
    await waitFor(() => expect(screen.queryByTestId("worktree-handback")).toBeNull());
    expect(posted).toEqual(["apply"]);
    expect(heard).toHaveBeenCalledTimes(1);
    window.removeEventListener(WORKTREE_CHANGED, heard);
  });

  it("keeps the copy and says why when the changes no longer fit", async () => {
    answer = { ok: false, error: "the changes do not fit what is in the folder now" };
    render(<WorktreeHandback childId="c1" />);
    fireEvent.click(await screen.findByTestId("handback-apply"));
    expect((await screen.findByTestId("handback-error")).textContent).toContain("do not fit");
    expect(screen.getByTestId("handback-apply")).toBeTruthy();
  });

  it("discards only on the second press", async () => {
    render(<WorktreeHandback childId="c1" />);
    const discard = await screen.findByTestId("handback-discard");
    fireEvent.click(discard);
    expect(discard.textContent).toBe("Discard for good?");
    expect(posted).toEqual([]);
    fireEvent.click(discard);
    await waitFor(() => expect(screen.queryByTestId("worktree-handback")).toBeNull());
    expect(posted).toEqual(["discard"]);
  });

  it("offers nothing to apply for an empty copy, and nothing at all while the child works", async () => {
    copy = { busy: false, files: [] };
    const { unmount } = render(<WorktreeHandback childId="c1" />);
    expect((await screen.findByTestId("handback-summary")).textContent).toBe("Its own copy holds no changes");
    expect(screen.queryByTestId("handback-apply")).toBeNull();
    unmount();
    copy = { busy: true, files: [{ path: "a", change: "modified", added: 1, removed: 0 }] };
    render(<WorktreeHandback childId="c1" />);
    expect(((await screen.findByTestId("handback-apply")) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("handback-discard") as HTMLButtonElement).disabled).toBe(true);
  });

  it("draws nothing for a child with no copy", async () => {
    copy = null;
    const { container } = render(<WorktreeHandback childId="c1" />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });
});

describe("which rows wait", () => {
  const node = (childId: string | null, outcome: DelegationNode["outcome"]) =>
    ({ key: String(childId), childId, outcome, kept: false }) as DelegationNode;

  it("marks only settled children the server says still hold a copy", () => {
    const nodes = [node("a", "completed"), node("b", "completed"), node("c", "running"), node(null, "completed")];
    expect(withKept(nodes, new Set(["a", "c"])).map((n) => n.kept)).toEqual([true, false, false, false]);
    // Nothing kept: the very same array, so a memoized roster does not redraw.
    expect(withKept(nodes, new Set())).toBe(nodes);
  });

  it("words one file and several", () => {
    const view = { path: "", branch: "", busy: false };
    expect(handbackSummary({ ...view, files: [{ path: "a", change: "created", added: 1, removed: 0 }] })).toBe(
      "1 file changed in its own copy — not in your folder yet",
    );
  });
});
