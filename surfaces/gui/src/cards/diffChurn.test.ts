import { describe, expect, it } from "vitest";
import { diffChurn } from "./diffChurn";
import { summarizeTurn } from "../turnSummary";
import type { Item } from "../types";

// GUI tools review, B1: a one-line edit read +5 −5 on the row and +0 −0 in the turn header,
// because the hunk's sides are context windows and a missing count was taken as zero.
const windowed = {
  path: "orders/line.py",
  old_text: "a\nb\nqty = 1\nc\nd\n",
  new_text: "a\nb\nqty = 2\nc\nd\n",
};

describe("diffChurn", () => {
  it("diffs a context-windowed hunk instead of counting its sides", () => {
    expect(diffChurn(windowed)).toEqual({ added: 1, removed: 1 });
  });

  it("prefers the engine's counts when the hunk carries them", () => {
    expect(diffChurn({ ...windowed, added: 3, removed: 2 })).toEqual({ added: 3, removed: 2 });
  });

  it("counts a creation as all additions", () => {
    expect(diffChurn({ path: "n.py", old_text: null, new_text: "x\ny\n" })).toEqual({
      added: 2,
      removed: 0,
    });
  });

  it("gives the turn header the same number as the row", () => {
    const tool = {
      kind: "tool",
      id: "t1",
      callId: "c1",
      name: "replace_in_file",
      args: { path: "orders/line.py" },
      status: "ok",
      presentation: { summary: "Edited", card: { card: "diff", diffs: [windowed] } },
    } as unknown as Item;
    const summary = summarizeTurn([tool]);
    expect(summary && [summary.added, summary.removed]).toEqual([1, 1]);
  });
});

describe("the turn header and the receipt", () => {
  it("prefer the receipt's per-file churn over summing the calls", () => {
    // Live app, 2026-09-25: write +10, then edit +1 −1 of the same file read "+11 −1" on the
    // header beside "+10 −0" on the receipt.
    const call = (id: string, diff: object) =>
      ({
        kind: "tool", id, callId: id, name: "replace_in_file", args: {}, status: "ok",
        presentation: { summary: "Edited", card: { card: "diff", diffs: [diff] } },
      }) as unknown as Item;
    const items = [
      call("a", { path: "demo.py", old_text: null, new_text: "x\n".repeat(10), added: 10, removed: 0 }),
      call("b", { path: "demo.py", old_text: "a\nx7 = 7\n", new_text: "a\nx7 = 70\n", added: 1, removed: 1 }),
    ];
    expect(summarizeTurn(items)).toMatchObject({ files: 1, added: 11, removed: 1 });
    const receipt = { kind: "changes", stats: [{ path: "demo.py", added: 10, removed: 0 }] } as const;
    expect(summarizeTurn(items, receipt as never)).toMatchObject({ files: 1, added: 10, removed: 0 });
  });

  it("count every file the receipt names, numbers or not", () => {
    // G4: a command's files come from git; one whose before-image was too big to keep is
    // named without numbers, and a deleted one is named in `deleted`. Both still changed.
    const shell = {
      kind: "tool", id: "s", callId: "s", name: "run_shell", args: { command: "sed -i" }, status: "ok",
    } as unknown as Item;
    const receipt = {
      kind: "changes",
      overwritten: ["/w/a.py", "/w/huge.sql"],
      deleted: ["/w/gone.py"],
      stats: [
        { path: "/w/a.py", added: 2, removed: 2, source: "shell" },
        { path: "/w/gone.py", added: 0, removed: 4, source: "shell" },
      ],
    } as const;
    expect(summarizeTurn([shell], receipt as never)).toMatchObject({ files: 3, added: 2, removed: 6 });
  });
});
