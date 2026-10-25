// The live step, as an ordered list of blocks.
//
// The browser half of `harness/providers/blocks.py`. A step is rarely "one thought and
// one answer": it is reasoning, then a sentence, then a tool call, then more reasoning,
// and the ORDER is what a person watching is reading. This surface used to hold two flat
// strings — `streaming` and `reasoningStream` — which cannot say which came first, so the
// live view showed thinking only while there was no text yet and dropped it the moment a
// sentence arrived.
//
// Ported from the `PartialAccumulator` (`packages/client/runtime/src/client/sessions/
// partial.ts`), which folds its adapters' block-indexed chunks the same way. The index
// arrives on every delta, so the fold is a lookup rather than a guess.
//
// Block-level immutability, as in the reference design: a delta swaps ONE block's object and leaves every
// other reference alone, so React re-renders the block that changed rather than the step.
//
// Pure on purpose (cf. todoPlan/modeNotice): the caller owns the state, so the rule is
// testable without rendering the app.

/** One block of a streaming step. `text` and `reasoning` differ only in how they render. */
export type StreamBlock =
  | { kind: "text"; text: string }
  | { kind: "reasoning"; text: string }
  | { kind: "tool_call"; callId: string; name: string; argsRaw: string };

/** The wire shape of one delta — `harness/providers/blocks.py`'s `BlockDelta.as_dict`. */
export interface BlockDeltaEvent {
  index?: number;
  block?: string;
  text?: string;
  call_id?: string;
  name?: string;
  arguments_delta?: string;
}

/** An empty block of the given kind, ready to receive its first delta. */
function emptyBlock(kind: string): StreamBlock {
  if (kind === "reasoning") return { kind: "reasoning", text: "" };
  if (kind === "tool_call") return { kind: "tool_call", callId: "", name: "", argsRaw: "" };
  return { kind: "text", text: "" };
}

/**
 * Fold one delta into the block list.
 *
 * `kind` comes from the EVENT TYPE the caller received, not from the payload: the three
 * delta events each mean exactly one kind, and reading it off the wire would make a
 * missing field able to turn thinking into prose.
 *
 * The index is the payload's when it has one. A server older than block streaming sends
 * `{text}` alone, and the fallback is the same rule its Python counterpart applies —
 * continue the last block of this kind, open a new one when the kind changed. That is a
 * bridge, not a second implementation: both ends agree that a change of kind opens a
 * block, and the index only spares this side from having to re-derive it.
 *
 * A delta for an index beyond the end extends the list. The server assigns indexes in
 * arrival order, so it only happens if a frame was dropped, and a gap left as an empty
 * block is a smaller lie than shifting everything after it onto the wrong index.
 */
export function foldBlock(
  blocks: StreamBlock[],
  kind: StreamBlock["kind"],
  event: BlockDeltaEvent,
): StreamBlock[] {
  const raw = event?.index;
  const indexed = typeof raw === "number" && Number.isInteger(raw) && raw >= 0;
  if (!indexed && raw !== undefined) return blocks; // a malformed index is not a gap
  const index = indexed
    ? (raw as number)
    : blocks.length > 0 && blocks[blocks.length - 1].kind === kind
      ? blocks.length - 1
      : blocks.length;

  const next = blocks.slice();
  while (next.length <= index) next.push(emptyBlock(next.length === index ? kind : "text"));
  const current = next[index];
  const base = current !== undefined && current.kind === kind ? current : emptyBlock(kind);

  if (base.kind === "tool_call") {
    next[index] = {
      kind: "tool_call",
      // The id and name arrive with the first fragment and repeat on the rest; an empty
      // one means "not sent yet", so it must never overwrite one already known.
      callId: event.call_id || base.callId,
      name: event.name || base.name,
      argsRaw: base.argsRaw + (event.arguments_delta || ""),
    };
  } else {
    next[index] = { kind: base.kind, text: base.text + (event.text || "") };
  }
  return next;
}

/** Every text block joined — what the answer bubble shows. */
export function textOf(blocks: readonly StreamBlock[]): string {
  return blocks.map((b) => (b.kind === "text" ? b.text : "")).join("");
}

/** Every reasoning block joined — the trace that folds into the finalized message. */
export function reasoningOf(blocks: readonly StreamBlock[]): string {
  return blocks.map((b) => (b.kind === "reasoning" ? b.text : "")).join("");
}

/** The edit tools whose partial arguments carry a `+N`/`−M` a reader is watching arrive. */
const EDIT_TOOLS = new Set([
  "write_file",
  "replace_in_file",
  "apply_patch",
  "apply_unified_diff",
]);

/** Lines in a string as a file would count them: a trailing newline closes the last line. */
function lineCount(text: string): number {
  if (!text) return 0;
  const n = text.split("\n").length;
  return text.endsWith("\n") ? n - 1 : n;
}

const UNESCAPE: Record<string, string> = {
  n: "\n",
  r: "\r",
  t: "\t",
  '"': '"',
  "\\": "\\",
  "/": "/",
};

