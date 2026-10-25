// The `workflow` row: running shows live narration; settled falls back to the script
// block plus the returned JSON (the result preview).

import { describe, expect, it } from "vitest";
import type { ToolItem } from "./cardModels";
import { rowSpec } from "./registry";

const item = (
  over: Partial<Extract<ToolItem, { kind: "tool" }>> = {},
): ToolItem =>
  ({
    kind: "tool",
    id: "c1",
    name: "workflow",
    args: { script: "return agent('x')", meta: { name: "audit-repo" } },
    status: "…",
    ...over,
  }) as ToolItem;

describe("the workflow tool row", () => {
  it("draws a running workflow as a live terminal headed by its name", () => {
    const spec = rowSpec(item({ liveOutput: "▸ gather\n  found 3 files\n" }));
    expect(spec.terminal?.running).toBe(true);
    expect(spec.terminal?.command).toBe("audit-repo (workflow)");
    expect(spec.terminal?.output).toContain("gather");
  });

  it("settles into the script block without the live terminal", () => {
    const spec = rowSpec(item({ status: "ok" }));
    expect(spec.terminal).toBeUndefined();
    expect(spec.code?.text).toContain("return agent('x')");
  });
});

describe("a workflow that went to the background", () => {
  const background = (subagent: Record<string, unknown>) =>
    item({ status: "ok", subagent: { background: true, startedAt: 1, ...subagent } });

  it("stays running after its call returned, and says what the run is on", () => {
    expect(rowSpec(background({})).state).toBe("running");
    expect(rowSpec(background({})).summarySuffix).toBe("in the background");
    const phase = rowSpec(background({ tool: "workflow", title: "verify" }));
    expect([phase.state, phase.summarySuffix]).toEqual(["running", "verify"]);
    expect(rowSpec(background({ tool: "task", title: "first" })).summarySuffix).toBe("task first");
  });

  it("settles on the run's settle frame, not on the call's return", () => {
    const spec = rowSpec(background({ endedAt: 9 }));
    expect([spec.state, spec.summarySuffix]).toEqual(["ok", "ran in the background"]);
  });
});
