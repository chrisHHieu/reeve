// Files a person attaches to a message — picked, dropped or pasted, in the composer or on the
// session's start panel — read into attachments, within what one message can carry.
import type { Attachment } from "./types";

/** The largest file read; a bigger one is skipped and said. */
export const MAX_BYTES = 10 * 1024 * 1024;
/** How many files go with one message; the engine holds to the same number and says so. */
export const MAX_ATTACHMENTS = 8;
/** A message is one WebSocket frame and the server closes the socket past 16 MiB, which eight
 *  files of 10 MB each would reach, so their encoded total is bounded as well. */
export const MAX_PAYLOAD_CHARS = 15_000_000;

const TEXT_NAMES = /\.(txt|md|markdown|csv|tsv|json|ya?ml|log|ini|toml|py|js|ts|tsx|jsx|rs|go|java|c|h|cpp|sh|html?|css|sql|xml)$/i;

export const isPdfFile = (file: File) => file.type === "application/pdf" || /\.pdf$/i.test(file.name);

/** A file read for attaching, or why it was not: nothing is dropped without a word. */
export type ReadOutcome = { ok: true; attachment: Attachment } | { ok: false; reason: string };

function read(file: File, as: "dataUrl"): Promise<string | null>;
function read(file: File, as: "bytes"): Promise<ArrayBuffer | null>;
function read(file: File, as: "dataUrl" | "bytes"): Promise<string | ArrayBuffer | null> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => resolve(null);
    if (as === "dataUrl") reader.readAsDataURL(file);
    else reader.readAsArrayBuffer(file);
  });
}

/** A file as an attachment: an image or a PDF as a data URL, text as text, and anything else as a
 *  `file` the engine reads (a document, an archive) or saves and names to the model.
 *
 *  Text is decoded here only when it is valid UTF-8. A CSV that Excel saved on a Vietnamese or
 *  Central European Windows is cp1258 or cp1250, and the browser's decoding turned `Báo cáo` into
 *  replacement characters with no notice. What does not decode goes to the engine as bytes, and the
 *  engine works out its encoding (`text_sniff.decode_bytes`). */
export async function readFileChecked(file: File): Promise<ReadOutcome> {
  const name = file.name || "file";
  const skipped = (why: string): ReadOutcome => ({ ok: false, reason: `${name} skipped — ${why}` });
  // A dropped folder arrives as an empty file with no type.
  if (file.size === 0) return skipped("it is empty, or a folder (attach the files inside)");
  if (file.size > MAX_BYTES) return skipped(`${(file.size / 1024 / 1024).toFixed(1)} MB is over the ${MAX_BYTES / 1024 / 1024} MB limit`);

  const pdf = isPdfFile(file);
  if (!pdf && (file.type.startsWith("text/") || TEXT_NAMES.test(file.name))) {
    const bytes = await read(file, "bytes");
    if (!bytes) return skipped("could not read it");
    try {
      return { ok: true, attachment: { kind: "text", name, mime: file.type, text: new TextDecoder("utf-8", { fatal: true }).decode(bytes) } };
    } catch {
      // Not UTF-8: send the bytes rather than guess an encoding here.
    }
  }
  const url = await read(file, "dataUrl");
  if (!url) return skipped("could not read it");
  if (file.type.startsWith("image/")) return { ok: true, attachment: { kind: "image", name: file.name || "image", mime: file.type, data_url: url } };
  if (pdf) return { ok: true, attachment: { kind: "pdf", name: file.name || "file.pdf", mime: "application/pdf", data_url: url } };
  return { ok: true, attachment: { kind: "file", name, mime: file.type || "application/octet-stream", data_url: url } };
}

/** The attachment alone; null when the file could not be attached. */
export async function readFile(file: File): Promise<Attachment | null> {
  const outcome = await readFileChecked(file);
  return outcome.ok ? outcome.attachment : null;
}

/** An attachment's identity, its name and size, so the same file picked twice (or a prefill
 *  applied twice) is one chip. */
export const attKey = (a: Attachment) => (a.kind === "text" ? `t:${a.name}:${a.text?.length ?? 0}` : `${a.kind[0]}:${a.name}:${a.data_url?.length ?? 0}`);

/** What an attachment adds to the message frame, in characters (base64 for the binary kinds). */
const frameChars = (a: Attachment) => (a.kind === "text" ? a.text?.length ?? 0 : a.data_url?.length ?? 0);

/** `add` put after `cur`, without duplicates and within the count and size limits. `dropped` is
 *  how many did not fit, for the caller to say so. */
export function mergeCapped(cur: Attachment[], add: Attachment[]): { merged: Attachment[]; dropped: number } {
  const seen = new Set(cur.map(attKey));
  const merged = [...cur];
  let used = cur.reduce((total, a) => total + frameChars(a), 0);
  let dropped = 0;
  for (const a of add) {
    if (seen.has(attKey(a))) continue;
    seen.add(attKey(a));
    if (merged.length >= MAX_ATTACHMENTS || used + frameChars(a) > MAX_PAYLOAD_CHARS) {
      dropped += 1;
      continue;
    }
    merged.push(a);
    used += frameChars(a);
  }
  return { merged, dropped };
}

/** The files a paste brings. An image always (a screenshot); another file (one copied in Explorer
 *  or Finder) only when no plain text came with it, since copied spreadsheet cells bring a picture
 *  of the cells along with their text, and the text is what was meant. */
export function pastedFiles(items: ArrayLike<{ kind: string; type: string; getAsFile(): File | null }>): File[] {
  const all = Array.from(items);
  const withText = all.some((it) => it.kind === "string" && it.type === "text/plain");
  return all
    .filter((it) => it.kind === "file")
    .map((it) => it.getAsFile())
    .filter((f): f is File => !!f && (f.type.startsWith("image/") || !withText));
}
