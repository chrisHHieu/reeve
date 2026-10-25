// "Format as Table": a table names a style, and the style says how its head row, its stripes,
// its first column and the rest look. A workbook carries a style only when it defines its own;
// Excel's sixty built-in ones are named and not carried, so they are kept here
// (tableStyles.data.ts, as Excel itself reports them). A cell's own formatting is over the
// table's, as it is in Excel.
import { tinted, type CellLook, type Edge, type PartLook } from "./look";
import { BUILTIN_TABLE_STYLES } from "./tableStyles.data";
import { child, parseXml } from "./xml";

export type TableStyle = Record<string, PartLook>;

export type Table = {
  r0: number;
  r1: number;
  c0: number;
  c1: number;
  /** How many rows at the top are the head, and at the bottom the totals. */
  header: number;
  totals: number;
  style: TableStyle;
  rowStripes: boolean;
  colStripes: boolean;
  firstColumn: boolean;
  lastColumn: boolean;
};

const LINES: Record<string, [number, Edge["style"]]> = {
  "1": [1, "solid"],
  "2": [2, "solid"],
  "3": [3, "solid"],
  d: [3, "double"],
  h: [1, "dotted"],
  "-": [1, "dashed"],
  "=": [2, "dashed"],
};
const SIDES: Record<string, "left" | "top" | "bottom" | "right" | "vertical" | "horizontal"> = { L: "left", T: "top", B: "bottom", R: "right", V: "vertical", H: "horizontal" };

function colourIn(word: string, theme: readonly string[]): string | undefined {
  if (word[0] === "#") return word.toUpperCase();
  const [index, tint] = word.slice(1).split("@");
  const hex = theme[Number(index)];
  if (!hex || !/^[0-9a-f]{6}$/i.test(hex)) return undefined;
  return "#" + (tint ? tinted(hex, Number(tint)) : hex).toUpperCase();
}

/** One of Excel's built-in table styles in this workbook's theme, or nothing for a name it does not have. */
export function builtinTableStyle(name: string, theme: readonly string[]): TableStyle | null {
  const spec = BUILTIN_TABLE_STYLES[name];
  if (!spec) return null;
  const style: TableStyle = {};
  for (const [type, words] of Object.entries(spec)) {
    const part: PartLook = {};
    for (const word of words.split(" ")) {
      if (word === "b") part.bold = true;
      else if (word === "i") part.italic = true;
      else if (word[1] !== ":") continue;
      else if (word[0] === "c") part.color = colourIn(word.slice(2), theme);
      else if (word[0] === "f") part.fill = colourIn(word.slice(2), theme);
      else if (word[0] === "n") part.size = Number(word.slice(2));
      else if (SIDES[word[0]]) {
        const line = LINES[word[2]];
        if (line) part[SIDES[word[0]]] = { width: line[0], style: line[1], color: colourIn(word.slice(3), theme) ?? "#000000" };
      }
    }
    style[type] = part;
  }
  return style;
}

const flag = (value: string | undefined, otherwise: boolean) => (value === undefined ? otherwise : value === "1" || value === "true");

function rangeOf(ref: string): [number, number, number, number] | null {
  const match = /^\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?$/.exec(ref.trim());
  if (!match) return null;
  const column = (letters: string) => {
    let col = 0;
    for (let i = 0; i < letters.length; i++) col = col * 26 + (letters.charCodeAt(i) - 64);
    return col - 1;
  };
  const r0 = Number(match[2]) - 1;
  const c0 = column(match[1]);
  const r1 = match[4] ? Number(match[4]) - 1 : r0;
  const c1 = match[3] ? column(match[3]) : c0;
  return [Math.min(r0, r1), Math.max(r0, r1), Math.min(c0, c1), Math.max(c0, c1)];
}

/** `A1:C7` (or one cell) as rows and columns from 0, both ends counted. */
export const readRange = rangeOf;

/**
 * A table part (`xl/tables/table1.xml`). `styleNamed` finds its style, the workbook's own
 * first; a table whose style is not known comes back as null, and a table with no style as a
 * table with nothing to draw.
 */
export function readTable(xml: string, styleNamed: (name: string) => TableStyle | null): Table | null | "unknown" {
  const table = child(parseXml(xml), "table");
  const range = table ? rangeOf(table.attrs.ref || "") : null;
  if (!table || !range) return null;
  const info = child(table, "tableStyleInfo");
  const name = info?.attrs.name;
  if (!name) return null;
  const style = styleNamed(name);
  if (!style) return "unknown";
  const header = Math.max(0, Number(table.attrs.headerRowCount ?? 1) || 0);
  const totals = Math.max(0, Number(table.attrs.totalsRowCount ?? 0) || 0);
  return {
    r0: range[0],
    r1: range[1],
    c0: range[2],
    c1: range[3],
    header,
    totals,
    style,
    rowStripes: flag(info?.attrs.showRowStripes, false),
    colStripes: flag(info?.attrs.showColumnStripes, false),
    firstColumn: flag(info?.attrs.showFirstColumn, false),
    lastColumn: flag(info?.attrs.showLastColumn, false),
  };
}

