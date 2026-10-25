// `upload_client.py:42` in an answer opens that file at that line, when the conversation's
// folder has the file (owner, 2026-10-13). 79 answers in the store name a file with a line, and
// reading the line meant opening the file and scrolling by hand.
//
// It is not a guess from the spelling: the token is looked up in the folder's own file list (the
// one the composer's "@" reads, `GET /v1/sessions/{id}/files/index`), read once per conversation
// and only when an answer holds such a token. A name the folder does not have, a name two files
// share (unless the turn delivered one of them), a folder too large to list whole past the
// point it was cut, and a conversation on another machine (the list is refused there) all leave
// the token as code.

import { useEffect, useSyncExternalStore } from "react";
import { sessionFileIndex } from "../../api";

/** `path.ext:12`, `path.ext:12:5`, `path.ext:12-20`, `path.ext#L12`, `path.ext#L12-L20`. */
const WITH_LINE = /^(.+\.[A-Za-z0-9]{1,8})(?::(\d+)(?:[:-]\d+)?|#L(\d+)(?:-L?\d+)?)$/;

/** The file and the line a code span names, or null when it is not `file:line`. */
export function fileWithLine(token: string): { path: string; line: number } | null {
  const found = WITH_LINE.exec(token.trim());
  if (!found) return null;
  const line = Number(found[2] ?? found[3]);
  const path = found[1];
  if (!Number.isSafeInteger(line) || line < 1 || /\s/.test(path) || /^[a-z]+:\/\//i.test(path)) return null;
  return { path, line };
}

const norm = (path: string) => path.replace(/\\/g, "/").replace(/^\.\//, "");

/**
 * The folder's own path for what a token names: the same relative path; an absolute path that
 * ends with one of the folder's paths (the longest, so `a/x.ts` beats `x.ts`); or a path that
 * one and only one of the folder's files ends with. Two candidates are settled by the turn's own
 * files (`preferred`), else nothing.
 */
export function resolveInFolder(
  written: string,
  files: readonly string[],
  preferred?: (token: string) => string | undefined,
): string | undefined {
  const token = norm(written);
  const lower = token.toLowerCase();
  const exact = files.find((file) => file === token) ?? files.find((file) => file.toLowerCase() === lower);
  if (exact) return exact;
  if (/^([a-z]:\/|\/)/i.test(token)) {
    const inside = files.filter((file) => lower.endsWith(`/${file.toLowerCase()}`));
    return inside.sort((a, b) => b.length - a.length)[0];
  }
  const tail = `/${lower}`;
  const matches = files.filter((file) => `/${file.toLowerCase()}`.endsWith(tail));
  if (matches.length === 1) return matches[0];
  if (matches.length > 1) {
    const turn = preferred?.(written);
    return turn && matches.includes(norm(turn)) ? norm(turn) : undefined;
  }
  return undefined;
}

// ---- the folder's file list, once per conversation ----------------------------------------

type Entry = { files: readonly string[] | null; at: number; loading: boolean };

const STALE_MS = 60_000;
const lists = new Map<string, Entry>();
const listeners = new Set<() => void>();
const EMPTY: readonly string[] = [];

function load(sessionId: string) {
  const known = lists.get(sessionId);
  if (known && (known.loading || Date.now() - known.at < STALE_MS)) return;
  lists.set(sessionId, { files: known?.files ?? null, at: known?.at ?? 0, loading: true });
  sessionFileIndex(sessionId)
    .then((index) => ({ files: index.ok ? index.files : EMPTY }))
    .catch(() => ({ files: EMPTY }))
    .then(({ files }) => {
      lists.set(sessionId, { files, at: Date.now(), loading: false });
      listeners.forEach((listener) => listener());
    });
}

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => void listeners.delete(listener);
};

/**
 * The conversation's files, asked for only by a component that needs them (`wanted`), and read
 * again when the last read is over a minute old (a file the turn just made is then found).
 */
export function useFolderFiles(sessionId: string | undefined, wanted: boolean): readonly string[] | null {
  const active = wanted && !!sessionId;
  useEffect(() => {
    if (active) load(sessionId!);
  }, [active, sessionId]);
  return useSyncExternalStore(subscribe, () => (active ? (lists.get(sessionId!)?.files ?? null) : null));
}

/** For tests: forget every list read. */
export function resetFolderFiles() {
  lists.clear();
}
