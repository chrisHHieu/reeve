// @vitest-environment jsdom
// Review does not draw a screenshot as a diff, and does not read a diff again for a file that did not
// move. Found from a laggy GUI: three untracked PNGs in one workspace were handed over as
// ~7,400 lines of replacement characters and read again each time the change list was (65 requests for
// 4 files in 20 minutes, 585-813 KB each).
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReviewPane } from "./ReviewPane";
import { formatSize, mergeChanges } from "./reviewModel";
import type { ChangeFile } from "../../api";

vi.mock("../../api", async () => {
  const actual: any = await vi.importActual("../../api");
  return {
    ...actual,
    getScmLog: vi.fn().mockResolvedValue({ is_git: true, branch: "main", ahead: 0, base: "main", remote: "origin", more: false, commits: [] }),
    getSessionChanges: vi.fn().mockResolvedValue({ is_git: true, files: [], workspace: "/ws" }),
    getPullRequest: vi.fn().mockResolvedValue({ available: true, pr: null }),
    getChangeDiff: vi.fn().mockResolvedValue({ ok: true, patch: "@@ -1 +1 @@\n-old\n+new", untracked: false }),
  };
});

beforeEach(async () => {
  localStorage.clear();
  const { getChangeDiff } = await import("../../api");
  vi.mocked(getChangeDiff).mockReset();
  vi.mocked(getChangeDiff).mockResolvedValue({ ok: true, patch: "@@ -1 +1 @@\n-old\n+new", untracked: false });
});
afterEach(cleanup);

const code: ChangeFile = { path: "src/app.py", status: "M", add: 3, del: 1, revision: "100:1" };
const shot: ChangeFile = { path: "alerts.png", status: "A", add: 0, del: 0, binary: true, size: 353264, revision: "353264:9" };

/** Opens a file's card in Review's column. */
function show(path: string) {
  fireEvent.click(screen.getByTestId(`rvw-toggle-${path}`));
}

function props(changes: ChangeFile[]) {
  return {
    sessionId: "s1",
    isGit: true,
    changes,
    recorded: [],
    roots: ["/ws"],
    agentTurn: () => undefined,
    onRefresh: vi.fn(),
    onOpenFile: vi.fn(),
    diffStyle: "unified" as const,
    onDiffStyle: vi.fn(),
  };
}

describe("reviewModel", () => {
  it("carries what the server knows about the file on disk", () => {
    const [file] = mergeChanges([{ ...shot }]);
    expect(file).toMatchObject({ binary: true, size: 353264, revision: "353264:9", signature: "A:0:0" });
    const [plain] = mergeChanges([{ path: "a.py", status: "M", add: 1, del: 0 }]);
    expect(plain).toMatchObject({ binary: false, revision: "" });
    // Staged and edited again: git lists it twice, either side moving is a change to read.
    const [both] = mergeChanges([
      { path: "a.py", status: "M", add: 1, del: 0, staged: true, revision: "10:1" },
      { path: "a.py", status: "M", add: 1, del: 0, revision: "10:1" },
    ]);
    expect(both.revision).toBe("10:1|10:1");
  });

  it("writes a size the way a person reads it", () => {
    expect(formatSize(512)).toBe("512 B");
    expect(formatSize(353264)).toBe("345 KB");
    expect(formatSize(5.5 * 1024 * 1024)).toBe("5.5 MB");
    expect(formatSize(-1)).toBe("");
  });
});

describe("ReviewPane with a screenshot", () => {
  it("never shows a binary file first, reads nothing for it, and says what it is when opened", async () => {
    const { getChangeDiff } = await import("../../api");
    vi.mocked(getChangeDiff).mockClear();
    render(<ReviewPane {...props([shot, code])} />);
    await act(async () => {});
    // However early it sits in the column, the card open first is the first one with lines to read.
    expect(screen.getByTestId("rvw-card-alerts.png").classList.contains("open")).toBe(false);
    expect(screen.getByTestId("rvw-card-src/app.py").classList.contains("open")).toBe(true);
    expect(getChangeDiff).not.toHaveBeenCalledWith("s1", "alerts.png", expect.anything());

    show("alerts.png");
    const card = screen.getByTestId("rvw-card-alerts.png");
    expect(within(card).getByTestId("rvw-binary-alerts.png").textContent).toContain("Binary file · 345 KB");
    expect(getChangeDiff).not.toHaveBeenCalledWith("s1", "alerts.png", expect.anything());  // facts are in the list
  });

  it("says the same when only the diff call knows the file is binary", async () => {
    const { getChangeDiff } = await import("../../api");
    vi.mocked(getChangeDiff).mockImplementation(async (_session: string, path: string) =>
      path === "x.bin"
        ? { ok: true, untracked: true, binary: true, size: 2048 }
        : { ok: true, patch: "@@ -1 +1 @@\n-old\n+new", untracked: false },
    );
    render(<ReviewPane {...props([{ path: "x.bin", status: "A", add: 0, del: 0, revision: "2048:1" }, code])} />);
    await act(async () => {});
    show("x.bin");
    expect((await within(screen.getByTestId("rvw-card-x.bin")).findByTestId("rvw-binary-x.bin")).textContent).toContain("2 KB");
  });

  it("says when a long file is only previewed", async () => {
    const { getChangeDiff } = await import("../../api");
    vi.mocked(getChangeDiff).mockImplementation(async () => ({ ok: true, untracked: true, content: "a\nb\n", truncated: true, size: 300000 }));
    render(<ReviewPane {...props([{ path: "big.log", status: "A", add: 2, del: 0, revision: "300000:1" }])} />);
    await act(async () => {});
    expect(screen.getByTestId("rvw-truncated-big.log").textContent).toContain("Showing the start of a long file (293 KB in all)");
  });
});

describe("ReviewPane reading diffs again", () => {
  it("reads a diff again only when its file moved, not each time the list was read", async () => {
    const { getChangeDiff } = await import("../../api");
    const { rerender } = render(<ReviewPane {...props([code])} />);
    await act(async () => {});
    expect(getChangeDiff).toHaveBeenCalledWith("s1", "src/app.py", "uncommitted");
    vi.mocked(getChangeDiff).mockClear();

    // The list was read again, unchanged: a new array, the same files.
    rerender(<ReviewPane {...props([{ ...code }])} />);
    await act(async () => {});
    rerender(<ReviewPane {...props([{ ...code }])} />);
    await act(async () => {});
    expect(getChangeDiff).not.toHaveBeenCalled();

    // The file was written: its revision moved, so the open card reads again, once.
    rerender(<ReviewPane {...props([{ ...code, revision: "140:2" }])} />);
    await act(async () => {});
    expect(getChangeDiff).toHaveBeenCalledTimes(1);
  });

  it("still reads again on every list read when the server sent no revision", async () => {
    const { getChangeDiff } = await import("../../api");
    const bare = { path: "src/app.py", status: "M", add: 3, del: 1 };
    const { rerender } = render(<ReviewPane {...props([bare])} />);
    await act(async () => {});
    vi.mocked(getChangeDiff).mockClear();
    rerender(<ReviewPane {...props([{ ...bare }])} />);
    await act(async () => {});
    expect(getChangeDiff).toHaveBeenCalledTimes(1);  // an older server: correct, as before, not fast
  });
});