/**
 * What the table's style gives one of its cells. The parts are laid in Excel's order, each
 * over the last: the whole table, the column stripes, the row stripes, the last and first
 * columns, the head row, the totals row, then the four corner cells. A line between two cells
 * is given to the upper or left one only, so it is drawn once.
 */
export function tableLookAt(table: Table, r: number, c: number): PartLook | null {
  const { r0, r1, c0, c1, style } = table;
  if (r < r0 || r > r1 || c < c0 || c > c1) return null;
  const headEnd = r0 + table.header - 1;
  const totalStart = r1 - table.totals + 1;
  const dataStart = headEnd + 1;
  const dataEnd = totalStart - 1;
  const out: PartLook = {};
  let any = false;
  const lay = (part: PartLook | undefined, ra: number, rb: number, ca: number, cb: number) => {
    if (!part) return;
    any = true;
    if (part.bold) out.bold = true;
    if (part.italic) out.italic = true;
    if (part.underline) out.underline = true;
    if (part.strike) out.strike = true;
    if (part.color) out.color = part.color;
    if (part.fill) out.fill = part.fill;
    const top = r === ra ? part.top : undefined;
    const bottom = r === rb ? part.bottom : part.horizontal;
    const left = c === ca ? part.left : undefined;
    const right = c === cb ? part.right : part.vertical;
    if (top) out.top = top;
    if (bottom) out.bottom = bottom;
    if (left) out.left = left;
    if (right) out.right = right;
  };
  const stripe = (first: PartLook | undefined, second: PartLook | undefined, at: number) => {
    const a = Math.max(1, first?.size ?? 1);
    const b = Math.max(1, second?.size ?? 1);
    const within = at % (a + b);
    return within < a ? { part: first, from: at - within, to: at - within + a - 1 } : { part: second, from: at - within + a, to: at - within + a + b - 1 };
  };

  lay(style.wholeTable, r0, r1, c0, c1);
  if (r >= dataStart && r <= dataEnd) {
    if (table.colStripes) {
      const band = stripe(style.firstColumnStripe, style.secondColumnStripe, c - c0);
      lay(band.part, dataStart, dataEnd, c0 + band.from, Math.min(c1, c0 + band.to));
    }
    if (table.rowStripes) {
      const band = stripe(style.firstRowStripe, style.secondRowStripe, r - dataStart);
      lay(band.part, dataStart + band.from, Math.min(dataEnd, dataStart + band.to), c0, c1);
    }
  }
  if (table.lastColumn && c === c1) lay(style.lastColumn, r0, r1, c1, c1);
  if (table.firstColumn && c === c0) lay(style.firstColumn, r0, r1, c0, c0);
  if (table.header && r <= headEnd) lay(style.headerRow, r0, headEnd, c0, c1);
  if (table.totals && r >= totalStart) lay(style.totalRow, totalStart, r1, c0, c1);
  if (table.header && r <= headEnd) {
    if (table.firstColumn && c === c0) lay(style.firstHeaderCell, r, r, c, c);
    if (table.lastColumn && c === c1) lay(style.lastHeaderCell, r, r, c, c);
  }
  if (table.totals && r >= totalStart) {
    if (table.firstColumn && c === c0) lay(style.firstTotalCell, r, r, c, c);
    if (table.lastColumn && c === c1) lay(style.lastTotalCell, r, r, c, c);
  }
  return any ? out : null;
}

const PLAIN = ["bold", "italic", "underline", "strike"] as const;
const EDGES = ["top", "right", "bottom", "left"] as const;

/** A cell's look with a part laid UNDER it: what the cell says itself stays. */
export function under(look: CellLook, part: PartLook): CellLook {
  const made: CellLook = { ...look };
  for (const key of PLAIN) if (part[key]) made[key] = true;
  if (!made.color && part.color) made.color = part.color;
  if (!made.fill && part.fill) made.fill = part.fill;
  for (const key of EDGES) if (!made[key] && part[key]) made[key] = part[key];
  return made;
}

/** A cell's look with a part laid OVER it: what the part says wins. */
export function over(look: CellLook, part: PartLook): CellLook {
  const made: CellLook = { ...look };
  for (const key of PLAIN) if (part[key]) made[key] = true;
  if (part.color) made.color = part.color;
  if (part.fill) made.fill = part.fill;
  for (const key of EDGES) if (part[key]) made[key] = part[key];
  return made;
}
