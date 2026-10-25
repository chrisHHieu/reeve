import { describe, expect, it } from "vitest";
import {
  diffRowsForChange,
  recordedFilesFromChanges,
  recordedChangesFromItems,
  summarizeRecordedChanges,
} from "./reviewChanges";
import type { Item } from "./types";

function tool(partial: Record<string, unknown>): Item {
  return {
    kind: "tool",
    id: "t1",
    name: "write_file",
    args: {},
    status: "success",
    ...partial,
  } as Item;
}

const writeCard = {
  summary: "wrote src/a.ts",
  card: { card: "diff", diffs: [{ path: "src/a.ts", old_text: null, new_text: "one\ntwo\n", added: 2, removed: 0 }] },
};

describe("recordedChangesFromItems", () => {
  it("folds result diff cards into one change per file", () => {
    const changes = recordedChangesFromItems([
      tool({ callId: "c1", undoable: true, presentation: writeCard }),
    ]);
    expect(changes).toHaveLength(1);
    expect(changes[0]).toMatchObject({
      callId: "c1",
      path: "src/a.ts",
      status: "added",
      added: 2,
      removed: 0,
      undoable: true,
    });
  });

  it("splits a multi-file card into one change each and reads status from the hunks", () => {
    const changes = recordedChangesFromItems([
      tool({
        callId: "c2",
        presentation: {
          summary: "edited two files",
          card: {
            card: "diff",
            diffs: [
              { path: "src/a.ts", old_text: "one\n", new_text: "one\ntwo\n", added: 1, removed: 0 },
              { path: "src/b.ts", old_text: "gone\n", new_text: "", added: 0, removed: 1 },
            ],
          },
        },
      }),
    ]);
    expect(changes.map((c) => [c.path, c.status])).toEqual([
      ["src/a.ts", "modified"],
      ["src/b.ts", "deleted"],
    ]);
  });

  it("falls back to the call card while the result is still running", () => {
    const changes = recordedChangesFromItems([
      tool({ id: "live", callPresentation: { summary: "writing", card: { card: "diff", diffs: [{ path: "x.md", old_text: "a\n", new_text: "a\nb\n" }] } } }),
    ]);
    expect(changes).toHaveLength(1);
    expect(changes[0].path).toBe("x.md");
    expect(changes[0].added).toBe(2);
  });

  it("ignores non-diff cards and malformed hunks", () => {
    expect(
      recordedChangesFromItems([
        tool({ presentation: { summary: "read", card: { card: "read", path: "a.ts" } } }),
        tool({ presentation: { summary: "bad", card: { card: "diff", diffs: [{ path: 1 }] } } }),
        { kind: "user", id: "u1", text: "hi" } as Item,
      ]),
    ).toEqual([]);
  });

  it("summarizes counts by change and by file", () => {
    const changes = recordedChangesFromItems([
      tool({
        callId: "c3",
        presentation: {
          summary: "two edits to one file",
          card: {
            card: "diff",
            diffs: [
              { path: "src/a.ts", old_text: "one\n", new_text: "two\n", added: 1, removed: 1 },
              { path: "src/a.ts", old_text: "two\n", new_text: "three\n", added: 1, removed: 1 },
            ],
          },
        },
      }),
    ]);
    expect(summarizeRecordedChanges(changes)).toEqual({
      changeCount: 2,
      fileCount: 1,
      additions: 2,
      deletions: 2,
    });
  });
});

describe("diffRowsForChange", () => {
  it("builds numbered rows from the old/new sides", () => {
    const changes = recordedChangesFromItems([
      tool({
        callId: "c4",
        presentation: {
          summary: "edit",
          card: { card: "diff", diffs: [{ path: "a.ts", old_text: "one\ntwo\n", new_text: "one\nTWO\n" }] },
        },
      }),
    ]);
    const rows = diffRowsForChange(changes[0]);
    expect(rows.filter((r) => r.kind === "ctx")).toHaveLength(1);
    expect(rows.find((r) => r.kind === "del")).toMatchObject({ old: 2, text: "two" });
    expect(rows.find((r) => r.kind === "add")).toMatchObject({ new: 2, text: "TWO" });
  });
});

// Found in a review of the Review panel (2026-09-25): absolute paths crowded out the file name,
// the Recorded diff was numbered from 1, and two edits of one file were two cards.
describe("recordedFilesFromChanges", () => {
  const edit = (callId: string, name: string, diff: object, undoable = true) =>
    tool({
      id: callId,
      callId,
      name,
      undoable,
      presentation: { summary: "edit", card: { card: "diff", diffs: [diff] } },
    });

  it("folds every change of one file into one row, naming the tools used", () => {
    const changes = recordedChangesFromItems([
      edit("c1", "replace_in_file", { path: "/w/orders/cart.py", old_text: "a\n", new_text: "b\n", added: 1, removed: 1 }),
      edit("c2", "write_file", { path: "/w/notes.md", old_text: null, new_text: "n\n", added: 1, removed: 0 }),
      edit("c3", "replace_in_file", { path: "/w/orders/cart.py", old_text: "c\n", new_text: "d\ne\n", added: 2, removed: 1 }),
    ]);
    const files = recordedFilesFromChanges(changes, ["/w"]);
    expect(files.map((f) => [f.displayPath, f.added, f.removed, f.tools])).toEqual([
      ["orders/cart.py", 3, 2, "replace_in_file ×2"],
      ["notes.md", 1, 0, "write_file"],
    ]);
    expect(files[0].changes.map((c) => c.callId)).toEqual(["c1", "c3"]);
    expect(files[0].undoable).toBe(true);
  });

  it("reads a path relative to the root that holds it, on either separator", () => {
    const changes = recordedChangesFromItems([
      edit("c1", "write_file", { path: String.raw`C:\Users\me\proj\src\a.ts`, old_text: null, new_text: "x\n" }),
      edit("c2", "write_file", { path: "/elsewhere/b.ts", old_text: null, new_text: "x\n" }),
    ]);
    const files = recordedFilesFromChanges(changes, ["c:/users/me/proj"]);
    expect(files.map((f) => f.displayPath)).toEqual(["src/a.ts", "/elsewhere/b.ts"]);
  });

  it("is undoable only when every change of the file is", () => {
    const changes = recordedChangesFromItems([
      edit("c1", "replace_in_file", { path: "/w/a.py", old_text: "a\n", new_text: "b\n" }),
      edit("c2", "replace_in_file", { path: "/w/a.py", old_text: "b\n", new_text: "c\n" }, false),
    ]);
    expect(recordedFilesFromChanges(changes, ["/w"])[0].undoable).toBe(false);
  });
});

describe("diffRowsForChange numbering", () => {
  it("numbers a context window from where it sits in the file (V6)", () => {
    const changes = recordedChangesFromItems([
      tool({
        callId: "c9",
        presentation: {
          summary: "edit",
          card: {
            card: "diff",
            diffs: [{ path: "a.ts", old_text: "x\nold\ny\n", new_text: "x\nnew\ny\n", old_start: 40, new_start: 41 }],
          },
        },
      }),
    ]);
    const rows = diffRowsForChange(changes[0]);
    expect(rows.find((r) => r.kind === "del")).toMatchObject({ old: 41, text: "old" });
    expect(rows.find((r) => r.kind === "add")).toMatchObject({ new: 42, text: "new" });
  });
});
