import { structuredPatch } from "diff";
import type { FileDiff } from "./cards/types";
import { diffChurn } from "./cards/diffChurn";
import type { Item } from "./types";

export type RecordedChangeStatus = "added" | "deleted" | "modified";

export interface RecordedChange {
  id: string;
  callId?: string;
  /** The tool that made the change (`replace_in_file`, `write_file`, …). */
  tool: string;
  path: string;
  status: RecordedChangeStatus;
  added: number;
  removed: number;
  diffs: FileDiff[];
  undoable: boolean;
  /** The turn that made it: 1-based count of the person's messages up to it (0 = before
   *  any), and that message's text — Review groups a session's changes by the ask. */
  turn?: number;
  turnText?: string;
}

export interface RecordedDiffRow {
  kind: "ctx" | "add" | "del";
  old?: number;
  new?: number;
  text: string;
}

export interface RecordedChangesSummary {
  changeCount: number;
  fileCount: number;
  additions: number;
  deletions: number;
}

function stripTrailingNewline(text: string): string {
  return text.endsWith("\n") ? text.slice(0, -1) : text;
}

function changeStatus(diff: FileDiff): RecordedChangeStatus {
  if (diff.old_text === null) return "added";
  return stripTrailingNewline(diff.new_text).trim() === "" ? "deleted" : "modified";
}

function diffsFromCard(item: Extract<Item, { kind: "tool" }>): FileDiff[] {
  const card = item.presentation?.card ?? item.callPresentation?.card;
  if (!card || card.card !== "diff") return [];
  const diffs = (card as { diffs?: unknown }).diffs;
  if (!Array.isArray(diffs)) return [];
  return diffs.filter(
    (diff): diff is FileDiff =>
      !!diff &&
      typeof diff === "object" &&
      typeof (diff as FileDiff).path === "string" &&
      typeof (diff as FileDiff).new_text === "string" &&
      ((diff as FileDiff).old_text === null || typeof (diff as FileDiff).old_text === "string"),
  );
}

export function recordedChangesFromItems(items: Item[]): RecordedChange[] {
  const changes: RecordedChange[] = [];
  let turn = 0;
  let turnText = "";
  for (const item of items) {
    if (item.kind === "user") {
      turn += 1;
      turnText = item.text;
      continue;
    }
    if (item.kind !== "tool") continue;
    for (const diff of diffsFromCard(item)) {
      changes.push({
        id: `${item.callId ?? item.id}:${diff.path}`,
        callId: item.callId,
        tool: item.name,
        path: diff.path,
        status: changeStatus(diff),
        ...diffChurn(diff),
        diffs: [diff],
        undoable: item.undoable === true,
        turn,
        turnText,
      });
    }
  }
  return changes;
}

/** One file's changes in this conversation — the Recorded list's row (review audit, V7:
 *  two edits of one file were two cards). */
export interface RecordedFile {
  path: string;
  /** Relative to the root that holds it, else the path as recorded (V3). */
  displayPath: string;
  status: RecordedChangeStatus;
  added: number;
  removed: number;
  /** "replace_in_file ×2 · write_file" — what touched it, in order of first use. */
  tools: string;
  changes: RecordedChange[];
  /** Every change of the file can be put back; a partial undo would leave a state no call
   *  ever wrote, so a file with one non-undoable change offers none. */
  undoable: boolean;
}

function normalized(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+$/, "");
}

/** `path` relative to the first root that contains it — separators and case folded, since
 *  a Windows root and a recorded path disagree on both. Outside every root: as recorded. */
export function relativeToRoots(path: string, roots: string[]): string {
  const target = normalized(path);
  const lower = target.toLowerCase();
  for (const root of roots) {
    const base = normalized(root);
    if (!base) continue;
    if (lower === base.toLowerCase()) return target.split("/").pop() || target;
    if (lower.startsWith(base.toLowerCase() + "/")) return target.slice(base.length + 1);
  }
  return path;
}

