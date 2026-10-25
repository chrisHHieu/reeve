import { describe, expect, it } from "vitest";
import { appendToolOutput, updateLastTool, updateToolMetadata } from "./toolRows";
import type { Item } from "./types";

const pending = (name: string, callId?: string): Item => ({
  kind: "tool",
  id: callId || name,
  name,
  args: {},
  status: "…",
  ...(callId ? { callId } : {}),
});

describe("pairing a finish to the row that opened it", () => {
  it("gives each concurrent call of one name its OWN result", () => {
    // The engine runs reads/searches concurrently, so all four are pending at once and
    // the results come back in whatever order they finish.
    let items: Item[] = [pending("query", "c1"), pending("query", "c2"), pending("query", "c3")];
    items = updateLastTool(items, { name: "query", status: "ok", preview: "second", callId: "c2" });
    items = updateLastTool(items, { name: "query", status: "ok", preview: "first", callId: "c1" });
    items = updateLastTool(items, { name: "query", status: "ok", preview: "third", callId: "c3" });
    expect(items.map((it) => (it.kind === "tool" ? it.preview : null))).toEqual([
      "first",
      "second",
      "third",
    ]);
  });

  it("falls back to the name when a server sends no call id", () => {
    const items = updateLastTool([pending("grep")], { name: "grep", status: "ok", preview: "hit" });
    expect(items[0].kind === "tool" && items[0].status).toBe("ok");
    expect(items[0].kind === "tool" && items[0].preview).toBe("hit");
  });

  it("leaves a settled row alone", () => {
    const done: Item = { kind: "tool", id: "t", name: "grep", args: {}, status: "ok", preview: "kept" };
    const items = updateLastTool([done], { name: "grep", status: "error", preview: "late" });
    expect(items[0].kind === "tool" && items[0].preview).toBe("kept");
  });

  it("carries model-visible media onto the row that produced it", () => {
    // A live screenshot/`read_image` arrives on `tool_finished`; the row must wear it, or the
    // thumbnail only appeared after reloading the session.
    const image = { kind: "image" as const, name: "screen", data_url: "data:image/png;base64,AAA" };
    const items = updateLastTool([pending("read_image", "c1")], {
      name: "read_image",
      status: "ok",
      callId: "c1",
      attachments: [image],
    });
    expect(items[0].kind === "tool" && items[0].attachments).toEqual([image]);
  });
});

describe("streaming a running call's output", () => {
  it("appends chunk by chunk to the call that is still running", () => {
    let items: Item[] = [pending("run_shell", "c1"), pending("run_shell", "c2")];
    items = appendToolOutput(items, "c2", "building\n");
    items = appendToolOutput(items, "c2", "done\n");
    items = appendToolOutput(items, "c1", "other\n");
    expect(items.map((it) => (it.kind === "tool" ? it.liveOutput : null))).toEqual([
      "other\n",
      "building\ndone\n",
    ]);
  });

  it("drops a chunk that arrives after the call settled", () => {
    // The result card replaced the live text: merging here would show output the tool has
    // already summarised, and hand React a new array for a render that changes nothing.
    const settled: Item = {
      kind: "tool",
      id: "t",
      name: "run_shell",
      args: {},
      status: "ok",
      callId: "c1",
    };
    const items = [settled];
    expect(appendToolOutput(items, "c1", "late\n")).toBe(items);
  });

  it("ignores an empty chunk, and a chunk nobody is waiting for", () => {
    const items: Item[] = [pending("run_shell", "c1")];
    expect(appendToolOutput(items, "c1", "")).toBe(items);
    expect(appendToolOutput(items, "nope", "x")).toBe(items);
  });

  it("still finds the call when an older server sends no id", () => {
    const items = appendToolOutput([pending("run_shell")], undefined, "line\n");
    expect(items[0].kind === "tool" && items[0].liveOutput).toBe("line\n");
  });
});

