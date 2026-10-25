// A markdown link destination that names a FILE rather than a page
// `parseFileLink`, ported because the rule
// for "is this link a file?" has to be one rule in both apps.
//
// Why a parser and not "does it look like a path": every markdown link the model writes goes
// through this, and the answer decides whether the reader gets a file chip or a browser tab.
// A wrong YES sends a click to the OS/browser with a relative URL (a dead tab); a wrong NO
// costs a file the reader cannot open. So the rejections below are deliberate and each one
// has a real sender behind it:
//
//   - `javascript:`, `data:`, `file:///`, `vscode:` — schemes. Only a Windows drive letter
//     (`C:\…`, `C:/…`) survives the scheme test, because it is the one colon that is a path.
//   - `//host/share` and `\\server\share` — a UNC/protocol-relative destination is another
//     machine, not a session file.
//   - `?` — a query means a URL with parameters, never a filename (a `?` in a real name is
//     percent-encoded).
//   - control characters, and a malformed percent escape — unreadable, so refusing beats
//     guessing what the author meant.
//   - `#L24` / `#L24-L30` — GitHub's line fragment. It is kept (and the caller drops it):
//     the destination is still the file, and a reader who linked line 24 means that file.
//     Any OTHER fragment (`#results`) is a page anchor, so it is not a file link at all.

/**
 * Decode a local markdown destination and its optional line fragment.
 *
 * @param value - The parsed markdown link destination, exactly as written.
 * @returns The path and the 1-based line when the destination names a file, else undefined.
 */
export function parseFileLink(value: string): { path: string; line?: number } | undefined {
  const hash = value.indexOf("#");
  const destination = hash < 0 ? value : value.slice(0, hash);
  if (destination.includes("?")) return undefined;
  let path: string;
  try {
    path = decodeURIComponent(destination);
  } catch {
    // A malformed percent escape cannot identify a file unambiguously.
    return undefined;
  }
  if (
    path.length === 0 ||
    /[\u0000-\u001f\u007f]/.test(path) ||
    /^[\\/]{2}/.test(path) ||
    // A scheme, unless it is a Windows drive letter (`C:\`, `C:/`).
    (/^[a-z][a-z\d+.-]*:/i.test(path) && !/^[a-z]:[\\/]/i.test(path))
  ) {
    return undefined;
  }
  if (hash < 0) return { path };
  const match = /^L([1-9]\d*)(?:-L([1-9]\d*))?$/.exec(value.slice(hash + 1));
  if (match === null) return undefined;
  const line = Number(match[1]);
  const end = match[2] === undefined ? line : Number(match[2]);
  if (!Number.isSafeInteger(line) || !Number.isSafeInteger(end) || end < line) return undefined;
  return { path, line };
}
