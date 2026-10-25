// How a workbook's cells look: read from the workbook's own style sheet and from each sheet's
// XML, which the library that reads the VALUES (SheetJS, community edition) leaves out. That
// library keeps a cell's fill and nothing else of its style: no font, no border, no alignment,
// and not which style a cell names. So the look is read here, by cell address, and joined to
// the values in book.ts.
import { child, children, parseXml, readAttrs, type XmlNode } from "./xml";

export type Edge = { width: number; style: "solid" | "dashed" | "dotted" | "double"; color: string };

/** One cell style, as what is drawn. Absent means Excel's default for it. */
export type CellLook = {
  font?: string;
  /** Points. */
  size?: number;
  bold?: true;
  italic?: true;
  underline?: true;
  strike?: true;
  color?: string;
  fill?: string;
  align?: "left" | "center" | "right";
  /** Excel's default is the bottom of the cell. */
  valign?: "top" | "middle";
  wrap?: true;
  indent?: number;
  top?: Edge;
  right?: Edge;
  bottom?: Edge;
  left?: Edge;
};

// The 56 colours a workbook may name by number (the default palette; 0-7 repeat as 8-15).
const INDEXED = (
  "000000 FFFFFF FF0000 00FF00 0000FF FFFF00 FF00FF 00FFFF 000000 FFFFFF FF0000 00FF00 0000FF FFFF00 FF00FF 00FFFF " +
  "800000 008000 000080 808000 800080 008080 C0C0C0 808080 9999FF 993366 FFFFCC CCFFFF 660066 FF8080 0066CC CCCCFF " +
  "000080 FF00FF FFFF00 00FFFF 800080 800000 008080 0000FF 00CCFF CCFFFF CCFFCC FFFF99 99CCFF FF99CC CC99FF FFCC99 " +
  "3366FF 33CCCC 99CC00 FFCC00 FF9900 FF6600 666699 969696 003366 339966 003300 333300 993300 993366 333399 333333"
).split(" ");

const HEX = /^[0-9a-f]{6}$/i;

/**
 * A theme colour made lighter or darker, the way Excel does it: on the colour's lightness,
 * towards white for a positive tint and towards black for a negative one.
 */
export function tinted(hex: string, tint: number): string {
  const r = parseInt(hex.slice(0, 2), 16) / 255;
  const g = parseInt(hex.slice(2, 4), 16) / 255;
  const b = parseInt(hex.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  let h = 0;
  let s = 0;
  let l = (max + min) / 2;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    h /= 6;
  }
  l = tint < 0 ? l * (1 + tint) : l * (1 - tint) + tint;
  const byte = (value: number) =>
    Math.round(Math.min(1, Math.max(0, value)) * 255)
      .toString(16)
      .padStart(2, "0");
  if (s === 0) return byte(l).repeat(3);
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const channel = (t: number) => {
    const turn = t < 0 ? t + 1 : t > 1 ? t - 1 : t;
    return byte(turn < 1 / 6 ? p + (q - p) * 6 * turn : turn < 1 / 2 ? q : turn < 2 / 3 ? p + (q - p) * (2 / 3 - turn) * 6 : p);
  };
  return channel(h + 1 / 3) + channel(h) + channel(h - 1 / 3);
}

/** A `<color>`-shaped element as `#RRGGBB`, or nothing for "automatic" and what cannot be read. */
export function colourOf(node: XmlNode | undefined, theme: readonly string[]): string | undefined {
  if (!node) return undefined;
  const { rgb, theme: themed, indexed, tint } = node.attrs;
  let hex: string | undefined;
  if (rgb && /^[0-9a-f]{6}([0-9a-f]{2})?$/i.test(rgb)) hex = rgb.slice(-6);
  else if (themed !== undefined) hex = theme[Number(themed)];
  else if (indexed !== undefined) hex = INDEXED[Number(indexed)];
  if (!hex || !HEX.test(hex)) return undefined;
  const by = Number(tint);
  return "#" + (by ? tinted(hex, by) : hex).toUpperCase();
}

const on = (node: XmlNode | undefined) => !!node && node.attrs.val !== "0" && node.attrs.val !== "false";

const EDGES: Record<string, [number, Edge["style"]]> = {
  thin: [1, "solid"],
  hair: [1, "dotted"],
  dotted: [1, "dotted"],
  dashed: [1, "dashed"],
  dashDot: [1, "dashed"],
  dashDotDot: [1, "dashed"],
  medium: [2, "solid"],
  mediumDashed: [2, "dashed"],
  mediumDashDot: [2, "dashed"],
  mediumDashDotDot: [2, "dashed"],
  slantDashDot: [2, "dashed"],
  thick: [3, "solid"],
  double: [3, "double"],
};

