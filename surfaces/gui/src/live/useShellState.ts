// What App draws from a conversation's live state, WITHOUT the live step's text.
//
// App used to subscribe to the whole `LiveState`, and every streamed token replaces `blocks`
// (assistant / reasoning / tool-call deltas), so the whole 3,000-line App — top bar, task
// head, composer, rail — re-rendered on every WebSocket frame of a long turn: the
// micro-freezes and the typing lag a person feels late in a running session (owner,
// 2026-10-16). App reads only a few FACTS about the stream (is answer text arriving, which
// block is live, is it empty); those change a handful of times a step, not per token. The
// text itself is read by the one component that draws it (SessionScroll → LiveStep), which
// subscribes to `blocks` on its own.

import { useRef, useSyncExternalStore } from "react";
import type { StreamBlock } from "../streamBlocks";
import { liveIndex } from "../streamBlocks";
import type { LiveState } from "./liveSession";

export interface ShellState extends Omit<LiveState, "blocks"> {
  /** Answer text has started arriving (what `!!textOf(blocks)` used to say). */
  streamOpen: boolean;
  /** No live step at all. */
  streamEmpty: boolean;
  /** The kind of the block being written now, if any. */
  liveKind: StreamBlock["kind"] | null;
  /** The tool a call being written names ("" while its name has not arrived). */
  liveTool: string;
}

interface Source {
  subscribe: (listener: () => void) => () => void;
  getSnapshot: () => LiveState;
}

export function shellOf(state: LiveState): ShellState {
  const { blocks, ...rest } = state;
  const at = liveIndex(blocks);
  const live = at < 0 ? undefined : blocks[at];
  return {
    ...rest,
    streamOpen: blocks.some((b) => b.kind === "text" && b.text !== ""),
    streamEmpty: blocks.length === 0,
    liveKind: live ? live.kind : null,
    liveTool: live && live.kind === "tool_call" ? live.name : "",
  };
}

export function sameShell(a: ShellState, b: ShellState): boolean {
  const keys = Object.keys(a) as Array<keyof ShellState>;
  if (keys.length !== Object.keys(b).length) return false;
  for (const key of keys) if (!Object.is(a[key], b[key])) return false;
  return true;
}

/** A store's state minus the live step's text: the same object back while only the text moved. */
export function useShellState(source: Source): ShellState {
  const cache = useRef<{ src: LiveState; out: ShellState } | null>(null);
  const getSnapshot = (): ShellState => {
    const state = source.getSnapshot();
    const held = cache.current;
    if (held && held.src === state) return held.out;
    const next = shellOf(state);
    const out = held && sameShell(held.out, next) ? held.out : next;
    cache.current = { src: state, out };
    return out;
  };
  return useSyncExternalStore(source.subscribe, getSnapshot);
}

/** The live step's blocks alone, for the component that draws them. */
export function useLiveBlocks(source: Source | undefined, fallback: StreamBlock[]): StreamBlock[] {
  return useSyncExternalStore(
    source ? source.subscribe : noSubscribe,
    source ? () => source.getSnapshot().blocks : () => fallback,
  );
}

const noSubscribe = () => () => {};
