import { describe, expect, it } from "vitest";
import { itemsFromMessages } from "./itemsFromMessages";

// A stored session replayed as transcript items: each case is what a reopened session must draw.

const replay = (messages: object[]) => itemsFromMessages(messages as any);
const kinds = (messages: object[]) => replay(messages).map((item) => item.kind);
const call = (id: string, name = "grep") => ({ id, function: { name, arguments: "{}" } });

describe("itemsFromMessages privacy counts", () => {
  it("puts the `_display` count of hidden results on its own call's row, and nowhere in the result", () => {
    const tools = replay([
      { role: "user", content: "check my mail" },
      { role: "assistant", content: "", tool_calls: [call("t1", "gmail_search_messages"), call("t2", "gmail_get_message")] },
      { role: "tool", tool_call_id: "t1", content: '{"ok": true, "data": {"messages": []}}', _display: { hidden_by_filters: 2, connector: "gmail" } },
      { role: "tool", tool_call_id: "t2", content: '{"ok": true}' },
    ]).filter((item) => item.kind === "tool") as any[];
    expect(tools.map((t) => t.hidden)).toEqual([2, undefined]);
    expect(tools[0].preview).not.toContain("hidden");
  });
});

describe("itemsFromMessages steered messages", () => {
  it("replays a `_steer` message as an in-turn note, not a turn-opening bubble", () => {
    const items = itemsFromMessages([
      { role: "user", content: "do the thing" },
      { role: "assistant", content: "working" },
      { role: "user", content: "tôi là ai?", _steer: true, ts: 1752969720 },
      { role: "assistant", content: "Anh là Nguyễn Hoàng Hiếu." },
    ] as any);

    expect(items).toEqual([
      { kind: "user", text: "do the thing" },
      { kind: "assistant", text: "working" },
      { kind: "steer", text: "tôi là ai?", ts: 1752969720 },
      { kind: "assistant", text: "Anh là Nguyễn Hoàng Hiếu." },
    ]);
  });

  it("prefers the `_display` text when a steered force-run carried one", () => {
    const items = itemsFromMessages([
      {
        role: "user",
        content: 'Use the skill "review"…',
        _steer: true,
        _display: "/review the diff",
      },
    ] as any);
    expect(items).toEqual([{ kind: "steer", text: "/review the diff" }]);
  });

  it("in a subagent's session, says the agent that started it sent the message", () => {
    const from = { synthetic: "parent_message", session_id: "p1" };
    const items = itemsFromMessages([
      { role: "user", content: "survey the repo" },
      { role: "assistant", content: "reading" },
      // Read inside the running turn: the steer note, labelled as the agent's.
      { role: "user", content: "include the cache", _steer: true, source: from },
      { role: "assistant", content: "done" },
      // One the turn missed opened a turn of its own: the agent speaking, like its brief, and
      // never a "reported back" line.
      { role: "user", content: "now the tests", source: from },
      { role: "assistant", content: "tests read" },
    ] as any);
    expect(items).toEqual([
      { kind: "user", text: "survey the repo" },
      { kind: "assistant", text: "reading" },
      { kind: "steer", text: "include the cache", from: "agent" },
      { kind: "assistant", text: "done" },
      { kind: "user", text: "now the tests" },
      { kind: "assistant", text: "tests read" },
    ]);
  });
});

describe("itemsFromMessages failed calls", () => {
  it("replays a call that returned its own refusal as failed (`_failed`), the rest as ok", () => {
    const call = (id: string) => ({ id, function: { name: "message_task", arguments: "{}" } });
    const items = itemsFromMessages([
      { role: "assistant", content: "", tool_calls: [call("a"), call("b")] },
      { role: "tool", tool_call_id: "a", content: "Error: abc is a workflow run", _failed: true },
      { role: "tool", tool_call_id: "b", content: "Delivered" },
    ] as any);
    const tools = items.filter((i) => i.kind === "tool") as Array<{ status: string }>;
    expect(tools.map((t) => t.status)).toEqual(["error", "ok"]);
  });
});

describe("itemsFromMessages stamps", () => {
  it("keeps the server's stamp on what has one, and gives none to what was saved before stamps", () => {
    expect(
      replay([
        { role: "user", content: "hi", ts: 1752969720 },
        { role: "assistant", content: "hello", ts: 1752969724 },
        { role: "user", content: "old message" },
      ]),
    ).toEqual([
      { kind: "user", text: "hi", ts: 1752969720 },
      { kind: "assistant", text: "hello", ts: 1752969724 },
      { kind: "user", text: "old message" },
    ]);
  });
});

