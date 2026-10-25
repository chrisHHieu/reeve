// The shape of a file's code read from its indentation, for the guides and the folds a code
// view draws (owner, 2026-10-15: "like VS Code": a thin line under each block, and an arrow that
// folds it). Indentation only, no grammar: it is what a reader sees, it works for every language
// the viewer shows, and it costs one pass over the lines.

/** Columns a tab advances; the views set the same `tab-size`. */
export const TAB_SIZE = 4;

export interface CodeShape {
  /** Columns one level of indentation spans (a tab file: TAB_SIZE). */
  unit: number;
  /** Per line, how many levels deep it sits. A blank line takes the shallower of its neighbours,
   *  so a guide runs through a gap inside a block and stops at one between blocks. */
  levels: number[];
}

function leadingColumns(line: string): number | null {
  let columns = 0;
  for (const ch of line) {
    if (ch === " ") columns += 1;
    else if (ch === "\t") columns += TAB_SIZE - (columns % TAB_SIZE);
    else return columns;
  }
  return null; // blank
}

/** The indentation step the file uses: tabs, else the most common step between lines. */
function detectUnit(lines: readonly string[], columns: (number | null)[]): number {
  if (lines.some((line) => line.startsWith("\t"))) return TAB_SIZE;
  const steps = new Map<number, number>();
  let previous = 0;
  for (const value of columns) {
    if (value === null) continue;
    const step = value - previous;
    if (step > 0 && step <= 8) steps.set(step, (steps.get(step) ?? 0) + 1);
    previous = value;
  }
  let unit = 0;
  let seen = 0;
  for (const [step, count] of steps) {
    if (count > seen || (count === seen && step < unit)) {
      unit = step;
      seen = count;
    }
  }
  return unit || TAB_SIZE;
}

export function codeShape(lines: readonly string[]): CodeShape {
  const columns = lines.map(leadingColumns);
  const unit = detectUnit(lines, columns);
  const own = columns.map((value) => (value === null ? null : Math.floor(value / unit)));
  const levels = new Array<number>(lines.length).fill(0);
  let before = 0;
  for (let i = 0; i < own.length; i++) {
    const level = own[i];
    if (level !== null) {
      levels[i] = level;
      before = level;
      continue;
    }
    let after = 0;
    for (let j = i + 1; j < own.length; j++) {
      const next = own[j];
      if (next !== null) {
        after = next;
        break;
      }
    }
    levels[i] = Math.min(before, after);
  }
  return { unit, levels };
}

/** Whether a line is blank (it opens no block and folds nothing). */
const blank = (line: string) => line.trim() === "";

/**
 * The lines a block header folds away: from the line after it to the block's last non-blank line.
 * Null when the line opens no block (the next non-blank line is not deeper).
 */
export function foldRange(lines: readonly string[], shape: CodeShape, index: number): { from: number; to: number } | null {
  if (index < 0 || index >= lines.length || blank(lines[index])) return null;
  const level = shape.levels[index];
  let to = -1;
  for (let j = index + 1; j < lines.length; j++) {
    if (blank(lines[j])) {
      if (shape.levels[j] > level) continue;
      break;
    }
    if (shape.levels[j] <= level) break;
    to = j;
  }
  return to === -1 ? null : { from: index + 1, to };
}

/**
 * The guide to draw stronger for a chosen line: the block it opens when it is a header, else the
 * innermost block it sits in. `column` is the guide's level (0 = the leftmost guide); the guide
 * runs on lines `from`..`to`. Null on a top-level line that opens nothing.
 */
export function activeGuide(
  lines: readonly string[],
  shape: CodeShape,
  index: number,
): { column: number; from: number; to: number } | null {
  if (index < 0 || index >= lines.length) return null;
  const opened = foldRange(lines, shape, index);
  if (opened) return { column: shape.levels[index], ...opened };
  const level = shape.levels[index];
  if (level === 0) return null;
  const column = level - 1;
  let from = index;
  while (from > 0 && shape.levels[from - 1] > column) from--;
  let to = index;
  while (to < lines.length - 1 && shape.levels[to + 1] > column) to++;
  return { column, from, to };
}
