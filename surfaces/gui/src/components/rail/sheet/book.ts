// A workbook, read once and then asked for the cells in view. What a cell HOLDS (its value, its
// formatted text, its formula, the merges, the column widths) is read by SheetJS, which the app
// already carries; how it LOOKS is read by look.ts from the same bytes: the cell styles, then
// what a table's style lays under them (tables.ts) and a rule of conditional formatting over
// them (rules.ts). Older formats (.xls, .ods) have no style sheet this reads, so they come out
// as their values, and say so.
//
// Nothing here touches the page: it runs in a worker (worker.ts), so a large file is read
// without the app standing still.
import { cellKey, formatColour, marksEmpty, readSheetFacts, readStyleSheet, type CellLook, type SheetFacts, type StyleSheet } from "./look";
import { readRules, type Bar, type Probe, type Rules } from "./rules";
import { builtinTableStyle, over, readTable, tableLookAt, under, type Table, type TableStyle } from "./tables";
import { child, children, parseXml, type XmlNode } from "./xml";

/** Inclusive, 0-based. */
export type Rect = [r0: number, r1: number, c0: number, c1: number];

export type BookInfo = {
  /** The sheets a person sees in Excel, in its order (hidden ones are left out). */
  sheets: string[];
  /** False for a format whose look is not read: values only. */
  styled: boolean;
  looks: CellLook[];
};

export type SheetInfo = {
  name: string;
  /** The used range, counted from A1. */
  rows: number;
  cols: number;
  /** Pixels at 100%, one per used column and row; 0 is hidden. */
  widths: number[];
  heights: number[];
  defaultWidth: number;
  defaultHeight: number;
  frozenRows: number;
  frozenCols: number;
  gridlines: boolean;
  merges: Rect[];
  pictures: Picture[];
  /** What the sheet has that is not drawn, in words ("1 chart", "a pivot table"). */
  notDrawn: string[];
};

/** A picture placed on a sheet: the cell its corner is in, how far into it, and its size. Pixels at 100%. */
export type Picture = { r: number; c: number; dx: number; dy: number; w: number; h: number; src: string; alt: string };

export type CellView = {
  r: number;
  c: number;
  text: string;
  kind: "text" | "number" | "bool" | "error";
  /** Index into `BookInfo.looks`. */
  look: number;
  /** Its look, whole, where a table style or a rule of conditional formatting changed it. */
  own?: CellLook;
  /** A data bar behind its value. */
  bar?: Bar;
  /** A merged cell: where it ends, and the style of its last cell (its right and bottom edges). */
  end?: [r: number, c: number];
  lookEnd?: number;
  /** The colour its number format gives it. */
  color?: string;
  /** What the formula bar shows, where that is not `text`. */
  held?: string;
  link?: string;
  /** Text that runs over the empty cells after it: the first column it may not enter. */
  until?: number;
};

export interface Book {
  info: BookInfo;
  sheet(index: number): SheetInfo;
  cells(index: number, rects: Rect[]): CellView[];
}

type RawCell = { t?: string; v?: unknown; w?: string; f?: string; z?: string; l?: { Target?: string } };
type RawSheet = Array<RawCell[] | undefined> & {
  "!ref"?: string;
  "!cols"?: Array<{ wpx?: number; hidden?: boolean } | undefined>;
  "!rows"?: Array<{ hpt?: number; hidden?: boolean } | undefined>;
  "!merges"?: Array<{ s: { r: number; c: number }; e: { r: number; c: number } }>;
};
type ZipEntry = { content?: Uint8Array | number[] | string };

const PT = 4 / 3;
const DEFAULT_WIDTH = 64;
const DEFAULT_HEIGHT = 20;
const SPILL = 64;
// A merge larger than this is drawn from its first cell and not tracked cell by cell.
const MERGE_CEILING = 200_000;

function textOf(entry: ZipEntry | undefined): string {
  const content = entry?.content;
  if (content === undefined) return "";
  if (typeof content === "string") return content;
  const bytes = content instanceof Uint8Array ? content : Uint8Array.from(content);
  // A part may be UTF-16, which says so in its first two bytes.
  const encoding = bytes[0] === 0xff && bytes[1] === 0xfe ? "utf-16le" : bytes[0] === 0xfe && bytes[1] === 0xff ? "utf-16be" : "utf-8";
  return new TextDecoder(encoding).decode(bytes);
}

