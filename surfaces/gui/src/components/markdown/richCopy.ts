// Copy an answer the way the place it is pasted into wants it (owner, 2026-10-13: Markdown
// pasted into Outlook or Word arrives as `**` and `|---|`).
//
// The clipboard gets two forms at once. `text/plain` is the Markdown as written, which is what a
// code editor, a terminal or a chat box takes; `text/html` is the same text drawn as a document,
// which Word, Outlook, Google Docs, Slack and Excel take. The reader makes no choice: the
// application they paste into picks the form it understands.
//
// The HTML is built from the Markdown with the same parser the answer is drawn with, not read
// back from the page: the page holds the app's own chrome (copy buttons, file marks, line
// numbers) that has no business in an email. Raw HTML in the answer is dropped here as it is on
// screen; only `<br>` is a break (htmlBreaks.ts). Styles are inline because a mail client reads
// no stylesheet.

import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import remarkRehype from "remark-rehype";
import { toHtml } from "hast-util-to-html";
import { unified } from "unified";
import { parseDirectives } from "./directives";
import { remarkHtmlBreaks } from "./htmlBreaks";

type HastNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

const MONO = "Consolas, 'Courier New', monospace";
const CELL = "border:1px solid #c8c6c4;padding:4px 10px;vertical-align:top";

const STYLE: Record<string, string> = {
  table: "border-collapse:collapse;margin:6px 0",
  th: `${CELL};background:#f3f2f1;font-weight:600;text-align:left`,
  td: CELL,
  pre: `font-family:${MONO};font-size:12px;background:#f6f6f6;padding:8px 10px;white-space:pre-wrap`,
  blockquote: "margin:6px 0;padding-left:10px;border-left:3px solid #c8c6c4;color:#555555",
};

const processor = unified()
  .use(remarkParse)
  .use(remarkGfm, { singleTilde: false })
  .use(remarkHtmlBreaks)
  .use(remarkRehype);

function style(node: HastNode, inPre: boolean) {
  if (node.type !== "element" || !node.tagName) {
    for (const child of node.children ?? []) style(child, inPre);
    return;
  }
  const props = (node.properties ??= {});
  const own = STYLE[node.tagName] ?? (node.tagName === "code" && !inPre ? `font-family:${MONO}` : "");
  // A column the writer aligned keeps its alignment (GFM puts it on the cell as `align`).
  const align = typeof props.align === "string" ? `;text-align:${props.align}` : "";
  if (own || align) props.style = own + align;
  // A link to one of this app's own files means nothing outside it: it is pasted as its text.
  if (node.tagName === "a" && typeof props.href === "string" && !/^(https?|mailto):/i.test(props.href)) {
    node.tagName = "span";
    delete props.href;
  }
  for (const child of node.children ?? []) style(child, inPre || node.tagName === "pre");
}

/** The Markdown drawn as a document fragment, for the `text/html` side of a copy. */
export function markdownToHtml(markdown: string): string {
  const { text } = parseDirectives(markdown);
  const tree = processor.runSync(processor.parse(text)) as unknown as HastNode;
  style(tree, false);
  return toHtml(tree as never);
}

const escapeHtml = (value: string) =>
  value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** Rows (the first is the header) as a document table, styled as `markdownToHtml` styles one. */
export function rowsToHtml(rows: string[][]): string {
  if (rows.length === 0) return "";
  const [head, ...body] = rows;
  const cells = (row: string[], tag: "th" | "td") =>
    `<tr>${row.map((cell) => `<${tag} style="${STYLE[tag]}">${escapeHtml(cell)}</${tag}>`).join("")}</tr>`;
  return `<table style="${STYLE.table}"><thead>${cells(head, "th")}</thead><tbody>${body.map((row) => cells(row, "td")).join("")}</tbody></table>`;
}

/** An answer's Copy: its Markdown, and the same drawn as a document. */
export async function copyAnswer(markdown: string): Promise<void> {
  let html = "";
  try {
    html = markdownToHtml(markdown);
  } catch {
    // A parse that throws costs the formatting, never the copy.
  }
  if (html) return writeRich(markdown, html);
  if (!navigator.clipboard?.writeText) throw new Error("no clipboard");
  await navigator.clipboard.writeText(markdown);
}

/**
 * Put both forms on the clipboard; the plain text alone where a rich write is refused (an older
 * webview, a denied permission). Resolves only once something was written, so a caller that says
 * "Copied" says it truthfully.
 */
export async function writeRich(plain: string, html: string): Promise<void> {
  if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
    try {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/plain": new Blob([plain], { type: "text/plain" }),
          "text/html": new Blob([html], { type: "text/html" }),
        }),
      ]);
      return;
    } catch {
      // Fall through to the plain write: a copy that loses its formatting beats no copy.
    }
  }
  if (!navigator.clipboard?.writeText) throw new Error("no clipboard");
  await navigator.clipboard.writeText(plain);
}
