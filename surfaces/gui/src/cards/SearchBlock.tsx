// Search results: grep hits grouped by file, or a flat list of paths.
//
// The behaviours that matter:
//
//   File groups fold. A search over a monorepo can return 40 hits in one file and 1 in each
//   of twenty others; folding the big group is the difference between a card and a wall.
//
//   The counts say "showing N of M" when the result was capped, because the failure this
//   guards against is someone concluding "there were 100 matches" from a list that was cut.
//
//   The pattern is highlighted inside each hit — the eye finds the match instead of scanning
//   the line — and a tall result scrolls inside a bounded body rather than collapsing behind
//   a "… more lines" control.

import { useMemo, useState, type ReactNode } from "react";
import type { SearchFileGroup, SearchResultCard } from "./types";
import { CopyButton } from "../components/primitives/CopyButton";
import { ScrollFade } from "../components/primitives/ScrollFade";
import { FileIcon } from "../components/FileIcon";

/** Kept for callers that still import it; the card no longer caps its rows. */
export const SEARCH_MAX_LINES = 8;

type SearchRow =
  | { type: "file"; path: string; count: number; fileIndex: number }
  | { type: "match"; lineNumber: number; line: string; fileIndex: number }
  | { type: "path"; path: string };

function toRows(card: SearchResultCard, collapsed: ReadonlySet<number>): SearchRow[] {
  if (card.shape === "paths") {
    return card.paths.map((path) => ({ type: "path" as const, path }));
  }
  const rows: SearchRow[] = [];
  card.files.forEach((group: SearchFileGroup, fileIndex: number) => {
    rows.push({
      type: "file",
      path: group.path,
      count: group.matches.length,
      fileIndex,
    });
    if (collapsed.has(fileIndex)) return;
    for (const match of group.matches) {
      rows.push({
        type: "match",
        lineNumber: match.line_number,
        line: match.line,
        fileIndex,
      });
    }
  });
  return rows;
}

/** Everything the result holds, regardless of which groups are folded. */
function copyText(card: SearchResultCard): string {
  if (card.shape === "paths") return card.paths.join("\n");
  return card.files
    .map((group) =>
      [`${group.path} (${group.matches.length})`]
        .concat(group.matches.map((match) => `  ${match.line_number}: ${match.line}`))
        .join("\n"),
    )
    .join("\n\n");
}

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Wrap every occurrence of `pattern` in a `<mark>` so the match reads at a glance. */
function highlight(text: string, pattern?: string): ReactNode {
  if (!pattern) return text || "\u00a0";
  let re: RegExp;
  try {
    re = new RegExp(`(${escapeRegExp(pattern)})`, "gi");
  } catch {
    return text || "\u00a0";
  }
  const parts = text.split(re);
  return parts.map((part, i) =>
    part.toLowerCase() === pattern.toLowerCase() ? <mark key={i}>{part}</mark> : part,
  );
}

export function SearchBlock({
  card,
  recovery,
  pattern,
}: {
  card: SearchResultCard;
  /** Raw result text, shown only when the search was capped. */
  recovery?: string;
  /** The query, highlighted inside each hit. */
  pattern?: string;
}) {
  const [collapsed, setCollapsed] = useState<ReadonlySet<number>>(() => new Set());
  const rows = useMemo(() => toRows(card, collapsed), [card, collapsed]);
  const shown =
    card.shape === "paths" ? card.paths.length : card.files.reduce((n, g) => n + g.matches.length, 0);
  const counts = card.truncated
    ? `showing ${shown} of ${card.total}`
    : `${shown} ${card.shape === "paths" ? (shown === 1 ? "path" : "paths") : shown === 1 ? "match" : "matches"}`;
  const summary =
    card.shape === "paths"
      ? `${counts}`
      : `${counts} · ${card.files.length} file${card.files.length === 1 ? "" : "s"}`;

  const row = (entry: SearchRow, index: number) => {
    if (entry.type === "file") {
      const isCollapsed = collapsed.has(entry.fileIndex);
      return (
        <button
          key={`f-${entry.fileIndex}-${index}`}
          type="button"
          className="rv-file-head"
          aria-expanded={!isCollapsed}
          onClick={() =>
            setCollapsed((current) => {
              const next = new Set(current);
              if (!next.delete(entry.fileIndex)) next.add(entry.fileIndex);
              return next;
            })
          }
        >
          <FileIcon path={entry.path} />
          <span>{entry.path}</span>
          <span className="rv-file-count">
            {entry.count} {entry.count === 1 ? "match" : "matches"}
          </span>
        </button>
      );
    }
    if (entry.type === "path") {
      return (
        // One flex row: the icon and the path used to break onto two lines (GUI tools
        // review, B5) because the row was a plain block around a block icon.
        <div key={`p-${index}`} className="rv-path-row" data-testid="search-path">
          <FileIcon path={entry.path} />
          <span className="rv-path-text">{entry.path}</span>
        </div>
      );
    }
    return (
      <div key={`m-${entry.fileIndex}-${entry.lineNumber}-${index}`} className="rv-hit">
        <span className="rv-hit-line">{entry.lineNumber}</span>
        <span className="rv-hit-text">{highlight(entry.line, pattern)}</span>
      </div>
    );
  };

  if (rows.length === 0) return <div className="rv-empty">No results</div>;

  return (
    <div className="rv-card" data-search="">
      <div className="rv-banner">
        <span className="rv-banner-label">{summary}</span>
        <span className="rv-banner-spacer" />
        <CopyButton text={copyText(card)} />
      </div>
      <ScrollFade className="rv-body rv-scroll" data-testid="search-body">
        {rows.map(row)}
      </ScrollFade>
      {card.truncated && recovery && (
        // The locator for a spilled full result lives in the raw text, which is the only
        // place it can be read from — the structured card has no room for a path.
        <div className="rv-foot" data-testid="search-recovery">
          {recovery}
        </div>
      )}
    </div>
  );
}
