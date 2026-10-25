// A window of a file, with the file's OWN line numbers and syntax highlighting.
//
// Two
// details are the difference between this and a plain code block:
//
//   The gutter keeps the FILE's line numbers, not the window's. A card showing lines
//   200-215 must say 200 next to the first row, or the reader cannot go and look at it —
//   and "go and look at it" is the only reason to show a window of a file at all.
//
//   The whole window is tokenized in ONE call, so grammar context survives across lines.
//
// The card shows the WHOLE window the agent read (offset → limit); a tall window scrolls
// inside the body (`.rv-scroll`) rather than collapsing behind a "… more lines" control —
// one expansion (the row) is the only disclosure a reader has to make.

import { useMemo } from "react";
import type { ReadLine } from "./types";
import { CopyButton } from "../components/primitives/CopyButton";
import { highlightLines, useGrammars } from "../components/primitives/highlight";
import { ScrollFade } from "../components/primitives/ScrollFade";

/** Kept for callers that still import it; the card no longer caps its rows. */
export const READ_MAX_LINES = 8;

/**
 * The first shown line's number, read from the lines themselves rather than from the card's
 * `offset`. The two agree on everything this engine emits; reading the rows means a
 * malformed offset cannot shift the whole gutter.
 */
function firstNumber(lines: readonly ReadLine[]): number {
  return lines.length > 0 ? lines[0].number : 0;
}

export function ReadBlock({
  lines,
  totalLines,
  lang,
}: {
  lines: readonly ReadLine[];
  totalLines: number;
  /** The file the window came from. Not drawn: the row header names it, and the card shows
   *  only the window's own fact — which slice of the file this is. */
  path?: string;
  lang?: string;
}) {
  const grammars = useGrammars(lang);
  const raw = useMemo(() => lines.map((line) => line.text).join("\n"), [lines]);
  const spans = useMemo(() => highlightLines(raw, lang), [raw, lang, grammars]);
  // The window is "partial" when it does not cover the file. That is worth saying out loud:
  // a reader who does not notice a window will believe they have seen the whole file.
  const windowed = lines.length > 0 && (firstNumber(lines) > 1 || lines.length < totalLines);

  const row = (line: ReadLine, index: number, highlight: typeof spans) => (
    <div key={`${line.number}-${index}`} className="rv-row">
      <span className="rv-gutter" aria-hidden>
        {line.number}
      </span>
      <span className="rv-row-text">
        {highlight?.[index]?.length
          ? highlight[index].map((span, spanIndex) => (
              <span key={spanIndex} style={span.style}>
                {span.text}
              </span>
            ))
          : line.text || "\u00a0"}
      </span>
    </div>
  );

  return (
    <div className="rv-card" data-read="">
      <div className="rv-banner">
        {windowed && (
          <span className="rv-banner-label" data-testid="read-window">
            {lines.length} / {totalLines} lines
          </span>
        )}
        <span className="rv-banner-spacer" />
        {lines.length > 0 && <CopyButton text={raw} />}
      </div>
      {lines.length === 0 ? (
        <div className="rv-empty">Empty file</div>
      ) : (
        <ScrollFade className="rv-body rv-nowrap rv-scroll" data-testid="read-body">
          {lines.map((line, index) => row(line, index, spans))}
        </ScrollFade>
      )}
    </div>
  );
}
