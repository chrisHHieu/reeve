import { describe, expect, it } from "vitest";
import { addTokens, addTurnUsage, emptyUsage, formatTokens, reconcileBreakdown, stepTokens, totalTokens, turnTokens, usageFromMessages, withContext } from "./usage";

const MODEL = "anthropic:claude-fable-5";
const sidecar = (over: Record<string, unknown> = {}) => ({ model: MODEL, input: 100, output: 20, cache_read: 50, cache_write: 10, ...over });
const fold = (...sidecars: unknown[]) => sidecars.reduce(addTurnUsage, emptyUsage());

describe("addTurnUsage", () => {
  it("sums each model's spend, and keeps the latest request's size", () => {
    const u = fold(sidecar(), sidecar({ input: 40, output: 5, cache_read: 200, cache_write: 0 }));
    expect(u.byModel[MODEL]).toEqual({ model: MODEL, input: 140, output: 25, cache_read: 250, cache_write: 10 });
    expect(u.prompt).toBe(240); // the second request's input classes
    expect(u.context).toBe(245); // and its output, which the next request carries
  });

  it("keeps a model switched to mid-session apart", () => {
    expect(Object.keys(fold(sidecar(), sidecar({ model: "gpt-5.5", input: 7 })).byModel).sort()).toEqual([MODEL, "gpt-5.5"]);
  });

  it("reads anything that is not a count as zero, and anything not a sidecar as nothing", () => {
    expect(fold(null)).toEqual(emptyUsage());
    expect(fold("x")).toEqual(emptyUsage());
    const u = fold(sidecar({ input: -5, output: "9" }));
    expect(u.byModel[MODEL]).toMatchObject({ input: 0, output: 9 });
  });

  it("puts a sidecar with no model under 'unknown'", () => {
    expect(fold(sidecar({ model: undefined })).byModel.unknown.input).toBe(100);
  });

  it("keeps the latest request's breakdown, its zero parts left out", () => {
    let u = fold(sidecar({ context_breakdown: { messages: 600, mcp_tools: 100, system_tools: 0, skills: 50 } }));
    expect(u.breakdown).toEqual({ messages: 600, mcp_tools: 100, skills: 50 });
    u = addTurnUsage(u, sidecar({ context_breakdown: { messages: 900 } }));
    expect(u.breakdown).toEqual({ messages: 900 });
  });

  it("keeps the instruction files as a part of their own", () => {
    expect(fold(sidecar({ context_breakdown: { messages: 600, instructions: 4000, system_prompt: 1500 } })).breakdown).toEqual({
      messages: 600,
      instructions: 4000,
      system_prompt: 1500,
    });
  });

  it.each([undefined, {}, "x"])("has no breakdown when the server sent %j", (breakdown) => {
    expect(fold(sidecar({ context_breakdown: breakdown })).breakdown).toBeUndefined();
  });
});

describe("usageFromMessages", () => {
  it("adds up the assistant messages' sidecars and nothing else", () => {
    const u = usageFromMessages([
      { role: "user", content: "hi" },
      { role: "assistant", content: "a", usage: sidecar() as any },
      { role: "tool", content: "r", usage: sidecar() as any },
      { role: "assistant", content: "b" },
      { role: "assistant", content: "c", usage: sidecar({ input: 300 }) as any },
    ]);
    expect(u.byModel[MODEL].input).toBe(400);
    expect(u.prompt).toBe(360);
    expect(u.context).toBe(380);
  });
});

describe("withContext", () => {
  it("takes the engine's size after a compaction and leaves the spend alone", () => {
    const u = { ...fold(sidecar()), breakdown: { messages: 600 } };
    const next = withContext(u, 30_000);
    expect(next.context).toBe(30_000);
    expect(next.byModel).toEqual(u.byModel);
    expect(next.prompt).toBe(u.prompt);
    expect(next.breakdown).toEqual({ messages: 600 });
  });

  it.each([undefined, 0, -5])("keeps the usage as it was for a size of %j", (size) => {
    const u = fold(sidecar());
    expect(withContext(u, size)).toBe(u);
  });
});

describe("reconcileBreakdown", () => {
  it("scales parts that add up past the prompt down to it", () => {
    expect(reconcileBreakdown({ messages: 150, system_prompt: 50 }, 100)).toEqual({ values: { messages: 75, system_prompt: 25 }, other: 0 });
  });

  it("calls what the parts leave of the prompt 'other'", () => {
    expect(reconcileBreakdown({ messages: 60, system_tools: 20 }, 100)).toEqual({ values: { messages: 60, system_tools: 20 }, other: 20 });
  });

  it("has nothing to say without parts or a prompt", () => {
    expect(reconcileBreakdown(undefined, 100)).toBeUndefined();
    expect(reconcileBreakdown({ messages: 10 }, 0)).toBeUndefined();
  });
});

describe("step spend", () => {
  it("reads a step's sidecar, and nothing from one that spent nothing", () => {
    expect(stepTokens(sidecar())).toEqual({ input: 100, output: 20, cache_read: 50, cache_write: 10 });
    expect(stepTokens({ model: MODEL })).toBeUndefined();
    expect(stepTokens(null)).toBeUndefined();
  });

  it("adds a turn's steps, skipping the items that carry none", () => {
    const step = { input: 1, output: 2, cache_read: 3, cache_write: 4 };
    expect(turnTokens([{ tokens: step }, { kind: "user" }, { tokens: step }])).toEqual({ input: 2, output: 4, cache_read: 6, cache_write: 8 });
    expect(turnTokens([{ kind: "user" }])).toBeUndefined();
    expect(addTokens(undefined, step)).toBe(step);
  });
});

describe("totalTokens and formatTokens", () => {
  it("totals every model and class", () => {
    expect(totalTokens(fold(sidecar(), sidecar({ model: "gpt-5.5" })))).toBe(360);
  });

  it.each([
    [0, "0"],
    [980, "980"],
    [10_000, "10k"],
    [12_400, "12.4k"],
    [982_000, "982k"],
    [1_240_000, "1.24M"],
    [2_000_000, "2M"],
    [NaN, "0"],
  ])("writes %d as %s", (n, text) => expect(formatTokens(n)).toBe(text));
});
