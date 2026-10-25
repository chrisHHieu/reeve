import { describe, expect, it, vi } from "vitest";
import type { WsEvent } from "../types";
import { LiveSession } from "./liveSession";
import { handleSessionEvent, type SessionEventHost } from "./sessionEvents";

function hostStub(): SessionEventHost & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    factsChanged: (_l, kind) => void calls.push(`facts:${kind}`),
    railStale: () => void calls.push("rail"),
    reloadTranscript: () => void calls.push("reload"),
    engineReplaced: () => void calls.push("engine"),
    turnEnded: () => void calls.push("turn-ended"),
    showsEnvironmentError: () => false,
  };
}

const feed = (live: LiveSession, host: SessionEventHost, frames: Array<[string, Record<string, unknown>?]>) => {
  for (const [type, data] of frames) {
    handleSessionEvent(live, { type, data: data ?? {} } as WsEvent, host);
  }
};

const kinds = (live: LiveSession) => live.current.items.map((i) => i.kind);

describe("a call that returned its own refusal", () => {
  it("is a failed row live, as the log counts it (`is_error`), while `status` stays the dispatch's", () => {
    const live = new LiveSession("s");
    const host = hostStub();
    feed(live, host, [
      ["turn_start", { input: "go" }],
      ["tool_proposed", { name: "message_task", call_id: "c1", arguments: { to: "abc" } }],
      ["tool_finished", { name: "message_task", call_id: "c1", status: "ok", is_error: true, result_preview: "Error: abc is a workflow run" }],
      ["tool_proposed", { name: "message_task", call_id: "c2", arguments: { to: "survey" } }],
      ["tool_finished", { name: "message_task", call_id: "c2", status: "ok", result_preview: "Delivered" }],
    ]);
    const tools = live.current.items.filter((i) => i.kind === "tool") as Array<{ status: string }>;
    expect(tools.map((t) => t.status)).toEqual(["error", "ok"]);
  });
});

describe("a subagent's session, live", () => {
  it("draws the agent's message inside the turn, and a turn it opens as the agent speaking", () => {
    const live = new LiveSession("child");
    const host = hostStub();
    const from = { synthetic: "parent_message", session_id: "p1" };
    feed(live, host, [
      ["turn_start", { input: "survey the repo" }],
      ["steering_queued", { text: "include the cache", source: from }],
      ["steering_queued", { text: "a person's own" }],
      ["turn_done"],
      ["turn_start", { input: "now the tests", source: from }],
    ]);
    const steers = live.current.items.filter((i) => i.kind === "steer") as Array<{ text: string; from?: string }>;
    expect(steers.map((s) => [s.text, s.from])).toEqual([
      ["include the cache", "agent"],
      ["a person's own", undefined],
    ]);
    // Not a "reported back" line: the turn opens with the agent's words, as its brief did.
    expect(kinds(live)).toEqual(["user", "steer", "steer", "user"]);
    expect((live.current.items[3] as { text: string }).text).toBe("now the tests");
  });
});

describe("a turn streamed into a conversation nobody is looking at", () => {
  // The whole point of the pool: these frames arrive while the reader is in another
  // conversation, and what they build must be exactly what the reader would have seen.
  const turn: Array<[string, Record<string, unknown>?]> = [
    ["turn_start", { input: "run the tests" }],
    ["assistant_delta", { index: 0, text: "Let me " }],
    ["assistant_delta", { index: 0, text: "check." }],
    ["tool_proposed", { name: "run_shell", call_id: "c1", arguments: { command: "pytest -q" } }],
    ["tool_output_delta", { call_id: "c1", text: "collecting...\n" }],
    ["tool_output_delta", { call_id: "c1", text: "3 passed\n" }],
    ["tool_finished", { name: "run_shell", call_id: "c1", status: "ok", result_preview: "3 passed", duration_ms: 1200 }],
    ["assistant_message", { text: "All green." }],
    ["turn_done"],
  ];

  it("records the stream while the turn runs", () => {
    const live = new LiveSession("bg");
    const host = hostStub();
    feed(live, host, turn.slice(0, 6));
    expect(live.current.running).toBe(true);
    expect(kinds(live)).toEqual(["user", "tool"]);
    const tool = live.current.items[1] as { liveOutput?: string; status: string };
    expect(tool.status).toBe("…");
    expect(tool.liveOutput).toContain("3 passed");
    // the half-written sentence is held in the live step, not lost
    expect(live.current.blocks).toHaveLength(1);
  });

  it("settles into the same transcript a reload would draw", () => {
    const live = new LiveSession("bg");
    const host = hostStub();
    feed(live, host, turn);
    expect(live.current.running).toBe(false);
    expect(kinds(live)).toEqual(["user", "tool", "assistant"]);
    expect(live.current.blocks).toEqual([]);
    const tool = live.current.items[1] as { status: string; callId?: string };
    expect(tool.status).toBe("ok");
    expect(tool.callId).toBe("c1");
    expect(host.calls).toContain("turn-ended");
  });

  it("does not echo the user's own message twice when the client already showed it", () => {
    const live = new LiveSession("fg");
    live.setItems([{ kind: "user", text: "run the tests" }]);
    feed(live, hostStub(), [["turn_start", { input: "run the tests" }]]);
    expect(kinds(live)).toEqual(["user"]);
  });
});