describe("itemsFromMessages notices", () => {
  it("draws an interruption and an error as they were drawn live; only the error offers Retry", () => {
    expect(
      replay([
        { role: "user", content: "hi" },
        { role: "assistant", content: "partial ans" },
        { role: "notice", kind: "interrupted", ts: 1752969720 },
        { role: "user", content: "again" },
        { role: "notice", kind: "error", text: "model down", ts: 1752969724 },
      ]),
    ).toEqual([
      { kind: "user", text: "hi" },
      { kind: "assistant", text: "partial ans" },
      { kind: "notice", tone: "warn", text: "Stopped." },
      { kind: "user", text: "again" },
      { kind: "notice", tone: "warn", text: "Error: model down", retriable: true },
    ]);
  });

  // Each of these fell to the error line once, and replayed with a Retry button.
  it.each([
    // No longer written since 2026-09-10; the ones on disk must not draw either.
    ["an old session's tools_changed", { kind: "tools_changed", text: "Tools available to the agent changed: +mcp__upwork__upwork__find_jobs" }],
    // Not a line of the chat (owner, 2026-10-13).
    ["a change of model", { kind: "model_switch", text: "Model switched to Kimi K2.6 · Moonshot" }],
    ["a change of thinking level", { kind: "thinking_switch", text: "Thinking set to high" }],
  ])("draws nothing for %s", (_what, notice) => {
    expect(kinds([{ role: "user", content: "hi" }, { role: "notice", ...notice }])).toEqual(["user"]);
  });

  it("draws a compaction as the divider", () => {
    expect(replay([{ role: "notice", kind: "compacted", text: "Context compacted — earlier turns were summarized" }])).toEqual([
      { kind: "notice", tone: "info", text: "Compacted", divider: true },
    ]);
  });

  it("puts the summary and what was freed under the divider", () => {
    expect(replay([{ role: "notice", kind: "compacted", text: "Context compacted", summary: "kept: the failing tests", saved_tokens: 48_200 }])[0]).toMatchObject({
      kind: "notice",
      tone: "info",
      divider: true,
      freed: "48.2k freed",
      detail: "kept: the failing tests",
    });
  });
});

describe("itemsFromMessages goal notices", () => {
  it("replays a goal outcome as a quiet info line, never as a retriable error", () => {
    const items = itemsFromMessages([
      {
        role: "notice",
        kind: "goal",
        text: "Goal paused: the round limit was reached (256 of 256).",
        status: "blocked",
        rounds: 256,
        max_rounds: 256,
      },
    ] as any);
    expect(items[0]).toEqual({
      kind: "notice",
      tone: "info",
      text: "Goal paused: the round limit was reached (256 of 256).",
    });
    // The catch-all would have rendered this as "Error: …" with a Retry button.
    expect((items[0] as any).retriable).toBeUndefined();
  });
});

describe("itemsFromMessages synthetic messages", () => {
  it("renders no bubble for the engine's injected background-task envelope", () => {
    // A finished background child's `<task …>` envelope goes in as a normal
    // user-role turn so the MODEL reads it, with a `source.synthetic` sidecar. The person
    // never wrote it, so the transcript must not show it as their bubble (or at all).
    const items = itemsFromMessages([
      { role: "user", content: "hi" },
      {
        role: "user",
        content: '<task id="child-1" state="completed">\n<task_result>done</task_result>\n</task>',
        source: { synthetic: "background_task", session_id: "child-1" },
      },
      { role: "assistant", content: "The background task finished." },
    ] as any);

    expect(items.some((i) => i.kind === "user" && i.text.includes("<task"))).toBe(false);
    // What stands in its place is one line saying what reported back: it is the boundary of
    // the turn the envelope starts, which was otherwise stacked onto the turn before it.
    expect(items.map((i) => i.kind)).toEqual(["user", "notice", "assistant"]);
    expect(items[1]).toMatchObject({ wake: "background_task:child-1", text: "A subagent finished and reported back" });
    expect(JSON.stringify(items[1])).not.toContain("task_result");
  });

  it("keeps a report steered into a running turn inside that turn", () => {
    // A busy session gets the envelope spliced into its live turn (`_steer`). A boundary
    // there would cut one turn into two groups with two clocks.
    const items = itemsFromMessages([
      { role: "user", content: "go" },
      { role: "user", content: "<task …/>", _steer: true, source: { synthetic: "background_task", session_id: "c2" } },
      { role: "assistant", content: "Carrying on." },
    ] as any);
    expect(items[1]).toMatchObject({ kind: "notice", inline: true, wake: "background_task:c2" });
  });

  it("says a background command reported back, and when", () => {
    const items = itemsFromMessages([
      { role: "user", content: "[background task t9 finished — exit 0]", ts: 1234, source: { synthetic: "background_shell", task_id: "t9" } },
    ] as any);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      kind: "notice",
      tone: "info",
      text: "A background command reported back",
      wake: "background_shell:t9",
      ts: 1234,
    });
  });
});

