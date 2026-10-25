// The plan strip's lifetime, tested as a rule rather than as a wiring detail: the live
// path and the replay path have to reach the same answer for the same session.
import { describe, expect, it } from "vitest";
import type { ConversationMessage } from "./api";
import { planFrom, planFromEvents, planFromMessages, planFromTodoWrite } from "./todoPlan";
import type { SessionEvent } from "./trajectoryEvents";

const user = (text: string): ConversationMessage => ({ role: "user", content: text });
const wrote = (...contents: string[]): ConversationMessage => ({
  role: "assistant",
  tool_calls: [
    {
      id: "call-" + contents.length,
      function: {
        name: "todo_write",
        arguments: JSON.stringify({
          todos: contents.map((content) => ({ content, status: "done" })),
        }),
      },
    },
  ],
});
const said = (text: string): ConversationMessage => ({ role: "assistant", content: text });
const result = (id: string): ConversationMessage => ({ role: "tool", tool_call_id: id, content: "{}" });

describe("planFromTodoWrite", () => {
  it("reads a plan off a todo_write proposal", () => {
    const plan = planFromTodoWrite("todo_write", {
      todos: [{ content: "Audit the retry paths", status: "in_progress" }],
    });
    expect(plan).toEqual([{ content: "Audit the retry paths", status: "in_progress" }]);
  });

  it("still reads pre-rename histories (legacy `items` key)", () => {
    expect(planFromTodoWrite("todo_write", { items: [{ content: "Old plan", status: "pending" }] })).toEqual([
      { content: "Old plan", status: "pending" },
    ]);
  });

  it("normalizes bare strings and the `completed` alias the models free-style", () => {
    expect(planFromTodoWrite("todo_write", { todos: ["Ship it", { content: "Test it", status: "completed" }] })).toEqual([
      { content: "Ship it", status: "pending" },
      { content: "Test it", status: "done" },
    ]);
  });

  it("an explicit empty write is a plan — it clears the strip", () => {
    expect(planFromTodoWrite("todo_write", { todos: [] })).toEqual([]);
  });

  it("another tool, or args that aren't a list, is not a plan write", () => {
    // null, not [] — a malformed write must leave the standing plan alone rather than wipe it.
    expect(planFromTodoWrite("run_shell", { command: "ls" })).toBeNull();
    expect(planFromTodoWrite("todo_write", { todos: "Ship it" })).toBeNull();
    expect(planFromTodoWrite("todo_write", {})).toBeNull();
  });
});

describe("planFromMessages", () => {
  it("keeps the finished checklist of the turn that wrote it", () => {
    const plan = planFromMessages([user("Fix the build"), wrote("Find it", "Fix it"), said("Fixed.")]);
    expect(plan.map((t) => t.content)).toEqual(["Find it", "Fix it"]);
  });

  it("survives the tool results and the answer inside its own turn", () => {
    const plan = planFromMessages([user("Fix the build"), wrote("Find it"), result("call-1"), said("Fixed.")]);
    expect(plan.map((t) => t.content)).toEqual(["Find it"]);
  });

  it("survives the turn boundary — a new user message does not clear it", () => {
    const before = [user("Fix the build"), wrote("Find it", "Fix it"), said("Fixed.")];
    expect(planFromMessages(before)).toHaveLength(2);
    // The rule has no boundary (owner call 2026-09-04): a turn that never writes its own
    // plan — errored, stopped, a one-line ask — must not wipe the standing one.
    expect(planFromMessages([...before, user("Now write the release note")])).toHaveLength(2);
  });

  it("a failed turn keeps the previous plan standing", () => {
    const before = [user("Fix the build"), wrote("Find it", "Fix it"), said("Fixed.")];
    // The next turn died on a provider error before any todo_write landed.
    const failed = [...before, user("Now refactor it"), { role: "notice", kind: "error", text: "provider 500" } as ConversationMessage];
    expect(planFromMessages(failed)).toHaveLength(2);
  });

  it("a background-delivered turn is not a boundary either", () => {
    const before = [user("Fix the build"), wrote("Find it"), said("Fixed.")];
    const wake: ConversationMessage = {
      role: "user",
      content: "Slack: can you check the deploy?",
      source: { connector: "slack", kind: "channel", ts: 1, text: "check the deploy" } as any,
    };
    expect(planFromMessages(before)).toHaveLength(1);
    expect(planFromMessages([...before, wake])).toHaveLength(1);
  });

  it("the latest write wins inside one turn", () => {
    const plan = planFromMessages([user("Fix the build"), wrote("Find it"), wrote("Find it", "Fix it")]);
    expect(plan.map((t) => t.content)).toEqual(["Find it", "Fix it"]);
  });

  it("an unparseable tool call leaves the standing plan alone", () => {
    const broken: ConversationMessage = {
      role: "assistant",
      tool_calls: [{ id: "x", function: { name: "todo_write", arguments: "{not json" } }],
    };
    const plan = planFromMessages([user("Fix the build"), wrote("Find it"), broken]);
    expect(plan.map((t) => t.content)).toEqual(["Find it"]);
  });

  it("is empty for a session that never planned", () => {
    expect(planFromMessages([user("Hello"), said("Hi.")])).toEqual([]);
    expect(planFromMessages([])).toEqual([]);
  });
});


