// What the composer's "@" lists: the files and folders of the session, from one list read when
// the menu opens, filtered as each letter is typed (owner, 2026-10-13: "@" was not live; it
// asked the server for a search per keystroke and the server walked the tree each time).
//
// A bare "@" shows what this conversation has touched, then the top of the folder, folders
// first. "@docs/" shows what is inside docs, with a row that adds the folder itself. Anything
// else is a search of every name and path. "@" is context only: agents are offered by "/".

/** A file the agent read or changed in this conversation, newest first. */
export interface Touched {
  path: string;
  /** "changed" or "read". */
  what: string;
}

export type AtRow =
  | { kind: "touched"; path: string; what: string }
  | { kind: "file"; path: string }
  | { kind: "dir"; path: string; count: number }
  /** The folder the list is inside, added whole. */
  | { kind: "here"; path: string; count: number };

/** How many rows a search shows: the list is a menu, not a file browser. */
const SEARCH_ROWS = 40;
const TOUCHED_ROWS = 4;

export const nameOf = (path: string): string => {
  const trimmed = path.endsWith("/") ? path.slice(0, -1) : path;
  return trimmed.slice(trimmed.lastIndexOf("/") + 1);
};

/** The folder a path sits in, with its slash ("docs/"), or "" at the top. */
export const folderOf = (path: string): string => {
  const trimmed = path.endsWith("/") ? path.slice(0, -1) : path;
  const cut = trimmed.lastIndexOf("/");
  return cut >= 0 ? trimmed.slice(0, cut + 1) : "";
};

/** Every folder the files sit in ("a/", "a/b/"), with how many files are under each. */
export function foldersOf(files: string[]): Map<string, number> {
  const out = new Map<string, number>();
  for (const file of files) {
    let at = file.indexOf("/");
    while (at >= 0) {
      const dir = file.slice(0, at + 1);
      out.set(dir, (out.get(dir) ?? 0) + 1);
      at = file.indexOf("/", at + 1);
    }
  }
  return out;
}

const byName = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: "base" });

/** The entries directly inside `dir` ("" is the top): folders first, then files. */
function inside(dir: string, files: string[], folders: Map<string, number>): AtRow[] {
  const dirs = [...folders.entries()]
    .filter(([path]) => path.startsWith(dir) && path !== dir && !path.slice(dir.length, -1).includes("/"))
    .sort(([a], [b]) => byName(a, b))
    .map(([path, count]): AtRow => ({ kind: "dir", path, count }));
  const own = files
    .filter((file) => file.startsWith(dir) && !file.slice(dir.length).includes("/"))
    .sort(byName)
    .map((path): AtRow => ({ kind: "file", path }));
  return [...dirs, ...own];
}

/** Rank a name against what was typed: starts with it, contains it, or only the path does. */
function rank(path: string, query: string): number {
  const name = nameOf(path).toLowerCase();
  if (name.startsWith(query)) return 0;
  if (name.includes(query)) return 1;
  return path.toLowerCase().includes(query) ? 2 : -1;
}

export function atMenuRows(files: string[], typed: string, touched: Touched[] = []): AtRow[] {
  const folders = foldersOf(files);
  const query = typed.toLowerCase();
  if (!query) {
    const seen = new Set<string>();
    const recent = touched
      .filter((t) => !seen.has(t.path) && seen.add(t.path))
      .slice(0, TOUCHED_ROWS)
      .map((t): AtRow => ({ kind: "touched", path: t.path, what: t.what }));
    return [...recent, ...inside("", files, folders)];
  }
  // "docs/": what is inside the folder, and the folder itself first.
  if (query.endsWith("/")) {
    const dir = [...folders.keys()].find((path) => path.toLowerCase() === query);
    if (dir) return [{ kind: "here", path: dir, count: folders.get(dir) ?? 0 }, ...inside(dir, files, folders)];
  }
  const hits: { row: AtRow; score: number }[] = [];
  for (const [path, count] of folders) {
    const score = rank(path, query);
    if (score >= 0) hits.push({ row: { kind: "dir", path, count }, score });
  }
  for (const path of files) {
    const score = rank(path, query);
    if (score >= 0) hits.push({ row: { kind: "file", path }, score });
  }
  hits.sort((a, b) => a.score - b.score || a.row.path.length - b.row.path.length || byName(a.row.path, b.row.path));
  return hits.slice(0, SEARCH_ROWS).map((hit) => hit.row);
}

/** Where the typed text matches inside a name, for the row to mark it. */
export function matchIn(name: string, typed: string): [string, string, string] | null {
  if (!typed) return null;
  const at = name.toLowerCase().indexOf(typed.toLowerCase());
  if (at < 0) return null;
  return [name.slice(0, at), name.slice(at, at + typed.length), name.slice(at + typed.length)];
}

/** The `@path` tokens a draft carries that name a file or folder of the session. */
export function pointersIn(text: string, files: string[]): string[] {
  const known = new Set(files);
  const folders = foldersOf(files);
  const out: string[] = [];
  for (const match of text.matchAll(/(^|\s)@(\S+)/g)) {
    const path = match[2];
    if ((known.has(path) || folders.has(path)) && !out.includes(path)) out.push(path);
  }
  return out;
}

const CHANGED = /(^|_)(edit|write|create|patch|delete|move|rename)/;
const READ = /(^|_)read/;

/** The files the agent read or changed in a conversation, newest first, relative to `root`
 *  where they sit under it. A change outranks a read of the same file. */
export function touchedFilesOf(
  items: ReadonlyArray<{ kind: string; name?: string; args?: unknown; status?: string }>,
  root?: string,
): Touched[] {
  const base = root ? root.replace(/\\/g, "/").replace(/\/+$/, "") + "/" : "";
  const out: Touched[] = [];
  const seen = new Set<string>();
  for (let i = items.length - 1; i >= 0 && out.length < 8; i--) {
    const item = items[i];
    if (item.kind !== "tool" || !item.name || item.status === "error") continue;
    const what = CHANGED.test(item.name) ? "changed" : READ.test(item.name) ? "read" : "";
    if (!what) continue;
    const args = (item.args ?? {}) as Record<string, unknown>;
    const raw = args.path ?? args.file_path ?? args.notebook_path;
    if (typeof raw !== "string" || !raw) continue;
    let path = raw.replace(/\\/g, "/");
    if (base && path.toLowerCase().startsWith(base.toLowerCase())) path = path.slice(base.length);
    if (seen.has(path)) {
      const listed = out.find((t) => t.path === path);
      if (listed && what === "changed") listed.what = "changed";
      continue;
    }
    seen.add(path);
    out.push({ path, what });
  }
  return out;
}