describe("itemsFromMessages call duration", () => {
  it("gives a replayed row the call's own wall time, from the result's timing", () => {
    // The live frame carries `duration_ms`; a reopened session reads the same number from the
    // stamps on the stored result, so a row says "4.2s" before and after a reload.
    const items = itemsFromMessages([
      { role: "user", content: "go" },
      {
        role: "assistant",
        content: "",
        tool_calls: [{ id: "c1", function: { name: "run_shell", arguments: '{"command":"pytest -q"}' } }],
      },
      { role: "tool", tool_call_id: "c1", content: "ok", ts: 1010, timing: { started: 1000.5, ended: 1004.7 } },
    ] as any);
    expect(items.find((item) => item.kind === "tool")).toMatchObject({ durationMs: 4200 });
  });

  it("says nothing when the result carries no timing", () => {
    const items = itemsFromMessages([
      { role: "assistant", content: "", tool_calls: [{ id: "c1", function: { name: "grep", arguments: "{}" } }] },
      { role: "tool", tool_call_id: "c1", content: "ok" },
    ] as any);
    expect(items.find((item) => item.kind === "tool")).not.toHaveProperty("durationMs");
  });
});

describe("itemsFromMessages reasoning", () => {
  it("keeps the reasoning on its answer, and draws a message that only thought", () => {
    const items = replay([
      { role: "user", content: "hi" },
      { role: "assistant", content: "answer", reasoning: "let me think" },
      { role: "assistant", content: "", reasoning: "stopped mid-thought" },
    ]);
    expect(items.slice(1)).toEqual([
      { kind: "assistant", text: "answer", reasoning: "let me think" },
      { kind: "assistant", text: "", reasoning: "stopped mid-thought" },
    ]);
  });
});

describe("itemsFromMessages quiet lines", () => {
  it("draws a server that would not start as one line with the error behind it, and no Retry", () => {
    // Stored before the `server` field: the name comes from the text, and the old pointer to
    // Settings goes, since the line's button is that way now.
    expect(replay([{ role: "notice", kind: "mcp_error", text: "MCP server “sales-db” failed to start — see Settings ▸ Connectors" }])).toEqual([
      { kind: "notice", tone: "warn", text: "MCP server “sales-db” didn’t start — its tools are unavailable here", server: "sales-db", detail: "MCP server “sales-db” failed to start" },
    ]);
  });

  it("keeps an MCP error with no server name in it as it was written", () => {
    expect(replay([{ role: "notice", kind: "mcp_error", text: "something opaque went wrong" }])).toEqual([{ kind: "notice", tone: "warn", text: "something opaque went wrong" }]);
  });

  it("draws a folder's project memory as an info line inside its turn", () => {
    const text = "“notes” already has project memory (3 entries) — bind it by name or start a session there to use it.";
    expect(replay([{ role: "notice", kind: "project_presence", text }])).toEqual([{ kind: "notice", tone: "info", text, inline: true }]);
  });
});

describe("itemsFromMessages revert boundary", () => {
  it("inserts the undo divider at the canonical boundary, keeping every message visible", () => {
    const items = itemsFromMessages(
      [
        { role: "system", content: "be helpful" },
        { role: "user", content: "first" },
        { role: "assistant", content: "answer one" },
        { role: "user", content: "second" },
        { role: "assistant", content: "answer two" },
      ] as any,
      { boundary: 3, staged_at: 5 },
    );

    expect(items.map((i) => i.kind)).toEqual([
      "user",
      "assistant",
      "notice",
      "user",
      "assistant",
    ]);
    expect(items[2]).toEqual({
      kind: "notice",
      tone: "info",
      boundary: "revert",
      hidden: 2,
      text: "Reverted — the next 2 messages are hidden from the model",
    });
    // The hidden span is still in the transcript (the marker is additive).
    expect(items[3]).toMatchObject({ kind: "user", text: "second" });
  });

  it("counts a single hidden message with the singular line", () => {
    const items = itemsFromMessages(
      [
        { role: "user", content: "first" },
        { role: "assistant", content: "answer one" },
      ] as any,
      { boundary: 1, staged_at: 2 },
    );
    expect(items[1]).toMatchObject({
      boundary: "revert",
      hidden: 1,
      text: "Reverted — the next message is hidden from the model",
    });
  });

  it("renders no boundary without a marker", () => {
    const items = itemsFromMessages(
      [{ role: "user", content: "hi" }, { role: "assistant", content: "hello" }] as any,
      null,
    );
    expect(items.some((i) => i.kind === "notice")).toBe(false);
  });
});

