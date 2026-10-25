// GFM tables with what a reader does with one: sort it by a column, keep the rows that match,
// read the sorted column's numbers, copy it, or take it to a sheet.
//
// Both export formats are built from the hast node rather than the rendered DOM: the node is
// the same data the renderer drew from, it exists before paint, and it does not need a browser
// to read back. The CSV is quoted per RFC 4180 (a cell containing a comma, quote or newline
// gets quoted with `""` escaping) because a table cell is exactly where commas leak in.
//
// The view (order, filter) is the reader's, never the model's: the rows the renderer drew are
// reordered as they are, links and code inside cells included, and Copy still copies the table
// as written. A table whose rendered rows do not line up with its hast rows is drawn as it
// came, without the controls — a view that might show the wrong row is worse than none.
//
// The scroll affordance is the shared `ScrollFade` in horizontal mode, so a table that is
// wider than the transcript says so at the edge instead of looking cut.

import {
  Children,
  cloneElement,
  isValidElement,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactElement,
} from "react";
import { ScrollFade } from "../primitives/ScrollFade";
import { useRowFlip } from "../primitives/useRowFlip";
import { Icon } from "../Icon";
import { rowsToHtml, writeRich } from "./richCopy";
import { columnStats, formatStat, isTotalRow, numericColumn, viewOrder, type TableView } from "./tableModel";

type HastNode = {
  type?: string;
  tagName?: string;
  value?: string;
  children?: HastNode[];
};

/** A table gets a filter box once it is long enough that finding a row is work. */
export const FILTER_FROM_ROWS = 8;

function cellsOf(row: HastNode): string[] {
  return (row.children ?? [])
    .filter((cell) => cell.tagName === "th" || cell.tagName === "td")
    .map((cell) => textOf(cell));
}

function textOf(node: HastNode): string {
  if (node.type === "text") return node.value ?? "";
  return (node.children ?? []).map(textOf).join("");
}

function rowsOf(node: HastNode): string[][] {
  const rows: HastNode[] = [];
  const walk = (child: HastNode) => {
    if (child.tagName === "tr") {
      rows.push(child);
      return;
    }
    (child.children ?? []).forEach(walk);
  };
  (node.children ?? []).forEach(walk);
  return rows.map(cellsOf).filter((cells) => cells.length > 0);
}

