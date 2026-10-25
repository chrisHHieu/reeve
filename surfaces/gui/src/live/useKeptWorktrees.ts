// Which of a session's children still have their own copy of the repository.
//
// The server is the one that knows: a copy is a folder on disk, kept only while it holds
// work, and removed by an Apply or a Discard that may have happened in another window. So
// the roster asks — once when the session's delegations are first seen, again each time one
// of them settles (that is when a copy starts to wait), and again when this window handed one
// back. It never polls: nothing changes a copy between those moments.

import { useEffect, useState } from "react";
import { getSessionChildren } from "../api/core";
import { WORKTREE_CHANGED } from "../components/session/WorktreeHandback";

const NONE: ReadonlySet<string> = new Set();

function same(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  if (a.size !== b.size) return false;
  for (const id of a) if (!b.has(id)) return false;
  return true;
}

/**
 * The ids of `sessionId`'s children that have work waiting in their own copy.
 *
 * `delegations` and `settled` are how many child runs the conversation holds and how many of
 * them are over; a session with none is never asked about.
 */
export function useKeptWorktrees(sessionId: string, delegations: number, settled: number): ReadonlySet<string> {
  const [kept, setKept] = useState<ReadonlySet<string>>(NONE);
  const [handed, setHanded] = useState(0);

  useEffect(() => {
    const bump = () => setHanded((n) => n + 1);
    window.addEventListener(WORKTREE_CHANGED, bump);
    return () => window.removeEventListener(WORKTREE_CHANGED, bump);
  }, []);

  useEffect(() => {
    if (!sessionId || delegations === 0) {
      setKept((prev) => (prev.size === 0 ? prev : NONE));
      return;
    }
    let alive = true;
    getSessionChildren(sessionId)
      .then((rows) => {
        if (!alive) return;
        const next = new Set(rows.filter((row) => row.worktree === true).map((row) => row.session_id));
        // The same set keeps its identity: the roster's rows are memoized on it.
        setKept((prev) => (same(prev, next) ? prev : next));
      })
      .catch(() => {
        /* a roster without the handback bar is still a roster */
      });
    return () => {
      alive = false;
    };
  }, [sessionId, delegations, settled, handed]);

  return kept;
}