export function recordedFilesFromChanges(changes: RecordedChange[], roots: string[] = []): RecordedFile[] {
  const byPath = new Map<string, RecordedChange[]>();
  for (const change of changes) {
    const bucket = byPath.get(change.path);
    if (bucket) bucket.push(change);
    else byPath.set(change.path, [change]);
  }
  return [...byPath.entries()].map(([path, list]) => {
    const counts = new Map<string, number>();
    for (const change of list) counts.set(change.tool, (counts.get(change.tool) ?? 0) + 1);
    const first = list[0].status;
    const last = list[list.length - 1].status;
    return {
      path,
      displayPath: relativeToRoots(path, roots),
      status: last === "deleted" ? "deleted" : first === "added" ? "added" : "modified",
      added: list.reduce((sum, change) => sum + change.added, 0),
      removed: list.reduce((sum, change) => sum + change.removed, 0),
      tools: [...counts.entries()].map(([tool, n]) => (n > 1 ? `${tool} ×${n}` : tool)).join(" · "),
      changes: list,
      undoable: list.every((change) => change.undoable && !!change.callId),
    };
  });
}

export function summarizeRecordedChanges(changes: RecordedChange[]): RecordedChangesSummary {
  const files = new Set<string>();
  let additions = 0;
  let deletions = 0;
  for (const change of changes) {
    files.add(change.path);
    additions += change.added;
    deletions += change.removed;
  }
  return { changeCount: changes.length, fileCount: files.size, additions, deletions };
}

export function diffRowsForChange(change: RecordedChange): RecordedDiffRow[] {
  const diff = change.diffs[0];
  if (!diff) return [];
  const oldText = diff.old_text === null ? "" : stripTrailingNewline(diff.old_text);
  const newText = stripTrailingNewline(diff.new_text);
  const patch = structuredPatch(diff.path, diff.path, oldText, newText, "", "", { context: 3 });
  // The sides are a context window of the file; its first line is `old_start`/`new_start`
  // there, so numbering from 1 put line 41 at line 2 (V6).
  const oldBase = (diff.old_start ?? 1) - 1;
  const newBase = (diff.new_start ?? 1) - 1;
  const rows: RecordedDiffRow[] = [];
  for (const hunk of patch.hunks) {
    let oldNo = hunk.oldStart + oldBase;
    let newNo = hunk.newStart + newBase;
    for (const line of hunk.lines) {
      const prefix = line.charAt(0);
      const text = line.slice(1);
      if (prefix === "\\") continue;
      if (prefix === "-") rows.push({ kind: "del", old: oldNo++, text });
      else if (prefix === "+") rows.push({ kind: "add", new: newNo++, text });
      else rows.push({ kind: "ctx", old: oldNo++, new: newNo++, text });
    }
  }
  return rows;
}

/** One ask's changes: the turn, the person's words, and each file it touched (V7's per-file
 *  merge, inside the turn). `blockedBy` names a file a LATER turn changed again — undoing
 *  this turn's copy would restore content that overwrites the later edit, so its undo waits
 *  for the later one. Newest turn first. */
export interface RecordedTurn {
  turn: number;
  text: string;
  files: (RecordedFile & { blockedBy?: number })[];
  added: number;
  removed: number;
}

export function recordedTurnsFromChanges(changes: RecordedChange[], roots: string[] = []): RecordedTurn[] {
  const byTurn = new Map<number, RecordedChange[]>();
  for (const change of changes) {
    const key = change.turn ?? 0;
    const bucket = byTurn.get(key);
    if (bucket) bucket.push(change);
    else byTurn.set(key, [change]);
  }
  const lastTurnOf = new Map<string, number>();
  for (const change of changes) {
    lastTurnOf.set(change.path, Math.max(lastTurnOf.get(change.path) ?? 0, change.turn ?? 0));
  }
  return [...byTurn.entries()]
    .sort((a, b) => b[0] - a[0])
    .map(([turn, list]) => {
      const files = recordedFilesFromChanges(list, roots).map((file) => {
        const later = lastTurnOf.get(file.path) ?? turn;
        return later > turn ? { ...file, blockedBy: later } : file;
      });
      return {
        turn,
        text: list[0].turnText ?? "",
        files,
        added: files.reduce((sum, file) => sum + file.added, 0),
        removed: files.reduce((sum, file) => sum + file.removed, 0),
      };
    });
}

/** The newest turn that changed `path` (absolute or root-relative), or undefined. */
export function lastTurnFor(changes: readonly Pick<RecordedChange, "path" | "turn">[], path: string, roots: string[] = []): number | undefined {
  const want = normalized(path).toLowerCase();
  const rel = relativeToRoots(path, roots).toLowerCase();
  let found: number | undefined;
  for (const change of changes) {
    const own = normalized(change.path).toLowerCase();
    const ownRel = relativeToRoots(change.path, roots).toLowerCase();
    if (own === want || ownRel === rel || own.endsWith("/" + rel)) {
      found = Math.max(found ?? 0, change.turn ?? 0);
    }
  }
  return found;
}
