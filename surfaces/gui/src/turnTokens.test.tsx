// What a turn spent, said beside its model in the answer's footer (owner, 2026-10-21): every step's
// tokens, cache reads included, live and after a reload, including steps that left no row.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { Item, WsEvent } from "./types";
import { itemsFromMessages } from "./itemsFromMessages";
import { LiveSession } from "./live/liveSession";
import { handleSessionEvent, type SessionEventHost } from "./live/sessionEvents";
import { turnTokens, tokensTotal } from "./usage";
import { Transcript } from "./components/Transcript";

afterEach(cleanup);

const step1 = { model: "openai:gpt-6-luna", input: 1000, cache_read: 4000, cache_write: 0, output: 50 };
const step2 = { model: "openai:gpt-6-luna", input: 200, cache_read: 5000, cache_write: 100, output: 30 };
const step3 = { model: "openai:gpt-6-luna", input: 300, cache_read: 5200, cache_write: 0, output: 120 };
const TOTAL = 1000 + 4000 + 50 + 200 + 5000 + 100 + 30 + 300 + 5200 + 120; // 16,000

const host: SessionEventHost = {
  factsChanged: () => {},
  railStale: () => {},
  reloadTranscript: () => {},
  engineReplaced: () => {},
  turnEnded: () => {},
  showsEnvironmentError: () => false,
};

describe("a turn's tokens", () => {
  it("are every step's after a reload, a step with no row of its own included", () => {
    const items = itemsFromMessages([
      { role: "user", content: "check the repo" },
      { role: "assistant", content: "", usage: step1, tool_calls: [{ id: "c1", function: { name: "grep", arguments: "{}" } }] },
      { role: "tool", tool_call_id: "c1", content: "3 hits" },
      // A hidden call (the To-dos list) makes no row: its step is counted on the next one.
      { role: "assistant", content: "", usage: step2, tool_calls: [{ id: "c2", function: { name: "todo_write", arguments: "{}" } }] },
      { role: "tool", tool_call_id: "c2", content: "ok" },
      { role: "assistant", content: "Done.", usage: step3 },
      { role: "user", content: "thanks" },
    ] as never);
    const turn = items.slice(0, items.findIndex((it, i) => i > 0 && it.kind === "user"));
    expect(tokensTotal(turnTokens(turn)!)).toBe(TOTAL);
    // The next turn starts at zero.
    expect(turnTokens(items.slice(turn.length))).toBeUndefined();
  });

  it("after a reload, count a last step that made no row (a turn stopped after a hidden call)", () => {
    const items = itemsFromMessages([
      { role: "user", content: "check the repo" },
      { role: "assistant", content: "", usage: step1, tool_calls: [{ id: "c1", function: { name: "grep", arguments: "{}" } }] },
      { role: "tool", tool_call_id: "c1", content: "3 hits" },
      { role: "assistant", content: "", usage: step2, tool_calls: [{ id: "c2", function: { name: "todo_write", arguments: "{}" } }] },
    ] as never);
    expect(tokensTotal(turnTokens(items)!)).toBe(1000 + 4000 + 50 + 200 + 5000 + 100 + 30);
  });

  it("are every step's live, a last step that made no row included", () => {
    const live = new LiveSession("s");
    const feed = (type: string, data: Record<string, unknown> = {}) =>
      handleSessionEvent(live, { type, data } as WsEvent, host);
    feed("turn_start", { input: "check the repo" });
    feed("assistant_message", { text: "", usage: step1, tool_calls: [{ name: "grep" }] });
    feed("tool_proposed", { name: "grep", call_id: "c1", arguments: {} });
    feed("assistant_message", { text: "Done.", usage: step2 });
    feed("assistant_message", { text: "", usage: step3, tool_calls: [{ name: "todo_write" }] });
    feed("tool_proposed", { name: "todo_write", call_id: "c2", arguments: {} });
    feed("turn_done");
    expect(tokensTotal(turnTokens(live.current.items)!)).toBe(TOTAL);
  });

  it("are said beside the model in the closing answer's footer", () => {
    const items: Item[] = [
      { kind: "user", text: "check the repo", ts: 990 },
      { kind: "tool", id: "t1", name: "grep", args: {}, status: "ok", ts: 1000, model: "openai:gpt-6-luna", tokens: step1 },
      { kind: "assistant", text: "Done.", ts: 1010, model: "openai:gpt-6-luna", tokens: { input: 500, cache_read: 10300, cache_write: 100, output: 150 } },
    ];
    render(<Transcript items={items} running={false} onApprove={vi.fn()} />);
    const spent = screen.getByTestId("turn-tokens");
    expect(spent.textContent).toBe("16.1k tokens");
    // After the model, as the owner asked.
    const model = screen.getByTestId("bubble-model");
    expect(model.compareDocumentPosition(spent) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("are said under an answer that thought before it wrote", () => {
    // A thinking model's answer carries its trace; the trace is split off into the turn, and
    // the step's spend stays with the answer.
    const items = itemsFromMessages([
      { role: "user", content: "do you design?", ts: 990 },
      { role: "assistant", content: "Yes.", reasoning: "The user asks about design.", usage: step1, ts: 1000 },
    ] as never);
    render(<Transcript items={items} running={false} onApprove={vi.fn()} />);
    expect(screen.getByTestId("turn-tokens").textContent).toBe("5k tokens");
  });

  it("say nothing while the turn runs, or for a server that reports no usage", () => {
    const items: Item[] = [
      { kind: "user", text: "hi", ts: 990 },
      { kind: "assistant", text: "Hello.", ts: 1000 },
    ];
    render(<Transcript items={items} running={false} onApprove={vi.fn()} />);
    expect(screen.queryByTestId("turn-tokens")).toBeNull();
  });
});
