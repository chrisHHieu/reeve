// What a running session is doing now, for its queue row (the layout's final mockup: "Running ·
// pytest tests/api"). Read from the conversation's own live state, which the app keeps
// for every conversation it holds, on screen or not; a session running where this window holds no
// state (another window, an automation nobody opened) has no line rather than a guessed one.
//
// The line changes at step boundaries only: while a call's arguments are still streaming it names
// the kind of work ("Running a command"), and the subject joins once the call is whole. A subject
// read from partial arguments changes with every fragment, and each change redraws the queue.

import { useEffect, useRef, useState } from "react";
import { liveIndex } from "../../streamBlocks";
import { activityLabel } from "../../toolMeta";
import { liveRegistry, type LiveRegistry, type LiveState } from "../../live/liveSession";

const SUBJECT_KEYS = ["path", "file_path", "file", "command", "pattern", "query", "url", "description"];
const PATH_KEYS = new Set(["path", "file_path", "file"]);
const SUBJECT_MAX = 60;

function parseArgs(args: unknown): Record<string, unknown> | null {
  if (args && typeof args === "object") return args as Record<string, unknown>;
  if (typeof args !== "string") return null;
  try {
    const parsed = JSON.parse(args);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

/** What a whole call acts on: a file's name, a command's first line, a pattern, a host. An MCP
 *  tool's arguments are its own business: nothing is read from them. */
export function callSubject(name: string, args: unknown): string | null {
  if (name.startsWith("mcp__")) return null;
  const a = parseArgs(args);
  if (!a) return null;
  const keys = name === "grep" ? ["pattern", ...SUBJECT_KEYS] : SUBJECT_KEYS;
  for (const key of keys) {
    const value = a[key];
    if (typeof value !== "string" || !value.trim()) continue;
    let text = value.trim().split("\n")[0];
    if (PATH_KEYS.has(key)) text = text.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || text;
    else if (key === "url") {
      try {
        text = new URL(text).host || text;
      } catch {
        /* keep it as written */
      }
    }
    return text.length > SUBJECT_MAX ? text.slice(0, SUBJECT_MAX - 1) + "…" : text;
  }
  return null;
}

/** The running conversation's line, or null when it is not running. */
export function liveLine(state: Pick<LiveState, "running" | "blocks" | "items" | "compacting">): string | null {
  if (!state.running) return null;
  if (state.compacting) return "Compacting context";
  const at = liveIndex(state.blocks);
  if (at >= 0) {
    const block = state.blocks[at];
    if (block.kind === "tool_call") return activityLabel(block.name);
    if (block.kind === "reasoning") return "Thinking";
    return "Writing";
  }
  // Between streams: the call that is running, as the transcript has it.
  for (let i = state.items.length - 1; i >= 0; i--) {
    const item = state.items[i];
    if (item.kind !== "tool") continue;
    if (item.status !== "…") break;
    const subject = callSubject(item.name, item.args);
    const label = activityLabel(item.name, item.looks);
    return subject ? `${label} · ${subject}` : label;
  }
  return "Working";
}

/** Each listed conversation's line, kept current from its live state. Only a changed line
 *  redraws. */
export function useLiveLines(ids: readonly string[], registry: LiveRegistry = liveRegistry): Record<string, string> {
  const [lines, setLines] = useState<Record<string, string>>({});
  // Compared here, not in an updater: an updater that hands back the same object still costs a
  // render of the queue on every streamed fragment.
  const shown = useRef(lines);
  const key = ids.join("\n");
  useEffect(() => {
    const ids = key ? key.split("\n") : [];
    const read = () => {
      const next: Record<string, string> = {};
      for (const id of ids) {
        const live = registry.get(id);
        const line = live ? liveLine(live.current) : null;
        if (line) next[id] = line;
      }
      const before = shown.current;
      if (Object.keys(next).length === Object.keys(before).length && Object.keys(next).every((id) => before[id] === next[id])) return;
      shown.current = next;
      setLines(next);
    };
    read();
    const offs = ids.map((id) => registry.get(id)?.subscribe(read));
    // A conversation the registry takes up after this ran (opened from the queue) is listed
    // when the registry says something changed.
    const offRegistry = registry.subscribe(read);
    return () => {
      for (const off of offs) off?.();
      offRegistry();
    };
  }, [key, registry]);
  return lines;
}
