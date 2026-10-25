// The sheet reader against Excel itself. `truth/excel.xlsx.b64.txt` is a workbook made and
// saved by Excel 16 (truth/make-truth.ps1), as base64, with its author and the folder it was
// saved in taken out: a table in each of Excel's sixty built-in styles, twice, with different
// parts switched on; sixteen kinds of conditional formatting; a picture. `truth/excel.json` is
// how Excel says it shows each of those cells (its DisplayFormat: fill, type colour, bold, the
// four edges). What the reader gives must be what Excel shows.
//
// Colours may differ by a step or two a channel: Excel's arithmetic for a lightened theme
// colour rounds apart from ours, and nobody can see it.
import { beforeAll, describe, expect, it } from "vitest";
// `?raw`, not `node:fs`: this package has no Node types, and Vite hands a test a file this way.
import shownByExcel from "./truth/excel.json?raw";
import workbook from "./truth/excel.xlsx.b64.txt?raw";
import { readBook, type Book, type CellView } from "./book";
import type { CellLook, Edge } from "./look";

type Shown = { r: number; c: number; fill: string | null; font: string | null; bold: boolean; top: string | null; left: string | null; bottom: string | null; right: string | null };
type Truth = { excel: string; tables: Array<{ style: string; set: number; ref: string; cells: unknown[] }>; rules: unknown[] };

const key = (r: number, c: number) => r * 16384 + c;
const near = (a: string | null | undefined, b: string | null | undefined) => {
  if (!a || !b) return !a && !b;
  const x = a.replace("#", "");
  const y = b.replace("#", "");
  return [0, 2, 4].every((at) => Math.abs(parseInt(x.slice(at, at + 2), 16) - parseInt(y.slice(at, at + 2), 16)) <= 3);
};
const kindOf = (edge: Edge | undefined) => (!edge ? null : edge.style === "double" ? "double" : edge.width >= 3 ? "thick" : edge.width === 2 ? "medium" : edge.style === "dotted" ? "hair" : "thin");
const heavier = (a: Edge | undefined, b: Edge | undefined) => (!a ? b : !b ? a : b.width > a.width ? b : a);
const column = (letters: string) => [...letters].reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0) - 1;
// Nine values a cell, as the script wrote them: row, column, fill, type colour, bold, edges.
const chunk = (flat: unknown[]): Shown[] => {
  const out: Shown[] = [];
  for (let at = 0; at < flat.length; at += 9) {
    const [r, c, fill, font, bold, top, left, bottom, right] = flat.slice(at, at + 9) as [number, number, string | null, string | null, boolean, string | null, string | null, string | null, string | null];
    out.push({ r, c, fill, font, bold, top, left, bottom, right });
  }
  return out;
};

let truth: Truth;
let book: Book;
const sheetNamed = (name: string) => {
  const index = book.info.sheets.indexOf(name);
  const info = book.sheet(index);
  const map = new Map<number, CellView>();
  for (const cell of book.cells(index, [[0, info.rows, 0, info.cols]])) map.set(key(cell.r, cell.c), cell);
  return { info, map };
};
const lookOf = (cell: CellView | undefined): CellLook => cell?.own ?? book.info.looks[cell?.look ?? 0] ?? {};

beforeAll(async () => {
  truth = JSON.parse(shownByExcel);
  book = await readBook(workbook.trim());
});

