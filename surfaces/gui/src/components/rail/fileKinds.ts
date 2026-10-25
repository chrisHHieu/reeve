// What an open file offers (workspace panel redesign, 2026-10-11).
//
// The owner's rule: a second view exists only where a file has a rendered form that differs from
// its text, the way an editor offers "Preview" for Markdown and nothing for a Python file.
// Markdown, HTML and SVG are drawn, a CSV is a table, a JSON document a tree, a notebook its
// cells and saved outputs. Everything else
// that is text is read as itself, and edited in place from one Edit button.

/** How a file is read when it is not being edited. */
export type FileView = "markdown" | "html" | "svg" | "table" | "tree" | "notebook" | "code";

/** Which of the two a file with a rendered form is showing. */
export type FileMode = "rendered" | "edit";

const MARKDOWN_RE = /\.(?:md|markdown|mdx)$/i;
const HTML_RE = /\.(?:html|htm)$/i;
const SVG_RE = /\.svg$/i;
const TABLE_RE = /\.(?:csv|tsv)$/i;
const TREE_RE = /\.(?:json|jsonc|json5)$/i;
const NOTEBOOK_RE = /\.ipynb$/i;

// The viewer's kind for a path the server has not classified yet, by its extension: near enough to
// the server's own table for the viewer to choose how to draw it.
const KIND_OF_EXTENSION: Record<string, string> = {
  ...Object.fromEntries(["png", "jpg", "jpeg", "gif", "svg", "webp"].map((ext) => [ext, "image"])),
  ...Object.fromEntries(["py", "js", "ts", "tsx", "jsx", "json", "sh", "css"].map((ext) => [ext, "code"])),
  html: "html",
  htm: "html",
  md: "markdown",
  csv: "csv",
  pdf: "pdf",
};

export function kindOfPath(path: string): string {
  return KIND_OF_EXTENSION[(path.split(".").pop() || "").toLowerCase()] ?? "text";
}

/** The reading view of a text file, by its name. */
export function fileView(path: string): FileView {
  if (MARKDOWN_RE.test(path)) return "markdown";
  if (HTML_RE.test(path)) return "html";
  if (SVG_RE.test(path)) return "svg";
  if (TABLE_RE.test(path)) return "table";
  if (TREE_RE.test(path)) return "tree";
  if (NOTEBOOK_RE.test(path)) return "notebook";
  return "code";
}

/** Whether the reading view is a drawing of the text rather than the text: only these get the
 *  two-way switch. */
export function hasRenderedForm(view: FileView): boolean {
  return view !== "code";
}

/** What the reading side of the switch is called. */
export function renderedLabel(view: FileView): string {
  if (view === "table") return "Table";
  if (view === "tree") return "Tree";
  return "Preview";
}

/** The kinds the server sends as text, which is what can be edited here. */
const TEXT_KINDS = new Set(["markdown", "html", "csv", "code", "text"]);

export function isTextKind(kind: string | undefined): boolean {
  return TEXT_KINDS.has(kind ?? "");
}

// The last side picked is remembered per view, so someone who edits Markdown opens the next
// Markdown file in the editor. v2: v1 also remembered a "preview" for code files, which no
// longer exists.
const MODE_KEY = "reeve:fm-mode:v2";

export function readFileMode(view: FileView): FileMode {
  try {
    const map = JSON.parse(localStorage.getItem(MODE_KEY) || "{}") as Record<string, FileMode>;
    return map[view] === "edit" ? "edit" : "rendered";
  } catch {
    return "rendered";
  }
}

export function writeFileMode(view: FileView, mode: FileMode): void {
  try {
    const map = JSON.parse(localStorage.getItem(MODE_KEY) || "{}") as Record<string, FileMode>;
    map[view] = mode;
    localStorage.setItem(MODE_KEY, JSON.stringify(map));
  } catch {
    /* best effort */
  }
}

/** What this system calls its file manager, for "show this file where it lives". */
export function revealLabel(platform: string = typeof navigator === "undefined" ? "" : navigator.platform): string {
  if (/Win/.test(platform)) return "Show in Explorer";
  if (/Mac|iPhone|iPad/.test(platform)) return "Show in Finder";
  return "Show in file manager";
}

const ENCODING_LABELS: Record<string, string> = {
  "utf-8": "UTF-8",
  "utf-8-sig": "UTF-8",
  "utf-16": "UTF-16",
  "utf-16-le": "UTF-16",
  "utf-16-be": "UTF-16",
  cp1258: "Windows-1258 (Vietnamese)",
  cp1252: "Windows-1252 (Western)",
  cp1250: "Windows-1250 (Central European)",
  cp1251: "Windows-1251 (Cyrillic)",
  shift_jis: "Shift JIS (Japanese)",
  gb18030: "GB18030 (Chinese)",
  big5: "Big5 (Traditional Chinese)",
  euc_kr: "EUC-KR (Korean)",
};

/** An encoding by the name a person knows it by. */
export function encodingLabel(encoding: string): string {
  return ENCODING_LABELS[encoding] ?? encoding;
}

/** UTF-8 is what a file is expected to be: nothing is said about it. */
export function isUtf8(encoding: string | undefined): boolean {
  return !encoding || encoding === "utf-8" || encoding === "utf-8-sig";
}

// Edits that were typed and not saved, kept outside the component: the Files tab is unmounted
// when another tab is shown, and a draft that lived in its state was dropped without a word
// (found reading the code, 2026-10-11). Kept per conversation and file until saved or discarded.
const drafts = new Map<string, string>();
const draftKey = (sessionId: string, path: string) => `${sessionId}\n${path}`;

export function readDraft(sessionId: string, path: string): string | null {
  return drafts.get(draftKey(sessionId, path)) ?? null;
}

export function writeDraft(sessionId: string, path: string, text: string | null): void {
  if (text === null) drafts.delete(draftKey(sessionId, path));
  else drafts.set(draftKey(sessionId, path), text);
}
