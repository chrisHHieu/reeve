import { describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { callSubject, liveLine, useLiveLines } from "./liveLines";
import { LiveRegistry } from "../../live/liveSession";
import type { Item } from "../../types";

const idle = { running: false, blocks: [], items: [] as Item[], compacting: false };
const running = { ...idle, running: true };
const tool = (name: string, args: unknown, status = "…"): Item => ({ kind: "tool", id: "t", name, args, status }) as Item;

describe("liveLine", () => {
  it("says nothing for a conversation that is not running", () => {
    expect(liveLine(idle)).toBeNull();
  });

  it("names the block being written, without the arguments still arriving", () => {
    expect(liveLine({ ...running, blocks: [{ kind: "reasoning", text: "hmm" }] })).toBe("Thinking");
    expect(liveLine({ ...running, blocks: [{ kind: "tool_call", callId: "c", name: "run_shell", argsRaw: '{"command": "npm te' }] })).toBe(
      "Running a command",
    );
    expect(liveLine({ ...running, blocks: [{ kind: "text", text: "Now I" }] })).toBe("Writing");
  });

  it("names the call that is running and what it acts on, once the call is whole", () => {
    expect(liveLine({ ...running, items: [tool("run_shell", { command: "npm test -- --run\necho done" })] })).toBe("Running a command · npm test -- --run");
    expect(liveLine({ ...running, items: [tool("replace_in_file", JSON.stringify({ path: "src/app/App.tsx" }))] })).toBe("Editing files · App.tsx");
    // A settled call is not what it is doing now.
    expect(liveLine({ ...running, items: [tool("read_file", { path: "a.py" }, "ok")] })).toBe("Working");
  });

  it("says compaction while the summarizer runs", () => {
    expect(liveLine({ ...running, compacting: true })).toBe("Compacting context");
  });

  it("reads nothing from an MCP tool's arguments", () => {
    expect(callSubject("mcp__jira__create_issue", { summary: "x" })).toBeNull();
    expect(callSubject("web_fetch", { url: "https://example.com/a/b" })).toBe("example.com");
    expect(callSubject("grep", { path: "src", pattern: "TODO" })).toBe("TODO");
  });
});

describe("useLiveLines", () => {
  it("follows each listed conversation and redraws only when a line changes", () => {
    const registry = new LiveRegistry({ factory: () => ({}) as never });
    const live = registry.ensure("s1");
    let renders = 0;
    const { result } = renderHook(() => {
      renders += 1;
      return useLiveLines(["s1", "s2"], registry);
    });
    expect(result.current).toEqual({});
    act(() => live.setRunning(true));
    expect(result.current).toEqual({ s1: "Working" });
    act(() => live.setItems([tool("run_shell", { command: "pytest -q" })]));
    expect(result.current).toEqual({ s1: "Running a command · pytest -q" });
    const before = renders;
    act(() => live.patch({ usage: { ...live.current.usage } }));
    expect(renders).toBe(before);
    act(() => live.setRunning(false));
    expect(result.current).toEqual({});
  });
});