describe("a turn the engine started", () => {
  it("opens with one line saying what reported back, once", () => {
    const live = new LiveSession("bg");
    const start: [string, Record<string, unknown>] = [
      "turn_start",
      { input: '<task id="c1" state="completed">…</task>', source: { synthetic: "background_task", session_id: "c1" } },
    ];
    feed(live, hostStub(), [start, start, ["assistant_message", { text: "The subagent is done." }], ["turn_done"]]);
    expect(kinds(live)).toEqual(["notice", "assistant"]);
    expect(live.current.items[0]).toMatchObject({ wake: "background_task:c1", text: "A subagent finished and reported back" });
    // the envelope is the model's to read: none of it is drawn
    expect(JSON.stringify(live.current.items)).not.toContain("<task");
  });
});

describe("prompts the agent is blocked on", () => {
  it("raises a card and marks the conversation as waiting on the reader", () => {
    const live = new LiveSession("bg");
    feed(live, hostStub(), [
      ["turn_start", { input: "edit it" }],
      ["tool_proposed", { name: "write_file", call_id: "w1", arguments: { path: "a.py" } }],
      ["permission_required", { name: "write_file", arguments: { path: "a.py" }, reason: "requires approval" }],
    ]);
    expect(kinds(live)).toEqual(["user", "tool", "approval"]);
    expect(live.current.needsAttention).toBe(true);
  });

  it("raises every prompt in its own conversation: there is no mode that sends them elsewhere", () => {
    const live = new LiveSession("bg");
    feed(live, hostStub(), [
      ["permission_required", { name: "write_file", arguments: {}, reason: "" }],
      ["plan_proposed", { plan: "do it" }],
    ]);
    expect(kinds(live)).toEqual(["approval", "planreq"]);
    expect(live.current.needsAttention).toBe(true);
  });
});

describe("facts a view applies later", () => {
  it("records ready and its later moves for the host to apply when the session is shown", () => {
    const live = new LiveSession("bg");
    const host = hostStub();
    feed(live, host, [
      ["ready", { mode: "workspace-write", model: "m1", running: true, compact_at: 1000 }],
      ["mode_changed", { mode: "danger-full-access" }],
      ["model_changed", { model: "m2", text: "Switched model" }],
    ]);
    expect(live.current.running).toBe(true);
    expect(live.facts.ready).toMatchObject({ mode: "danger-full-access", model: "m2", compact_at: 1000 });
    expect(host.calls.filter((c) => c.startsWith("facts:"))).toEqual(["facts:ready", "facts:patch", "facts:patch"]);
  });

  it("keeps the model a fresh session bound, so a later mode move does not bring the default back", () => {
    const live = new LiveSession("draft");
    feed(live, hostStub(), [
      ["ready", { mode: "workspace-write", model: "default-model" }],
      ["model_changed", { model: "claude-pick", text: null }],
      ["mode_changed", { mode: "danger-full-access" }],
    ]);
    expect(live.facts.ready).toMatchObject({ model: "claude-pick", mode: "danger-full-access" });
    // the first bind has no history to mark: no transcript line
    expect(kinds(live)).toEqual([]);
  });

  it("takes server truth about a turn already in flight on connect", () => {
    const live = new LiveSession("bg");
    feed(live, hostStub(), [["ready", { running: true }]]);
    expect(live.current.running).toBe(true);
    feed(live, hostStub(), [["ready", { running: false }]]);
    expect(live.current.running).toBe(false);
  });
});

