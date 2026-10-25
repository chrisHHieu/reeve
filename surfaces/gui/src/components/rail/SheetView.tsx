// A spreadsheet, drawn as it looks in Excel and only to be read (workspace panel redesign,
// 2026-10-11): the cells' fonts, fills, borders, merges, widths and number formats, the frozen
// rows, the sheets as tabs, and a bar that shows what the selected cell holds.
//
// The workbook is read off this thread (sheet/source.ts) and asked for the cells in sight, so
// a sheet of any length scrolls: only what is in view is in the page. The grid is the app's
// own elements with the file's values as text and its styles as style properties, so nothing
// in a workbook can run here and none of it reaches the app's styles.
//
// A table ("Format as Table") is drawn in its style, Excel's built-in ones included; a rule of
// conditional formatting that can be judged from the cells' values is applied (comparisons,
// text, top and bottom, averages, duplicates, colour scales, data bars); a picture is drawn
// where it is anchored. Not drawn, and said on the sheet that has them: charts, pivot tables,
// sparklines, rules that need a formula worked out, icon sets. An older format (.xls, .ods)
// is shown as its values.
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent } from "react";
import { revealArtifact } from "../../api";
import { openExternal } from "../../desktop";
import { Icon } from "../Icon";
import { readFailure, type BookInfo, type CellView, type Picture, type Rect, type SheetInfo } from "./sheet/book";
import type { CellLook, Edge } from "./sheet/look";
import { openSource, type SheetSource } from "./sheet/source";

const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2];
const HEAD = 22;
const ROW_HEAD = 40;
const PT = 4 / 3;
// Cells are asked for a little past what is in sight, in steps, so a scroll within the margin
// asks for nothing.
const OVER_ROWS = 20;
const OVER_COLS = 4;
const STEP_ROWS = 10;
const STEP_COLS = 2;
// More frozen rows or columns than this would leave nothing to scroll; they are let go.
const FROZEN_CEILING = 40;

type Geometry = {
  /** Where each column starts, and after the last, where it ends. Pixels at this zoom. */
  colX: Float64Array;
  rowY: Float64Array;
  cols: number;
  rows: number;
  head: number;
  rowHead: number;
  frozenRows: number;
  frozenCols: number;
  frozenW: number;
  frozenH: number;
  width: number;
  height: number;
};
type Window = { r0: number; r1: number; c0: number; c1: number };
type At = { r: number; c: number };

/** The largest index whose start is at or before `value`. */
function indexAt(starts: Float64Array, value: number): number {
  let low = 0;
  let high = starts.length - 2;
  while (low < high) {
    const mid = (low + high + 1) >> 1;
    if (starts[mid] <= value) low = mid;
    else high = mid - 1;
  }
  return Math.max(0, low);
}

export function columnName(col: number): string {
  let name = "";
  for (let n = col + 1; n > 0; n = Math.floor((n - 1) / 26)) name = String.fromCharCode(65 + ((n - 1) % 26)) + name;
  return name;
}

/**
 * Where everything is: the sheet's own columns and rows at this zoom, then blank ones as far
 * as the panel reaches, the way Excel's grid goes on past what is used. A sheet smaller than
 * the panel does not scroll.
 */
function layout(sheet: SheetInfo, zoom: number, viewW: number, viewH: number): Geometry {
  const head = Math.round(HEAD * zoom);
  const defaultW = Math.max(8, Math.round(sheet.defaultWidth * zoom));
  const defaultH = Math.max(6, Math.round(sheet.defaultHeight * zoom));
  let usedH = 0;
  for (const height of sheet.heights) usedH += Math.round(height * zoom);
  const moreRows = Math.max(0, Math.ceil((viewH - head - usedH) / defaultH));
  const ownRows = sheet.heights.length;
  const ownCols = sheet.widths.length;
  const rows = ownRows + moreRows;
  const rowHead = Math.round(Math.max(ROW_HEAD, String(rows).length * 8 + 16) * zoom);
  let usedW = 0;
  for (const width of sheet.widths) usedW += Math.round(width * zoom);
  const moreCols = Math.max(0, Math.ceil((viewW - rowHead - usedW) / defaultW));
  const cols = ownCols + moreCols;
  const colX = new Float64Array(cols + 1);
  for (let c = 0; c < cols; c++) colX[c + 1] = colX[c] + (c < ownCols ? Math.round(sheet.widths[c] * zoom) : defaultW);
  const rowY = new Float64Array(rows + 1);
  for (let r = 0; r < rows; r++) rowY[r + 1] = rowY[r] + (r < ownRows ? Math.round(sheet.heights[r] * zoom) : defaultH);
  const frozenRows = sheet.frozenRows <= FROZEN_CEILING ? sheet.frozenRows : 0;
  const frozenCols = sheet.frozenCols <= FROZEN_CEILING ? sheet.frozenCols : 0;
  return {
    colX,
    rowY,
    cols,
    rows,
    head,
    rowHead,
    frozenRows,
    frozenCols,
    frozenW: colX[frozenCols],
    frozenH: rowY[frozenRows],
    width: Math.max(usedW, viewW - rowHead),
    height: Math.max(usedH, viewH - head),
  };
}