/** A relationship's target, from the part that names it: `../drawings/d1.xml` under `xl/worksheets`. */
function resolve(from: string, target: string): string {
  if (target.startsWith("/")) return target.slice(1);
  const parts = from.split("/").slice(0, -1);
  for (const piece of target.split("/")) {
    if (piece === "..") parts.pop();
    else if (piece !== ".") parts.push(piece);
  }
  return parts.join("/");
}

const relsOf = (part: string) => {
  const cut = part.lastIndexOf("/");
  return `${part.slice(0, cut)}/_rels/${part.slice(cut + 1)}.rels`;
};

const shown = (cell: RawCell | undefined): string => {
  if (!cell || cell.t === "z") return "";
  if (cell.w !== undefined) return String(cell.w);
  if (cell.t === "b") return cell.v ? "TRUE" : "FALSE";
  return cell.v === undefined || cell.v === null ? "" : String(cell.v);
};

const many = (n: number, one: string) => (n === 1 ? `1 ${one}` : `${n} ${one}s`);

const probeOf = (cell: RawCell | undefined): Probe => ({
  text: shown(cell),
  num: cell && cell.t === "n" && typeof cell.v === "number" ? cell.v : null,
  error: cell?.t === "e",
});

// What a webview draws by itself. A metafile (.emf, .wmf) is not on the list.
const PICTURE_TYPES: Record<string, string> = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", bmp: "image/bmp", webp: "image/webp" };
const EMU = 9525;
// A sheet's pictures are handed to the page whole; past this they are said, not drawn.
const PICTURES_CEILING = 12 * 1024 * 1024;

function bytesOf(entry: ZipEntry | undefined): Uint8Array | null {
  const content = entry?.content;
  if (content === undefined || typeof content === "string") return null;
  return content instanceof Uint8Array ? content : Uint8Array.from(content);
}

function base64Of(bytes: Uint8Array): string {
  let binary = "";
  for (let at = 0; at < bytes.length; at += 0x8000) binary += String.fromCharCode(...bytes.subarray(at, at + 0x8000));
  return btoa(binary);
}

/** Makes `sizes` long enough to hold `length` pixels from `offset` pixels into `start`. */
function reach(sizes: number[], start: number, offset: number, length: number, usual: number) {
  let left = offset + length;
  while (sizes.length <= start) sizes.push(usual);
  for (let at = start; left > 0 && at < start + 4096; at++) {
    if (at >= sizes.length) sizes.push(usual);
    left -= sizes[at];
  }
}

const numberIn = (node: XmlNode | undefined) => Number(node?.text.trim() ?? 0) || 0;

