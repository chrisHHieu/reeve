// What a fence with no language holds, when it is not code (owner, 2026-10-13).
//
// 102 of the 189 fences in the owner's store name no language. Read by shape, 23 are a folder
// tree drawn with box characters (`├──`, `└──`, `│`) and 36 are prose: a draft email, a
// message, a template, put in a fence so it is easy to copy. Both were drawn as code: the tree
// as mono text with its lines, the draft as mono on one long line that scrolled sideways.
//
// Each reading is strict and falls back to code: a tree is a fence whose every line is a tree
// line, and prose is a fence with no mark that code, a command, a log or a diagram would have.
// A wrong reading changes how the text looks, never what Copy gives.

/** The fence languages that say nothing about what is inside. */
const UNNAMED = new Set(["", "text", "txt", "plain", "plaintext", "tree"]);

export const isUnnamedFence = (lang: string | undefined) => UNNAMED.has((lang ?? "").toLowerCase());

// ---- a folder tree ------------------------------------------------------------------------

export interface TreeEntry {
  depth: number;
  name: string;
  /** What the writer said about the entry after it (`# the cart`, `← changed`). */
  note?: string;
  dir: boolean;
}

export interface TextTree {
  /** The first line, when it names the folder the tree is of (`shop/`); not `.`. */
  root?: string;
  entries: TreeEntry[];
}

const BRANCH = /^((?:[│|┃][ \u00a0]{2,3}|[ \u00a0]{3,4})*)(?:├──|└──|├─|└─|┣━━|┗━━|\|--|`--|\+--)[ \u00a0]?(.*)$/;
const SPACER = /^[│|┃\s]*$/;
const NOTE = /\s{2,}(?:#|\/\/|←|<-|--)\s*|\s+#\s+|\s+←\s*/;
const BOX = /[├└│┃┣┗]/;

/** The tree a fence draws, or null when any of its lines is not a tree line. */
export function parseTextTree(text: string): TextTree | null {
  const lines = text.replace(/\s+$/, "").split("\n").map((line) => line.replace(/\r$/, ""));
  let root: string | undefined;
  const raw: { depth: number; name: string; note?: string; slash: boolean }[] = [];
  let foreign = false;
  lines.forEach((line, index) => {
    if (foreign) return;
    const branch = BRANCH.exec(line);
    if (!branch) {
      if (index === 0 && line.trim() && !BOX.test(line)) {
        const name = line.trim().split(NOTE)[0].trim();
        root = name === "." || name === "./" ? undefined : name.replace(/\/$/, "");
        return;
      }
      if (!SPACER.test(line)) foreign = true;
      return;
    }
    const [name, ...rest] = branch[2].split(NOTE);
    const clean = name.trim();
    raw.push({
      depth: Math.round(branch[1].replace(/\u00a0/g, " ").length / 4),
      name: clean.replace(/\/$/, ""),
      note: rest.join(" ").trim() || undefined,
      slash: clean.endsWith("/"),
    });
  });
  if (foreign || raw.length < 2 || raw.some((entry) => !entry.name)) return null;
  // A tree of files and folders: names, not sentences. A decision tree drawn with the same
  // branches (`├── Có → …`) stays as written.
  if (raw.filter((entry) => !/\s/.test(entry.name)).length / raw.length < 0.6) return null;
  const entries = raw.map((entry, index) => ({
    depth: entry.depth,
    name: entry.name,
    note: entry.note,
    // A folder says so with its slash, or by having entries under it.
    dir: entry.slash || (raw[index + 1]?.depth ?? -1) > entry.depth,
  }));
  return { root, entries };
}

// ---- prose ---------------------------------------------------------------------------------

// Marks that code, markup, a command line, a log or a drawn diagram have and a letter does not.
const CODE_MARK = /[{};<>=$`\\|]|->|=>|[→←↑↓⇒├└│┌─]/;
// `accent-gold: lab(…)`, `max_items: 50`: a setting's name, joined with a hyphen, an underscore
// or a dot, then a colon. Seen on the dev app (2026-10-13): two colour values read as a draft.
const KEY_START = /^\s*[\w.-]*[-_.][\w.-]*\s*:/;
// A setting with a quoted or listed value (`objective: "…"`), and a commit log's hash first.
const VALUE_START = /^\s*\w+\s*:\s*["'[{]|^\s*[0-9a-f]{7,40}\s/;
const CODE_START =
  /^\s*(\$|>|#|\/\/|--|\*|import\s|from\s|def\s|class\s|function\s|const\s|let\s|var\s|return\s|select\s|insert\s|update\s|npm\s|npx\s|git\s|pip\s|python|node\s|cd\s|ps\s|c:\\|\/[a-z])/i;
const LOG_START = /^\s*(error|warning|warn|info|debug|trace|traceback|exception|fatal|at\s|file\s")\b/i;
const SUBJECT = /^(subject|tiêu đề|chủ đề|re|fw|fwd)\s*:\s*(.+)$/i;

/** True when a fence holds prose (a draft, a message, a template), not code. */
export function isProse(text: string): boolean {
  const lines = text.replace(/\s+$/, "").split("\n");
  const filled = lines.filter((line) => line.trim() !== "");
  if (filled.length === 0) return false;
  const words = filled.join(" ").split(/\s+/).filter(Boolean).length;
  if (words < 12) return false;
  for (const line of filled) {
    if (/^(\t| {4})/.test(line) || CODE_MARK.test(line) || CODE_START.test(line) || LOG_START.test(line) || KEY_START.test(line) || VALUE_START.test(line))
      return false;
  }
  const count = (line: string) => line.trim().split(/\s+/).length;
  // Prose has at least one sentence, and most of its lines are made of words.
  if (!filled.some((line) => count(line) >= 8)) return false;
  return filled.filter((line) => count(line) >= 3).length / filled.length >= 0.6;
}

export interface Draft {
  /** The subject line's own word, as written (`Subject`, `Tiêu đề`), and its text. */
  subjectLabel?: string;
  subject?: string;
  /** Paragraphs, each a list of its lines. */
  paragraphs: string[][];
}

/** The draft's subject line pulled out, and its paragraphs. */
export function readDraft(text: string): Draft {
  const lines = text.replace(/\s+$/, "").split("\n").map((line) => line.replace(/\r$/, ""));
  const found = SUBJECT.exec(lines[0]?.trim() ?? "");
  const body = found ? lines.slice(1) : lines;
  const paragraphs: string[][] = [];
  let open: string[] = [];
  for (const line of body) {
    if (line.trim() === "") {
      if (open.length) paragraphs.push(open);
      open = [];
    } else open.push(line.trim());
  }
  if (open.length) paragraphs.push(open);
  return { subjectLabel: found?.[1], subject: found?.[2].trim(), paragraphs };
}