/** The scrolling rows and columns in sight, widened and stepped. */
function windowOf(g: Geometry, left: number, top: number, viewW: number, viewH: number): Window {
  const x0 = g.frozenW + left;
  const x1 = x0 + Math.max(0, viewW - g.rowHead - g.frozenW);
  const y0 = g.frozenH + top;
  const y1 = y0 + Math.max(0, viewH - g.head - g.frozenH);
  const c0 = Math.floor(indexAt(g.colX, x0) / STEP_COLS) * STEP_COLS - OVER_COLS;
  const c1 = Math.ceil((indexAt(g.colX, x1) + 1) / STEP_COLS) * STEP_COLS + OVER_COLS;
  const r0 = Math.floor(indexAt(g.rowY, y0) / STEP_ROWS) * STEP_ROWS - OVER_ROWS;
  const r1 = Math.ceil((indexAt(g.rowY, y1) + 1) / STEP_ROWS) * STEP_ROWS + OVER_ROWS;
  return {
    r0: Math.max(g.frozenRows, r0),
    r1: Math.min(g.rows - 1, r1),
    c0: Math.max(g.frozenCols, c0),
    c1: Math.min(g.cols - 1, c1),
  };
}

const edge = (side: Edge | undefined) => (side ? `${side.width}px ${side.style} ${side.color}` : undefined);

/** A cell style as style properties. The right and bottom edges of a merge are its last cell's. */
function styleOf(look: CellLook, last: CellLook | undefined, zoom: number): CSSProperties {
  const style: CSSProperties = { fontSize: Math.round((look.size ?? 11) * PT * zoom * 100) / 100 };
  if (look.font) style.fontFamily = `"${look.font}", Calibri, "Segoe UI", Arial, sans-serif`;
  if (look.bold) style.fontWeight = 700;
  if (look.italic) style.fontStyle = "italic";
  if (look.underline || look.strike) style.textDecoration = [look.underline && "underline", look.strike && "line-through"].filter(Boolean).join(" ");
  if (look.color) style.color = look.color;
  if (look.fill) style.backgroundColor = look.fill;
  style.borderTop = edge(look.top);
  style.borderLeft = edge(look.left);
  style.borderRight = edge((last ?? look).right);
  style.borderBottom = edge((last ?? look).bottom);
  if (look.valign === "top") style.alignItems = "flex-start";
  else if (look.valign === "middle") style.alignItems = "center";
  return style;
}

// Measures a number against its cell, for the #### Excel shows when it does not fit. Without a
// way to measure (the test DOM), numbers are shown as they are.
type Ruler = { font: string; measureText(text: string): { width: number } };
let ruler: Ruler | null | undefined;
function widthOf(text: string, font: string): number | null {
  if (ruler === undefined) {
    try {
      ruler = typeof OffscreenCanvas !== "undefined" ? (new OffscreenCanvas(1, 1).getContext("2d") as Ruler | null) : null;
    } catch {
      ruler = null;
    }
  }
  if (!ruler) return null;
  ruler.font = font;
  return ruler.measureText(text).width;
}

type PaneProps = {
  kind: "main" | "pin";
  band: "frozen" | "body";
  cells: CellView[];
  pictures: Picture[];
  looks: CellLook[];
  g: Geometry;
  zoom: number;
  gridlines: boolean;
  rows: [number, number];
  cols: [number, number];
  ids: string;
  selected: At | null;
  selectedEnd: At | null;
  onPick(event: MouseEvent<HTMLDivElement>): void;
};

/**
 * One of the four parts a frozen sheet is in: the cells of its rows and columns, the lines
 * between them, the row numbers when it is a pinned part, and the selection when it is here.
 */
