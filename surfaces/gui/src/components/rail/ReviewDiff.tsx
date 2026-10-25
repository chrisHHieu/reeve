// A recorded/working-tree change, drawn with the transcript's diff rows.
//
// The reference design renders the review pane's diffs with the same rich viewer as everywhere else
// (pierre + shiki); this is the same idea on our own rows — one grammar call per file, the
// sticky line-number gutter, the add/del fills and stripes, and the `+`/`-` signs from CSS.
// The rows come from `reviewChanges.diffRowsForChange` (recorded changes) or
// `rowsFromPatch` (a working-tree patch), so both scopes render identically.

import { useMemo } from "react";
import { highlightLines, languageForPath, useGrammars } from "../primitives/highlight";

export interface ReviewRow {
  kind: "ctx" | "add" | "del";
  /** The line's own number on the side it came from. */
  no?: number;
  text: string;
}

export function ReviewDiff({ path, rows }: { path: string; rows: ReviewRow[] }) {
  const lang = useMemo(() => languageForPath(path), [path]);
  const grammars = useGrammars(lang);
  const spans = useMemo(
    () => highlightLines(rows.map((row) => row.text).join("\n"), lang),
    // The grammar's load is what turns plain rows into token spans.
    [rows, lang, grammars],
  );
  return (
    <div className="review-diff" data-testid={`review-diff-${path}`}>
      <div className="rv-diff-rows">
        {rows.map((row, index) => (
          <div key={index} className={`rv-diff-line rv-diff-${row.kind}`}>
            <span className="rv-diff-no" aria-hidden>
              {row.no ?? ""}
            </span>
            <span className="rv-diff-text">
              {spans?.[index]?.length
                ? spans[index].map((span, spanIndex) => (
                    <span key={spanIndex} style={span.style}>
                      {span.text}
                    </span>
                  ))
                : row.text || "\u00a0"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
