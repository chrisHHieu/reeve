import { act, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { StreamBlock } from "../streamBlocks";
import type { LiveState } from "./liveSession";
import { useLiveBlocks, useShellState } from "./useShellState";

function store(initial: Partial<LiveState>) {
  let state = { items: [], blocks: [], running: true, ...initial } as unknown as LiveState;
  const listeners = new Set<() => void>();
  return {
    subscribe: (l: () => void) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    getSnapshot: () => state,
    set(patch: Partial<LiveState>) {
      state = { ...state, ...patch };
      for (const l of Array.from(listeners)) l();
    },
  };
}

const text = (t: string): StreamBlock => ({ kind: "text", text: t }) as StreamBlock;
const call = (name: string): StreamBlock => ({ kind: "tool_call", name, args: "" }) as unknown as StreamBlock;

describe("useShellState", () => {
  it("does not re-render its reader while only the live step's text grows", () => {
    const s = store({});
    let shellRenders = 0;
    let blockRenders = 0;
    function Shell() {
      const shell = useShellState(s);
      shellRenders++;
      return <span data-testid="kind">{`${shell.streamOpen}:${shell.liveKind}:${shell.liveTool}`}</span>;
    }
    function Text() {
      const blocks = useLiveBlocks(s, []);
      blockRenders++;
      return <span data-testid="text">{blocks.map((b) => (b.kind === "text" ? b.text : "")).join("")}</span>;
    }
    const view = render(
      <>
        <Shell />
        <Text />
      </>,
    );
    expect(view.getByTestId("kind").textContent).toBe("false:null:");
    act(() => s.set({ blocks: [text("He")] }));
    const afterOpen = shellRenders;
    expect(view.getByTestId("kind").textContent).toBe("true:text:");
    for (const t of ["Hel", "Hell", "Hello", "Hello there"]) act(() => s.set({ blocks: [text(t)] }));
    // Four tokens: the text reader drew each, the shell reader none.
    expect(shellRenders).toBe(afterOpen);
    expect(view.getByTestId("text").textContent).toBe("Hello there");
    expect(blockRenders).toBeGreaterThanOrEqual(6);
    // A call starts being written: that IS news for the header.
    act(() => s.set({ blocks: [text("Hello there"), call("edit_file")] }));
    expect(view.getByTestId("kind").textContent).toBe("true:tool_call:edit_file");
    expect(shellRenders).toBe(afterOpen + 1);
  });

  it("re-renders when anything other than the live step changes", () => {
    const s = store({});
    let renders = 0;
    function Shell() {
      const shell = useShellState(s);
      renders++;
      return <span>{String(shell.running)}</span>;
    }
    render(<Shell />);
    const before = renders;
    act(() => s.set({ running: false }));
    expect(renders).toBe(before + 1);
    act(() => s.set({ items: [] }));
    // A new items array is a change, even if empty: the transcript is replaced, never mutated.
    expect(renders).toBe(before + 2);
  });
});
