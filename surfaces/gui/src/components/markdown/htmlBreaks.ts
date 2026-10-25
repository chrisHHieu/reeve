// `<br>` in an answer is a line break (owner, 2026-10-13: a table's cells read "Warm<br>Cool").
//
// GFM has no line break inside a table cell, so models write `<br>` there, and also inside a
// sentence. The renderer does not draw raw HTML (no rehype-raw: an answer is model output and
// must not inject markup), so the tag reached the page as text. Counted over the owner's store
// (8,057 answers, each clipped at 4,000 characters by the log): `<br>` 35 times in 3 answers,
// every other tag at most once. This turns exactly that one tag into a markdown break; anything
// else written as HTML stays text, and `<br>` inside code stays code (it is not an `html` node).

interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
}

const BREAK = /^<br\s*\/?>$/i;

function visit(node: MdNode) {
  if (!node.children) return;
  node.children = node.children.map((child) =>
    child.type === "html" && typeof child.value === "string" && BREAK.test(child.value.trim()) ? { type: "break" } : child,
  );
  for (const child of node.children) visit(child);
}

/** The remark plugin. */
export function remarkHtmlBreaks() {
  return (tree: MdNode) => visit(tree);
}
