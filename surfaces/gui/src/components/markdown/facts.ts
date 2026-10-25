// Lines written as `**Label:** value` are a list of facts, drawn with the labels in one column
// and the values lined up beside them (owner, 2026-10-13).
//
// It is the most common shape in the owner's store: 17% of 4,751 agent messages hold such a
// line, 507 hold two or more in a row (a status report: cause, effect, fix, check, what is
// left). Drawn as bold-then-text, each value started at a different place and the eye hunted for
// them. Two or more in a row become a `<dl>`; one alone in a paragraph stays a sentence.
//
// Three spellings reach here, and all three are read: the lines in one paragraph (a soft or a
// hard break between them), the lines as separate paragraphs (a blank line between them, which
// `joinFactRuns` closes before the answer is cut into blocks, since each block is drawn alone),
// and a bulleted list whose every item is one such line. A numbered list keeps its numbers.

interface MdNode {
  type: string;
  value?: string;
  ordered?: boolean | null;
  checked?: boolean | null;
  children?: MdNode[];
  data?: Record<string, unknown>;
}

const LABEL_MAX = 40;

// ---- the text, before it is cut into blocks ---------------------------------------------

const LABEL_LINE = /^ {0,3}\*\*([^*\n]{1,40})\*\*([ \t]*:)?[ \t]+\S/;
// A line that starts something other than a paragraph: a list, a heading, a quote, a table, a fence.
const NOT_PROSE = /^\s*([-*+]\s|\d+[.)]\s|#{1,6}\s|>|\||```|~~~)/;
const FENCE = /^\s{0,3}(```|~~~)/;

function isLabelLine(line: string): boolean {
  const found = LABEL_LINE.exec(line);
  return !!found && (found[1].trim().endsWith(":") || !!found[2]);
}

/**
 * Close the blank lines between paragraphs that each open with a label, so a run written with
 * a blank line between its lines is one paragraph (one block) and can be drawn as one list.
 * Everything else, fenced code above all, is left exactly as written.
 */
export function joinFactRuns(text: string): string {
  if (!text.includes("**")) return text;
  const lines = text.split("\n");
  // Paragraphs: runs of non-blank prose lines outside fences, with whether each opens with a label.
  const paragraphs: { start: number; end: number; label: boolean }[] = [];
  let fence: string | null = null;
  let open: { start: number; end: number; label: boolean; prose: boolean } | null = null;
  const close = () => {
    if (open && open.prose) paragraphs.push({ start: open.start, end: open.end, label: open.label });
    open = null;
  };
  lines.forEach((line, index) => {
    const marker = FENCE.exec(line)?.[1];
    if (fence) {
      if (marker === fence) fence = null;
      return;
    }
    if (marker) {
      close();
      fence = marker;
      return;
    }
    if (line.trim() === "") return close();
    if (!open) open = { start: index, end: index, label: isLabelLine(line), prose: true };
    open.end = index;
    if (NOT_PROSE.test(line)) open.prose = false;
  });
  close();
  const drop = new Set<number>();
  for (let i = 1; i < paragraphs.length; i++) {
    const before = paragraphs[i - 1];
    const after = paragraphs[i];
    if (!before.label || !after.label) continue;
    for (let j = before.end + 1; j < after.start; j++) drop.add(j);
  }
  return drop.size ? lines.filter((_, index) => !drop.has(index)).join("\n") : text;
}

// ---- the tree ---------------------------------------------------------------------------

const textOf = (node: MdNode): string =>
  typeof node.value === "string" ? node.value : (node.children ?? []).map(textOf).join("");

interface Row {
  term: MdNode[];
  value: MdNode[];
}

/** The paragraph's children cut at its line ends (a soft break inside a text, or a hard break). */
function linesOf(paragraph: MdNode): MdNode[][] {
  const lines: MdNode[][] = [[]];
  for (const child of paragraph.children ?? []) {
    if (child.type === "break") {
      lines.push([]);
      continue;
    }
    if (child.type !== "text" || !child.value?.includes("\n")) {
      lines[lines.length - 1].push(child);
      continue;
    }
    child.value.split("\n").forEach((part, index) => {
      if (index > 0) lines.push([]);
      if (part) lines[lines.length - 1].push({ type: "text", value: part });
    });
  }
  return lines.map((line) => {
    const first = line[0];
    return first?.type === "text" ? [{ ...first, value: first.value!.replace(/^\s+/, "") }, ...line.slice(1)] : line;
  });
}

function labelOf(line: MdNode[]): Row | null {
  const [strong, next] = line;
  if (strong?.type !== "strong") return null;
  const label = textOf(strong).trim();
  const colonAfter = next?.type === "text" && /^\s*:/.test(next.value ?? "");
  const bare = label.replace(/:$/, "").trim();
  if (!bare || label.length > LABEL_MAX || !(label.endsWith(":") || colonAfter)) return null;
  const term = (strong.children ?? []).map((child) => ({ ...child }));
  const last = term[term.length - 1];
  if (last?.type === "text") last.value = last.value!.replace(/\s*:\s*$/, "");
  const value = line.slice(1).map((child) => ({ ...child }));
  if (value[0]?.type === "text") value[0].value = value[0].value!.replace(/^\s*:?\s*/, "");
  return { term, value };
}

/** The rows of a paragraph that opens with a label, or null when it does not. */
function rowsOf(paragraph: MdNode): Row[] | null {
  const lines = linesOf(paragraph);
  const rows: Row[] = [];
  for (const line of lines) {
    const row = labelOf(line);
    if (row) rows.push(row);
    else if (rows.length === 0) return null;
    else if (line.length) rows[rows.length - 1].value.push({ type: "text", value: "\n" }, ...line);
  }
  return rows.every((row) => row.value.some((node) => textOf(node).trim() !== "" || node.type !== "text")) ? rows : null;
}

function factList(rows: Row[]): MdNode {
  return {
    type: "facts",
    data: { hName: "dl", hProperties: { className: ["md-facts"] } },
    children: rows.flatMap((row) => [
      { type: "factTerm", data: { hName: "dt" }, children: row.term },
      { type: "factValue", data: { hName: "dd" }, children: row.value },
    ]),
  };
}

function asFacts(node: MdNode): MdNode | null {
  if (node.type === "paragraph") {
    const rows = rowsOf(node);
    return rows && rows.length >= 2 ? factList(rows) : null;
  }
  if (node.type === "list" && !node.ordered) {
    const items = node.children ?? [];
    if (items.length < 2) return null;
    const rows: Row[] = [];
    for (const item of items) {
      if (item.checked != null || item.children?.length !== 1 || item.children[0].type !== "paragraph") return null;
      const own = rowsOf(item.children[0]);
      if (!own || own.length !== 1) return null;
      rows.push(own[0]);
    }
    return factList(rows);
  }
  return null;
}

function visit(node: MdNode) {
  if (!node.children) return;
  node.children = node.children.map((child) => asFacts(child) ?? child);
  for (const child of node.children) if (child.type !== "facts") visit(child);
}

/** The remark plugin. */
export function remarkFacts() {
  return (tree: MdNode) => visit(tree);
}