describe("failure paths", () => {
  it("keeps a half-written answer when the turn is interrupted", () => {
    const live = new LiveSession("bg");
    feed(live, hostStub(), [
      ["turn_start", { input: "write" }],
      ["assistant_delta", { index: 0, text: "Half an ans" }],
      ["interrupted"],
    ]);
    expect(kinds(live)).toEqual(["user", "assistant", "notice"]);
    expect((live.current.items[1] as { text: string }).text).toBe("Half an ans");
    expect(live.current.blocks).toEqual([]);
  });

  it("settles a step still marked running when the turn ends", () => {
    const live = new LiveSession("bg");
    feed(live, hostStub(), [
      ["tool_proposed", { name: "run_shell", call_id: "c9", arguments: {} }],
      ["turn_end", {}],
    ]);
    expect((live.current.items[0] as { status: string }).status).toBe("no result");
  });

  it("hands the optimistic running state back when the message is rejected", () => {
    const live = new LiveSession("bg");
    live.setRunning(true);
    feed(live, hostStub(), [["input_rejected", { error: "already running" }]]);
    expect(live.current.running).toBe(false);
    expect(kinds(live)).toEqual(["notice"]);
  });

  it("says why a stopped turn stopped", () => {
    const live = new LiveSession("bg");
    feed(live, hostStub(), [["turn_end", { status: "max_iterations_exceeded", iterations: 40 }]]);
    expect((live.current.items[0] as { text: string }).text).toMatch(/Stopped after 40 steps/);
  });

  it("reconnects the conversation whose engine was replaced, shown or not", () => {
    const live = new LiveSession("bg");
    const host = hostStub();
    feed(live, host, [["plugins_reloaded", { plugins: 2, errors: 0 }]]);
    expect(host.calls).toContain("engine");
  });

  it("leaves the To-dos plan alone until the call that wrote it lands", () => {
    const live = new LiveSession("bg");
    const todos = [{ content: "a", status: "pending" }];
    feed(live, hostStub(), [["tool_proposed", { name: "todo_write", arguments: { todos } }]]);
    expect(live.current.todo).toEqual([]);
    feed(live, hostStub(), [["tool_finished", { name: "todo_write", status: "ok" }]]);
    expect(live.current.todo).toHaveLength(1);
  });
});

