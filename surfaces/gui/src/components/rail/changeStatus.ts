// What a git status letter means, and which changed paths a tool generated rather than a
// person wrote — shared by Source Control's rows and the diff header.

export function statusType(status: string): "added" | "deleted" | "modified" {
  if (status === "A") return "added";
  if (status === "D") return "deleted";
  return "modified";
}

// Folders a tool fills as a side effect of running — interpreter and tool caches, installed
// dependencies. Their files are real changes to git but not to the person's code: five
// `__pycache__/*.pyc` sat between the real files (review audit, V11). Same list the turn
// receipt skips (harness/worktree_state.py).
const GENERATED = new Set([
  "__pycache__",
  ".pytest_cache",
  ".mypy_cache",
  ".ruff_cache",
  ".tox",
  ".nox",
  ".venv",
  "venv",
  "node_modules",
  ".next",
  ".turbo",
  ".parcel-cache",
]);

export function isGeneratedPath(path: string): boolean {
  return path.split("/").slice(0, -1).some((part) => GENERATED.has(part));
}
