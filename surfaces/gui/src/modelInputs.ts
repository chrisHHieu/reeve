import { getModelCatalog, type CatalogModel } from "./api";
import type { Attachment } from "./types";

// The model picker's "Inputs" row is a promise: pick the model, attach the file. The composer keeps
// that promise from the other side. The moment a file is attached it is checked against the same row
// for the model that will receive it, so "this model cannot hear" is said while the person can still
// choose another model, not found out from the model's reply.

let catalog: Promise<CatalogModel[]> | null = null;
let loadedAt = 0;
const FRESH_MS = 60_000; // a provider connected a minute ago should show up without a restart

function loadCatalog(): Promise<CatalogModel[]> {
  if (!catalog || Date.now() - loadedAt > FRESH_MS) {
    loadedAt = Date.now();
    catalog = Promise.resolve(getModelCatalog())
      .then((c) => c?.models ?? [])
      .catch(() => {
        catalog = null; // try again next time; an unreachable catalogue must not stick
        return [];
      });
  }
  return catalog;
}

/** For tests: forget the cached catalogue. */
export function resetModelInputsCache(): void {
  catalog = null;
}

/** The row the picker shows for `modelId`: the exact id, else the one id whose bare name matches. */
export function rowFor(rows: CatalogModel[], modelId: string): CatalogModel | null {
  const exact = rows.find((r) => r.id === modelId);
  if (exact) return exact;
  const bare = rows.filter((r) => r.id.split(":").slice(1).join(":") === modelId || r.id.endsWith(`:${modelId}`));
  return bare.length === 1 ? bare[0] : null;
}

/** The input a file needs the model to take, or null when a model that cannot is still served:
 *  text and PDFs have local fallbacks, everything else the model must read itself. */
export function inputNeeded(a: Attachment): "image" | "audio" | "video" | null {
  if (a.kind === "image") return "image";
  const mime = (a.mime || "").toLowerCase();
  if (a.kind === "file" && mime.startsWith("audio/")) return "audio";
  if (a.kind === "file" && mime.startsWith("video/")) return "video";
  return null;
}

/** One sentence per attached file the selected model cannot read, empty when all are fine or the
 *  model's inputs are unknown (an unknown is never reported as a "no"). */
export async function unreadableNotices(modelId: string, attachments: Attachment[]): Promise<string[]> {
  const needs = attachments.map((a) => [a, inputNeeded(a)] as const).filter(([, n]) => n !== null);
  if (!needs.length) return [];
  const row = rowFor(await loadCatalog(), modelId);
  const inputs = row?.inputs;
  if (!row || !inputs || !inputs.length) return [];
  return needs
    .filter(([, need]) => !inputs.includes(need as string))
    .map(
      ([a, need]) =>
        `${a.name}: ${row.name} doesn't take ${need} (inputs: ${inputs.join(", ")}) — it will only be told the file's name. Pick a model that lists ${need}.`,
    );
}
