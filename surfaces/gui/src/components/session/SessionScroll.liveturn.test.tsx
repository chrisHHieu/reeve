// @vitest-environment jsdom
// A streamed token does not redraw the rows already written in the turn that is running.
//
// Found from a lagging GUI: the running turn's group cannot be memo'd as a whole (its live tail changes on
// every token), so each of its rows was drawn again per token: a turn 300 steps in cost 117 ms per frame, the
// same work for rows that had not changed. Rows are memo'd now, and the callbacks they are given are stable.
// What has to hold: a new token (new `blocks`, and the fresh inline callbacks App builds each render) draws no
// finished row again, and a row whose own call changed is drawn.
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Item } from "../../types";
import { SessionScroll, type SessionScrollProps } from "./SessionScroll";

const specFor: string[] = [];

vi.mock("../../cards/registry", async () => {
  const actual: any = await vi.importActual("../../cards/registry");
  return {
    ...actual,
    rowSpec: (...args: any[]) => {
      specFor.push(String(args[0]?.callId ?? args[0]?.id ?? args[0]?.name));
      return actual.rowSpec(...args);
    },
  };
});

afterEach(() => {
  cleanup();
  specFor.length = 0;
});

// Alternating kinds: a run of the same read-only or shell call folds into one closed group, which draws no rows.
const tool = (id: string, status = "ok", extra: object = {}): Item =>
  ({
    kind: "tool",
    id,
    callId: id,
    name: id === "b" ? "read_file" : "run_shell",
    args: id === "b" ? { path: "a.py" } : { command: "git status" },
    status,
    preview: "ok",
    ts: 1755600001,
    ...extra,
  }) as Item;

const finished = [tool("a"), tool("b"), tool("c")];

function props(over: Partial<SessionScrollProps> = {}): SessionScrollProps {
  return {
    idle: false,
    hydrating: false,
    sessionId: "s1",
    items: [{ kind: "user", text: "Check the build", ts: 1755600000 } as Item, ...finished],
    running: true,
    liveActivity: undefined,
    blocks: [{ kind: "text", text: "Reading" }],
    streaming: "Reading",
    compacting: false,
    following: true,
    connectorToolIndex: undefined,
    agentColors: {} as any,
    scrollRef: { current: null },
    handleScroll: () => {},
    // The inline arrows App builds on every render.
    approve: () => {},
    retry: () => {},
    revealArtifact: () => {},
    undoCall: async () => ({ ok: true }),
    setSurface: () => {},
    followLatest: () => {},
    openChild: () => {},
    promoteTask: () => {},
    onForkSession: () => {},
    ...over,
  };
}

describe("SessionScroll while the running turn streams", () => {
  it("does not draw the finished rows of the turn again for a new token", () => {
    const { rerender } = render(<SessionScroll {...props()} />);
    expect(specFor.length).toBeGreaterThan(0); // the mock sits on the real path
    specFor.length = 0;

    for (const text of ["Reading t", "Reading th", "Reading the files"]) {
      rerender(<SessionScroll {...props({ blocks: [{ kind: "text", text }], streaming: text })} />);
    }
    expect(specFor).toEqual([]);
  });

  it("draws the row whose own call changed, and only that one", () => {
    const { rerender } = render(<SessionScroll {...props()} />);
    specFor.length = 0;
    const items = [{ kind: "user", text: "Check the build", ts: 1755600000 } as Item, finished[0], tool("b", "ok", { liveOutput: "more" }), finished[2]];
    rerender(<SessionScroll {...props({ items })} />);
    expect(specFor.length).toBeGreaterThan(0);
    expect(new Set(specFor).size).toBe(1);
  });
});
