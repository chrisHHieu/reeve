// Review by what was asked: the working tree's files grouped by the request whose turn changed
// each last, with the check that turn ran, and what changed outside the conversation apart.
import { describe, expect, it } from "vitest";
import { askChangeDraft, groupByAsk, turnChecks, turnTouches } from "./reviewAsks";
import { mergeChanges } from "./reviewModel";
import type { RecordedChange } from "../../reviewChanges";
import type { Item } from "../../types";

const change = (path: string, turn: number, text: string, over: Partial<RecordedChange> = {}): RecordedChange => ({
  id: `${turn}:${path}`,
  callId: `c-${turn}-${path}`,
  tool: "replace_in_file",
  path,
  status: "modified",
  added: 1,
  removed: 0,
  diffs: [],
  undoable: true,
  turn,
  turnText: text,
  ...over,
});

const files = mergeChanges([
  { path: "upload/client.py", status: "M", add: 31, del: 7 },
  { path: "tests/test_client.py", status: "A", add: 44, del: 0 },
  { path: "README.md", status: "M", add: 6, del: 1 },
  { path: ".env.example", status: "M", add: 1, del: 1 },
]);

const recorded = [
  change("/repo/upload/client.py", 1, "Add retry with backoff\nand tests"),
  change("/repo/tests/test_client.py", 1, "Add retry with backoff\nand tests"),
  change("/repo/README.md", 2, "Explain the retry"),
];

describe("groupByAsk", () => {
  it("groups each file under the request that changed it last, newest first, and keeps the rest apart", () => {
    const { asks, outside } = groupByAsk(files, recorded, ["/repo"]);
    expect(asks.map((a) => a.turn)).toEqual([2, 1]);
    expect(asks[1].text).toBe("Add retry with backoff\nand tests");
    expect(asks[1].files.map((f) => f.path).sort()).toEqual(["tests/test_client.py", "upload/client.py"]);
    expect([asks[1].add, asks[1].del]).toEqual([75, 7]);
    expect(outside.map((f) => f.path)).toEqual([".env.example"]);
  });

  it("puts a file changed again later under the later request, and the earlier one waits to be undone", () => {
    const again = [...recorded, change("/repo/upload/client.py", 2, "Explain the retry")];
    const { asks } = groupByAsk(files, again, ["/repo"]);
    expect(asks[0].files.map((f) => f.path).sort()).toEqual(["README.md", "upload/client.py"]);
    expect(asks[1].files.map((f) => f.path)).toEqual(["tests/test_client.py"]);
    expect(asks[1].blockedBy).toBe(2);
    expect(asks[0].blockedBy).toBeUndefined();
  });

  it("counts a file a command changed, from the turn's receipt, and offers no Undo that would put back only part", () => {
    const items = [
      { kind: "user", text: "Add retry with backoff\nand tests" },
      { kind: "user", text: "Explain the retry" },
      { kind: "changes", created: [], overwritten: [], deleted: [], stats: [{ path: "/repo/.env.example", added: 1, removed: 1 }] },
    ] as unknown as Item[];
    const { asks, outside } = groupByAsk(files, recorded, ["/repo"], new Map(), turnTouches(items));
    expect(outside).toEqual([]);
    const second = asks.find((a) => a.turn === 2)!;
    expect(second.files.map((f) => f.path).sort()).toEqual([".env.example", "README.md"]);
    expect(second.text).toBe("Explain the retry");
    // The README has a saved copy, .env.example (a command's edit) has none: no Undo for the request.
    expect(second.undoable).toBe(false);
    expect(asks.find((a) => a.turn === 1)!.undoable).toBe(true);
  });

  it("offers Undo only when every change of the request has a saved copy", () => {
    const partly = [...recorded.slice(0, 2), change("/repo/README.md", 2, "Explain the retry", { undoable: false })];
    const { asks } = groupByAsk(files, partly, ["/repo"]);
    expect(asks[0].undoable).toBe(false);
    expect(asks[1].undoable).toBe(true);
  });
});

describe("turnChecks", () => {
  const user = (text: string): Item => ({ kind: "user", text }) as Item;
  const receipt = (check?: { ok: boolean; summary: string; command: string; editsSince: number }): Item =>
    ({ kind: "changes", created: [], overwritten: ["a.py"], deleted: [], check }) as unknown as Item;

  it("keeps the check a turn ran after its last edit, under that turn's number", () => {
    const checks = turnChecks([
      user("one"),
      receipt({ ok: true, summary: "14 passed", command: "pytest", editsSince: 0 }),
      user("two"),
      receipt({ ok: true, summary: "3 passed", command: "pytest", editsSince: 2 }),
      user("three"),
      receipt(),
    ]);
    expect(checks.get(1)?.summary).toBe("14 passed");
    // Edits followed the check: it proves nothing about them.
    expect(checks.has(2)).toBe(false);
    expect(checks.has(3)).toBe(false);
  });
});

describe("askChangeDraft", () => {
  it("quotes the request's first line, cut when long, and leaves room to say what to change", () => {
    expect(askChangeDraft("Add retry\nwith tests")).toBe("About “Add retry”: ");
    expect(askChangeDraft("x".repeat(120))).toMatch(/^About “x{89}…”: $/);
    expect(askChangeDraft("")).toBe("");
  });
});
