// The question line's reading of the transcript (session/TurnPin.tsx): what a turn is called
// in one line, which of the person's messages it is, and the marks it reads them from. Where the
// line sits and how it moves is checked in the browser (e2e/turn-pin.spec.ts): jsdom lays
// nothing out.
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { anchorWords, filesWords, ordinalOf, readAnchors, turnAbove } from "./TurnPin";
import { Transcript } from "../Transcript";
import { wakeNotice } from "../../wakeNotice";
import type { Item } from "../../types";

afterEach(cleanup);

describe("what a turn is called", () => {
  it("is its words, and its files when it had none", () => {
    expect(anchorWords({ text: "What does this show?", files: 2 })).toBe("What does this show?");
    expect(anchorWords({ text: "  ", files: 2 })).toBe("2 files");
    expect(anchorWords({ text: "", files: 1 })).toBe("1 file");
    expect(anchorWords({ text: "", files: 0 })).toBe("");
    expect(filesWords(3)).toBe("3 files");
  });
});

describe("which of your messages it is", () => {
  const turns = [
    { id: "0", kind: "user" as const },
    { id: "2", kind: "user" as const },
    { id: "4", kind: "engine" as const },
    { id: "6", kind: "user" as const },
  ];

  it("counts your own messages only", () => {
    expect(ordinalOf(turns, "0")).toBe(1);
    expect(ordinalOf(turns, "2")).toBe(2);
    expect(ordinalOf(turns, "6")).toBe(3);
  });

  it("gives a turn nobody typed the number of your last message before it", () => {
    expect(ordinalOf(turns, "4")).toBe(2);
    expect(ordinalOf([{ id: "0", kind: "engine" }], "0")).toBe(0);
    expect(ordinalOf(turns, "missing")).toBe(0);
  });
});

describe("the row that has scrolled away", () => {
  const at = (bottom: number) => ({ getBoundingClientRect: () => ({ bottom }) }) as unknown as HTMLElement;
  it("is the last one whose box is above the line", () => {
    const [a, b, c] = [at(10), at(90), at(400)];
    expect(turnAbove([a, b, c], 100)).toBe(b);
    expect(turnAbove([a, b, c], 5)).toBeNull();
  });
});

describe("the marks the transcript puts on a turn's first row", () => {
  it("names your messages and the turns the engine started, in order", () => {
    const wake = wakeNotice(
      { synthetic: "background_task", session_id: "c1", title: "Probe MCP access", state: "completed", steps: 6 },
      1020,
    )!;
    const items: Item[] = [
      { kind: "user", text: "Can a subagent call MCP tools?", ts: 1000 },
      { kind: "assistant", text: "Checking.", ts: 1010 },
      wake,
      { kind: "assistant", text: "It can.", ts: 1030 },
      {
        kind: "user",
        text: "",
        ts: 1040,
        attachments: [
          { kind: "file", name: "a.txt" },
          { kind: "file", name: "b.txt" },
        ],
      } as Item,
      { kind: "assistant", text: "Two files.", ts: 1050 },
    ];
    const { container } = render(<Transcript items={items} running={false} onApprove={() => {}} />);
    const anchors = readAnchors(container as HTMLElement);
    expect(anchors.map((a) => [a.kind, a.label, anchorWords(a), a.ts])).toEqual([
      ["user", "", "Can a subagent call MCP tools?", 1000],
      ["engine", "Subagent finished", "Probe MCP access", 1020],
      ["user", "", "2 files", 1040],
    ]);
    expect(anchors[1].icon).toBe("bot");
    expect(ordinalOf(anchors, anchors[2].id)).toBe(2);
  });
});
