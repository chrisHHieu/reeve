// Review by what was asked (owner, 2026-10-14, direction B of the Review mockup): the working
// tree's changes grouped by the person's request that made them, newest first, each with the
// check its turn ran; what changed in the folder outside this conversation is its own group, so
// it is never committed by surprise. Pure, so the pane stays layout.

import type { Item } from "../../types";
import { lastTurnFor, type RecordedChange } from "../../reviewChanges";
import type { ReviewFile } from "./reviewModel";

/** The check a turn ran after its last edit, as its receipt says. */
export interface AskCheck {
  ok: boolean | null;
  summary: string;
  command: string;
}

/** Each turn's check, keyed the way `recordedChangesFromItems` counts turns (the person's
 *  messages up to it). A check that edits followed proves nothing about them: not kept. */
export function turnChecks(items: readonly Item[]): Map<number, AskCheck> {
  const out = new Map<number, AskCheck>();
  let turn = 0;
  for (const item of items) {
    if (item.kind === "user") {
      turn += 1;
      continue;
    }
    if (item.kind !== "changes") continue;
    const check = (item as Extract<Item, { kind: "changes" }>).check;
    if (check && check.editsSince === 0) out.set(turn, { ok: check.ok, summary: check.summary, command: check.command });
    else out.delete(turn);
  }
  return out;
}

/** A path a turn's receipt says the turn changed, by any tool, a command included. */
export interface TurnTouch {
  turn: number;
  text: string;
  path: string;
}

/** Every path each turn's receipt names, with the turn's words; the agent's own notes apart. */
export function turnTouches(items: readonly Item[]): TurnTouch[] {
  const out: TurnTouch[] = [];
  let turn = 0;
  let text = "";
  for (const item of items) {
    if (item.kind === "user") {
      turn += 1;
      text = item.text;
      continue;
    }
    if (item.kind !== "changes") continue;
    const receipt = item as Extract<Item, { kind: "changes" }>;
    const notes = new Set(receipt.notes ?? []);
    const paths = new Set([
      ...(receipt.created ?? []),
      ...(receipt.overwritten ?? []),
      ...(receipt.deleted ?? []),
      ...(receipt.stats ?? []).map((s) => s.path),
    ]);
    for (const path of paths) if (!notes.has(path)) out.push({ turn, text, path });
  }
  return out;
}

export interface AskGroup {
  turn: number;
  /** The person's words that started the turn (its first line is the heading). */
  text: string;
  /** The files whose newest change was made in this turn, in the order of `files`. */
  files: ReviewFile[];
  add: number;
  del: number;
  check?: AskCheck;
  /** Every change the turn made can be put back (each has a saved copy). */
  undoable: boolean;
  /** A later turn changed one of its files again: that one is undone first. */
  blockedBy?: number;
}

/** The working tree's files by the request whose turn changed each last, newest first, and
 *  the files no turn of this conversation changed. A turn changed a file when one of its tool
 *  calls recorded the edit, or its receipt names the file (a command's edits have no diff of
 *  their own, but the receipt saw them). */
export function groupByAsk(
  files: ReviewFile[],
  recorded: RecordedChange[],
  roots: string[],
  checks: Map<number, AskCheck> = new Map(),
  touches: readonly TurnTouch[] = [],
): { asks: AskGroup[]; outside: ReviewFile[] } {
  const marks = [...recorded.map((change) => ({ path: change.path, turn: change.turn ?? 0 })), ...touches];
  const byTurn = new Map<number, ReviewFile[]>();
  const outside: ReviewFile[] = [];
  for (const file of files) {
    const turn = lastTurnFor(marks, file.path, roots);
    if (turn === undefined) outside.push(file);
    else byTurn.set(turn, [...(byTurn.get(turn) ?? []), file]);
  }
  const asks = [...byTurn.entries()]
    .sort((a, b) => b[0] - a[0])
    .map(([turn, list]) => {
      const made = recorded.filter((change) => (change.turn ?? 0) === turn);
      const later = recorded.filter(
        (change) => (change.turn ?? 0) > turn && made.some((own) => own.path === change.path),
      );
      return {
        turn,
        text: made.find((change) => change.turnText)?.turnText ?? touches.find((t) => t.turn === turn)?.text ?? "",
        files: list,
        add: list.reduce((sum, file) => sum + file.add, 0),
        del: list.reduce((sum, file) => sum + file.del, 0),
        check: checks.get(turn),
        // A file a command changed has no saved copy to go back to: such a request is not
        // offered an Undo that would put back only part of it.
        undoable:
          made.length > 0 &&
          made.every((change) => change.undoable && !!change.callId) &&
          list.every((file) => lastTurnFor(made, file.path, roots) === turn),
        blockedBy: later.length ? Math.max(...later.map((change) => change.turn ?? 0)) : undefined,
      };
    });
  return { asks, outside };
}

/** Said on `window` with `{ text }`: the person wants a change to what one request made. The
 *  app puts the request's words into the composer for them to finish and send. */
export const ASK_CHANGE_EVENT = "reeve:ask-change";

export function askForChange(request: string): void {
  window.dispatchEvent(new CustomEvent(ASK_CHANGE_EVENT, { detail: { text: request } }));
}

/** What the composer is given for a request: its first line, quoted, and room to say what to change. */
export function askChangeDraft(request: string): string {
  const first = request.split("\n")[0].trim();
  const quoted = first.length > 90 ? `${first.slice(0, 89)}…` : first;
  return quoted ? `About “${quoted}”: ` : "";
}
