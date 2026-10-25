// What a reader can do with a table's rows: sort them by a column, keep the ones that match,
// and read the column they sorted by the way a sheet's status bar reads a selection (sum,
// mean, range). Pure functions over the cell text, so the rules are tested without a DOM and
// the table component only maps the order back onto the rows the renderer drew.
//
// Nothing here changes what the model wrote: the order and the filter are a view, the copy
// button still copies the table as written, and a column's numbers are summed only when the
// reader picked that column and every value in it is a number.

/** A number as tables write one: `6.500.000đ`, `1,250,000`, `$1.2`, `-3,5`, `12%`. */
export function parseNumber(text: string): number | null {
  let s = text.trim().replace(/−/g, "-").replace(/[\s ]/g, "");
  if (!s) return null;
  // A currency sign before, or one known unit after (đ, VND, %, tr, k, ms…), is not part of
  // the number; any other letters make the cell text ("abc12def", "v1.2").
  s = s.replace(/^[$€£¥₫]/, "").replace(/(%|₫|đ|vnd|usd|eur|triệu|tr|tỷ|k|m|bn|b|x|×|ms|s|kb|mb|gb)$/i, "");
  if (!/^[+-]?[\d.,]*\d[\d.,]*$/.test(s)) return null;
  const sign = s.startsWith("-") ? -1 : 1;
  s = s.replace(/^[+-]/, "");
  const grouped = /^\d{1,3}([.,])\d{3}(?:\1\d{3})*(?:([.,])\d+)?$/.exec(s);
  if (grouped) {
    const group = grouped[1];
    const decimal = grouped[2];
    // `1.250` alone could be either; a group separator followed by exactly three digits is
    // read as grouping, which is how both vi (6.500) and en (1,250) tables write money.
    if (decimal && decimal === group) return null;
    const whole = s.split(decimal ?? "\u0000")[0].split(group).join("");
    const frac = decimal ? s.slice(s.lastIndexOf(decimal) + 1) : "";
    return sign * Number(frac ? `${whole}.${frac}` : whole);
  }
  if (/^\d+,\d+$/.test(s)) return sign * Number(s.replace(",", "."));
  if (/^\d*\.?\d+$/.test(s)) return sign * Number(s);
  return null;
}

/** A row that closes the table with its own total ("Tổng", "Total") stays last and out of the view's sums. */
export function isTotalRow(cells: string[]): boolean {
  return /^\s*\**\s*(tổng|tong|total|sum|cộng)\b/i.test(cells[0] ?? "");
}

/** Whether every non-empty cell of `column` in `body` reads as a number (and at least two do). */
export function numericColumn(body: string[][], column: number): boolean {
  let seen = 0;
  for (const cells of body) {
    const text = (cells[column] ?? "").trim();
    if (!text || text === "—" || text === "-") continue;
    if (parseNumber(text) === null) return false;
    seen += 1;
  }
  return seen >= 2;
}

export type SortDirection = "ascending" | "descending";
export interface TableView {
  column: number | null;
  direction: SortDirection;
  query: string;
}

/** The body rows' indices in display order: matching `query`, sorted by `column`. A sort is
 *  stable, so rows that tie keep the order the model wrote them in. */
export function viewOrder(body: string[][], view: TableView): number[] {
  const q = fold(view.query.trim());
  const keep = body
    .map((cells, index) => ({ cells, index }))
    .filter(({ cells }) => !q || cells.some((cell) => fold(cell).includes(q)));
  if (view.column === null) return keep.map(({ index }) => index);
  const column = view.column;
  const numeric = numericColumn(body, column);
  const sign = view.direction === "ascending" ? 1 : -1;
  const key = (cells: string[]) => (cells[column] ?? "").trim();
  return keep
    .slice()
    .sort((a, b) => {
      const x = key(a.cells);
      const y = key(b.cells);
      // Empty cells sink to the end whichever way the column is sorted.
      if (!x || !y) return !x && !y ? a.index - b.index : !x ? 1 : -1;
      let order: number;
      if (numeric) order = (parseNumber(x) ?? 0) - (parseNumber(y) ?? 0);
      else order = x.localeCompare(y, undefined, { numeric: true, sensitivity: "base" });
      return order !== 0 ? sign * order : a.index - b.index;
    })
    .map(({ index }) => index);
}

export interface ColumnStats {
  count: number;
  sum: number;
  mean: number;
  min: number;
  max: number;
}

/** The sorted column's numbers over the rows in view, or null when the column is not numeric. */
export function columnStats(body: string[][], rows: number[], column: number): ColumnStats | null {
  if (!numericColumn(body, column)) return null;
  const values = rows.map((index) => parseNumber(body[index]?.[column] ?? "")).filter((v): v is number => v !== null);
  if (values.length === 0) return null;
  const sum = values.reduce((total, value) => total + value, 0);
  return { count: values.length, sum, mean: sum / values.length, min: Math.min(...values), max: Math.max(...values) };
}

/** Case and accent folded, so "tien nha" finds "Tiền nhà". */
export function fold(text: string): string {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D").toLowerCase();
}

/** A stat as the column wrote its numbers: grouping kept, at most two decimals. */
export function formatStat(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: Math.abs(value) >= 100 ? 0 : 2 });
}
