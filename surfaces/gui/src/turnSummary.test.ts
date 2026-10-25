import { describe, expect, it } from "vitest";
import type { Item } from "./types";
import { summarizeTurn, summaryParts } from "./turnSummary";

function tool(over: Partial<Extract<Item, { kind: "tool" }>>): Item {
  return {
    kind: "tool",
    id: over.id ?? over.name ?? "t",
    name: over.name ?? "read_file",
    args: {},
    status: over.status ?? "ok",
    ...over,
  } as Item;
}

function edit(path: string, added: number, removed: number, status = "ok"): Item {
  return tool({
    name: "replace_in_file",
    status,
    presentation: {
      summary: `Edited ${path}`,
      card: { card: "diff", title: path, diffs: [{ path, old_text: "a", new_text: "b", added, removed }] },
    },
  });
}

describe("summarizeTurn", () => {
  it("says nothing about a turn that made no calls", () => {
    expect(summarizeTurn([{ kind: "assistant", text: "hi" }])).toBeNull();
  });

  it("counts files by distinct path and sums the churn", () => {
    const summary = summarizeTurn([edit("src/a.ts", 10, 2), edit("src/b.ts", 3, 1)]);
    expect(summary).toMatchObject({ steps: 2, files: 2, added: 13, removed: 3 });
  });

  it("reports a file edited twice as one file with the turn's total", () => {
    const summary = summarizeTurn([edit("src/a.ts", 10, 2), edit("src/a.ts", 4, 4)]);
    expect(summary).toMatchObject({ files: 1, added: 14, removed: 6 });
  });

  it("separates commands and failures from ordinary steps", () => {
    const summary = summarizeTurn([
      tool({ name: "run_shell", status: "ok" }),
      tool({ name: "terminal_read", status: "ok" }),
      tool({ name: "read_file", status: "ok" }),
      tool({ name: "run_shell", status: "error" }),
      tool({ name: "read_file", status: "interrupted" }),
    ]);
    expect(summary).toMatchObject({ steps: 5, commands: 3, failed: 1 });
    // An interruption is not a failure — the same rule the row colours follow. It IS still a
    // command: it ran, and a failed run is exactly what a reader wants counted.
    expect(summaryParts(summary!)).toEqual(["3 commands", "1 failed"]);
  });

  it("does not count the bookkeeping rows the transcript hides", () => {
    const summary = summarizeTurn([tool({ name: "todo_write" }), tool({ name: "read_file" })]);
    expect(summary).toMatchObject({ steps: 1 });
  });

  it("reads churn from the result card, and diffs a hunk that carries no counts", () => {
    const summary = summarizeTurn([
      tool({
        name: "write_file",
        presentation: {
          summary: "Wrote a file",
          card: { card: "diff", title: "new.ts", diffs: [{ path: "new.ts", old_text: null, new_text: "x" }] },
        },
      }),
    ]);
    // A one-line creation is +1: a missing count used to read as 0 (GUI tools review, B1).
    expect(summary).toMatchObject({ files: 1, added: 1, removed: 0 });
  });

  it("names each part in reading order, and omits the empty ones", () => {
    expect(summaryParts({ steps: 3, commands: 0, files: 1, added: 1, removed: 0, failed: 0 })).toEqual([
      "1 file",
    ]);
    expect(summaryParts({ steps: 9, commands: 3, files: 2, added: 1, removed: 0, failed: 2 })).toEqual([
      "2 files",
      "3 commands",
      "2 failed",
    ]);
  });
});