function csvCell(value: string): string {
  return /[",\n\r]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

export function tableToCsv(rows: string[][]): string {
  return rows.map((cells) => cells.map(csvCell).join(",")).join("\r\n");
}

/** The table as the model wrote it — pipes escaped, header rule included. */
export function tableToMarkdown(rows: string[][]): string {
  if (rows.length === 0) return "";
  const escape = (value: string) => value.replace(/\|/g, "\\|").replace(/\n/g, " ");
  const [head, ...body] = rows;
  const lines = [
    `| ${head.map(escape).join(" | ")} |`,
    `| ${head.map(() => "---").join(" | ")} |`,
    ...body.map((cells) => `| ${cells.map(escape).join(" | ")} |`),
  ];
  return lines.join("\n");
}

/** The table as Markdown for an editor, and as a document table for Excel, Word or a mail
 *  (richCopy.ts): pasted into a sheet it lands as cells. */
async function copyTable(rows: string[][]): Promise<void> {
  try {
    await writeRich(tableToMarkdown(rows), rowsToHtml(rows));
  } catch {
    // Clipboard denied (insecure origin, permission): the toolbar is not worth an error
    // dialog — the table is still on screen, selectable.
  }
}

function downloadCsv(rows: string[][]): void {
  const blob = new Blob([tableToCsv(rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "table.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

const elementsOf = (children: unknown): ReactElement<any>[] =>
  Children.toArray(children as any).filter(isValidElement) as ReactElement<any>[];

function sectionOf(children: unknown, tag: "thead" | "tbody"): ReactElement<any> | undefined {
  return elementsOf(children).find((child) => child.type === tag);
}

export function MarkdownTable({ node, children, ...props }: any) {
  const rows = useMemo(() => (node ? rowsOf(node) : []), [node]);
  const [view, setView] = useState<TableView>({ column: null, direction: "ascending", query: "" });
  const tbodyRef = useRef<HTMLTableSectionElement>(null);

  const head = rows[0] ?? [];
  const allBody = rows.slice(1);
  const pinned = allBody.length > 2 && isTotalRow(allBody[allBody.length - 1]) ? allBody.length - 1 : null;
  const body = pinned === null ? allBody : allBody.slice(0, pinned);

  const thead = sectionOf(children, "thead");
  const tbody = sectionOf(children, "tbody");
  const drawnRows = tbody ? elementsOf(tbody.props.children) : [];
  const headRow = thead ? elementsOf(thead.props.children)[0] : undefined;
  const headCells = headRow ? elementsOf(headRow.props.children) : [];
  // The controls need the rendered rows to be the hast rows, one for one.
  const interactive = body.length >= 2 && drawnRows.length === allBody.length && headCells.length === head.length;

  // A column of numbers lines its digits up at the right, as a sheet does (owner, 2026-10-13),
  // unless the writer aligned the column themselves.
  const numeric = useMemo(() => head.map((_, column) => numericColumn(body, column)), [head, body]);
  const aligned = (cell: ReactElement<any>) => !!cell.props.style?.textAlign || !!cell.props.align;
  const numberCells = (row: ReactElement<any>, extra: Record<string, unknown>) =>
    cloneElement(
      row,
      extra,
      elementsOf(row.props.children).map((cell, column) =>
        numeric[column] && !aligned(headCells[column] ?? cell)
          ? cloneElement(cell, { key: column, className: [cell.props.className, "md-num"].filter(Boolean).join(" ") })
          : cloneElement(cell, { key: column }),
      ),
    );

  const order = useMemo(() => viewOrder(body, view), [body, view]);
  const stats = view.column !== null ? columnStats(body, order, view.column) : null;
  const viewing = view.query.trim() !== "" || view.column !== null;

  // Rows slide from where they stood to where the reader's view puts them (FLIP); a table
  // growing while the answer streams in never animates.
  const measure = useRowFlip(tbodyRef, ":scope > tr[data-row]", order);

  // Ascending, then descending, then the order the model wrote.
  const sortBy = (column: number) => {
    measure();
    setView((current) => {
      if (current.column !== column) return { ...current, column, direction: "ascending" };
      if (current.direction === "ascending") return { ...current, direction: "descending" };
      return { ...current, column: null, direction: "ascending" };
    });
  };

  const shownRows = () => [head, ...order.map((index) => body[index]), ...(pinned === null ? [] : [allBody[pinned]])];

  let table = <table {...props}>{children}</table>;
  if (interactive && thead && tbody && headRow) {
    const sortableHead = cloneElement(
      thead,
      undefined,
      cloneElement(
        headRow,
        undefined,
        headCells.map((cell, column) => {
          const sorted = view.column === column ? view.direction : undefined;
          return cloneElement(
            cell,
            {
              key: column,
              tabIndex: 0,
              "aria-sort": sorted ?? "none",
              className: [cell.props.className, "md-th-sort", numeric[column] && !aligned(cell) ? "md-num" : ""]
                .filter(Boolean)
                .join(" "),
              title: "Sort by this column",
              onClick: () => sortBy(column),
              onKeyDown: (event: KeyboardEvent) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  sortBy(column);
                }
              },
            },
            cell.props.children,
            <span key="arrow" className="md-sort-arrow" aria-hidden>
              <Icon name="chevronUp" size={11} />
            </span>,
          );
        }),
      ),
    );
    table = (
      <table {...props}>
        {sortableHead}
        <tbody {...tbody.props} ref={tbodyRef}>
          {order.map((index) => numberCells(drawnRows[index], { key: index, "data-row": index }))}
          {pinned !== null && numberCells(drawnRows[pinned], { key: "total", className: "md-total-row" })}
        </tbody>
      </table>
    );
  }

  const onFilter = (query: string) => {
    measure();
    setView((current) => ({ ...current, query }));
  };

  return (
    <div className="md-tableblock">
      {rows.length > 0 && (
        <div className="md-tablebar">
          {interactive && body.length >= FILTER_FROM_ROWS && (
            <label className="md-tablefilter">
              <Icon name="search" size={12} />
              <input
                type="search"
                data-testid="table-filter"
                placeholder="Filter rows"
                aria-label="Filter the table's rows"
                value={view.query}
                onChange={(event) => onFilter(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape" && view.query) {
                    event.stopPropagation();
                    onFilter("");
                  }
                }}
              />
            </label>
          )}
          <span className="md-tablebar-spacer" />
          <button
            type="button"
            data-testid="table-copy"
            title="Copy the table"
            onClick={() => void copyTable(rows)}
          >
            <Icon name="copy" size={12} />
            <span>Copy</span>
          </button>
          <button
            type="button"
            data-testid="table-csv"
            title={viewing ? "Download the rows shown as CSV" : "Download as CSV"}
            onClick={() => downloadCsv(interactive ? shownRows() : rows)}
          >
            <Icon name="file" size={12} />
            <span>CSV</span>
          </button>
        </div>
      )}
      <ScrollFade axis="horizontal" className="md-tablewrap">
        {table}
      </ScrollFade>
      {interactive && viewing && (
        <div className="md-tablestatus" data-testid="table-status" aria-live="polite">
          <span>
            {order.length === body.length ? `${body.length} rows` : `${order.length} of ${body.length} rows`}
          </span>
          {stats && view.column !== null && (
            <span data-testid="table-stats">
              <b>{head[view.column]}</b> · Sum {formatStat(stats.sum)} · Avg {formatStat(stats.mean)} · Range{" "}
              {formatStat(stats.min)}–{formatStat(stats.max)}
            </span>
          )}
          {order.length === 0 && <span>No row matches “{view.query.trim()}”.</span>}
        </div>
      )}
    </div>
  );
}
