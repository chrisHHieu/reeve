// @vitest-environment jsdom
// A streamed token does not redraw the conversation that is already settled.
//
// Found from a lagging GUI: App hands SessionScroll a NEW `onForkSession` arrow on every render, App
// renders on every delta, and TurnGroup's memo compares that prop — so it missed in 10,944 of 10,944
// comparisons and every turn of the conversation re-rendered per token (40 turns: 133 ms per frame;
// 120 turns: 150-180 ms). What has to hold: the settled turn is NOT rendered again when only the
// callback's identity changed, and the fork action still runs the newest callback, not the first.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Item } from "../../types";
import { SessionScroll, type SessionScrollProps } from "./SessionScroll";

const drawn: string[] = [];

vi.mock("../Markdown", async () => {
  const actual: any = await vi.importActual("../Markdown");
  return {
    ...actual,
    Markdown: ({ text }: { text: string }) => {
      drawn.push(text);
      return <div>{text}</div>;
    },
  };
});

afterEach(() => {
  cleanup();
  drawn.length = 0;
});

const items: Item[] = [
  { kind: "user", text: "Check the build", ts: 1755600000 },
  { kind: "tool", id: "t1", name: "read_file", args: { path: "a.py" }, status: "ok", preview: "ok", ts: 1755600001 } as Item,
  { kind: "assistant", text: "Settled answer.", ts: 1755600010 },
];

function props(over: Partial<SessionScrollProps> = {}): SessionScrollProps {
  return {
    idle: false,
    hydrating: false,
    sessionId: "s1",
    items,
    running: false,
    liveActivity: undefined,
    blocks: [],
    streaming: false,
    compacting: false,
    following: true,
    connectorToolIndex: undefined,
    agentColors: {} as any,
    scrollRef: { current: null },
    handleScroll: () => {},
    approve: vi.fn(),
    retry: vi.fn(),
    revealArtifact: vi.fn(),
    undoCall: vi.fn(),
    setSurface: vi.fn(),
    followLatest: vi.fn(),
    openChild: vi.fn(),
    promoteTask: () => {},
    ...over,
  };
}

describe("SessionScroll while a message streams", () => {
  it("does not draw a settled turn again when only the fork callback's identity changed", () => {
    const { rerender } = render(<SessionScroll {...props({ onForkSession: () => {} })} />);
    const first = drawn.filter((t) => t === "Settled answer.").length;
    expect(first).toBeGreaterThan(0); // the answer was drawn at all: the mock sits on the real path

    for (let i = 0; i < 5; i++) rerender(<SessionScroll {...props({ onForkSession: () => {} })} />);
    expect(drawn.filter((t) => t === "Settled answer.").length).toBe(first);
  });

  it("runs the newest fork callback", () => {
    const old = vi.fn();
    const newest = vi.fn();
    const { rerender } = render(<SessionScroll {...props({ onForkSession: old })} />);
    rerender(<SessionScroll {...props({ onForkSession: newest })} />);
    fireEvent.click(screen.getAllByTestId("bubble-fork")[0]);
    expect(newest).toHaveBeenCalledTimes(1);
    expect(old).not.toHaveBeenCalled();
  });

  it("offers no fork action when there is no callback", () => {
    render(<SessionScroll {...props()} />);
    expect(screen.queryAllByTestId("bubble-fork")).toHaveLength(0);
  });
});