/**
 * The string value of a key inside PARTIAL JSON, up to the closing quote (or the end of
 * what has arrived). `JSON.parse` cannot run here — the arguments are mid-stream — so the
 * value is read by hand, honouring the backslash escapes that matter for line counting
 * (`\n`, `\t`, `\"`, `\\`). Returns null when the key has not been seen yet.
 */
function partialString(raw: string, key: string): string | null {
  const re = new RegExp(`"${key}"\\s*:\\s*"`);
  const match = re.exec(raw);
  if (!match) return null;
  let i = match.index + match[0].length;
  let out = "";
  while (i < raw.length) {
    const c = raw[i];
    if (c === "\\") {
      const next = raw[i + 1];
      if (next === undefined) break; // a dangling escape: nothing more has arrived
      out += UNESCAPE[next] ?? next;
      i += 2;
      continue;
    }
    if (c === '"') break;
    out += c;
    i += 1;
  }
  return out;
}

/** The argument that names what a call acts on, per tool, in the order they are tried. */
const SUBJECT_KEYS = ["path", "file_path", "file", "command", "pattern", "query", "url", "name", "description"];

/**
 * What an in-flight call acts on — a path, a command, a pattern — read from its partial
 * arguments, unescaped. Null until that value has started arriving. The live row shows
 * this instead of the raw argument JSON, which put `{"command": "python3 -c \"\nimport…`
 * on screen for the whole stream (GUI review F1). An MCP tool's arguments are its own
 * business: nothing is read from them.
 */
export function liveSubject(name: string, argsRaw: string): string | null {
  if (!argsRaw || name.startsWith("mcp__")) return null;
  // A search's subject is what it looks for, not where.
  const keys = name === "grep" ? ["pattern", ...SUBJECT_KEYS] : SUBJECT_KEYS;
  for (const key of keys) {
    const value = partialString(argsRaw, key);
    if (value !== null && value.trim()) return value;
  }
  return null;
}

export interface ArgChurn {
  added: number;
  removed: number;
}

/**
 * The churn an in-flight edit call has ALREADY described, from its partial arguments.
 *
 * The engine streams `tool_call.arguments` a fragment at a time; the GUI reads the
 * `content`/`newString`/`oldString` that has landed and counts its lines, so a long write
 * shows `+N` climbing before the tool has even run — the same number the finished diff will
 * carry, just earlier. Only the edit family is considered, so a stray `content` argument on
 * an unrelated tool cannot grow the row a churn it does not have.
 */
export function churnFromArgs(name: string, argsRaw: string): ArgChurn | null {
  if (!argsRaw || !EDIT_TOOLS.has(name)) return null;
  const content = partialString(argsRaw, "content");
  // Reeve's `replace_in_file` names them `old`/`new`; the longer spellings are MCP's and
  // other harnesses'. Reading only those made every Reeve edit fall back to the raw JSON
  // preview for its whole stream (GUI review F1).
  const newString =
    partialString(argsRaw, "new") ??
    partialString(argsRaw, "new_string") ??
    partialString(argsRaw, "new_str");
  const oldString =
    partialString(argsRaw, "old") ??
    partialString(argsRaw, "old_string") ??
    partialString(argsRaw, "old_str");
  const patch = partialString(argsRaw, "patch") ?? partialString(argsRaw, "diff");
  let added = 0;
  let removed = 0;
  let seen = false;
  if (content !== null) {
    added += lineCount(content);
    seen = true;
  }
  if (newString !== null) {
    added += lineCount(newString);
    seen = true;
  }
  if (oldString !== null) {
    removed += lineCount(oldString);
    seen = true;
  }
  if (patch !== null) {
    for (const line of patch.split("\n")) {
      if (line.startsWith("+++") || line.startsWith("---")) continue;
      if (line.startsWith("+")) added += 1;
      else if (line.startsWith("-")) removed += 1;
    }
    seen = true;
  }
  if (!seen) return null;
  return { added, removed };
}
/**
 * Whether this block is the one currently being written.
 *
 * The last block that has anything in it, not simply the last one: a `tool_call` block
 * opens with an empty argument string, and treating that as "the live block" would move
 * the running marker off the reasoning a person is still reading.
 */
export function liveIndex(blocks: readonly StreamBlock[]): number {
  for (let i = blocks.length - 1; i >= 0; i--) {
    const block = blocks[i];
    if (block.kind === "tool_call" ? block.name !== "" : block.text !== "") return i;
  }
  return -1;
}

/**
 * The newest line of the trace being written, or undefined when the live block is not a
 * trace. The running turn's status line shows it (design B, 2026-09-28): one live line at
 * the bottom instead of a "Thinking" row above the status.
 */
export function liveThoughtOf(blocks: readonly StreamBlock[]): string | undefined {
  const at = liveIndex(blocks);
  const block = at < 0 ? undefined : blocks[at];
  if (!block || block.kind !== "reasoning") return undefined;
  const lines = block.text.trimEnd().split("\n");
  const line = lines[lines.length - 1].trim();
  return line === "" ? undefined : line;
}