describe("against Excel", () => {
  it("draws every built-in table style as Excel does: fills, type, and each line once", () => {
    const { info, map } = sheetNamed("Tables");
    expect(info.notDrawn).toEqual([]);
    const wrong: string[] = [];
    const seen = { cells: 0, fills: 0, lines: 0, styles: new Set<string>() };
    for (const table of truth.tables) {
      const [, a, r0, b, r1] = /^([A-Z]+)(\d+):([A-Z]+)(\d+)$/.exec(table.ref) as RegExpExecArray;
      const top = Number(r0) - 1;
      const bottom = Number(r1) - 1;
      const left = column(a);
      const right = column(b);
      const shown = new Map<number, Shown>();
      for (const cell of chunk(table.cells)) shown.set(key(cell.r, cell.c), cell);
      seen.styles.add(table.style);
      const miss = (what: string) => wrong.push(`${table.style} set ${table.set} ${table.ref}: ${what}`);
      for (let r = top; r <= bottom + 1; r++) {
        for (let c = left; c <= right + 1; c++) {
          const want = shown.get(key(r, c));
          const got = lookOf(map.get(key(r, c)));
          const at = `row ${r - top} col ${c - left}`;
          if (want) {
            seen.cells += 1;
            if (want.fill) seen.fills += 1;
            if (!near(want.fill, got.fill)) miss(`${at} fill: Excel ${want.fill}, here ${got.fill}`);
            if (!near(want.font, got.color ?? "#000000")) miss(`${at} type: Excel ${want.font}, here ${got.color}`);
            if (want.bold !== !!got.bold) miss(`${at} bold: Excel ${want.bold}, here ${!!got.bold}`);
          }
          // A line on the grid is one line, whichever of the two cells beside it says it.
          if (c <= right) {
            const excel = want?.top ?? shown.get(key(r - 1, c))?.bottom ?? null;
            const here = heavier(r <= bottom ? got.top : undefined, r > top ? lookOf(map.get(key(r - 1, c))).bottom : undefined);
            if (excel) seen.lines += 1;
            if ((excel?.split(":")[0] ?? null) !== kindOf(here) || (excel && here && !near(excel.split(":")[1], here.color))) miss(`line over ${at}: Excel ${excel}, here ${kindOf(here)}:${here?.color}`);
          }
          if (r <= bottom) {
            const excel = want?.left ?? shown.get(key(r, c - 1))?.right ?? null;
            const here = heavier(c <= right ? got.left : undefined, c > left ? lookOf(map.get(key(r, c - 1))).right : undefined);
            if (excel) seen.lines += 1;
            if ((excel?.split(":")[0] ?? null) !== kindOf(here) || (excel && here && !near(excel.split(":")[1], here.color))) miss(`line left of ${at}: Excel ${excel}, here ${kindOf(here)}:${here?.color}`);
          }
        }
      }
    }
    expect(wrong).toEqual([]);
    // The comparison had something to compare: all sixty styles, most cells filled, lines drawn.
    expect(seen.styles.size).toBe(60);
    expect(seen.cells).toBe(1890);
    expect(seen.fills).toBeGreaterThan(1000);
    expect(seen.lines).toBeGreaterThan(1500);
  });

  it("applies conditional formatting to the cells Excel applies it to, in Excel's colours", () => {
    const { info, map } = sheetNamed("Rules");
    expect(info.notDrawn).toEqual([]);
    const heads = ["less", "between", "greater-mixed", "contains", "top3", "above", "duplicate", "scale3", "scale2", "bar", "bottom20", "equal-text", "blank", "two-rules", "begins", "not-equal"];
    const wrong: string[] = [];
    let formatted = 0;
    for (const want of chunk(truth.rules)) {
      const cell = map.get(key(want.r, want.c));
      const got = lookOf(cell);
      const at = `${heads[want.c]} row ${want.r + 1} "${cell?.text ?? ""}"`;
      if (want.fill) formatted += 1;
      if (!near(want.fill, got.fill)) wrong.push(`${at} fill: Excel ${want.fill}, here ${got.fill}`);
      if (!near(want.font, got.color ?? "#000000")) wrong.push(`${at} type: Excel ${want.font}, here ${got.color}`);
      if (want.bold !== !!got.bold) wrong.push(`${at} bold: Excel ${want.bold}, here ${!!got.bold}`);
    }
    expect(wrong).toEqual([]);
    expect(formatted).toBeGreaterThan(60);
    // Excel does not say how long it draws a bar; its default runs from 10% to 90% of the cell.
    const bars = [...map.values()].filter((cell) => cell.bar);
    expect(bars).toHaveLength(10);
    expect(bars.find((cell) => cell.text === "10")?.bar).toEqual({ width: 0.9, color: "#638EC6" });
    expect(bars.find((cell) => cell.text === "1")?.bar?.width).toBeCloseTo(0.1, 5);
  });

  it("puts a picture where Excel was told to put it", () => {
    const { info } = sheetNamed("Picture");
    expect(info.notDrawn).toEqual([]);
    expect(info.pictures).toHaveLength(1);
    // Asked of Excel in points: 6 right and 4 down from the corner of C3, 120 by 60.
    const picture = info.pictures[0];
    expect(picture).toMatchObject({ r: 2, c: 2, alt: "A test card" });
    expect(picture.dx).toBeCloseTo(8, 1);
    expect(picture.dy).toBeCloseTo(5.33, 1);
    expect(picture.w).toBeCloseTo(160, 0);
    expect(picture.h).toBeCloseTo(80, 0);
    expect(picture.src.startsWith("data:image/png;base64,iVBOR")).toBe(true);
  });
});