// The fold reads the session event log — the separation, ported (harness/engine.py
// records `todo/write` when a call lands). The message list is the model's view; the plan
// a person reads is not derived from it.
describe("planFromEvents", () => {
  const ev = (type: string, extra: Record<string, unknown> = {}): SessionEvent => ({
    seq: 0,
    t: 1,
    type,
    ...extra,
  });
  const wrote2 = (...contents: string[]) =>
    ev("todo/write", { todos: contents.map((content) => ({ content, status: "pending" })) });

  it("takes the latest write, across turns as well as within one", () => {
    const plan = planFromEvents([
      ev("turn/start", { turn: 1 }),
      wrote2("First pass"),
      wrote2("Second pass"),
      ev("turn/start", { turn: 2 }),
      wrote2("New work"),
    ]);
    expect(plan.map((t) => t.content)).toEqual(["New work"]);
  });

  it("a turn that never wrote leaves the previous plan standing", () => {
    const plan = planFromEvents([
      ev("turn/start", { turn: 1 }),
      wrote2("First pass"),
      ev("turn/end", { turn: 1 }),
      ev("turn/start", { turn: 2 }),
      ev("turn/end", { turn: 2 }), // died before its first write
    ]);
    expect(plan.map((t) => t.content)).toEqual(["First pass"]);
  });

  it("keeps the finished checklist standing after the turn ends", () => {
    const plan = planFromEvents([ev("turn/start", { turn: 1 }), wrote2("Done"), ev("turn/end", { turn: 1 })]);
    expect(plan.map((t) => t.content)).toEqual(["Done"]);
  });

  it("treats an empty list as the model clearing its plan, a malformed one as no plan", () => {
    expect(planFromEvents([wrote2("Something"), ev("todo/write", { todos: [] })])).toEqual([]);
    expect(
      planFromEvents([wrote2("Something"), ev("todo/write", { todos: "not a list" })]).map((t) => t.content),
    ).toEqual(["Something"]);
  });

  it("survives a compaction, because the log is not the message list", () => {
    // The bug: compacting mid-turn summarized away every todo_write call, and the panel
    // froze while the agent carried on. The log keeps its own record of the write.
    const plan = planFromEvents([
      ev("turn/start", { turn: 1 }),
      wrote2("Chart revenue"),
      ev("compaction/start"),
      ev("compaction/end"),
      ev("assistant/message", { turn: 1, text: "carrying on" }),
    ]);
    expect(plan.map((t) => t.content)).toEqual(["Chart revenue"]);
  });
});

describe("planFrom", () => {
  it("prefers the real log and falls back to the messages", () => {
    const events: SessionEvent[] = [
      { seq: 0, t: 1, type: "todo/write", todos: [{ content: "From the log", status: "pending" }] },
    ];
    const messages = [user("Fix the build"), wrote("From the messages")] as ConversationMessage[];

    expect(planFrom(events, messages as unknown as Record<string, unknown>[]).map((t) => t.content)).toEqual([
      "From the log",
    ]);
    expect(planFrom([], messages as unknown as Record<string, unknown>[]).map((t) => t.content)).toEqual([
      "From the messages",
    ]);
    expect(planFrom([], [])).toEqual([]);
  });

  it("drops a write the transcript proves failed, and keeps one it cannot judge", () => {
    // A stored transcript can be saved between a call and its result, so absence of a
    // result is not evidence of failure — only an error payload is.
    const failed: ConversationMessage[] = [
      user("Fix the build"),
      wrote("Never landed"),
      { role: "tool", tool_call_id: "call-1", content: JSON.stringify({ error: "boom" }) },
    ];
    expect(planFromMessages(failed)).toEqual([]);

    const unjudged: ConversationMessage[] = [user("Fix the build"), wrote("Landed")];
    expect(planFromMessages(unjudged).map((t) => t.content)).toEqual(["Landed"]);
  });
});
