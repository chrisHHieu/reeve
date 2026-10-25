// Row expansion state, hoisted out of the rows themselves.
//
// A row cannot own its `open` flag in this transcript, and the reason is specific: a group
// row (Explored, a tool family, an MCP server) renders `{open && rows}`, so closing it
// UNMOUNTS every row inside. Component-local state would be gone, and opening the group again
// would show every row closed — the reader would lose the exact place they were reading,
// which is the only reason they had a row open. (The turn itself no longer folds, since
// 2026-09-28; the group rows still do.)
//
// The set lives in a provider mounted by `Transcript`, which stays mounted across group
// toggles. `useRowOpen` falls back to local state when there is no provider, so a row can be
// rendered standalone (a test) without one.
//
// The store is a subscription, not a context VALUE. It used to be `{ isOpen, toggle }`
// rebuilt on every toggle, and a new context value re-renders every consumer past every
// memo: opening one row re-rendered every tool row of the whole conversation, which in a
// long session is the wait between the click and the body appearing. The context now holds
// one object for the provider's life and each row subscribes to its own boolean, so a toggle
// re-renders the row that changed and nothing else (pinned in rowState.test.tsx).

import {
  createContext,
  useCallback,
  useContext,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

// The store also remembers, per call, the two things a row's own refs cannot keep across that
// same unmount: that this view watched it run, and that the person has opened or closed it
// (from then on the harness leaves it alone). A call that finishes is folded or opened by those two facts (Settings ▸ General ▸ Open
// tools when they finish), and a row is remounted exactly when a call finishes: the second
// look of a run moves the first into an "Explored" group.

export interface RowStateStore {
  isOpen: (key: string) => boolean;
  /** The person's click. From then on the row is theirs: the harness never opens or folds it. */
  toggle: (key: string) => void;
  /** The harness opens or folds a row, unless the person has opened or closed it. */
  setByHarness: (key: string, open: boolean) => void;
  /** This view saw the call running. */
  sawRunning: (key: string) => void;
  /** Whether this view saw the call running and has not yet handled its finish; true once. */
  takeFinish: (key: string) => boolean;
  subscribe: (listener: () => void) => () => void;
}

export function createRowStateStore(): RowStateStore {
  const keys = new Set<string>();
  const theirs = new Set<string>();
  const running = new Set<string>();
  const listeners = new Set<() => void>();
  const changed = () => {
    for (const listener of [...listeners]) listener();
  };
  return {
    isOpen: (key) => keys.has(key),
    toggle: (key) => {
      theirs.add(key);
      if (!keys.delete(key)) keys.add(key);
      changed();
    },
    setByHarness: (key, open) => {
      if (theirs.has(key) || open === keys.has(key)) return;
      if (open) keys.add(key);
      else keys.delete(key);
      changed();
    },
    sawRunning: (key) => {
      running.add(key);
    },
    takeFinish: (key) => running.delete(key),
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

const RowStateContext = createContext<RowStateStore | null>(null);

export function ToolRowStateProvider({ children }: { children: ReactNode }) {
  const [store] = useState(createRowStateStore);
  return <RowStateContext.Provider value={store}>{children}</RowStateContext.Provider>;
}

/** The transcript's store, or the row's own where there is no provider (a row drawn alone). */
export function useRowStore(): RowStateStore {
  const shared = useContext(RowStateContext);
  const [own] = useState(createRowStateStore);
  return shared ?? own;
}

/** `[open, toggle]` for one row, keyed by something stable — the tool call id. */
export function useRowOpen(key: string, store?: RowStateStore): [boolean, () => void] {
  const fallback = useRowStore();
  const s = store ?? fallback;
  const open = useSyncExternalStore(s.subscribe, () => s.isOpen(key));
  const toggle = useCallback(() => s.toggle(key), [s, key]);
  return [open, toggle];
}
