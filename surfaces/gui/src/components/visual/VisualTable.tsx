// A visual's table: the same reading a Markdown table gets (sort by a header, filter a long
// one, the sorted column's sum and range), over the rows the block carries. A long
// table shows its first rows in the order the reader chose and opens the rest on request, so it
// stands in an answer as a few rows, not a scrolling pane.

import { useMemo, useRef, useState } from "react";
import { Icon } from "../Icon";
import { columnStats, formatStat, numericColumn, viewOrder, type TableView } from "../markdown/tableModel";
import { useRowFlip } from "../primitives/useRowFlip";
import type { TableSpec } from "./visualModel";

const FILTER_FROM_ROWS = 8;
/** Rows a long table shows before "Show all"; a filter shows every row it keeps. */
const FOLDED_ROWS = 8;

export function VisualTable({ spec }: { spec: TableSpec }) {
  const [view, setView] = useState<TableView>({ column: null, direction: "ascending", query: "" });
  const [all, setAll] = useState(false);
  const body = useRef<HTMLTableSectionElement>(null);
  const rows = spec.rows;
  const order = useMemo(() => viewOrder(rows, view), [rows, view]);
  const measure = useRowFlip(body, ":scope > tr[data-row]", order);
  const numeric = useMemo(() => spec.columns.map((_, i) => numericColumn(rows, i)), [spec.columns, rows]);
  const stats = view.column !== null ? columnStats(rows, order, view.column) : null;
  const folded = !all && !view.query && order.length > FOLDED_ROWS + 2;
  const shown = folded ? order.slice(0, FOLDED_ROWS) : order;
  const sortBy = (column: number) => {
    measure();
    setView((v) =>
      v.column !== column
        ? { ...v, column, direction: "ascending" }
        : v.direction === "ascending"
          ? { ...v, direction: "descending" }
          : { ...v, column: null, direction: "ascending" },
    );
  };
  const shownOf = spec.source?.rows && spec.source.rows > rows.length ? `first ${rows.length} of ${spec.source.rows} rows` : null;
  return (
    <div className="vz-table">
      {rows.length >= FILTER_FROM_ROWS && (
        <label className="md-tablefilter vz-table-filter">
          <Icon name="search" size={12} />
          <input
            type="search"
            placeholder="Filter rows"
            aria-label="Filter the table's rows"
            value={view.query}
            onChange={(event) => {
              measure();
              const query = event.target.value;
              setView((v) => ({ ...v, query }));
            }}
          />
        </label>
      )}
      <div className="vz-table-scroll">
        <table>
          <thead>
            <tr>
              {spec.columns.map((column, i) => (
                <th
                  key={i}
                  tabIndex={0}
                  aria-sort={view.column === i ? view.direction : "none"}
                  className={numeric[i] ? "vz-num" : undefined}
                  onClick={() => sortBy(i)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      sortBy(i);
                    }
                  }}
                >
                  {column}
                  <span className="md-sort-arrow" aria-hidden>
                    <Icon name="chevronUp" size={11} />
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody ref={body}>
            {shown.map((index) => (
              <tr key={index} data-row={index}>
                {spec.columns.map((_, i) => (
                  <td key={i} className={numeric[i] ? "vz-num" : undefined}>
                    {rows[index][i] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {folded && (
        <button type="button" className="vz-table-more" onClick={() => setAll(true)}>
          Show all {order.length} rows
        </button>
      )}
      <div className="vz-table-status">
        <span>{order.length === rows.length ? `${rows.length} rows` : `${order.length} of ${rows.length} rows`}</span>
        {shownOf && <span>{shownOf}</span>}
        {stats && view.column !== null && (
          <span>
            <b>{spec.columns[view.column]}</b> · Sum {formatStat(stats.sum)} · Avg {formatStat(stats.mean)} · Range{" "}
            {formatStat(stats.min)}–{formatStat(stats.max)}
          </span>
        )}
      </div>
    </div>
  );
}
