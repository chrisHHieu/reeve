// One file mutation, as a UNIFIED diff: real old/new line numbers in one gutter, context
// lines shared, and a left accent bar (red for a removed line, green for an added one).
//
// The old/new sides the engine sends are context-windowed blocks; `diff`'s `structuredPatch`
// turns them into a proper unified diff, which is what gives the row its line numbers. The
// `-`/`+` signs are drawn by CSS `::before`, so colour is never the only channel telling an
// added line from a removed one.
//
// The code is SYNTAX-HIGHLIGHTED with the file's own grammar (the same shared highlighter the
// read/code cards use), one `highlightLines` call per file so grammar context survives across
// lines. The row's fill and stripe stay the diff channel; the token colours are the code
// channel — the split, and the reason a one-line edit is readable at a glance.
//
// The footer's counts come from the hunk when the engine supplied them, and from the diff
// otherwise — for a context-windowed hunk those differ, and the engine's number is the one
// that also appears on the turn's receipt. A tall diff scrolls inside a bounded body.

import { useMemo } from "react";
import { structuredPatch } from "diff";
import type { FileDiff } from "./types";
import { CopyButton } from "../components/primitives/CopyButton";
import { highlightLines, languageForPath, useGrammars } from "../components/primitives/highlight";
import { fileDisplay } from "../toolMeta";
import { ScrollFade } from "../components/primitives/ScrollFade";
import { IconButton } from "../ui";

/** Kept for callers that still import it; the card no longer caps its rows. */
export const DIFF_MAX_LINES = 8;

/** Past this many lines a card renders plain: the tokenizer is the main thread's work, and a
 *  huge patch is read by scrolling, not by colour. The read card's window is bounded by the
 *  agent; a diff's size is whatever the change was. */
const MAX_HIGHLIGHT_LINES = 800;

type DiffRow = {
  kind: "del" | "add" | "ctx";
  text: string;
  no?: number;
  /** Index into the file's plain-text line list (the input to the highlighter). */
  line: number;
  /** A "⋯" marker between two hunks of the same file — not part of the text. */
  gap?: boolean;
};

type DiffGroup = {
  path: string;
  rows: DiffRow[];
  /** The group's lines with the diff markers stripped, joined — one tokenizer input. */
  text: string;
  added: number;
  removed: number;
};

/** A single trailing newline is a terminator, never an extra blank line. */
function stripTrailingNewline(text: string): string {
  return text.endsWith("\n") ? text.slice(0, -1) : text;
}

function buildGroups(diffs: FileDiff[]): {
  groups: DiffGroup[];
  added: number;
  removed: number;
  files: number;
} {
  const groups: DiffGroup[] = [];
  const paths = new Set<string>();
  let added = 0;
  let removed = 0;
  let previous: string | undefined;
  for (const diff of diffs) {
    const rows: DiffRow[] = [];
    if (diff.path === previous) rows.push({ kind: "ctx", text: "⋯", line: -1, gap: true });
    previous = diff.path;
    paths.add(diff.path);
    const oldText = diff.old_text === null ? "" : stripTrailingNewline(diff.old_text);
    const newText = stripTrailingNewline(diff.new_text);
    const patch = structuredPatch(diff.path, diff.path, oldText, newText, "", "", { context: 3 });
    let fileAdded = 0;
    let fileRemoved = 0;
    let lineIndex = 0;
    // The hunk is a WINDOW of the file: jsdiff numbers inside the window, and the engine
    // says where the window starts. A diff with no start (built from call arguments) has
    // no honest line numbers, so it shows none (GUI tools review, B2).
    const numbered = diff.new_start !== undefined;
    const oldBase = (diff.old_start ?? diff.new_start ?? 1) - 1;
    const newBase = (diff.new_start ?? 1) - 1;
    for (const hunk of patch.hunks) {
      let oldNo = hunk.oldStart + oldBase;
      let newNo = hunk.newStart + newBase;
      for (const line of hunk.lines) {
        const prefix = line.charAt(0);
        const text = line.slice(1);
        // jsdiff's `\ No newline at end of file` marker is not a line of the file; counted as
        // context it showed as a numbered code line, even between a − and its + (GUI tools
        // review, B2). reviewChanges.ts already skips it.
        if (prefix === "\\") continue;
        if (prefix === "-") {
          const no = oldNo++;
          rows.push({ kind: "del", text, no: numbered ? no : undefined, line: lineIndex++ });
          fileRemoved++;
        } else if (prefix === "+") {
          const no = newNo++;
          rows.push({ kind: "add", text, no: numbered ? no : undefined, line: lineIndex++ });
          fileAdded++;
        } else {
          const no = newNo++;
          rows.push({ kind: "ctx", text, no: numbered ? no : undefined, line: lineIndex++ });
          oldNo++;
        }
      }
    }
    const plain = rows.filter((row) => !row.gap);
    groups.push({
      path: diff.path,
      rows,
      text: plain.map((row) => row.text).join("\n"),
      added: diff.added ?? fileAdded,
      removed: diff.removed ?? fileRemoved,
    });
    added += diff.added ?? fileAdded;
    removed += diff.removed ?? fileRemoved;
  }
  return { groups, added, removed, files: paths.size };
}