/** Reads a workbook from its bytes, given as base64 (how the app is handed a file). */
export async function readBook(base64: string): Promise<Book> {
  const XLSX = await import("xlsx");
  const workbook = XLSX.read(base64, { type: "base64", cellStyles: true, cellNF: true, dense: true, bookFiles: true });
  const files = ((workbook as unknown as { files?: Record<string, ZipEntry> }).files ?? {}) as Record<string, ZipEntry | undefined>;
  // The package says where its parts are; the usual places are only the usual ones.
  const directory = (workbook as unknown as { Directory?: { workbooks?: string[]; styles?: string[] } }).Directory;
  const partAt = (listed: string[] | undefined, usual: string) => (listed?.[0] ? listed[0].replace(/^\//, "") : usual);
  const workbookPart = partAt(directory?.workbooks, "xl/workbook.xml");
  const stylesXml = textOf(files[partAt(directory?.styles, "xl/styles.xml")]);
  const styled = stylesXml.length > 0;
  const scheme = (workbook as unknown as { Themes?: { themeElements?: { clrScheme?: Array<{ rgb?: string }> } } }).Themes?.themeElements?.clrScheme;
  const theme = (scheme ?? []).map((entry) => entry?.rgb ?? "");
  const sheetStyles: StyleSheet = styled ? readStyleSheet(stylesXml, theme) : { looks: [], parts: [], tableStyles: new Map() };
  const looks = sheetStyles.looks;
  if (!looks.length) looks.push({});
  // A table style by its name: the workbook's own first, then Excel's built-in ones.
  const tableStyles = new Map<string, TableStyle | null>();
  const tableStyleNamed = (name: string): TableStyle | null => {
    if (!tableStyles.has(name)) tableStyles.set(name, sheetStyles.tableStyles.get(name) ?? builtinTableStyle(name, theme));
    return tableStyles.get(name) ?? null;
  };

  // Where each sheet's XML is: the workbook names a relationship, the relationship a part.
  const parts = new Map<string, string>();
  for (const rel of children(parseXml(textOf(files[relsOf(workbookPart)])).kids[0], "Relationship")) {
    if (rel.attrs.Id && rel.attrs.Target) parts.set(rel.attrs.Id, resolve(workbookPart, rel.attrs.Target));
  }
  const listed = workbook.Workbook?.Sheets ?? [];
  const visible: number[] = [];
  workbook.SheetNames.forEach((_, index) => {
    if (!listed[index]?.Hidden) visible.push(index);
  });

  type Loaded = { info: SheetInfo; raw: RawSheet; facts: SheetFacts | null; colXf: number[]; covered: Map<number, number>; tables: Table[]; rules: Rules | null };
  const loaded = new Map<number, Loaded>();

  const load = (index: number): Loaded => {
    const kept = loaded.get(index);
    if (kept) return kept;
    const at = visible[index];
    const name = workbook.SheetNames[at];
    const raw = (workbook.Sheets[name] ?? []) as unknown as RawSheet;
    const part = parts.get((listed[at] as { id?: string } | undefined)?.id ?? "") ?? "";
    const xml = styled && part ? textOf(files[part]) : "";
    const facts = xml ? readSheetFacts(xml) : null;
    const range = raw["!ref"] ? XLSX.utils.decode_range(raw["!ref"]) : null;
    const rows = range ? range.e.r + 1 : 0;
    const cols = range ? range.e.c + 1 : 0;

    const defaultWidth = facts?.defaultColChars ? Math.round(facts.defaultColChars * 7 + 5) : DEFAULT_WIDTH;
    const defaultHeight = facts?.defaultRowPt ? Math.round(facts.defaultRowPt * PT) : DEFAULT_HEIGHT;
    const widths: number[] = [];
    for (let c = 0; c < cols; c++) {
      const col = raw["!cols"]?.[c];
      widths.push(col?.hidden ? 0 : col?.wpx && col.wpx > 0 ? Math.round(col.wpx) : defaultWidth);
    }
    // A row with no height of its own is as tall as its largest type needs, as Excel makes it
    // when the file is opened (a 16pt title in a row that was never sized).
    const grown = new Map<number, number>();
    const base = looks[0]?.size ?? 11;
    facts?.styleAt.forEach((xf, key) => {
      const size = looks[xf]?.size;
      if (!size || size <= base) return;
      const row = Math.floor(key / 16384);
      if (size > (grown.get(row) ?? 0)) grown.set(row, size);
    });
    const heights: number[] = [];
    for (let r = 0; r < rows; r++) {
      const row = raw["!rows"]?.[r];
      const size = grown.get(r);
      heights.push(row?.hidden ? 0 : row?.hpt && row.hpt > 0 ? Math.round(row.hpt * PT) : size ? Math.round(Math.ceil(size * 1.3) * PT) : defaultHeight);
    }

    const merges: Rect[] = (raw["!merges"] ?? []).map((merge) => [merge.s.r, merge.e.r, merge.s.c, merge.e.c]);
    const covered = new Map<number, number>();
    merges.forEach(([r0, r1, c0, c1], which) => {
      if ((r1 - r0 + 1) * (c1 - c0 + 1) > MERGE_CEILING) return;
      for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) covered.set(cellKey(r, c), which);
    });

    const colXf: number[] = new Array(cols).fill(0);
    for (const [min, max, style] of facts?.colStyle ?? []) {
      for (let c = Math.max(0, min); c <= Math.min(cols - 1, max); c++) colXf[c] = style;
    }

    const notDrawn: string[] = [];
    const pictures: Picture[] = [];
    const tables: Table[] = [];
    let rules: Rules | null = null;
    if (facts) {
      const rels = children(parseXml(textOf(files[relsOf(part)])).kids[0], "Relationship");
      const drawing = facts.drawing ? rels.find((rel) => rel.attrs.Id === facts.drawing) : undefined;
      if (drawing?.attrs.Target) {
        const drawingPart = resolve(part, drawing.attrs.Target);
        const drawn = textOf(files[drawingPart]);
        const charts = (drawn.match(/<(?:\w+:)?graphicFrame\b/g) || []).length;
        if (charts) notDrawn.push(many(charts, "chart"));
        // Pictures: each is anchored to a cell, and is drawn there at the size the file gives.
        const media = new Map<string, string>();
        for (const rel of children(parseXml(textOf(files[relsOf(drawingPart)])).kids[0], "Relationship")) {
          if (rel.attrs.Id && rel.attrs.Target && rel.attrs.TargetMode !== "External") media.set(rel.attrs.Id, resolve(drawingPart, rel.attrs.Target));
        }
        let missed = 0;
        let carried = 0;
        for (const anchor of parseXml(drawn).kids[0]?.kids ?? []) {
          const pic = child(anchor, "pic");
          if (!pic || (anchor.tag !== "twoCellAnchor" && anchor.tag !== "oneCellAnchor")) {
            if (pic) missed += 1;
            continue;
          }
          const from = child(anchor, "from");
          const to = child(anchor, "to");
          const size = child(child(child(pic, "spPr"), "xfrm"), "ext") ?? child(anchor, "ext");
          const blip = child(child(pic, "blipFill"), "blip");
          const path = media.get(blip?.attrs.embed ?? "");
          const type = PICTURE_TYPES[(path ?? "").split(".").pop()?.toLowerCase() ?? ""];
          const bytes = type && path ? bytesOf(files[path]) : null;
          const r = numberIn(child(from, "row"));
          const c = numberIn(child(from, "col"));
          const dx = numberIn(child(from, "colOff")) / EMU;
          const dy = numberIn(child(from, "rowOff")) / EMU;
          let w = Number(size?.attrs.cx) / EMU;
          let h = Number(size?.attrs.cy) / EMU;
          if (!(w > 0 && h > 0) && to) {
            // No size of its own: from its first cell to its last.
            reach(widths, numberIn(child(to, "col")), 0, 0, defaultWidth);
            reach(heights, numberIn(child(to, "row")), 0, 0, defaultHeight);
            const across = (sizes: number[], a: number, b: number) => sizes.slice(a, b).reduce((sum, value) => sum + value, 0);
            w = across(widths, c, numberIn(child(to, "col"))) + numberIn(child(to, "colOff")) / EMU - dx;
            h = across(heights, r, numberIn(child(to, "row"))) + numberIn(child(to, "rowOff")) / EMU - dy;
          }
          if (!bytes || !from || !(w > 0 && h > 0) || carried + bytes.length > PICTURES_CEILING) {
            missed += 1;
            continue;
          }
          carried += bytes.length;
          // The grid reaches as far as the picture does, so it can be scrolled to.
          reach(widths, c, dx, w, defaultWidth);
          reach(heights, r, dy, h, defaultHeight);
          const named = child(child(pic, "nvPicPr"), "cNvPr")?.attrs;
          pictures.push({ r, c, dx, dy, w, h, src: `data:${type};base64,${base64Of(bytes)}`, alt: named?.descr || named?.name || "" });
        }
        if (missed) notDrawn.push(many(missed, "picture"));
      }
      const pivots = rels.filter((rel) => (rel.attrs.Type || "").endsWith("/pivotTable")).length;
      if (pivots) notDrawn.push(many(pivots, "pivot table"));
      // Tables: each names a style, which is laid under its cells.
      let unknown = 0;
      for (const rel of rels) {
        if (!(rel.attrs.Type || "").endsWith("/table") || !rel.attrs.Target) continue;
        const table = readTable(textOf(files[resolve(part, rel.attrs.Target)]), tableStyleNamed);
        if (table === "unknown") unknown += 1;
        else if (table) tables.push(table);
      }
      if (unknown) notDrawn.push("a table style");
      if (facts.conditional) {
        const end = xml.search(/<\/(?:\w+:)?sheetData>/);
        rules = readRules(end < 0 ? "" : xml.slice(end), sheetStyles.parts, theme, (r, c) => {
          const cell = raw[r]?.[c];
          return cell ? probeOf(cell) : null;
        });
        if (rules.unsupported) notDrawn.push("some conditional formatting");
      }
      if (facts.sparklines) notDrawn.push("sparklines");
      if (facts.slicers) notDrawn.push("slicers");
      // Read: the look is kept above, and the XML is not needed again.
      delete files[part];
    }

    const info: SheetInfo = {
      name,
      rows,
      cols,
      widths,
      heights,
      defaultWidth,
      defaultHeight,
      frozenRows: Math.min(facts?.frozenRows ?? 0, rows),
      frozenCols: Math.min(facts?.frozenCols ?? 0, cols),
      gridlines: facts?.gridlines ?? true,
      merges,
      pictures,
      notDrawn,
    };
    const entry: Loaded = { info, raw, facts, colXf, covered, tables, rules };
    loaded.set(index, entry);
    return entry;
  };

  const cells = (index: number, rects: Rect[]): CellView[] => {
    const { info, raw, facts, colXf, covered, tables, rules } = load(index);
    const out: CellView[] = [];
    const sent = new Set<number>();
    const lookOf = (r: number, c: number) => (facts ? (facts.styleAt.get(cellKey(r, c)) ?? facts.rowStyle.get(r) ?? colXf[c] ?? 0) : 0);

    const put = (r: number, c: number) => {
      const key = cellKey(r, c);
      if (sent.has(key)) return;
      sent.add(key);
      const cell = raw[r]?.[c];
      const text = shown(cell);
      const xf = lookOf(r, c);
      const look = looks[xf];
      const merge = covered.get(key);
      // A table's style goes under what the cell says itself; a rule of conditional formatting
      // that the cell meets goes over both.
      let own: CellLook | undefined;
      for (const table of tables) {
        const part = tableLookAt(table, r, c);
        if (part) {
          own = under(look ?? {}, part);
          break;
        }
      }
      const verdict = rules ? rules.at(r, c, probeOf(cell)) : null;
      if (verdict?.part) own = over(own ?? look ?? {}, verdict.part);
      if (!text && !marksEmpty(own ?? look) && merge === undefined) return;
      const view: CellView = {
        r,
        c,
        text,
        kind: cell?.t === "n" || cell?.t === "d" ? "number" : cell?.t === "b" ? "bool" : cell?.t === "e" ? "error" : "text",
        look: looks[xf] ? xf : 0,
      };
      if (own) view.own = own;
      if (verdict?.bar && text) view.bar = verdict.bar;
      if (merge !== undefined) {
        const [, r1, , c1] = info.merges[merge];
        view.end = [r1, c1];
        view.lookEnd = lookOf(r1, c1);
      }
      if (cell && text) {
        if (view.kind === "number" && typeof cell.v === "number") {
          const color = formatColour(cell.z, cell.v);
          if (color) view.color = color;
        }
        const held = cell.f
          ? "=" + cell.f
          : typeof cell.v === "number"
            ? XLSX.SSF.is_date(cell.z || "General")
              ? text
              : String(Number(cell.v.toPrecision(15)))
            : text;
        if (held !== text) view.held = held;
        const target = cell.l?.Target;
        if (target && /^https?:\/\//i.test(target)) view.link = target;
        // Text runs on over what is empty to its right, as it does in Excel.
        if (view.kind === "text" && merge === undefined && !look?.wrap && look?.align !== "right" && look?.align !== "center") {
          const row = raw[r];
          let stop = c + 1;
          const limit = Math.min(info.cols, c + 1 + SPILL);
          while (stop < limit && !shown(row?.[stop]) && !covered.has(cellKey(r, stop))) stop += 1;
          if (stop > c + 1 || stop >= info.cols) view.until = stop >= info.cols ? c + 1 + SPILL : stop;
        }
      }
      out.push(view);
    };

    for (const [from, to, left, right] of rects) {
      const r1 = Math.min(to, info.rows - 1);
      const c1 = Math.min(right, info.cols - 1);
      for (let r = Math.max(0, from); r <= r1; r++) {
        for (let c = Math.max(0, left); c <= c1; c++) {
          const merge = covered.get(cellKey(r, c));
          // Any cell of a merge stands for its first one, which is the one that is drawn.
          if (merge !== undefined) put(info.merges[merge][0], info.merges[merge][2]);
          else put(r, c);
        }
      }
    }
    return out;
  };

  return {
    info: { sheets: visible.map((at) => workbook.SheetNames[at]), styled, looks },
    sheet: (index) => load(index).info,
    cells,
  };
}

/** Why a workbook could not be read, in words a person can act on. */
export function readFailure(error: unknown, name: string): string {
  const message = error instanceof Error ? error.message : String(error);
  if (/password|encrypt/i.test(message)) return `${name} is protected with a password, so it cannot be drawn here.`;
  return `${name} could not be drawn here: ${message}`;
}