const Pane = memo(function Pane({ kind, band, cells, pictures, looks, g, zoom, gridlines, rows, cols, ids, selected, selectedEnd, onPick }: PaneProps) {
  const ox = kind === "main" ? g.frozenW : 0;
  const oy = band === "body" ? g.frozenH : 0;
  // A pinned part starts with the row numbers, so its cells sit to their right.
  const dx = kind === "pin" ? g.rowHead : 0;
  const pad = Math.max(2, Math.round(3 * zoom));

  const byRow = new Map<number, CellView[]>();
  for (const cell of cells) {
    const list = byRow.get(cell.r);
    if (list) list.push(cell);
    else byRow.set(cell.r, [cell]);
  }

  const lines: JSX.Element[] = [];
  if (gridlines && cols[1] >= cols[0] && rows[1] >= rows[0]) {
    const top = g.rowY[rows[0]] - oy;
    const tall = g.rowY[rows[1] + 1] - g.rowY[rows[0]];
    const left = g.colX[cols[0]] - ox + dx;
    const wide = g.colX[cols[1] + 1] - g.colX[cols[0]];
    for (let c = cols[0]; c <= cols[1]; c++) {
      if (g.colX[c + 1] > g.colX[c]) lines.push(<i key={`v${c}`} className="sheet-vline" style={{ left: g.colX[c + 1] - ox + dx - 1, top, height: tall }} />);
    }
    for (let r = rows[0]; r <= rows[1]; r++) {
      if (g.rowY[r + 1] > g.rowY[r]) lines.push(<i key={`h${r}`} className="sheet-hline" style={{ top: g.rowY[r + 1] - oy - 1, left, width: wide }} />);
    }
  }

  const names: JSX.Element[] = [];
  if (kind === "pin") {
    for (let r = rows[0]; r <= rows[1]; r++) {
      const height = g.rowY[r + 1] - g.rowY[r];
      if (height <= 0) continue;
      names.push(
        <span key={r} className={"sheet-rowname" + (selected && r >= selected.r && r <= (selectedEnd?.r ?? selected.r) ? " is-on" : "")} style={{ top: g.rowY[r] - oy, height, width: g.rowHead, fontSize: Math.round(11.5 * zoom * 10) / 10 }}>
          {(r + 1).toLocaleString("en-US")}
        </span>,
      );
    }
  }

  const drawn: JSX.Element[] = [];
  byRow.forEach((list, r) => {
    drawn.push(
      <div key={r} role="row" aria-rowindex={r + 1} className="sheet-row">
        {list.map((cell) => {
          const look = cell.own ?? looks[cell.look] ?? {};
          const last = cell.lookEnd !== undefined ? looks[cell.lookEnd] : undefined;
          const endR = Math.min(cell.end?.[0] ?? cell.r, g.rows - 1);
          const endC = Math.min(cell.end?.[1] ?? cell.c, g.cols - 1);
          const width = g.colX[endC + 1] - g.colX[cell.c];
          const height = g.rowY[endR + 1] - g.rowY[cell.r];
          if (width <= 0 || height <= 0) return null;
          const style = styleOf(look, last, zoom);
          // A filled or bordered cell covers the lines around it, as it does in Excel.
          const boxed = !!(look.fill || look.top || look.left || (last ?? look).right || (last ?? look).bottom);
          style.left = g.colX[cell.c] - ox + dx - (boxed ? 1 : 0);
          style.top = g.rowY[cell.r] - oy - (boxed ? 1 : 0);
          style.width = width + (boxed ? 1 : 0);
          style.height = height + (boxed ? 1 : 0);
          if (cell.color) style.color = cell.color;
          const across = look.align ?? (cell.kind === "number" ? "right" : cell.kind === "text" ? "left" : "center");
          style.justifyContent = across === "right" ? "flex-end" : across === "center" ? "center" : "flex-start";
          const inset = pad + (look.indent ? Math.round(look.indent * 9 * zoom) : 0);
          style.paddingLeft = across === "right" ? pad : inset;
          style.paddingRight = across === "right" ? inset : pad;

          let text = cell.text;
          let spill: CSSProperties | undefined;
          if (cell.kind === "number" && !cell.end) {
            const measured = widthOf(text, `${look.italic ? "italic " : ""}${look.bold ? "700 " : ""}${style.fontSize}px ${style.fontFamily ?? 'Calibri, "Segoe UI", Arial, sans-serif'}`);
            // Half a number is a wrong number: Excel shows #### for one that does not fit.
            if (measured !== null && measured > width - 2 * pad + 1) text = "#".repeat(Math.max(1, Math.floor((width - 2 * pad) / (0.52 * Number(style.fontSize)))));
          } else if (cell.until !== undefined && cell.until > cell.c + 1) {
            const reach = g.colX[Math.min(cell.until, g.cols)] - g.colX[cell.c];
            if (reach > width) spill = { maxWidth: reach - 2 * pad, backgroundColor: look.fill ?? "var(--sheet-ground)" };
          }
          const on = selected?.r === cell.r && selected.c === cell.c;
          return (
            <div
              key={cell.c}
              id={on ? `${ids}-on` : undefined}
              role="gridcell"
              aria-colindex={cell.c + 1}
              aria-selected={on || undefined}
              className={"sheet-cell" + (look.wrap ? " is-wrap" : "") + (spill ? " is-spill" : "")}
              data-cell={`${cell.r}:${cell.c}`}
              style={style}
            >
              {cell.bar && <i className="sheet-bar" style={{ width: `${Math.round(cell.bar.width * 1000) / 10}%`, backgroundColor: cell.bar.color }} />}
              {text &&
                (cell.link ? (
                  <span className="sheet-text sheet-link" role="link" data-link={cell.link} style={spill}>
                    {text}
                  </span>
                ) : (
                  <span className="sheet-text" style={spill}>
                    {text}
                  </span>
                ))}
            </div>
          );
        })}
      </div>,
    );
  });

  let box: JSX.Element | null = null;
  if (selected) {
    const here = (kind === "pin") === selected.c < g.frozenCols && (band === "frozen") === selected.r < g.frozenRows;
    const end = selectedEnd ?? selected;
    const width = g.colX[Math.min(end.c, g.cols - 1) + 1] - g.colX[selected.c];
    const height = g.rowY[Math.min(end.r, g.rows - 1) + 1] - g.rowY[selected.r];
    if (here && width > 0 && height > 0) {
      box = <div className="sheet-sel" data-testid="sheet-selection" style={{ left: g.colX[selected.c] - ox + dx - 1, top: g.rowY[selected.r] - oy - 1, width: width + 1, height: height + 1 }} />;
    }
  }

  return (
    <div
      className={kind === "pin" ? "sheet-pin" : "sheet-main"}
      data-ox={ox - dx}
      data-oy={oy}
      style={kind === "pin" ? { width: g.rowHead + g.frozenW } : { width: g.width - g.frozenW }}
      onClick={onPick}
    >
      {lines}
      {drawn}
      {pictures.map((picture, at) =>
        picture.r < g.rows && picture.c < g.cols ? (
          <img
            key={at}
            className="sheet-picture"
            data-testid="sheet-picture"
            src={picture.src}
            alt={picture.alt}
            draggable={false}
            style={{
              left: g.colX[picture.c] - ox + dx + Math.round(picture.dx * zoom),
              top: g.rowY[picture.r] - oy + Math.round(picture.dy * zoom),
              width: Math.round(picture.w * zoom),
              height: Math.round(picture.h * zoom),
            }}
          />
        ) : null,
      )}
      {names}
      {box}
    </div>
  );
});

