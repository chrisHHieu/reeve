// What a finished turn actually did, in one line.
//
// The settled header used to say only "Worked for 12s · 5 steps". That answers the clock and
// nothing else — the questions a reader has about a turn they did not watch are "what did it
// touch", "did it run anything", "did anything fail". The reference design puts the same facts on its
// completion row (files, +N/−N, View Changes); this is our version of that row, computed from
// the turn's own items so it is correct on replay as well as live.
//
// It is a pure fold on purpose: a summary that disagrees with the rows beneath it is worse
// than no summary, and the only way to keep them in step is to derive both from one input.

import type { Item } from "./types";
import type { ToolItem } from "./cards/cardModels";
import { toolState } from "./cards/cardModels";
import { HIDDEN_TOOLS, isCommandTool } from "./toolMeta";
import { diffChurn } from "./cards/diffChurn";

export interface TurnSummary {
  /** Calls the turn made, hidden bookkeeping excluded. */
  steps: number;
  /** Shell/terminal calls. */
  commands: number;
  /** Distinct files the turn changed. */
  files: number;
  added: number;
  removed: number;
  failed: number;
}

/** Files a call changed, when its result card carried hunks. */
function changedFiles(tool: ToolItem): { path: string; added: number; removed: number }[] {
  const card = tool.presentation?.card;
  if (card === null || card === undefined || card.card !== "diff") return [];
  return card.diffs.map((diff) => ({ path: diff.path, ...diffChurn(diff) }));
}

export function summarizeTurn(
  items: Item[],
  receipt?: Extract<Item, { kind: "changes" }>,
): TurnSummary | null {
  const tools = items.filter((item): item is ToolItem => item.kind === "tool");
  if (tools.length === 0) return null;

  const perFile = new Map<string, { added: number; removed: number }>();
  let commands = 0;
  let failed = 0;
  let steps = 0;
  for (const tool of tools) {
    // The plan/todo bookkeeping rows are hidden from the transcript, so counting them would
    // report steps the reader cannot see.
    if (HIDDEN_TOOLS.has(tool.name)) continue;
    steps += 1;
    if (isCommandTool(tool.name)) commands += 1;
    if (toolState(tool) === "error") failed += 1;
    for (const change of changedFiles(tool)) {
      const seen = perFile.get(change.path);
      if (seen === undefined) {
        perFile.set(change.path, { added: change.added, removed: change.removed });
      } else {
        // One file can be edited several times in a turn; the header reports the turn's total
        // against that file, not the largest single edit.
        seen.added += change.added;
        seen.removed += change.removed;
      }
    }
  }

  // The receipt's numbers win when the turn has one: they diff each file against the
  // turn-start snapshot, where summing the calls counts a file written then edited twice
  // (+11 -1 on the header beside +10 -0 on the receipt, live app 2026-09-25).
  if (receipt?.stats?.length) {
    perFile.clear();
    for (const stat of receipt.stats) perFile.set(stat.path, { added: stat.added, removed: stat.removed });
    // A file the receipt names without numbers (a command's file too large to diff, G4)
    // still changed: it counts as a file, with no churn claimed for it.
    for (const path of [...(receipt.created ?? []), ...(receipt.overwritten ?? []), ...(receipt.deleted ?? [])])
      if (!perFile.has(path)) perFile.set(path, { added: 0, removed: 0 });
  }
  let added = 0;
  let removed = 0;
  for (const file of perFile.values()) {
    added += file.added;
    removed += file.removed;
  }
  return { steps, commands, files: perFile.size, added, removed, failed };
}

/** The header's added facts, in the order they are worth reading. Empty when nothing to say. */
export function summaryParts(summary: TurnSummary): string[] {
  const parts: string[] = [];
  if (summary.files > 0) {
    parts.push(`${summary.files} file${summary.files === 1 ? "" : "s"}`);
  }
  if (summary.commands > 0) {
    parts.push(`${summary.commands} command${summary.commands === 1 ? "" : "s"}`);
  }
  if (summary.failed > 0) {
    parts.push(`${summary.failed} failed`);
  }
  return parts;
}
