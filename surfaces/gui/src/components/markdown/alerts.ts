// GitHub's alerts in an answer: a quote whose first line is `[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`,
// `[!WARNING]` or `[!CAUTION]` is a callout of that kind (owner, 2026-10-13). They rendered as
// a plain quote with the marker printed in it. The marker is taken out and the kind is put on
// the quote as `data-alert`, which the `blockquote` renderer in Markdown.tsx draws.
//
// Only the marker on the quote's own first line counts, as GitHub reads it: `[!NOTE]` later in
// a quote, or in a paragraph, is text.

export const ALERT_KINDS = ["note", "tip", "important", "warning", "caution"] as const;
export type AlertKind = (typeof ALERT_KINDS)[number];

const MARKER = /^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][ \t]*(?:\r?\n|$)/i;

interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  data?: { hProperties?: Record<string, unknown> };
}

function visit(node: MdNode) {
  for (const child of node.children ?? []) {
    if (child.type === "blockquote") mark(child);
    visit(child);
  }
}

function mark(quote: MdNode) {
  const first = quote.children?.[0];
  if (first?.type !== "paragraph") return;
  const text = first.children?.[0];
  if (text?.type !== "text" || typeof text.value !== "string") return;
  const found = MARKER.exec(text.value);
  if (!found) return;
  text.value = text.value.slice(found[0].length);
  // The marker was the whole first line: a paragraph left with nothing in it goes too.
  if (text.value === "") first.children!.shift();
  // A marker ended with a hard break (two spaces) leaves that break first.
  if (first.children![0]?.type === "break") first.children!.shift();
  if (first.children!.length === 0) quote.children!.shift();
  quote.data = { ...quote.data, hProperties: { ...quote.data?.hProperties, dataAlert: found[1].toLowerCase() } };
}

/** The remark plugin: marks each alert quote in the tree. */
export function remarkAlerts() {
  return (tree: MdNode) => visit(tree);
}