describe("the view is told only what it needs", () => {
  it("refreshes the rail when a file write lands", () => {
    const live = new LiveSession("bg");
    const host = hostStub();
    const spy = vi.spyOn(host, "railStale");
    feed(live, host, [
      ["tool_proposed", { name: "write_file", call_id: "w", arguments: {} }],
      ["tool_finished", { name: "write_file", call_id: "w", status: "ok" }],
    ]);
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe("the pushed jobs roster", () => {
  const job = (id: string, over: Record<string, unknown> = {}) => ({
    id,
    command: "npm run build",
    status: "running",
    started_at: 100,
    exit_code: null,
    ...over,
  });

  it("replaces the conversation's roster with each frame, whole", () => {
    const live = new LiveSession("a");
    const host = hostStub();
    feed(live, host, [["jobs", { jobs: [job("bg-1")] }]]);
    expect(live.current.jobs.map((row) => row.id)).toEqual(["bg-1"]);
    // The next frame is the truth, not a patch: a job that left the list is gone.
    feed(live, host, [["jobs", { jobs: [job("bg-2", { last_line: "ready" })] }]]);
    expect(live.current.jobs).toEqual([job("bg-2", { last_line: "ready" })]);
    feed(live, host, [["jobs", { jobs: [] }]]);
    expect(live.current.jobs).toEqual([]);
  });

  it("lands in a conversation nobody is looking at, and tells the view nothing else", () => {
    const live = new LiveSession("background");
    const host = hostStub();
    feed(live, host, [["jobs", { jobs: [job("bg-1")] }]]);
    expect(live.current.jobs).toHaveLength(1);
    expect(host.calls).toEqual([]);
  });

  it("ignores a frame that carries no list", () => {
    const live = new LiveSession("a");
    const host = hostStub();
    feed(live, host, [["jobs", { jobs: [job("bg-1")] }], ["jobs", {}], ["jobs", { jobs: "nope" }]]);
    expect(live.current.jobs.map((row) => row.id)).toEqual(["bg-1"]);
  });
});

describe("the model of each step, live", () => {
  // A step's assistant_message comes before the calls it made (main_loop), so its model is
  // known when they are proposed; the answer's footer counts both.
  it("stamps the step's calls and its answer with the model its usage names", () => {
    const live = new LiveSession("models");
    const host = hostStub();
    const usage = (model: string) => ({ model, input: 1, output: 1, cache_read: 0, cache_write: 0 });
    feed(live, host, [
      ["turn_start", { input: "run the tests" }],
      ["assistant_message", { text: "", tool_calls: ["run_shell"], usage: usage("anthropic:claude-haiku-5-5") }],
      ["tool_proposed", { name: "run_shell", call_id: "c1", arguments: { command: "pytest -q" } }],
      ["tool_finished", { name: "run_shell", call_id: "c1", status: "ok", result_preview: "3 passed" }],
      ["assistant_message", { text: "All green.", usage: usage("google:gemini-3.8-flash") }],
      ["turn_done"],
    ]);
    const items = live.current.items as Array<{ kind: string; model?: string }>;
    expect(items.find((i) => i.kind === "tool")?.model).toBe("anthropic:claude-haiku-5-5");
    expect(items.find((i) => i.kind === "assistant")?.model).toBe("google:gemini-3.8-flash");
  });
});

describe("a batch of calls written in one message", () => {
  // The engine proposes (screens) every call of a message before the first one runs, and runs
  // writes one after another; `tool_started` is the moment each is dispatched.
  const batch: Array<[string, Record<string, unknown>?]> = [
    ["turn_start", { input: "fix the four files" }],
    ["tool_proposed", { name: "edit_file", call_id: "e1", arguments: { path: "a.py" } }],
    ["tool_proposed", { name: "edit_file", call_id: "e2", arguments: { path: "b.py" } }],
    ["tool_started", { name: "edit_file", call_id: "e1" }],
  ];
  const tools = (live: LiveSession) =>
    live.current.items.filter((i) => i.kind === "tool") as Array<{ callId?: string; status: string; queued?: boolean }>;

  it("holds every proposed call as next in line until it is dispatched", () => {
    const live = new LiveSession("b");
    const host = hostStub();
    feed(live, host, batch.slice(0, 3));
    expect(tools(live).map((t) => t.queued)).toEqual([true, true]);
    feed(live, host, batch.slice(3));
    expect(tools(live).map((t) => !!t.queued)).toEqual([false, true]);
  });

  it("settles the first call while the second is still next in line", () => {
    const live = new LiveSession("b");
    const host = hostStub();
    feed(live, host, [
      ...batch,
      ["tool_finished", { name: "edit_file", call_id: "e1", status: "ok", result_preview: "edited" }],
    ]);
    expect(tools(live).map((t) => [t.status, !!t.queued])).toEqual([
      ["ok", false],
      ["…", true],
    ]);
  });

  it("takes streamed output as proof a call runs, even without its start frame", () => {
    const live = new LiveSession("b");
    const host = hostStub();
    feed(live, host, [
      ["turn_start", { input: "run it" }],
      ["tool_proposed", { name: "run_shell", call_id: "c9", arguments: { command: "ls" } }],
      ["tool_output_delta", { call_id: "c9", text: "a.py\n" }],
    ]);
    expect(tools(live)[0].queued).toBeFalsy();
  });

  it("does not queue a program's sub-call, which is never announced as started", () => {
    // `run_code` proposes its sub-calls from inside the program and reports them finished
    // (`program_calls.py`): there is no `tool_started` to clear a queued mark, so the row would
    // read "waiting to run" until the call ended.
    const live = new LiveSession("b");
    const host = hostStub();
    feed(live, host, [
      ["turn_start", { input: "run it" }],
      ["tool_proposed", { name: "edit_file", call_id: "p1", in_program: true, arguments: { path: "a.py" } }],
    ]);
    expect(tools(live).map((t) => [t.callId, t.queued])).toEqual([["p1", undefined]]);
  });
});
