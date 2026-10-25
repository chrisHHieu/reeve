export type DiffRow = {
  kind: "ctx" | "add" | "del" | "hunk" | "meta" | "gap";
  old?: number;
  new?: number;
  text: string;
  /** A `hunk` row: how many unchanged lines the patch leaves out before it. */
  count?: number;
  /** A `hunk` row: the section git names after the second `@@` (`def build_client(config):`). */
  label?: string;
};

/**
 * A git patch as diff rows. Everything before the first `@@` is git's plumbing (`diff --git`,
 * `index …`, `--- a/…`, `+++ b/…`) and is not shown — the review audit's V8. After it, a line
 * is content by its first character alone: a removed SQL comment is `-` + `-- note`, and
 * reading `---` as a header anywhere turned it into plumbing. jsdiff's and git's
 * `\ No newline at end of file` is dropped; a binary file's one sentence is kept, since it
 * has no lines to show instead.
 */
export function parsePatch(lines: string[]): DiffRow[] {
  const out: DiffRow[] = [];
  let oldNo = 0;
  let newNo = 0;
  let inHunk = false;
  for (const ln of lines) {
    const m = ln.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if (m) {
      inHunk = true;
      oldNo = Number(m[1]);
      newNo = Number(m[2]);
      out.push({ kind: "hunk", text: ln });
    } else if (!inHunk) {
      if (ln.startsWith("Binary files ")) out.push({ kind: "meta", text: ln });
    } else if (ln.startsWith("\\")) {
      continue;
    } else if (ln.startsWith("+")) {
      out.push({ kind: "add", new: newNo++, text: ln.slice(1) });
    } else if (ln.startsWith("-")) {
      out.push({ kind: "del", old: oldNo++, text: ln.slice(1) });
    } else if (ln.startsWith(" ")) {
      out.push({ kind: "ctx", old: oldNo++, new: newNo++, text: ln.slice(1) });
    } else if (ln.startsWith("diff --git ")) {
      // The next file's header in a multi-file patch.
      inHunk = false;
    }
  }
  return out;
}

/**
 * A reader is told where a hunk sits instead of shown git's `@@ -40,2 +40,3 @@`: how many
 * unchanged lines the patch skips before it (above the first hunk, and between two), as
 * GitHub and Codex do, and the section git names after the header. The count comes from the
 * hunk headers' old-file line numbers; what follows the last hunk is not known from a patch.
 * The raw header stays as `text` (the hunk actions send it back to identify the hunk).
 */
export function withGaps(rows: DiffRow[]): DiffRow[] {
  let lastOld = 0;
  return rows.map((row) => {
    if (row.kind !== "hunk") {
      if (row.old !== undefined) lastOld = row.old;
      return row;
    }
    const m = row.text.match(/^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@ ?(.*)$/);
    if (!m) return row;
    // `-a,0` (a pure insertion) names the line BEFORE the change, not the first one.
    const start = m[2] !== undefined && Number(m[2]) === 0 ? Number(m[1]) + 1 : Number(m[1]);
    const skipped = start - (lastOld + 1);
    return { ...row, count: skipped > 0 ? skipped : 0, label: m[3].trim() || undefined };
  });
}

/** What a hunk's row says: the lines it skips, then its section. Empty when neither. */
export function hunkCaption(row: DiffRow, withCount = true): string {
  const parts = [];
  if (withCount && row.count) parts.push(`${row.count} unmodified line${row.count === 1 ? "" : "s"}`);
  if (row.label) parts.push(row.label);
  return parts.join("  \u00b7  ");
}

/** A new, untracked file: every line an addition, numbered from 1. The final newline ends the
 *  last line rather than starting an empty one. */
export function additionRows(content: string): DiffRow[] {
  const lines = content.split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines.map((text, i) => ({ kind: "add", new: i + 1, text }));
}
