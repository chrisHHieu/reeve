// `/open` — the file picker: a search dialog over the
// session's roots. The server walk is mocked; this pins the fetch shape (debounced `q`),
// the grouped rendering, the empty state, keyboard nav, and the open callback the App
// wires to the RightRail viewer.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { OpenFileDialog } from "./OpenFileDialog";

const FILES = [
  {
    path: "reports/security-review.html",
    abs_path: "/ws/reports/security-review.html",
    name: "security-review.html",
  },
  { path: "notes.md", abs_path: "/ws/notes.md", name: "notes.md" },
  { path: "reports/rollup.md", abs_path: "/ws/reports/rollup.md", name: "rollup.md" },
];

function stubFetch() {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url);
      const q = new URL(url).searchParams.get("q") || "";
      const files = FILES.filter((f) => f.path.toLowerCase().includes(q.toLowerCase()));
      return { ok: true, json: async () => ({ ok: true, files, truncated: false }) } as Response;
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("OpenFileDialog", () => {
  it("lists the session's files grouped by directory and opens a pick by its abs path", async () => {
    const calls = stubFetch();
    const onOpen = vi.fn();
    const onClose = vi.fn();
    render(<OpenFileDialog sessionId="s1" onOpen={onOpen} onClose={onClose} />);

    await waitFor(() => expect(screen.getAllByTestId("open-file-row")).toHaveLength(3));
    expect(calls[0]).toContain("/v1/sessions/s1/files?");
    expect(calls[0]).toContain("q=");
    // Grouped by directory, in the server's rank order: `reports` first (its best hit
    // leads the server list), then the root-level file.
    expect(screen.getByText("reports")).toBeTruthy();
    expect(screen.getAllByText("notes.md").length).toBeGreaterThan(0);
    expect(
      screen.getAllByTestId("open-file-row").map((r) => r.getAttribute("title")),
    ).toEqual(["reports/security-review.html", "reports/rollup.md", "notes.md"]);

    fireEvent.click(screen.getAllByTestId("open-file-row")[2]); // notes.md
    expect(onOpen).toHaveBeenCalledWith("/ws/notes.md");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("sends the typed query to the server and shows the empty state when nothing matches", async () => {
    const calls = stubFetch();
    render(<OpenFileDialog sessionId="s1" onOpen={vi.fn()} onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getAllByTestId("open-file-row")).toHaveLength(3));

    fireEvent.change(screen.getByTestId("open-file-input"), { target: { value: "rollup" } });

    await waitFor(() => expect(screen.getAllByTestId("open-file-row")).toHaveLength(1));
    expect(calls.some((c) => c.includes("q=rollup"))).toBe(true);

    fireEvent.change(screen.getByTestId("open-file-input"), { target: { value: "zzz" } });
    await waitFor(() => expect(screen.getByTestId("open-file-empty")).toBeTruthy());
    expect(screen.getByTestId("open-file-empty").textContent).toContain("No files match");
  });

  it("walks the results with the keyboard and Enter opens the highlighted file", async () => {
    stubFetch();
    const onOpen = vi.fn();
    render(<OpenFileDialog sessionId="s1" onOpen={onOpen} onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getAllByTestId("open-file-row")).toHaveLength(3));

    const dialog = screen.getByTestId("open-file-dialog");
    fireEvent.keyDown(dialog, { key: "ArrowDown" });
    fireEvent.keyDown(dialog, { key: "Enter" });

    // The flat order is the grouped one: reports/security, reports/rollup, notes.
    expect(onOpen).toHaveBeenCalledWith("/ws/reports/rollup.md");
  });

  it("Escape closes without opening anything", async () => {
    stubFetch();
    const onOpen = vi.fn();
    const onClose = vi.fn();
    render(<OpenFileDialog sessionId="s1" onOpen={onOpen} onClose={onClose} />);
    await waitFor(() => expect(screen.getAllByTestId("open-file-row")).toHaveLength(3));

    fireEvent.keyDown(screen.getByTestId("open-file-dialog"), { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();
  });
});
