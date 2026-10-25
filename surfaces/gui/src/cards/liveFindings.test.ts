import { describe, expect, it } from "vitest";
import { narrowDiffs } from "./cardModels";
import { rowSpec } from "./registry";
import type { ToolItem } from "./cardModels";

// Both found on the live app (2026-09-25): the engine sent the right cards, and the GUI
// dropped a field of each on the way to the screen.
describe("fields that must survive to the screen", () => {
  it("keeps a hunk's window start through narrowing", () => {
    const [hunk] = narrowDiffs([
      { path: "a.py", old_text: "x", new_text: "y", added: 1, removed: 1, old_start: 4, new_start: 4 },
    ])!;
    expect([hunk.old_start, hunk.new_start]).toEqual([4, 4]);
  });

  it("draws what a run_code program printed under the program", () => {
    const item = {
      kind: "tool",
      id: "t",
      name: "run_code",
      args: { code: "print(len(lines))" },
      status: "ok",
      presentation: { summary: "Count lines", card: { card: "terminal", output: "10", exit_code: 0 } },
    } as unknown as ToolItem;
    const spec = rowSpec(item);
    expect(spec.code?.text).toBe("print(len(lines))");
    expect(spec.code?.output).toBe("10");
  });
});