export function edgeOf(node: XmlNode | undefined, theme: readonly string[]): Edge | undefined {
  const drawn = node && EDGES[node.attrs.style];
  if (!drawn) return undefined;
  return { width: drawn[0], style: drawn[1], color: colourOf(child(node, "color"), theme) ?? "#000000" };
}

/** A font name is put into a style: only what a font name can be is let through. */
const fontName = (name: string | undefined) => (name && /^[\w .,'()&+-]{1,64}$/u.test(name) ? name.replace(/'/g, "") : undefined);

/** Every cell style of the workbook (`cellXfs`, in order). */
export const readLooks = (stylesXml: string, theme: readonly string[]): CellLook[] => readStyleSheet(stylesXml, theme).looks;

/**
 * The workbook's style sheet (`xl/styles.xml`). `theme` is the workbook's twelve theme colours
 * in the order a style names them.
 */
export function readStyleSheet(stylesXml: string, theme: readonly string[]): StyleSheet {
  const sheet = child(parseXml(stylesXml), "styleSheet");
  if (!sheet) return { looks: [], parts: [], tableStyles: new Map() };
  const fonts = children(child(sheet, "fonts"), "font");
  const fills = children(child(sheet, "fills"), "fill");
  const borders = children(child(sheet, "borders"), "border");
  const parts = children(child(sheet, "dxfs"), "dxf").map((dxf) => partOf(dxf, theme));
  const tableStyles = new Map<string, Record<string, PartLook>>();
  for (const style of children(child(sheet, "tableStyles"), "tableStyle")) {
    if (!style.attrs.name || style.attrs.table === "0") continue;
    const made: Record<string, PartLook> = {};
    for (const element of children(style, "tableStyleElement")) {
      const part = parts[Number(element.attrs.dxfId)];
      if (!element.attrs.type || !part) continue;
      const size = Number(element.attrs.size);
      made[element.attrs.type] = size > 1 ? { ...part, size } : part;
    }
    tableStyles.set(style.attrs.name, made);
  }
  const looks = children(child(sheet, "cellXfs"), "xf").map((xf) => {
    const look: CellLook = {};
    const font = fonts[Number(xf.attrs.fontId) || 0];
    if (font) {
      const name = fontName(child(font, "name")?.attrs.val);
      const size = Number(child(font, "sz")?.attrs.val);
      const color = colourOf(child(font, "color"), theme);
      if (name) look.font = name;
      if (size > 0 && size < 410) look.size = size;
      if (on(child(font, "b"))) look.bold = true;
      if (on(child(font, "i"))) look.italic = true;
      const underline = child(font, "u");
      if (underline && underline.attrs.val !== "none") look.underline = true;
      if (on(child(font, "strike"))) look.strike = true;
      if (color && color !== "#000000") look.color = color;
    }
    const pattern = child(fills[Number(xf.attrs.fillId) || 0], "patternFill");
    if (pattern?.attrs.patternType === "solid") {
      const fill = colourOf(child(pattern, "fgColor"), theme);
      if (fill) look.fill = fill;
    }
    const border = borders[Number(xf.attrs.borderId) || 0];
    if (border) {
      const top = edgeOf(child(border, "top"), theme);
      const bottom = edgeOf(child(border, "bottom"), theme);
      const left = edgeOf(child(border, "left") ?? child(border, "start"), theme);
      const right = edgeOf(child(border, "right") ?? child(border, "end"), theme);
      if (top) look.top = top;
      if (bottom) look.bottom = bottom;
      if (left) look.left = left;
      if (right) look.right = right;
    }
    const alignment = child(xf, "alignment")?.attrs;
    if (alignment) {
      const across = alignment.horizontal;
      if (across === "left" || across === "right") look.align = across;
      else if (across === "center" || across === "centerContinuous") look.align = "center";
      if (alignment.vertical === "top") look.valign = "top";
      else if (alignment.vertical === "center") look.valign = "middle";
      if (alignment.wrapText === "1" || alignment.wrapText === "true") look.wrap = true;
      const indent = Number(alignment.indent);
      if (indent > 0 && indent < 100) look.indent = indent;
    }
    return look;
  });
  return { looks, parts, tableStyles };
}

/**
 * A part of a look laid over a cell: what a table style gives its head row, or a rule of
 * conditional formatting a cell that meets it. Only what it changes is set. `vertical` and
 * `horizontal` are the lines between the cells of the part.
 */
