import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Transcript } from "./Transcript";
import type { Item } from "../types";

afterEach(cleanup);

// The row's expanded body is the whole story: the tool's own account of its result (the
// engine's `_presentation`) plus its IN/OUT. The hover-revealed Inspect pill and its side
// panel were removed by the owner (2026-09-19), so nothing here reaches a second surface.
// An offer the engine cannot honour is never made: no undo it did not record, no link to a
// file nothing kept.

// A turn's steps are always shown (no fold since 2026-09-28), so a "queryByTestId is null"
// check here is about the row, never about a closed turn hiding it.
function show(items: Item[], props: Record<string, unknown> = {}) {
  return render(<Transcript items={items} running={false} onApprove={vi.fn()} {...props} />);
}

const step = (extra: Partial<Extract<Item, { kind: "tool" }>> = {}): Item[] => [
  {
    kind: "tool",
    id: "t1",
    name: "run_shell",
    args: { command: "pytest -q", description: "Run the tests" },
    status: "ok",
    ...extra,
  },
];

describe("the row's own body", () => {
  it("prefers the tool's account of the result over the one derived from its arguments", () => {
    show(
      step({
        status: "error",
        presentation: {
          summary: "Command failed (exit 2)",
          detail: "47 lines of output, 1,203 omitted",
        },
      }),
    );
    expect(screen.getByText("Command failed (exit 2)")).toBeTruthy();
    // The argument-derived line said the opposite of what happened.
    expect(screen.queryByText(/Ran /)).toBeNull();
  });

  it("still describes tools that declared no contract", () => {
    // Most of the registry. The fallback sentence must keep working — it is what the
    // engine never had a contract to describe, so it comes from the arguments alone.
    show(step());
    expect(screen.getByText(/Ran pytest -q/)).toBeTruthy();
  });

  it("carries the arguments and the result inline, so the row never needs a side panel", () => {
    show(step({ preview: '{"exit_code":0,"output":"3 passed"}' }));
    fireEvent.click(document.querySelector("[data-disclosure-row]")!);
    // `.tool-io`, not `.tool-io-json`: a built-in tool's arguments are labelled lines now.
    const bodies = Array.from(document.querySelectorAll(".tool-io")).map(
      (el) => el.textContent || "",
    );
    expect(bodies.join("\n")).toContain("pytest -q");
    expect(bodies.join("\n")).toContain("3 passed");
  });

  it("shows the filters' hidden count, which the agent never saw", () => {
    show(step({ hidden: 3 }));
    expect(screen.getByTestId("stepgroup-hidden").textContent).toContain("3 hidden");
  });
});

