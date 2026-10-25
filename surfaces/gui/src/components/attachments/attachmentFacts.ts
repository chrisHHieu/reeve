// What an attachment's card says under its name: what kind of file it is, in a word, and how big
// (or, for text, how long). Read off the attachment itself where its bytes are here (the composer, a
// message just sent), else off the row the message kept (`_attached`) when a conversation
// is reopened.

import type { Attachment } from "../../types";
import { fileTypeLabel } from "../Markdown";

/** The decoded size of a base64 data URL, without decoding it. */
function dataUrlBytes(url: string): number | undefined {
  const comma = url.indexOf(",");
  if (comma < 0 || !url.slice(0, comma).includes(";base64")) return undefined;
  const body = url.length - comma - 1;
  const padding = url.endsWith("==") ? 2 : url.endsWith("=") ? 1 : 0;
  return Math.max(0, Math.floor((body * 3) / 4) - padding);
}

export function attachmentBytes(a: Attachment): number | undefined {
  if (typeof a.size === "number") return a.size;
  if (a.data_url) return dataUrlBytes(a.data_url);
  if (typeof a.text === "string") return new TextEncoder().encode(a.text).length;
  return undefined;
}

export function attachmentLines(a: Attachment): number | undefined {
  if (typeof a.lines === "number") return a.lines;
  if (typeof a.text !== "string" || !a.text) return undefined;
  const breaks = a.text.split("\n").length - 1;
  return a.text.endsWith("\n") ? breaks : breaks + 1;
}

/** "820 B", "180 KB", "2.4 MB". */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  const mb = bytes / 1024 / 1024;
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

const count = (n: number) => n.toLocaleString("en-US");

/** The kind in a word: the file's own type where its name says one, else the attachment's kind. */
export function attachmentKind(a: Attachment): string {
  return fileTypeLabel(a.name) || (a.kind === "pdf" ? "PDF" : a.kind === "image" ? "Image" : a.kind === "text" ? "Text" : "File");
}

/** "PDF · 2.4 MB", "CSV · 840 rows", "TypeScript · 2,930 lines". A CSV's first line is its header. */
export function attachmentMeta(a: Attachment): string {
  const kind = attachmentKind(a);
  const lines = attachmentLines(a);
  if (lines !== undefined && lines > 0) {
    if (kind === "CSV") {
      const rows = Math.max(lines - 1, 0);
      return `${kind} · ${count(rows)} row${rows === 1 ? "" : "s"}`;
    }
    return `${kind} · ${count(lines)} line${lines === 1 ? "" : "s"}`;
  }
  const bytes = attachmentBytes(a);
  return bytes === undefined ? kind : `${kind} · ${formatBytes(bytes)}`;
}

/** "5 attached · 4.1 MB": the composer's line under the text, once there is more than nothing. */
export function attachedTotal(list: Attachment[]): string {
  const bytes = list.reduce((sum, a) => sum + (attachmentBytes(a) ?? 0), 0);
  return `${list.length} attached` + (bytes > 0 ? ` · ${formatBytes(bytes)}` : "");
}
