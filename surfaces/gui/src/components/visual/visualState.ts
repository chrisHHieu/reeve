// Where the person left a visual, said to the model with their next message.
//
// A what-if is recomputed in the GUI as the person drags, with no request to the model, so a
// question like "and at this rate?" reached a model that did not know the rate. A drawn what-if
// registers how to say where it stands; the next message the person sends carries one line per
// visual that was moved since it was shown (or since the last message said where it was):
//
//   [v1 as the user left it: Lãi B sau 2 năm = 9%. It shows Gói A · trả hàng tháng = 13.495.889 đ]
//
// The line is part of the message's text, so it is in the session log as the person's message
// and the model reads it exactly as logged. The person's own bubble shows it as a chip.

import { createContext } from "react";

/** Which visual a block belongs to: its name and its call. Set where the answer draws it. */
export const VisualIdentity = createContext<{ id: string; callId: string } | null>(null);

type Reader = () => { id: string; line: string } | null;

const readers = new Map<string, Reader>();
/** What the last message said of each visual, so an unmoved one is not said again. */
const said = new Map<string, string>();

// Where the person left each visual, kept in this browser across a reload, keyed by the block
// (its call and inputs). ChatGPT's generated interfaces lose it on reload (Thesys's teardown,
// 2026-10); here the sliders come back where they were, and what the last message said of them
// comes back too, so a reload does not make the next message say it again. A view's own state,
// like a scroll position: per browser, and nothing breaks when storage is refused.
const STORE = "reeve.visualState.v1";
const KEEP = 200;

interface Kept {
  inputs?: Record<string, number>;
  said?: string;
  at: number;
}

function readStore(): Record<string, Kept> {
  try {
    const raw = localStorage.getItem(STORE);
    const value = raw ? JSON.parse(raw) : {};
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
}

function writeKept(key: string, patch: Partial<Kept>): void {
  try {
    const all = readStore();
    all[key] = { ...all[key], ...patch, at: Date.now() };
    const keys = Object.keys(all);
    if (keys.length > KEEP) {
      keys.sort((a, b) => all[a].at - all[b].at).slice(0, keys.length - KEEP).forEach((k) => delete all[k]);
    }
    localStorage.setItem(STORE, JSON.stringify(all));
  } catch {
    // Storage refused (private window, quota): the sliders simply start where the model put them.
  }
}

/** Where the person left a block's inputs before this view, if this browser kept it. */
export function keptInputs(key: string): Record<string, number> | null {
  const inputs = readStore()[key]?.inputs;
  return inputs && typeof inputs === "object" ? inputs : null;
}

export function keepInputs(key: string, inputs: Record<string, number>): void {
  writeKept(key, { inputs });
}

/** A drawn visual says how to read where it stands; the returned function forgets it. */
export function registerVisualState(key: string, read: Reader): () => void {
  readers.set(key, read);
  if (!said.has(key)) {
    const before = readStore()[key]?.said;
    if (typeof before === "string") said.set(key, before);
  }
  return () => {
    if (readers.get(key) === read) readers.delete(key);
  };
}

/** The lines for the visuals moved since they were last said, marked as said. */
export function takeVisualStates(): string[] {
  const lines: string[] = [];
  for (const [callId, read] of readers) {
    let state: ReturnType<Reader> = null;
    try {
      state = read();
    } catch {
      state = null;
    }
    if (!state || said.get(callId) === state.line) continue;
    said.set(callId, state.line);
    writeKept(callId, { said: state.line });
    lines.push(`[${state.id} as the user left it: ${state.line}]`);
  }
  return lines;
}

/** The message text with the lines appended after a blank line. */
export function withVisualStates(text: string, lines: string[]): string {
  return lines.length ? `${text}${text ? "\n\n" : ""}${lines.join("\n")}` : text;
}

const LINE = /^\[(v\d+) as the user left it: (.*)\]$/;

/** A sent message split into what the person typed and the visual lines it carried. */
export function splitVisualStates(text: string): { text: string; states: { id: string; state: string }[] } {
  const lines = text.split("\n");
  const states: { id: string; state: string }[] = [];
  while (lines.length > 0) {
    const match = LINE.exec(lines[lines.length - 1]);
    if (!match) break;
    states.unshift({ id: match[1], state: match[2] });
    lines.pop();
  }
  if (states.length === 0) return { text, states };
  return { text: lines.join("\n").replace(/\s+$/, ""), states };
}

/** For tests: start from nothing. */
export function resetVisualStates(): void {
  readers.clear();
  said.clear();
  try {
    localStorage.removeItem(STORE);
  } catch {
    // no storage in this environment
  }
}