export type PartLook = {
  bold?: true;
  italic?: true;
  underline?: true;
  strike?: true;
  color?: string;
  fill?: string;
  top?: Edge;
  right?: Edge;
  bottom?: Edge;
  left?: Edge;
  vertical?: Edge;
  horizontal?: Edge;
  /** A stripe's height in rows (or width in columns), where it is not one. */
  size?: number;
};

/** A differential style (`<dxf>`). Its fill is the pattern's background, where a cell's is the foreground. */
function partOf(dxf: XmlNode, theme: readonly string[]): PartLook {
  const part: PartLook = {};
  const font = child(dxf, "font");
  if (font) {
    const color = colourOf(child(font, "color"), theme);
    if (on(child(font, "b"))) part.bold = true;
    if (on(child(font, "i"))) part.italic = true;
    const underline = child(font, "u");
    if (underline && underline.attrs.val !== "none") part.underline = true;
    if (on(child(font, "strike"))) part.strike = true;
    if (color) part.color = color;
  }
  const pattern = child(child(dxf, "fill"), "patternFill");
  if (pattern && pattern.attrs.patternType !== "none") {
    const fill = colourOf(child(pattern, "bgColor"), theme) ?? colourOf(child(pattern, "fgColor"), theme);
    if (fill) part.fill = fill;
  }
  const border = child(dxf, "border");
  if (border) {
    const sides: Array<[keyof PartLook & ("top" | "right" | "bottom" | "left" | "vertical" | "horizontal"), string[]]> = [
      ["top", ["top"]],
      ["bottom", ["bottom"]],
      ["left", ["left", "start"]],
      ["right", ["right", "end"]],
      ["vertical", ["vertical"]],
      ["horizontal", ["horizontal"]],
    ];
    for (const [side, names] of sides) {
      const drawn = edgeOf(names.map((name) => child(border, name)).find(Boolean), theme);
      if (drawn) part[side] = drawn;
    }
  }
  return part;
}

export type StyleSheet = {
  /** The cell styles (`cellXfs`), in order. */
  looks: CellLook[];
  /** The differential styles (`dxfs`), in order: a rule of conditional formatting names one. */
  parts: PartLook[];
  /** The table styles the workbook defines itself, by name, each part by its type. */
  tableStyles: Map<string, Record<string, PartLook>>;
};

/** Whether a style changes anything about a cell that holds nothing. */
export const marksEmpty = (look: CellLook | undefined) => !!look && !!(look.fill || look.top || look.right || look.bottom || look.left);

/** Rows by 16384 columns (Excel's own width), so a cell is one number. */
export const cellKey = (row: number, col: number) => row * 16384 + col;

export type SheetFacts = {
  /** The style each cell names, where it is not the default one. */
  styleAt: Map<number, number>;
  /** Rows styled as a whole, and columns: what an empty cell there looks like. */
  rowStyle: Map<number, number>;
  colStyle: Array<[min: number, max: number, style: number]>;
  frozenRows: number;
  frozenCols: number;
  gridlines: boolean;
  defaultRowPt: number | null;
  defaultColChars: number | null;
  /** What the sheet has that is not drawn. */
  conditional: number;
  tables: number;
  sparklines: boolean;
  slicers: boolean;
  /** The relationship id of the sheet's drawing (charts and pictures), if it has one. */
  drawing: string | null;
};

const columnOf = (letters: string) => {
  let col = 0;
  for (let i = 0; i < letters.length; i++) col = col * 26 + (letters.charCodeAt(i) - 64);
  return col - 1;
};

const count = (text: string, pattern: RegExp) => (text.match(pattern) || []).length;

