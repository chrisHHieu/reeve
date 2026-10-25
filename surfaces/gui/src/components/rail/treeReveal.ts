import type { RootInfo } from "../../api/session-roots";

const forward = (path: string) => path.replace(/\\/g, "/").replace(/\/+$/, "");
const isAbsolute = (path: string) => /^([A-Za-z]:|\/)/.test(path);

/** Where a file sits in the Files tree, in the tree's own spelling: the folders to open, outermost
 *  first (a root's path as the server gave it, then names joined with "/"), and the file's row.
 *
 *  A file opened from outside the tree (a transcript card or chip, Review) is named the way its
 *  sender knew it: `AGENTS.md`, or `C:/Users/…/AGENTS.md`, while the tree's rows read
 *  `C:\Users\…\Downloads/AGENTS.md`. Compared as they were, nothing matched, so the tree neither
 *  opened to the file nor marked it (owner, 2026-10-21). A relative path is the primary folder's. */
export function treeChain(path: string, roots: RootInfo[]): { dirs: string[]; file: string } | null {
  const shown = roots.filter((root) => !root.reached);
  const target = forward(path.trim()).replace(/^\.\//, "");
  if (!target) return null;
  let root: RootInfo | undefined;
  let rest = "";
  if (isAbsolute(target)) {
    const lower = target.toLowerCase();
    // The deepest root that holds it: a scratch folder can sit inside the workspace.
    let depth = -1;
    for (const candidate of shown) {
      const base = forward(candidate.path);
      if (!base || base.length <= depth || !lower.startsWith(`${base.toLowerCase()}/`)) continue;
      root = candidate;
      depth = base.length;
      rest = target.slice(base.length + 1);
    }
  } else {
    root = shown.find((candidate) => candidate.primary) ?? shown[0];
    rest = target;
  }
  const names = rest.split("/").filter(Boolean);
  if (!root || !names.length || names.some((name) => name === "." || name === "..")) return null;
  // The tree builds a child as `${parent without trailing "/"}/${name}` (FileTree's childPath).
  const dirs = [root.path];
  for (const name of names.slice(0, -1)) dirs.push(`${dirs[dirs.length - 1].replace(/\/+$/, "")}/${name}`);
  const file = `${dirs[dirs.length - 1].replace(/\/+$/, "")}/${names[names.length - 1]}`;
  return { dirs, file };
}