// Item 4.1 — the receipt. The engine computes it from the calls it SAW, so the test that
// matters is that it renders facts the model's prose cannot reach into.
describe("Transcript — turn receipt", () => {
  it("separates what was created from what was overwritten", () => {
    render(
      <Transcript
        items={[
          {
            kind: "changes",
            created: ["/w/report.md", "/w/data.csv"],
            overwritten: ["/w/config.py"],
          },
        ]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    const block = screen.getByTestId("turn-changes");
    // Collapsed: just the count — no "WHAT CHANGED" card, no path wall.
    expect(screen.getByTestId("changes-count").textContent).toContain("3 files changed");
    expect(screen.queryByText("report.md")).toBeNull();
    // Expanding lists basenames; the full path lives in the title attribute.
    fireEvent.click(screen.getByTestId("changes-toggle"));
    expect(screen.getByText("report.md")).toBeTruthy();
    expect(screen.getByText("config.py")).toBeTruthy();
    expect(block.textContent).not.toContain("/w/report.md");
  });

  it("names what a command changed and removed, and says so", () => {
    // G4: a `sed -i` turn named no path to any write tool and ended with no receipt. The
    // engine now reads those files from git and marks them `source: "shell"`.
    render(
      <Transcript
        items={[
          {
            kind: "changes",
            overwritten: ["/w/a.py"],
            deleted: ["/w/gone.py"],
            stats: [
              { path: "/w/a.py", added: 2, removed: 2, source: "shell" },
              { path: "/w/gone.py", added: 0, removed: 5, source: "shell" },
            ],
          },
        ]}
        running={false}
        onApprove={vi.fn()}
        onReveal={vi.fn()}
        onUndo={vi.fn()}
      />,
    );
    expect(screen.getByTestId("changes-count").textContent).toContain("2 files changed");
    expect(screen.getByTestId("receipt-diff").textContent).toBe("+2 −7");
    fireEvent.click(screen.getByTestId("changes-toggle"));
    const rows = screen.getAllByTestId("receipt-file");
    expect(rows.map((row) => row.querySelector(".rc-file-name")?.textContent)).toEqual(["a.py", "gone.py"]);
    // Each row wears its file's own mark, as the transcript's rows do (owner, 2026-10-13).
    expect(rows.map((row) => row.querySelector('[data-component="file-icon"] use')?.getAttribute("href")?.split("#")[1])).toEqual([
      "python",
      "python",
    ]);
    // What kind of change, in a word.
    expect(rows[0].textContent).toContain("by a command");
    expect(rows[1].textContent).toContain("deleted");
    // A command's change has nothing to undo.
    // Only the sides that moved: the deleted file lost five lines and gained none.
    expect(rows[1].querySelector('[data-testid="receipt-file-diff"]')?.textContent).toBe("−5");
    expect(screen.queryByTestId("receipt-file-undo")).toBeNull();
  });

  it("says only what changed and by how much, never the check's last line (owner, 2026-10-15)", () => {
    // A runner's last line read as noise here, and its colour codes reached it as boxes.
    render(
      <Transcript
        items={[
          {
            kind: "changes",
            overwritten: ["/w/a.py"],
            stats: [{ path: "/w/a.py", added: 3, removed: 1 }],
            check: { command: "npm test", ok: true, summary: "Duration 2.22s", editsSince: 0, timedOut: false },
          } as never,
        ]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    const line = screen.getByTestId("turn-changes");
    expect(screen.getByTestId("changes-count").textContent).toBe("1 file changed");
    expect(line.textContent).toContain("+3");
    expect(line.textContent).not.toContain("Checked");
    expect(line.textContent).not.toContain("Duration");
    // Opened, the files are listed and the check's command line is not repeated under them.
    fireEvent.click(screen.getByTestId("changes-toggle"));
    expect(screen.getAllByTestId("receipt-file")).toHaveLength(1);
    expect(line.textContent).not.toContain("npm test");
  });

  it("the agent's own notes are not counted as files changed", () => {
    render(
      <Transcript
        items={[
          {
            kind: "changes",
            overwritten: ["/w/a.py", "/state/memory/PROGRESS.md"],
            notes: ["/state/memory/PROGRESS.md"],
          },
        ]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.getByTestId("changes-count").textContent).toBe("1 file changed");
    fireEvent.click(screen.getByTestId("changes-toggle"));
    expect(screen.getAllByTestId("receipt-file")).toHaveLength(1);
    expect(screen.getByTestId("receipt-notes").textContent).toContain("1 of its own notes");
    cleanup();
    // A turn that only wrote its notes has no receipt at all.
    render(
      <Transcript
        items={[{ kind: "changes", overwritten: ["/state/memory/PROGRESS.md"], notes: ["/state/memory/PROGRESS.md"] }]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("turn-changes")).toBeNull();
  });

  it("a big change set opens in full from its own receipt", () => {
    const paths = Array.from({ length: 9 }, (_, n) => `/w/src/file_${n}.ts`);
    render(
      <Transcript items={[{ kind: "changes", overwritten: paths } as never]} running={false} onApprove={vi.fn()} />,
    );
    fireEvent.click(screen.getByTestId("changes-toggle"));
    expect(screen.getAllByTestId("receipt-file")).toHaveLength(6);
    fireEvent.click(screen.getByTestId("receipt-more"));
    expect(screen.getAllByTestId("receipt-file")).toHaveLength(9);
    // Opened, the list is the bounded scrolling box, and the same control folds it back.
    expect(screen.getByTestId("receipt-files").classList.contains("expanded")).toBe(true);
    expect(screen.getByTestId("receipt-more").textContent).toBe("Show less");
    expect(screen.queryByTestId("receipt-filter")).toBeNull(); // nine files need no filter
    fireEvent.click(screen.getByTestId("receipt-more"));
    expect(screen.getAllByTestId("receipt-file")).toHaveLength(6);
    expect(screen.getByTestId("receipt-more").textContent).toBe("Show 3 more");
  });

  it("a failed turn's receipt does not take its Retry away", () => {
    // The receipt arrives after the error notice (live and on replay), and the Retry
    // anchor used to stop at the first non-notice item — the button vanished whenever the
    // failed turn had changed a file.
    render(
      <Transcript
        items={[
          { kind: "notice", tone: "warn", text: "Error: model unreachable", retriable: true },
          { kind: "changes", created: ["/w/half.txt"] },
        ]}
        running={false}
        onApprove={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByTestId("notice-retry")).toBeTruthy();
  });

  it("no files changed, no receipt — did lines are the steps' story to tell", () => {
    // The "Done · …" wall duplicated the step rows above it, noise wearing facts as a
    // costume. A turn that touched no files gets no receipt at all.
    render(
      <Transcript
        items={[{ kind: "changes", did: ["Send a message to #sales on Slack"] }]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("turn-changes")).toBeNull();
  });

  it("caps a long list instead of printing two hundred names", () => {
    const many = Array.from({ length: 200 }, (_, i) => `/w/file-${i}.txt`);
    render(
      <Transcript items={[{ kind: "changes", created: many }]} running={false} onApprove={vi.fn()} />,
    );
    expect(screen.getByTestId("changes-count").textContent).toContain("200 files changed");
    fireEvent.click(screen.getByTestId("changes-toggle"));
    expect(screen.getByTestId("turn-changes").textContent).toContain("Show 194 more");
    // Hundreds of files: opened, they scroll inside the box, and a filter finds one.
    fireEvent.click(screen.getByTestId("receipt-more"));
    expect(screen.getAllByTestId("receipt-file")).toHaveLength(200);
    fireEvent.change(screen.getByTestId("receipt-filter"), { target: { value: "file-137" } });
    expect(screen.getAllByTestId("receipt-file").map((row) => row.getAttribute("title"))).toEqual([
      "/w/file-137.txt",
    ]);
    fireEvent.change(screen.getByTestId("receipt-filter"), { target: { value: "nothing-like-it" } });
    expect(screen.queryAllByTestId("receipt-file")).toHaveLength(0);
    expect(screen.getByTestId("turn-changes").textContent).toContain("No changed file matches");
  });

  it("renders nothing for a turn that changed nothing", () => {
    // The whole transcript IS rendered — otherwise this would pass for the wrong reason.
    render(
      <Transcript
        items={[{ kind: "assistant", text: "The answer is 42." }]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.getByText("The answer is 42.")).toBeTruthy();
    expect(screen.queryByTestId("turn-changes")).toBeNull();
  });
});

// The settled turn: the header answers "how long", the receipt answers
// "how much" — per-file +added −removed from the engine's turn-start snapshot, with a
// real Undo button per file wired to the same call the step rows use.
describe("a finished turn reports its duration and its churn", () => {
  const tsStep = (id: string, ts: number): Item => ({
    kind: "tool",
    id,
    name: "run_shell",
    args: { command: "pytest -q" },
    status: "ok",
    ts,
  });

  it("the footer says how long the turn worked when the stamps span a duration", () => {
    render(<Transcript items={[tsStep("t1", 1000), tsStep("t2", 1000 + 21 * 60 + 30)]} running={false} onApprove={vi.fn()} />);
    expect(screen.getByTestId("turn-worked").textContent).toBe("Worked for 21m 30s");
  });

  it("says no time when stamps are missing or instantaneous", () => {
    render(<Transcript items={[tsStep("t1", 1000), tsStep("t2", 1000)]} running={false} onApprove={vi.fn()} />);
    expect(screen.queryByTestId("turn-worked")).toBeNull();
  });

  it("says nothing, not 'Worked for 0s', for a turn under a second", () => {
    render(<Transcript items={[tsStep("t1", 1000), tsStep("t2", 1000.4)]} running={false} onApprove={vi.fn()} />);
    expect(screen.queryByTestId("turn-worked")).toBeNull();
  });

  it("the receipt shows the churn and a working Undo — header and per file", async () => {
    const onUndo = vi.fn().mockResolvedValue({ ok: true });
    const items: Item[] = [
      tsStep("write-1", 1000),
      {
        kind: "changes",
        created: ["/proj/report.md"],
        stats: [{ path: "/proj/report.md", added: 120, removed: 0, callId: "write-1", undoable: true }],
      },
    ];
    render(<Transcript items={items} running={false} onUndo={onUndo} onApprove={vi.fn()} />);
    // The head answers "how much" without expanding.
    // Only the side that moved: "+120", never "+120 −0" (the red zero read as a loss).
    expect(screen.getByTestId("receipt-diff").textContent).toBe("+120");
    // Undo all asks once, in place, then reverts the whole change set through the engine.
    fireEvent.click(screen.getByTestId("receipt-undo"));
    expect(onUndo).not.toHaveBeenCalled();
    expect(screen.getByTestId("receipt-undo-confirm").textContent).toBe("Put 1 file back?");
    fireEvent.click(screen.getByTestId("receipt-undo-confirm"));
    await waitFor(() => expect(screen.getByText("Put back")).toBeTruthy());
    expect(onUndo).toHaveBeenCalledWith("write-1");
    expect(screen.queryByTestId("receipt-undo")).toBeNull();
  });

  it("a file's row opens its change in Review, and its undo is an icon that does not ask", async () => {
    const onUndo = vi.fn().mockResolvedValue({ ok: true });
    const onReveal = vi.fn();
    const asked: string[] = [];
    const listen = (event: Event) => asked.push(String((event as CustomEvent).detail?.path));
    window.addEventListener("reeve-see-changes", listen);
    const items: Item[] = [
      {
        kind: "changes",
        created: ["/proj/src/report.md"],
        stats: [{ path: "/proj/src/report.md", added: 12, removed: 3, callId: "w1", undoable: true }],
      },
    ];
    render(<Transcript items={items} running={false} onUndo={onUndo} onReveal={onReveal} onApprove={vi.fn()} />);
    fireEvent.click(screen.getByTestId("changes-toggle"));
    expect(screen.getByTestId("receipt-file-diff").textContent).toContain("+12");
    // A file the turn created says so, and there is no "Open" beside a name that is a button.
    const row = screen.getByTestId("receipt-file");
    expect(row.textContent).toContain("new");
    expect(row.textContent).not.toContain("Open");
    fireEvent.click(row.querySelector(".rc-file-open")!);
    expect(asked).toEqual(["/proj/src/report.md"]);
    expect(onReveal).not.toHaveBeenCalled();
    expect(screen.getByTestId("receipt-file-undo").getAttribute("aria-label")).toBe("Remove report.md");
    fireEvent.click(screen.getByTestId("receipt-file-undo"));
    await waitFor(() => expect(onUndo).toHaveBeenCalledWith("w1"));
    window.removeEventListener("reeve-see-changes", listen);
  });

  it("no churn numbers, no chips", () => {
    render(
      <Transcript
        items={[
          tsStep("write-1", 1000),
          { kind: "changes", created: ["/proj/empty.md"] },
        ]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("receipt-diff")).toBeNull();
  });
});

describe("Transcript — screen reader status (G6)", () => {
  it("carries one polite status region that says the turn is working", () => {
    render(<Transcript items={[{ kind: "user", text: "go" } as Item]} running onApprove={vi.fn()} />);
    const regions = screen.getAllByRole("status").filter((el) => el.getAttribute("data-testid") === "turn-announcer");
    expect(regions).toHaveLength(1);
    expect(regions[0].getAttribute("aria-live")).toBe("polite");
    expect(regions[0].textContent).toBe("Working");
  });
});