/** The letters over the columns in one part of the head. */
const ColumnNames = memo(function ColumnNames({ g, cols, pinned, selected, selectedEnd, zoom }: { g: Geometry; cols: [number, number]; pinned: boolean; selected: At | null; selectedEnd: At | null; zoom: number }) {
  const ox = pinned ? -g.rowHead : g.frozenW;
  const names: JSX.Element[] = [];
  for (let c = cols[0]; c <= cols[1]; c++) {
    const width = g.colX[c + 1] - g.colX[c];
    if (width <= 0) continue;
    names.push(
      <span key={c} className={"sheet-colname" + (selected && c >= selected.c && c <= (selectedEnd?.c ?? selected.c) ? " is-on" : "")} style={{ left: g.colX[c] - ox, width, fontSize: Math.round(11.5 * zoom * 10) / 10 }}>
        {columnName(c)}
      </span>,
    );
  }
  return <>{names}</>;
});

/** The sheets as tabs. When they do not fit, the strip is moved with the two arrows. */
function SheetTabs({ sheets, active, onPick }: { sheets: string[]; active: number; onPick(index: number): void }) {
  const strip = useRef<HTMLDivElement | null>(null);
  const [crowded, setCrowded] = useState(false);
  useEffect(() => {
    const element = strip.current;
    if (!element) return;
    const measure = () => setCrowded(element.scrollWidth > element.clientWidth + 1);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [sheets]);
  useEffect(() => {
    strip.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [active]);
  const move = (by: number) => strip.current?.scrollBy?.({ left: by * 140 });
  return (
    <>
      {crowded && (
        <>
          <button type="button" className="artifact-icon-btn sheet-tab-move" aria-label="Earlier sheets" data-testid="sheet-tabs-back" onClick={() => move(-1)}>
            <span className="sheet-flip" aria-hidden="true">
              <Icon name="chevronRight" size={13} />
            </span>
          </button>
          <button type="button" className="artifact-icon-btn sheet-tab-move" aria-label="Later sheets" data-testid="sheet-tabs-on" onClick={() => move(1)}>
            <Icon name="chevronRight" size={13} />
          </button>
        </>
      )}
      <div className="sheet-tabs" role="tablist" aria-label="Sheets" ref={strip} data-testid="sheet-tabs">
        {sheets.map((name, index) => (
          <button key={index} type="button" role="tab" className={"sheet-tab" + (index === active ? " is-on" : "")} aria-selected={index === active} title={name} onClick={() => onPick(index)}>
            {name}
          </button>
        ))}
      </div>
    </>
  );
}

const NO_CELLS: CellView[] = [];
const keyOf = (win: Window) => `${win.r0}:${win.r1}:${win.c0}:${win.c1}`;

export function SheetView({ sessionId, path, name, dataUrl }: { sessionId: string; path: string; name: string; dataUrl: string }) {
  const source = useRef<SheetSource | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);
  const ids = useRef(`sheet-${Math.random().toString(36).slice(2, 8)}`).current;
  const [book, setBook] = useState<BookInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  // The sheet that is drawn, with the tab it belongs to: while the next one is being read, the
  // last stays on screen.
  const [shown, setShown] = useState<{ index: number; sheet: SheetInfo } | null>(null);
  const [zoom, setZoom] = useState(1);
  const [view, setView] = useState({ w: 800, h: 600 });
  const [scroll, setScroll] = useState({ left: 0, top: 0 });
  const [cells, setCells] = useState<CellView[]>(NO_CELLS);
  const [selected, setSelected] = useState<At>({ r: 0, c: 0 });
  const [held, setHeld] = useState<CellView | null>(null);
  const picked = useRef(new Map<number, At>());

  // The workbook: read once per file.
  useEffect(() => {
    let alive = true;
    setBook(null);
    setError(null);
    setShown(null);
    setCells(NO_CELLS);
    setActive(0);
    setSelected({ r: 0, c: 0 });
    picked.current.clear();
    const opened = openSource(dataUrl.slice(dataUrl.indexOf(",") + 1));
    source.current = opened;
    opened.open().then(
      (info) => alive && (info.sheets.length ? setBook(info) : setError(`${name} has no sheet to show.`)),
      (failure: unknown) => alive && setError(readFailure(failure, name)),
    );
    return () => {
      alive = false;
      opened.close();
      if (source.current === opened) source.current = null;
    };
  }, [dataUrl, name]);

  // The sheet of the tab that is open.
  useEffect(() => {
    if (!book) return;
    let alive = true;
    source.current?.sheet(active).then(
      (sheet) => {
        if (!alive) return;
        setCells(NO_CELLS);
        setShown({ index: active, sheet });
        setSelected(picked.current.get(active) ?? { r: 0, c: 0 });
        // A sheet opens at its top left (its grid is a new element, so it is there already).
        setScroll({ left: 0, top: 0 });
      },
      (failure: unknown) => alive && setError(readFailure(failure, name)),
    );
    return () => {
      alive = false;
    };
  }, [book, active, name]);

  // The panel's size: the grid fills it, and asks for what fits in it.
  useEffect(() => {
    const element = scroller.current;
    if (!element || !shown) return;
    const measure = () => {
      const w = element.clientWidth;
      const h = element.clientHeight;
      if (w > 0 && h > 0) setView((now) => (now.w === w && now.h === h ? now : { w, h }));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [shown]);

  const sheet = shown?.sheet ?? null;
  const g = useMemo(() => (sheet ? layout(sheet, zoom, view.w, view.h) : null), [sheet, zoom, view.w, view.h]);
  // `scroll` is where the grid was when its window last moved, not where it is now: a scroll
  // inside the margin changes no state, so nothing is drawn again for it.
  const win = useMemo(() => (g ? windowOf(g, scroll.left, scroll.top, view.w, view.h) : null), [g, scroll.left, scroll.top, view.w, view.h]);
  const winKey = win ? keyOf(win) : "";
  const winNow = useRef(winKey);
  winNow.current = winKey;
  // The same arrays while the window stands still, so the parts of the grid are not drawn again.
  const range = useMemo(
    () => ({
      rows: [win?.r0 ?? 0, win?.r1 ?? -1] as [number, number],
      cols: [win?.c0 ?? 0, win?.c1 ?? -1] as [number, number],
      frozenRows: [0, (g?.frozenRows ?? 0) - 1] as [number, number],
      frozenCols: [0, (g?.frozenCols ?? 0) - 1] as [number, number],
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [winKey, g?.frozenRows, g?.frozenCols],
  );

  // The cells in sight, asked for when the window moves a step.
  useEffect(() => {
    if (!shown || !g || !win) return;
    let alive = true;
    const rects: Rect[] = [[win.r0, win.r1, win.c0, win.c1]];
    if (g.frozenRows) rects.push([0, g.frozenRows - 1, win.c0, win.c1]);
    if (g.frozenCols) rects.push([win.r0, win.r1, 0, g.frozenCols - 1]);
    if (g.frozenRows && g.frozenCols) rects.push([0, g.frozenRows - 1, 0, g.frozenCols - 1]);
    source.current?.cells(shown.index, rects).then(
      (next) => alive && setCells(next),
      () => undefined,
    );
    return () => {
      alive = false;
    };
    // The window is compared by its four numbers, not by the object that holds them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown, winKey, g?.frozenRows, g?.frozenCols]);

  // A merged cell is selected, and drawn, from its first cell to its last.
  const mergeAt = useCallback(
    (at: At): Rect | null => {
      for (const merge of sheet?.merges ?? []) {
        if (at.r >= merge[0] && at.r <= merge[1] && at.c >= merge[2] && at.c <= merge[3]) return merge;
      }
      return null;
    },
    [sheet],
  );
  const selectedMerge = useMemo(() => mergeAt(selected), [mergeAt, selected]);
  const selectedEnd = useMemo<At | null>(() => (selectedMerge ? { r: selectedMerge[1], c: selectedMerge[3] } : null), [selectedMerge]);

  // What the selected cell holds, for the bar. Asked for, since it may not be in sight.
  useEffect(() => {
    if (!shown) return;
    let alive = true;
    setHeld(null);
    source.current?.cells(shown.index, [[selected.r, selected.r, selected.c, selected.c]]).then(
      (found) => alive && setHeld(found.find((cell) => cell.r === selected.r && cell.c === selected.c) ?? null),
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, [shown, selected]);

  const select = useCallback(
    (at: At) => {
      const merge = mergeAt(at);
      const next = merge ? { r: merge[0], c: merge[2] } : at;
      setSelected(next);
      if (shown) picked.current.set(shown.index, next);
    },
    [mergeAt, shown],
  );

  const onPick = useCallback(
    (event: MouseEvent<HTMLDivElement>) => {
      if (!g) return;
      const target = event.target as HTMLElement;
      const link = target.closest<HTMLElement>("[data-link]")?.dataset.link;
      const cell = target.closest<HTMLElement>("[data-cell]")?.dataset.cell;
      if (cell) {
        const [r, c] = cell.split(":").map(Number);
        select({ r, c });
      } else {
        const pane = event.currentTarget;
        const bounds = pane.getBoundingClientRect();
        const x = event.clientX - bounds.left + Number(pane.dataset.ox);
        const y = event.clientY - bounds.top + Number(pane.dataset.oy);
        if (x >= 0 && y >= 0) select({ r: indexAt(g.rowY, y), c: indexAt(g.colX, x) });
      }
      if (link) void openExternal(link);
      scroller.current?.focus({ preventScroll: true });
    },
    [g, select],
  );

  // Scrolls the least it can to bring a cell into the part of the grid that scrolls.
  const reveal = (at: At) => {
    const element = scroller.current;
    if (!element || !g) return;
    if (at.c >= g.frozenCols) {
      const x0 = g.colX[at.c] - g.frozenW;
      const x1 = g.colX[at.c + 1] - g.frozenW;
      const wide = view.w - g.rowHead - g.frozenW;
      if (x0 < element.scrollLeft) element.scrollLeft = x0;
      else if (x1 > element.scrollLeft + wide) element.scrollLeft = x1 - wide;
    }
    if (at.r >= g.frozenRows) {
      const y0 = g.rowY[at.r] - g.frozenH;
      const y1 = g.rowY[at.r + 1] - g.frozenH;
      const tall = view.h - g.head - g.frozenH;
      if (y0 < element.scrollTop) element.scrollTop = y0;
      else if (y1 > element.scrollTop + tall) element.scrollTop = y1 - tall;
    }
  };

  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!g || !sheet) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "c") {
      if (held?.text) void navigator.clipboard?.writeText(held.text);
      return;
    }
    const lastRow = Math.max(0, g.rows - 1);
    const lastCol = Math.max(0, g.cols - 1);
    const page = Math.max(1, Math.floor((view.h - g.head - g.frozenH) / Math.max(1, Math.round(sheet.defaultHeight * zoom))) - 1);
    const from = selectedMerge;
    let { r, c } = selected;
    // A hidden row or column is stepped over, and a merged cell is left from its far side.
    const step = (dr: number, dc: number) => {
      if (dr > 0 && from) r = from[1];
      if (dc > 0 && from) c = from[3];
      for (;;) {
        const nextR = Math.min(lastRow, Math.max(0, r + dr));
        const nextC = Math.min(lastCol, Math.max(0, c + dc));
        if (nextR === r && nextC === c) break;
        r = nextR;
        c = nextC;
        if (g.rowY[r + 1] > g.rowY[r] && g.colX[c + 1] > g.colX[c]) break;
      }
    };
    const whole = event.ctrlKey || event.metaKey;
    if (event.key === "ArrowDown") step(1, 0);
    else if (event.key === "ArrowUp") step(-1, 0);
    else if (event.key === "ArrowRight") step(0, 1);
    else if (event.key === "ArrowLeft") step(0, -1);
    else if (event.key === "PageDown") r = Math.min(lastRow, r + page);
    else if (event.key === "PageUp") r = Math.max(0, r - page);
    else if (event.key === "Home") {
      c = 0;
      // The top of the sheet is its first cell that scrolls, with the grid back at its start.
      if (whole) {
        r = Math.min(g.frozenRows, lastRow);
        c = Math.min(g.frozenCols, lastCol);
        if (scroller.current) {
          scroller.current.scrollTop = 0;
          scroller.current.scrollLeft = 0;
        }
      }
    } else if (event.key === "End") {
      c = Math.max(0, sheet.cols - 1);
      if (whole) r = Math.max(0, sheet.rows - 1);
    } else return;
    event.preventDefault();
    const merge = mergeAt({ r, c });
    const next = merge ? { r: merge[0], c: merge[2] } : { r, c };
    select(next);
    reveal(next);
  };

  // One read of the scroll position a frame, and new state only when the window moves a step.
  const frame = useRef(0);
  const onScroll = () => {
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      const element = scroller.current;
      if (!element || !g) return;
      if (keyOf(windowOf(g, element.scrollLeft, element.scrollTop, view.w, view.h)) !== winNow.current) {
        setScroll({ left: element.scrollLeft, top: element.scrollTop });
      }
    });
  };
  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  // A new zoom keeps the same cell at the top left.
  const corner = useRef<At | null>(null);
  const zoomTo = (next: number) => {
    const element = scroller.current;
    if (g && element) corner.current = { r: indexAt(g.rowY, g.frozenH + element.scrollTop), c: indexAt(g.colX, g.frozenW + element.scrollLeft) };
    setZoom(next);
  };
  useLayoutEffect(() => {
    const element = scroller.current;
    const at = corner.current;
    corner.current = null;
    if (!element || !g || !at) return;
    element.scrollTop = Math.max(0, g.rowY[Math.min(at.r, g.rows)] - g.frozenH);
    element.scrollLeft = Math.max(0, g.colX[Math.min(at.c, g.cols)] - g.frozenW);
    // Only after a zoom: the geometry it reads is the one that zoom made.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoom]);

  // A picture belongs to the part of the grid its corner is in, as a cell does.
  const pictures = useMemo(() => {
    const split = { main: [] as Picture[], pin: [] as Picture[], topMain: [] as Picture[], topPin: [] as Picture[] };
    for (const picture of sheet?.pictures ?? []) {
      const top = picture.r < (g?.frozenRows ?? 0);
      const pinned = picture.c < (g?.frozenCols ?? 0);
      (top ? (pinned ? split.topPin : split.topMain) : pinned ? split.pin : split.main).push(picture);
    }
    return split;
  }, [sheet, g?.frozenRows, g?.frozenCols]);

  const parts = useMemo(() => {
    const split = { main: [] as CellView[], pin: [] as CellView[], topMain: [] as CellView[], topPin: [] as CellView[] };
    if (!g) return split;
    for (const cell of cells) {
      const top = cell.r < g.frozenRows;
      const pinned = cell.c < g.frozenCols;
      (top ? (pinned ? split.topPin : split.topMain) : pinned ? split.pin : split.main).push(cell);
    }
    return split;
  }, [cells, g]);

  const app = /\.ods$/i.test(name) ? "your spreadsheet app" : "Excel";
  const outside = (
    <button type="button" className="btn sm" onClick={() => revealArtifact(sessionId, path, "open")}>
      Open in {app}
    </button>
  );
  if (error !== null) {
    return (
      <div className="artifact-open-prompt" data-testid="sheet-undrawn">
        <p>{error}</p>
        {outside}
      </div>
    );
  }

  const step = (by: 1 | -1) => {
    const next = by > 0 ? ZOOMS.find((z) => z > zoom + 0.01) : [...ZOOMS].reverse().find((z) => z < zoom - 0.01);
    if (next !== undefined) zoomTo(next);
  };
  const ready = !!(book && sheet && g && win);
  const notDrawn = sheet?.notDrawn ?? [];
  const note = !book
    ? null
    : !book.styled
      ? `${/\.(xls|ods)$/i.test(name) ? "Older format: shown" : "Shown"} as its values, without its formatting.`
      : notDrawn.length
        ? `This sheet has ${notDrawn.length > 1 ? `${notDrawn.slice(0, -1).join(", ")} and ${notDrawn[notDrawn.length - 1]}` : notDrawn[0]}, which ${notDrawn.length > 1 || /^\d+ \w+s$|^(sparklines|slicers)$/.test(notDrawn[0]) ? "are" : "is"} not drawn here.`
        : null;
  const address = `${columnName(selected.c)}${selected.r + 1}`;
  const holds = held ? (held.held ?? held.text) : "";

  return (
    <div className="page-frame sheet-view" data-testid="sheet-view" aria-busy={!ready || undefined}>
      {note && (
        <div className="fv-note sheet-note" role="note" data-testid="sheet-note">
          <span>{note}</span>
          {outside}
        </div>
      )}
      <div className="sheet-fx" aria-live="polite">
        <span className="sheet-fx-at" data-testid="sheet-address" aria-label="Selected cell">
          {ready ? address : ""}
        </span>
        <span className="sheet-fx-mark" aria-hidden="true">
          fx
        </span>
        <span className="sheet-fx-holds" data-testid="sheet-holds" aria-label="What the cell holds" title={holds}>
          {holds}
        </span>
      </div>

      {!ready && (
        <div className="sheet-wait" data-testid="sheet-wait" role="status" aria-label={`Opening ${name}`}>
          <div className="sheet-wait-head" />
          {[62, 48, 74, 40, 66, 54].map((wide, row) => (
            <div key={row} className="sheet-wait-row">
              <span style={{ width: 14 }} />
              <span style={{ width: `${wide * 0.4}%` }} />
              <span style={{ width: `${(100 - wide) * 0.3}%` }} />
              <span style={{ width: `${wide * 0.25}%` }} />
            </div>
          ))}
        </div>
      )}

      {ready && g && win && sheet && book && (
        <div
          key={shown?.index}
          ref={scroller}
          className={"sheet-grid" + (g.frozenCols ? " has-frozen-cols" : "")}
          data-testid="sheet-grid"
          role="grid"
          aria-label={sheet.name}
          aria-rowcount={sheet.rows}
          aria-colcount={sheet.cols}
          aria-activedescendant={held ? `${ids}-on` : undefined}
          tabIndex={0}
          onScroll={onScroll}
          onKeyDown={onKey}
        >
          <div className="sheet-size" style={{ width: g.rowHead + g.width, height: g.head + g.height }}>
            <div className="sheet-band is-head" style={{ height: g.head }} aria-hidden="true">
              <div className="sheet-pin" style={{ width: g.rowHead + g.frozenW }}>
                <span className="sheet-corner" style={{ width: g.rowHead }} />
                <ColumnNames g={g} cols={range.frozenCols} pinned selected={selected} selectedEnd={selectedEnd} zoom={zoom} />
              </div>
              <div className="sheet-main" style={{ width: g.width - g.frozenW }}>
                <ColumnNames g={g} cols={range.cols} pinned={false} selected={selected} selectedEnd={selectedEnd} zoom={zoom} />
              </div>
            </div>
            {g.frozenRows > 0 && (
              <div className="sheet-band is-frozen" style={{ height: g.frozenH, top: g.head }}>
                <Pane kind="pin" band="frozen" cells={parts.topPin} pictures={pictures.topPin} looks={book.looks} g={g} zoom={zoom} gridlines={sheet.gridlines} rows={range.frozenRows} cols={range.frozenCols} ids={ids} selected={selected} selectedEnd={selectedEnd} onPick={onPick} />
                <Pane kind="main" band="frozen" cells={parts.topMain} pictures={pictures.topMain} looks={book.looks} g={g} zoom={zoom} gridlines={sheet.gridlines} rows={range.frozenRows} cols={range.cols} ids={ids} selected={selected} selectedEnd={selectedEnd} onPick={onPick} />
              </div>
            )}
            <div className="sheet-band is-body" style={{ height: g.height - g.frozenH }}>
              <Pane kind="pin" band="body" cells={parts.pin} pictures={pictures.pin} looks={book.looks} g={g} zoom={zoom} gridlines={sheet.gridlines} rows={range.rows} cols={range.frozenCols} ids={ids} selected={selected} selectedEnd={selectedEnd} onPick={onPick} />
              <Pane kind="main" band="body" cells={parts.main} pictures={pictures.main} looks={book.looks} g={g} zoom={zoom} gridlines={sheet.gridlines} rows={range.rows} cols={range.cols} ids={ids} selected={selected} selectedEnd={selectedEnd} onPick={onPick} />
            </div>
          </div>
        </div>
      )}

      <div className="sheet-foot">
        {book && <SheetTabs sheets={book.sheets} active={active} onPick={setActive} />}
        <span className="fv-fill" />
        {sheet && (
          <span className="sheet-dims" data-testid="sheet-dims">
            {sheet.rows.toLocaleString("en-US")} {sheet.rows === 1 ? "row" : "rows"} · {sheet.cols.toLocaleString("en-US")} {sheet.cols === 1 ? "column" : "columns"}
          </span>
        )}
        <button type="button" className="artifact-icon-btn" aria-label="Zoom out" data-testid="sheet-zoom-out" disabled={!ready || zoom <= ZOOMS[0]} onClick={() => step(-1)}>
          <span aria-hidden="true">−</span>
        </button>
        <button type="button" className="fv-note-btn office-zoom" data-testid="sheet-zoom" title="Back to the sheet's own size" disabled={!ready} onClick={() => zoomTo(1)}>
          {Math.round(zoom * 100)}%
        </button>
        <button type="button" className="artifact-icon-btn" aria-label="Zoom in" data-testid="sheet-zoom-in" disabled={!ready || zoom >= ZOOMS[ZOOMS.length - 1]} onClick={() => step(1)}>
          <Icon name="plus" size={13} />
        </button>
      </div>
    </div>
  );
}