it("does not draw a divider for a prune-only pass", () => {
  const items = itemsFromMessages([
    {
      role: "notice",
      kind: "compacted",
      reason: "pruned",
      text: "Older tool output was shortened to free up context",
      saved_tokens: 804,
    },
  ] as any);

  expect(items).toHaveLength(0);
});

describe("itemsFromMessages discovery bridge", () => {
  // GUI tools review, B3: history keeps the model's own `tool_call{name, arguments}`, so a
  // reloaded session showed "Tool · Used tool_call" with the call as JSON.
  it("replays the tool that ran, not the bridge that carried it", () => {
    const items = itemsFromMessages([
      {
        role: "assistant",
        content: "",
        tool_calls: [
          {
            id: "b1",
            function: {
              name: "tool_call",
              arguments: JSON.stringify({ name: "git_status", arguments: {} }),
            },
          },
          {
            id: "b2",
            function: {
              name: "tool_call",
              arguments: JSON.stringify({ name: "remember", arguments: '{"content":"x"}' }),
            },
          },
        ],
      },
    ] as any);
    const tools = items.filter((i: any) => i.kind === "tool") as any[];
    expect(tools.map((t) => t.name)).toEqual(["git_status", "remember"]);
    expect(tools[1].args).toEqual({ content: "x" });
  });
});

describe("itemsFromMessages receipt", () => {
  it("replays the turn's receipt, so a reopened session still shows what changed", () => {
    // GUI review: a reopened session had no "N files changed" line — the receipt rode only
    // the live turn-end frame. The engine now persists it as a `changes` notice.
    const items = itemsFromMessages([
      { role: "user", content: "rename qty" },
      { role: "assistant", content: "Done." },
      {
        role: "notice",
        kind: "changes",
        changes: {
          overwritten: ["/w/a.py"],
          deleted: ["/w/gone.py"],
          stats: [
            { path: "/w/a.py", added: 2, removed: 2, call_id: "c1", undoable: true },
            { path: "/w/gone.py", added: 0, removed: 3, call_id: "", undoable: false, source: "shell" },
          ],
          check: { command: "pytest -q", ok: true, summary: "4 passed", edits_since: 0, timed_out: false },
        },
      },
    ]);
    const receipt = items[items.length - 1];
    expect(receipt).toEqual({
      kind: "changes",
      overwritten: ["/w/a.py"],
      deleted: ["/w/gone.py"],
      stats: [
        { path: "/w/a.py", added: 2, removed: 2, callId: "c1", undoable: true },
        { path: "/w/gone.py", added: 0, removed: 3, source: "shell" },
      ],
      check: { command: "pytest -q", ok: true, summary: "4 passed", editsSince: 0, timedOut: false },
    });
    // Not the catch-all: an unknown notice replays as a retriable error.
    expect(items.some((it) => it.kind === "notice")).toBe(false);
  });
});

describe("itemsFromMessages tool rows", () => {
  it("carry their call id, as live rows do, so undo and review can name them", () => {
    const items = itemsFromMessages([
      { role: "user", content: "edit it" },
      {
        role: "assistant",
        content: "",
        tool_calls: [{ id: "call_7", type: "function", function: { name: "write_file", arguments: "{}" } }],
      },
      { role: "tool", tool_call_id: "call_7", content: "ok" },
    ] as never);
    const tool = items.find((it) => it.kind === "tool");
    expect(tool).toMatchObject({ id: "call_7", callId: "call_7" });
  });
});

describe("itemsFromMessages: the model that wrote each message", () => {
  it("stamps the message and the calls it made with its usage sidecar's model", () => {
    const usage = (model: string) => ({ model, input: 1, output: 1, cache_read: 0, cache_write: 0 });
    const items = itemsFromMessages([
      { role: "user", content: "run the tests" },
      {
        role: "assistant",
        content: "",
        tool_calls: [{ id: "t1", function: { name: "run_shell", arguments: "{}" } }],
        usage: usage("anthropic:claude-haiku-5-5"),
      },
      { role: "tool", tool_call_id: "t1", content: "ok" },
      { role: "assistant", content: "All green.", usage: usage("google:gemini-3.8-flash") },
      // A message from an older server carries no usage, and so no model.
      { role: "assistant", content: "And again." },
    ] as never);
    expect(items.find((it) => it.kind === "tool")).toMatchObject({ model: "anthropic:claude-haiku-5-5" });
    const answers = items.filter((it) => it.kind === "assistant");
    expect(answers[0]).toMatchObject({ model: "google:gemini-3.8-flash" });
    expect("model" in answers[1]).toBe(false);
  });
});