/** The text a copy hands over: exactly what the card shows, signs included. */
function copyText(groups: DiffGroup[]): string {
  return groups
    .flatMap((group) => group.rows)
    .map((row) =>
      row.gap
        ? "⋯"
        : row.kind === "del"
          ? `- ${row.text}`
          : row.kind === "add"
            ? `+ ${row.text}`
            : row.text,
    )
    .join("\n");
}

export function DiffBlock({
  diffs,
  path,
  onOpenFile,
}: {
  diffs: FileDiff[];
  path?: string;
  /** Opens the file in the OS. Only offered for a single-file card: which of five files
   *  "Open" would mean is a question the card cannot answer. */
  onOpenFile?: (path: string) => void;
}) {
  const { groups } = useMemo(() => buildGroups(diffs), [diffs]);
  // One subscription covers every grammar this card needs: the counter is global, so the
  // re-render after a grammar load arrives whichever file asked for it.
  const grammars = useGrammars(groups[0] === undefined ? undefined : languageForPath(groups[0].path));
  const highlighted = useMemo(
    () =>
      groups.map((group) =>
        group.rows.filter((row) => !row.gap).length > MAX_HIGHLIGHT_LINES
          ? undefined
          : highlightLines(group.text, languageForPath(group.path)),
      ),
    [groups, grammars],
  );
  if (groups.length === 0) return null;
  const unique = new Set(diffs.map((d) => d.path));
  const single = unique.size === 1 ? diffs[0] : undefined;
  // Several hunks of ONE file need no header (the `⋯` gap says so); several FILES do, since
  // the banner that used to name them is gone.
  const multiFile = unique.size > 1;
  void path;
  return (
    <div className="rv-card" data-diff="">
      {/* No banner and no footer: the diff card is the diff. The file NAME, its folder and
          the churn already ride the row header above it (`Edit · file.py  harness/tools  +18`),
          and a second copy inside the card was the chrome the rows did not need. What stays is
          what the row cannot do: copy the patch, open the file (single-file cards only — five
          files cannot answer "which one"), and, for a genuinely multi-file patch, the per-file
          headers below. */}
      <div className="rv-tools rv-tools-float" data-testid="diff-tools">
        <span className="rv-tools-spacer" />
        {single !== undefined && onOpenFile !== undefined && (
          <IconButton
            label="Open the file"
            icon="externalLink"
            size="sm"
            onClick={() => onOpenFile(single.path)}
            data-testid="diff-open-file"
          />
        )}
        <CopyButton text={copyText(groups)} />
      </div>
      <ScrollFade className="rv-body rv-nowrap rv-scroll" data-testid="diff-body">
        <div className="rv-diff-rows">
          {groups.map((group, groupIndex) => (
            <div key={groupIndex} data-diff-file={group.path}>
              {multiFile && (
                <div className="rv-diff-filehead" title={group.path}>
                  {fileDisplay(group.path).name}
                  <span>{fileDisplay(group.path).dir}</span>
                </div>
              )}
              {group.rows.map((row, index) => {
                if (row.gap) {
                  return (
                    <div key={index} className="rv-diff-line rv-diff-ctx rv-diff-gap">
                      <span className="rv-diff-no" aria-hidden />
                      <span className="rv-diff-text">{row.text}</span>
                    </div>
                  );
                }
                const spans = highlighted[groupIndex]?.[row.line];
                return (
                  <div key={index} className={`rv-diff-line rv-diff-${row.kind}`}>
                    <span className="rv-diff-no" aria-hidden>
                      {row.no ?? ""}
                    </span>
                    <span className="rv-diff-text">
                      {spans?.length
                        ? spans.map((span, spanIndex) => (
                            <span key={spanIndex} style={span.style}>
                              {span.text}
                            </span>
                          ))
                        : row.text || "\u00a0"}
                    </span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </ScrollFade>
    </div>
  );
}
