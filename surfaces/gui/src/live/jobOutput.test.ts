// The live job-output buffer (`jobOutput.ts`): what a streamed frame does to it, and the two
// dishonesties it exists to prevent — a hole that is not admitted, and a duplicate that is
// appended twice.

import { describe, expect, it } from "vitest";
import { JOB_OUTPUT_LIMIT, appendJobChunk, emptyJobOutput, outputAfter } from "./jobOutput";

describe("job output buffer", () => {
  it("appends the frames of one job in order", () => {
    let buffer = emptyJobOutput();
    // The engine's `total` is the job's cumulative count AFTER the chunk; these two add up.
    buffer = appendJobChunk(buffer, "compiling 3 files\n", 18);
    buffer = appendJobChunk(buffer, "done\n", 23);
    expect(buffer.text).toBe("compiling 3 files\ndone\n");
    expect(buffer.total).toBe(23);
    expect(buffer.gapBefore).toBe(false);
    expect(buffer.ended).toBe(false);
  });

  it("drops a frame it already has, instead of printing it twice", () => {
    // A reconnect can replay the tail; the panel must not show the line twice.
    let buffer = appendJobChunk(emptyJobOutput(), "line 1\n", 7);
    const same = appendJobChunk(buffer, "line 1\n", 7);
    expect(same).toBe(buffer);
  });

  it("admits a hole when the stream skips ahead", () => {
    let buffer = appendJobChunk(emptyJobOutput(), "line 1\n", 7);
    buffer = appendJobChunk(buffer, "line 9\n", 63);
    expect(buffer.text).toBe("line 1\nline 9\n");
    expect(buffer.gapBefore).toBe(true);
    expect(buffer.total).toBe(63);
  });

  it("keeps the tail bounded and says the start is gone", () => {
    let buffer = emptyJobOutput();
    const line = "x".repeat(1000) + "\n";
    // Comfortably more than the limit, so the trim is exercised whatever the bound is.
    const lines = Math.ceil((JOB_OUTPUT_LIMIT * 1.5) / line.length);
    for (let i = 0; i < lines; i++) buffer = appendJobChunk(buffer, line, (i + 1) * line.length);
    expect(buffer.text.length).toBeLessThanOrEqual(JOB_OUTPUT_LIMIT);
    expect(buffer.gapBefore).toBe(true);
    expect(buffer.text.endsWith("\n")).toBe(true);
  });

  it("never cuts a surrogate pair in half when it trims", () => {
    let buffer = emptyJobOutput();
    // Astral characters: two UTF-16 units each, so an odd cut would leave half of one.
    const chunk = "😀".repeat(5000);
    buffer = appendJobChunk(buffer, chunk, chunk.length);
    buffer = appendJobChunk(buffer, "tail", chunk.length + 4);
    // Re-encoding and decoding must round-trip: a lone surrogate would not survive it.
    expect(Array.from(buffer.text).every((ch) => ch.length === 2 || ch === "l" || ch === "t" || ch === "a" || ch === "i")).toBe(true);
    expect(buffer.text.endsWith("tail")).toBe(true);
  });

  it("marks the end on the engine's final flush", () => {
    let buffer = appendJobChunk(emptyJobOutput(), "bye\n", 4);
    buffer = appendJobChunk(buffer, "", 4, true);
    expect(buffer.ended).toBe(true);
    expect(buffer.text).toBe("bye\n");
  });

  it("accepts a frame with no total as what it is", () => {
    const buffer = appendJobChunk(emptyJobOutput(), "no position\n");
    expect(buffer.text).toBe("no position\n");
    expect(buffer.total).toBe(12);
    expect(buffer.gapBefore).toBe(false);
  });
});

describe("the stream after an anchor", () => {
  const buffer = { text: "three\nfour\n", total: 19, gapBefore: true, ended: false };

  it("adds nothing when the anchor already holds everything the stream has", () => {
    expect(outputAfter(buffer, 19)).toEqual({ text: "", gap: false });
    expect(outputAfter(buffer, 40)).toEqual({ text: "", gap: false });
    expect(outputAfter(undefined, 0)).toEqual({ text: "", gap: false });
  });

  it("cuts the part a frame shares with the anchor, by position", () => {
    // The anchor ended at 14, just after "three\n" of a job whose text ends at 19.
    expect(outputAfter(buffer, 14)).toEqual({ text: "four\n", gap: false });
    expect(outputAfter(buffer, 8)).toEqual({ text: "three\nfour\n", gap: false });
  });

  it("admits a hole when the stream starts later than the anchor ended", () => {
    expect(outputAfter(buffer, 2)).toEqual({ text: "three\nfour\n", gap: true });
  });
});
