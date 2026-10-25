import { getSettings, inspectPdf } from "../../api";
import { isPdfFile } from "../../attach";
import type { Attachment } from "../../types";

// The person's limits on an attached PDF (Settings ▸ Token savings): a PDF over its pages or its
// size is not attached, and a card says why. A big PDF costs tokens on every turn after it.

export type PdfLimits = { maxPages: number; maxMb: number };

const DEFAULT: PdfLimits = { maxPages: 20, maxMb: 10 };

const WHERE = "(Settings → Token savings)";

/** The limits, read only when a PDF is among the files; with the settings out of reach, the
 *  defaults. */
export async function pdfLimitsFor(files: File[]): Promise<PdfLimits> {
  if (!files.some(isPdfFile)) return DEFAULT;
  try {
    const settings = await getSettings();
    return { maxPages: settings.pdf_max_pages || DEFAULT.maxPages, maxMb: settings.pdf_max_mb || DEFAULT.maxMb };
  } catch {
    return DEFAULT;
  }
}

/** Why a file is too big to read at all, or null. */
export function overSize(file: File, { maxMb }: PdfLimits): string | null {
  if (!isPdfFile(file) || file.size <= maxMb * 1024 * 1024) return null;
  return `${file.name} skipped — ${(file.size / 1024 / 1024).toFixed(1)} MB is over your ${maxMb} MB limit ${WHERE}`;
}

/** Why a read PDF is not attached — too many pages, or it could not be read — or null. */
export async function overPages(attachment: Attachment, { maxPages }: PdfLimits): Promise<string | null> {
  if (attachment.kind !== "pdf" || !attachment.data_url) return null;
  const info = await inspectPdf(attachment.data_url).catch(() => null);
  if (info?.ok && (info.pages ?? 0) > maxPages) return `${attachment.name} skipped — ${info.pages} pages is over your ${maxPages}-page limit ${WHERE}`;
  if (info && !info.ok) return `${attachment.name} skipped — ${info.error || "could not read PDF"}`;
  return null;
}
