// Markdown that is a FILE, not a message (workspace panel redesign, 2026-10-11).
//
// A message's Markdown has no folder. A file's does, and its pictures are written from there:
// `![](images/flow.png)`, `![](C:\docs\flow.png)`. The renderer was handed only the text, so
// the browser resolved those against the app's own address and every local picture was broken
// (owner-hit; found in the `img` renderer, which passed `src` straight through). A file
// preview now says where the file lives, and a local picture is read through the session's
// own file reader, which only reads inside the session's folders.
import { createContext, useContext, useEffect, useState } from "react";
import { readArtifact } from "../../api";

export interface MarkdownFile {
  sessionId: string;
  /** The file's own path, as the Files tab opened it. */
  path: string;
  /** Draw ```mermaid fences as diagrams. Only a file preview asks for this; a chat message
   *  keeps the fence as code (owner, 2026-10-03 and 2026-10-11). */
  diagrams?: boolean;
}

export const MarkdownFileContext = createContext<MarkdownFile | null>(null);

const REMOTE_RE = /^(?:https?:|data:|blob:)/i;
const ABSOLUTE_RE = /^(?:[A-Za-z]:[\\/]|[\\/])/;

/** Where a picture written in a file lives, or null when it is not a file on this machine. */
export function localImagePath(filePath: string, src: string): string | null {
  let target = src.trim();
  if (!target || REMOTE_RE.test(target)) return null;
  if (/^file:/i.test(target)) target = target.replace(/^file:\/{2,3}/i, "").replace(/^([A-Za-z]:)/, "$1");
  target = target.split(/[?#]/)[0];
  try {
    target = decodeURIComponent(target);
  } catch {
    /* a stray % is part of the name */
  }
  target = target.replace(/\\/g, "/");
  if (ABSOLUTE_RE.test(target)) return target;
  const folder = filePath.replace(/\\/g, "/").split("/").slice(0, -1);
  for (const part of target.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") folder.pop();
    else folder.push(part);
  }
  return folder.join("/");
}

// One read per picture per conversation: a preview re-renders on every keystroke of its editor.
const reads = new Map<string, Promise<string | null>>();

function readImage(sessionId: string, path: string): Promise<string | null> {
  const key = `${sessionId}\n${path}`;
  let pending = reads.get(key);
  if (!pending) {
    pending = readArtifact(sessionId, path)
      .then((file) => {
        if (!file?.ok) return null;
        if (file.data_url) return file.data_url;
        // An SVG is text to the server; it is a picture here.
        if (typeof file.content === "string" && /\.svg$/i.test(path)) {
          return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(file.content)}`;
        }
        return null;
      })
      .catch(() => null);
    reads.set(key, pending);
    // A missing picture may be written a moment later: do not remember the miss.
    void pending.then((found) => {
      if (found === null) reads.delete(key);
    });
  }
  return pending;
}

export interface ResolvedImage {
  src: string;
  alt?: string;
  /** "missing": the file names a picture that could not be read. */
  state: "ready" | "loading" | "missing";
  /** What the file wrote, for the "not found" line. */
  written: string;
}

/** The pictures of one paragraph, with a local path turned into something a browser can draw. */
export function useFileImages(images: { src: string; alt?: string }[]): ResolvedImage[] {
  const file = useContext(MarkdownFileContext);
  const key = images.map((image) => image.src).join("\n");
  const [found, setFound] = useState<Record<string, string | null>>({});

  useEffect(() => {
    if (!file) return;
    let alive = true;
    for (const image of images) {
      const path = localImagePath(file.path, image.src);
      if (path === null) continue;
      void readImage(file.sessionId, path).then((url) => {
        if (alive) setFound((prev) => (prev[image.src] === url ? prev : { ...prev, [image.src]: url }));
      });
    }
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file?.sessionId, file?.path, key]);

  return images.map((image) => {
    if (!file || localImagePath(file.path, image.src) === null) {
      return { src: image.src, alt: image.alt, state: "ready", written: image.src };
    }
    const url = found[image.src];
    if (url === undefined) return { src: "", alt: image.alt, state: "loading", written: image.src };
    if (url === null) return { src: "", alt: image.alt, state: "missing", written: image.src };
    return { src: url, alt: image.alt, state: "ready", written: image.src };
  });
}
