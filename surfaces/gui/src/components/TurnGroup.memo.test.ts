// The comparator that keeps a streaming delta from re-rendering every turn in the session.
//
// This is worth its own test because both failure directions are silent: too strict and the
// transcript keeps rendering 200 turn groups per token (the thing the memo exists to stop);
// too loose and a settled turn shows stale rows, which no snapshot would catch — the data is in
// the DOM, just not the new data.

import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { sameTurn, splitAssistant, useStableItems, type TurnGroupProps } from "./Transcript";
import type { Item } from "../types";

const item = (id: string): Extract<Item, { kind: "tool" }> => ({
  kind: "tool",
  id,
  name: "read_file",
  args: {},
  status: "ok",
});

const props = (over: Partial<TurnGroupProps> = {}): TurnGroupProps => ({
  items: [item("a"), item("b")],
  live: false,
  startTs: 1,
  liveActivity: undefined,
  ...over,
});

describe("sameTurn", () => {
  it("is equal when every item keeps its identity", () => {
    const a = props();
    // A new ARRAY of the same objects is what a streaming append produces for old turns.
    expect(sameTurn(a, { ...a, items: [...a.items] })).toBe(true);
  });

  it("is not equal when an item was replaced", () => {
    const a = props();
    expect(sameTurn(a, props({ items: [item("a"), item("c")] }))).toBe(false);
  });

  it("is not equal when an item was appended", () => {
    const a = props();
    expect(sameTurn(a, props({ items: [...a.items, item("c")] }))).toBe(false);
  });

  it("is not equal when the turn's own state changed", () => {
    const a = props();
    expect(sameTurn(a, { ...a, live: true })).toBe(false);
    expect(sameTurn(a, { ...a, startTs: 2 })).toBe(false);
    const running = props({ live: true });
    expect(sameTurn(running, { ...running, liveActivity: "Reading" })).toBe(false);
  });

  it("a settled turn ignores the status line only the running turn shows", () => {
    // The running header's words change many times a turn; every settled turn used to
    // re-render with them although none of them draws it.
    const a = props();
    expect(sameTurn(a, { ...a, liveActivity: "Reading" })).toBe(true);
  });

  it("a settled answer that thought keeps its identity across renders", () => {
    // flush() splits a reasoning+text answer into a trace and an answer. Fresh objects on
    // every render made sameTurn false for every such turn: the whole history re-rendered
    // on each streamed token.
    const answer = { kind: "assistant" as const, text: "Done.", reasoning: "plan", ts: 5, model: "m:x" };
    const first = splitAssistant(answer);
    const second = splitAssistant(answer);
    expect(second.trace).toBe(first.trace);
    expect(second.answer).toBe(first.answer);
    expect(first.trace).toMatchObject({ text: "", reasoning: "plan", ts: 5 });
    expect(first.answer).toMatchObject({ text: "Done.", ts: 5, model: "m:x" });
    expect("reasoning" in first.answer).toBe(false);
    const a = props({ items: [first.trace], answers: [first.answer] });
    expect(sameTurn(a, props({ items: [second.trace], answers: [second.answer] }))).toBe(true);
  });
});

describe("useStableItems", () => {
  it("keeps one array while its elements are the same, and moves when they change", () => {
    const a = item("a");
    const b = item("b");
    const { result, rerender } = renderHook(({ list }) => useStableItems(list), {
      initialProps: { list: [a, b] },
    });
    const held = result.current;
    rerender({ list: [a, b] });
    expect(result.current).toBe(held);
    const c = item("c");
    const grown = [a, b, c];
    rerender({ list: grown });
    expect(result.current).toBe(grown);
  });
});
