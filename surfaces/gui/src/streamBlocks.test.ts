// The live step's block fold, tested as a rule.
//
// The property under test throughout: the ORDER a step happened in survives. Two flat
// strings could not say whether the thinking came before the sentence; a block list can,
// and that is the whole reason this module exists.
import { describe, expect, it } from "vitest";
import { churnFromArgs, foldBlock, liveIndex, liveThoughtOf, reasoningOf, textOf } from "./streamBlocks";
import type { BlockDeltaEvent, StreamBlock } from "./streamBlocks";

type Kinded = BlockDeltaEvent & { kind: StreamBlock["kind"] };

const fold = (...events: Kinded[]): StreamBlock[] =>
  events.reduce<StreamBlock[]>((blocks, e) => foldBlock(blocks, e.kind, e), []);

const think = (index: number, text: string) => ({ kind: "reasoning" as const, index, text });
const say = (index: number, text: string) => ({ kind: "text" as const, index, text });
const call = (index: number, rest: BlockDeltaEvent) => ({
  kind: "tool_call" as const,
  index,
  ...rest,
});

describe("foldBlock", () => {
  it("appends same-index deltas into one block", () => {
    expect(fold(say(0, "The "), say(0, "answer "), say(0, "is 4."))).toEqual([
      { kind: "text", text: "The answer is 4." },
    ]);
  });

  it("keeps thinking and prose in the order they arrived", () => {
    const blocks = fold(
      think(0, "first thought"),
      say(1, "Let me start."),
      think(2, "second thought"),
      say(3, "Done."),
    );
    expect(blocks.map((b) => b.kind)).toEqual(["reasoning", "text", "reasoning", "text"]);
    expect(blocks.map((b) => (b.kind === "tool_call" ? "" : b.text))).toEqual([
      "first thought",
      "Let me start.",
      "second thought",
      "Done.",
    ]);
  });

  it("assembles a tool call from its fragments, holding the name once it is known", () => {
    const blocks = fold(
      call(0, { call_id: "c1", name: "read_file", arguments_delta: "" }),
      call(0, { call_id: "c1", name: "read_file", arguments_delta: '{"path":' }),
      call(0, { call_id: "", name: "", arguments_delta: '"q3.csv"}' }),
    );
    expect(blocks).toEqual([
      { kind: "tool_call", callId: "c1", name: "read_file", argsRaw: '{"path":"q3.csv"}' },
    ]);
  });

  it("keeps two concurrent calls' arguments apart", () => {
    const blocks = fold(
      call(0, { call_id: "a", name: "read_file", arguments_delta: '{"path":' }),
      call(1, { call_id: "b", name: "run_shell", arguments_delta: '{"cmd":' }),
      call(0, { arguments_delta: '"a.csv"}' }),
      call(1, { arguments_delta: '"ls"}' }),
    );
    expect(blocks.map((b) => (b.kind === "tool_call" ? b.argsRaw : ""))).toEqual([
      '{"path":"a.csv"}',
      '{"cmd":"ls"}',
    ]);
  });

  it("only replaces the block that changed, so the rest keep their references", () => {
    const before = fold(think(0, "thinking"), say(1, "prose"));
    const after = foldBlock(before, "text", say(1, " more"));
    expect(after[0]).toBe(before[0]); // untouched block, same object
    expect(after[1]).not.toBe(before[1]);
  });

  it("continues the current block when a server sends no index", () => {
    // A server older than block streaming sends `{text}` alone. Dropping those deltas
    // would leave the live view blank against a backend that still works.
    const started = fold(say(0, "The "));
    const same = foldBlock(started, "text", { text: "answer." });
    expect(same).toEqual([{ kind: "text", text: "The answer." }]);
    // ...and a change of kind still opens the next block, exactly as the index would.
    expect(foldBlock(same, "reasoning", { text: "hmm" })).toEqual([
      { kind: "text", text: "The answer." },
      { kind: "reasoning", text: "hmm" },
    ]);
  });

  it("refuses a malformed index rather than treating it as absent", () => {
    const blocks = fold(say(0, "hi"));
    expect(foldBlock(blocks, "text", { index: -1, text: "?" })).toBe(blocks);
    expect(foldBlock(blocks, "text", { index: 1.5, text: "?" })).toBe(blocks);
  });

  it("fills a gap rather than reordering everything after it", () => {
    // Only reachable if a frame was dropped; a placeholder is a smaller lie than shifting
    // every later block onto the wrong index.
    const blocks = fold(say(0, "one"), say(2, "three"));
    expect(blocks.map((b) => b.kind)).toEqual(["text", "text", "text"]);
    expect(textOf(blocks)).toBe("onethree");
  });
});

describe("the derived strings", () => {
  it("join each kind, so the existing stream gate and the answer bubble still work", () => {
    const blocks = fold(think(0, "a"), say(1, "B"), think(2, "c"), say(3, "D"));
    expect(textOf(blocks)).toBe("BD");
    expect(reasoningOf(blocks)).toBe("ac");
  });

  it("are empty for a step that has not said anything", () => {
    expect(textOf([])).toBe("");
    expect(reasoningOf([])).toBe("");
  });
});

describe("liveIndex", () => {
  it("names the block being written", () => {
    expect(liveIndex(fold(think(0, "a"), say(1, "B")))).toBe(1);
  });

  it("skips a tool-call block that has not been named yet", () => {
    // It opens empty. Treating it as live would move the running marker off the reasoning
    // the person is still reading.
    const blocks = fold(think(0, "still thinking"), call(1, { arguments_delta: "" }));
    expect(liveIndex(blocks)).toBe(0);
  });

  it("is -1 with nothing to point at", () => {
    expect(liveIndex([])).toBe(-1);
    expect(liveIndex(fold(say(0, "")))).toBe(-1);
  });
});

describe("liveThoughtOf", () => {
  // The running turn's status line shows this instead of a live "Thinking" row (design B).
  it("is the newest line of the trace being written", () => {
    expect(liveThoughtOf(fold(think(0, "first idea\nnow the tests\n")))).toBe("now the tests");
  });

  it("is nothing once the stream has moved on to prose or a call", () => {
    expect(liveThoughtOf(fold(think(0, "done thinking"), say(1, "Here goes.")))).toBeUndefined();
    expect(liveThoughtOf([])).toBeUndefined();
  });
});

describe("churnFromArgs", () => {
  it("counts the lines a write has described so far, mid-stream", () => {
    // The JSON never closes; the count grows with what has arrived.
    const partial = '{"path":"report.py","content":"import pandas\\nimport numpy\\n';
    expect(churnFromArgs("write_file", partial)).toEqual({ added: 2, removed: 0 });
  });

  it("counts both sides of a replacement", () => {
    const partial = '{"old_string":"a\\nb\\nc","new_string":"x\\ny"';
    expect(churnFromArgs("replace_in_file", partial)).toEqual({ added: 2, removed: 3 });
  });

  it("reads +/- lines out of a patch envelope", () => {
    const partial = '{"patch":"--- a/x\\n+++ b/x\\n@@\\n-old\\n+new\\n+more\\n';
    expect(churnFromArgs("apply_patch", partial)).toEqual({ added: 2, removed: 1 });
  });

  it("says nothing for a tool that cannot edit, or before a value has arrived", () => {
    expect(churnFromArgs("run_shell", '{"command":"ls -la"}')).toBeNull();
    expect(churnFromArgs("write_file", '{"path":"a.py","content":"')).toEqual({
      added: 0,
      removed: 0,
    });
    expect(churnFromArgs("write_file", "")).toBeNull();
  });
});
