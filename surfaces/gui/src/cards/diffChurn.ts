// One answer to "how many lines did this hunk add and remove" (GUI tools review, B1).
//
// Three places asked it three ways: the tool row counted each side's lines (+5 −5 for a
// one-line edit, because a hunk's sides are context windows), the turn header took a
// missing count as 0 (+0 −0), and the diff block diffed the sides — the only right one.
// The engine's exact counts win when the hunk carries them; otherwise the sides are
// diffed, the way `DiffBlock` does.

import { structuredPatch } from "diff";
import type { FileDiff } from "./types";

const stripTrailingNewline = (text: string) => text.replace(/\n$/, "");

export function diffChurn(diff: FileDiff): { added: number; removed: number } {
  if (typeof diff.added === "number" && typeof diff.removed === "number") {
    return { added: diff.added, removed: diff.removed };
  }
  const oldText = diff.old_text === null ? "" : stripTrailingNewline(diff.old_text);
  const patch = structuredPatch("a", "a", oldText, stripTrailingNewline(diff.new_text), "", "", {
    context: 0,
  });
  let added = 0;
  let removed = 0;
  for (const hunk of patch.hunks) {
    for (const line of hunk.lines) {
      if (line.startsWith("+")) added += 1;
      else if (line.startsWith("-")) removed += 1;
    }
  }
  return { added: diff.added ?? added, removed: diff.removed ?? removed };
}