describe("the parent's tool_metadata frames", () => {
  it("grows the running row's view of the child frame by frame", () => {
    let items: Item[] = [pending("task", "sub-1")];
    // First frame: the child session id, before anything has run.
    items = updateToolMetadata(items, "sub-1", { session_id: "child-1", toolcalls: 0 });
    expect(items[0].kind === "tool" && items[0].subagent).toEqual({
      sessionId: "child-1",
      toolcalls: 0,
    });
    // Later frames add the latest tool without dropping the id.
    items = updateToolMetadata(items, "sub-1", {
      session_id: "child-1",
      tool: "grep",
      title: "max_iterations",
      toolcalls: 1,
      phase: "start",
    });
    expect(items[0].kind === "tool" && items[0].subagent).toEqual({
      sessionId: "child-1",
      tool: "grep",
      title: "max_iterations",
      toolcalls: 1,
      phase: "start",
    });
  });

  it("keeps what the child is doing between its tools, and what it has spent so far", () => {
    let items: Item[] = [pending("task", "sub-1")];
    items = updateToolMetadata(items, "sub-1", { session_id: "child-1", tool: "grep", phase: "start" });
    items = updateToolMetadata(items, "sub-1", { session_id: "child-1", tokens: 4200 });
    items = updateToolMetadata(items, "sub-1", { session_id: "child-1", phase: "thinking" });
    // The tool it last ran is still known; the PHASE is the newest word.
    expect(items[0].kind === "tool" && items[0].subagent).toEqual({
      sessionId: "child-1",
      tool: "grep",
      phase: "thinking",
      tokens: 4200,
    });
    items = updateToolMetadata(items, "sub-1", { session_id: "child-1", phase: "writing", tokens: 9100 });
    expect(items[0].kind === "tool" && items[0].subagent?.phase).toBe("writing");
    expect(items[0].kind === "tool" && items[0].subagent?.tokens).toBe(9100);
    // A word this client does not know, and a figure that is not one, change nothing.
    const before = items;
    items = updateToolMetadata(items, "sub-1", { phase: "dreaming", tokens: -5 });
    expect(items).toBe(before);
  });

  it("lands on the row whose call id matches, leaving other running calls alone", () => {
    const items = updateToolMetadata(
      [pending("task", "sub-1"), pending("task", "sub-2")],
      "sub-2",
      { session_id: "child-2", toolcalls: 1 },
    );
    expect(items[0].kind === "tool" && items[0].subagent).toBeUndefined();
    expect(items[1].kind === "tool" && items[1].subagent?.sessionId).toBe("child-2");
  });

  it("never settles a row — a late frame after the result is a no-op", () => {
    const done: Item = {
      kind: "tool",
      id: "t",
      name: "task",
      args: {},
      status: "ok",
      callId: "sub-1",
    };
    expect(updateToolMetadata([done], "sub-1", { session_id: "child-1", toolcalls: 9 })).toEqual([
      done,
    ]);
  });

  it("keeps feeding a SETTLED background child its frames (the child outlives the call)", () => {
    // A `background=true` spawn returns immediately, so the row settles while the child
    // works on. The manager's later frames carry the child's real progress and its
    // settle (`ended_at`/`duration_ms`) — dropping them froze the card at spawn time.
    const returned: Item = {
      kind: "tool",
      id: "t",
      name: "task",
      args: {},
      status: "ok",
      callId: "sub-9",
      subagent: { sessionId: "child-9", background: true, toolcalls: 0 },
    };
    const items = updateToolMetadata([returned], "sub-9", {
      session_id: "child-9",
      tool: "run_shell",
      title: "sleep 8",
      toolcalls: 1,
      ended_at: 1_700_000_100,
      duration_ms: 8_000,
    });
    expect(items[0].kind === "tool" && items[0].subagent).toMatchObject({
      sessionId: "child-9",
      background: true,
      tool: "run_shell",
      toolcalls: 1,
      endedAt: 1_700_000_100,
      durationMs: 8_000,
    });
  });
});