/** What one sheet's XML says about its look. The values in it are read elsewhere. */
export function readSheetFacts(xml: string): SheetFacts {
  const facts: SheetFacts = {
    styleAt: new Map(),
    rowStyle: new Map(),
    colStyle: [],
    frozenRows: 0,
    frozenCols: 0,
    gridlines: true,
    defaultRowPt: null,
    defaultColChars: null,
    conditional: 0,
    tables: 0,
    sparklines: false,
    slicers: false,
    drawing: null,
  };
  const start = xml.search(/<(?:\w+:)?sheetData\b/);
  const end = xml.search(/<\/(?:\w+:)?sheetData>/);
  const head = start < 0 ? xml : xml.slice(0, start);
  const tail = end < 0 ? "" : xml.slice(end);

  const view = /<(?:\w+:)?sheetView\b([^>]*)>/.exec(head);
  if (view && /^(0|false)$/.test(readAttrs(view[1]).showGridLines ?? "")) facts.gridlines = false;
  const pane = /<(?:\w+:)?pane\b([^>]*)>/.exec(head);
  if (pane) {
    const attrs = readAttrs(pane[1]);
    if (attrs.state === "frozen" || attrs.state === "frozenSplit") {
      facts.frozenCols = Math.max(0, Math.floor(Number(attrs.xSplit) || 0));
      facts.frozenRows = Math.max(0, Math.floor(Number(attrs.ySplit) || 0));
    }
  }
  const format = /<(?:\w+:)?sheetFormatPr\b([^>]*)>/.exec(head);
  if (format) {
    const attrs = readAttrs(format[1]);
    if (Number(attrs.defaultRowHeight) > 0) facts.defaultRowPt = Number(attrs.defaultRowHeight);
    if (Number(attrs.defaultColWidth) > 0) facts.defaultColChars = Number(attrs.defaultColWidth);
  }
  const cols = /<(?:\w+:)?col\b([^>]*)>/g;
  for (let match = cols.exec(head); match; match = cols.exec(head)) {
    const attrs = readAttrs(match[1]);
    const style = Number(attrs.style);
    if (style > 0) facts.colStyle.push([Number(attrs.min) - 1, Number(attrs.max) - 1, style]);
  }

  if (start >= 0) {
    const data = end < 0 ? xml.slice(start) : xml.slice(start, end);
    const tags = /<(?:\w+:)?(row|c)\b([^>]*)>/g;
    let row = -1;
    let col = -1;
    for (let match = tags.exec(data); match; match = tags.exec(data)) {
      const attrs = match[2];
      if (match[1] === "row") {
        const named = /\br="(\d+)"/.exec(attrs);
        row = named ? Number(named[1]) - 1 : row + 1;
        col = -1;
        if (attrs.indexOf("customFormat") >= 0 && /\bcustomFormat="(1|true)"/.test(attrs)) {
          const style = /\bs="(\d+)"/.exec(attrs);
          if (style && style[1] !== "0") facts.rowStyle.set(row, Number(style[1]));
        }
        continue;
      }
      const at = /\br="([A-Z]+)(\d+)"/.exec(attrs);
      if (at) {
        col = columnOf(at[1]);
        row = Number(at[2]) - 1;
      } else col += 1;
      if (attrs.indexOf(' s="') < 0) continue;
      const style = /\bs="(\d+)"/.exec(attrs);
      if (style && style[1] !== "0") facts.styleAt.set(cellKey(row, col), Number(style[1]));
    }
  }

  facts.conditional = count(tail, /<(?:\w+:)?conditionalFormatting\b/g);
  facts.tables = count(tail, /<(?:\w+:)?tablePart\b/g);
  facts.sparklines = tail.indexOf("sparklineGroup") >= 0;
  facts.slicers = tail.indexOf("slicerList") >= 0;
  const drawing = /<(?:\w+:)?drawing\b([^>]*)>/.exec(tail);
  if (drawing) facts.drawing = readAttrs(drawing[1]).id ?? null;
  return facts;
}

const NAMED: Record<string, string> = {
  black: "#000000",
  white: "#FFFFFF",
  red: "#C00000",
  green: "#008000",
  blue: "#0000FF",
  yellow: "#B8860B",
  magenta: "#C000C0",
  cyan: "#008B8B",
};

/**
 * The colour a number format gives a value: `$#,##0.00;[Red]($#,##0.00)` shows a negative
 * number in red. The library formats the text and does not say the colour.
 */
export function formatColour(format: string | undefined, value: number): string | undefined {
  if (!format || format.indexOf("[") < 0) return undefined;
  const sections: string[] = [];
  let piece = "";
  let quoted = false;
  for (let i = 0; i < format.length; i++) {
    const ch = format[i];
    if (ch === '"') quoted = !quoted;
    if (ch === "\\" && !quoted) {
      piece += ch + (format[i + 1] ?? "");
      i += 1;
    } else if (ch === ";" && !quoted) {
      sections.push(piece);
      piece = "";
    } else piece += ch;
  }
  sections.push(piece);
  const section =
    sections.length === 1 ? sections[0] : value < 0 ? sections[1] : value === 0 && sections.length > 2 ? sections[2] : sections[0];
  const named = /\[(black|white|red|green|blue|yellow|magenta|cyan)\]/i.exec(section ?? "");
  if (named) return NAMED[named[1].toLowerCase()];
  const numbered = /\[color\s*(\d+)\]/i.exec(section ?? "");
  // "Color n" counts the palette from 1, and its first eight are the named ones.
  const hex = numbered ? INDEXED[Number(numbered[1]) + 7] : undefined;
  return hex ? "#" + hex : undefined;
}
