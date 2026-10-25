// The providers the model picker lists down its left side, and the provider an id belongs to.
//
// The catalogue (`/v1/models/catalog`) is the source of truth for titles; when it is
// unreachable the provider is derived from the model id's own prefix so the picker never
// goes blank (the same degradation the model list's fallback uses).

import type { CatalogModel } from "./api";
import { displayProvider } from "./modelName";

/** The provider key an id carries: "deepseek:x" -> "deepseek"; bare ids are OpenAI's. */
export function providerKeyOf(id: string): string {
  const at = id.indexOf(":");
  return at > 0 ? id.slice(0, at) : "openai";
}

/** A provider's title as every model list names it: "Claude subscription (Pro / Max)" reads
 *  "Claude subscription"; the plans in the parenthesis are its own page's to say. */
export function shortProviderTitle(title: string): string {
  return title.split(" (")[0];
}

function titleize(raw: string): string {
  return raw
    .split(/[-_]/)
    .map((word) => (word ? word.charAt(0).toUpperCase() + word.slice(1) : word))
    .join(" ");
}

/** A provider KEY as a person reads it: "openai" -> "OpenAI", "zai" -> "Z.ai". */
function titleFromKey(key: string): string {
  return displayProvider(`${key}:x`) || titleize(key);
}

export interface ProviderOption {
  key: string;
  title: string;
  /** The provider's first catalogue model. */
  modelId: string;
  /** How many models the picker has for this provider. */
  count: number;
}

/** The provider list, in catalogue order. */
export function providerOptions(
  rows: CatalogModel[],
  models: string[],
  value: string,
): ProviderOption[] {
  const known: { id: string; provider: string; title: string }[] = rows.length
    ? rows.map((row) => ({
        id: row.id,
        provider: row.provider || providerKeyOf(row.id),
        title: row.provider_title || row.provider || providerKeyOf(row.id),
      }))
    : models.map((id) => ({
        id,
        provider: providerKeyOf(id),
        title: displayProvider(id) || titleFromKey(providerKeyOf(id)),
      }));
  const counts = new Map<string, number>();
  for (const row of known) counts.set(row.provider, (counts.get(row.provider) || 0) + 1);
  const out = new Map<string, ProviderOption>();
  for (const row of known) {
    if (out.has(row.provider)) continue;
    out.set(row.provider, {
      key: row.provider,
      title: titleize(shortProviderTitle(row.title)),
      modelId: row.id,
      count: counts.get(row.provider) || 1,
    });
  }
  // The active model's provider is always offered so the picker can name it even when the
  // catalogue predates the model (a just-added custom id).
  const current = providerKeyOf(value);
  if (!out.has(current)) {
    const row = rows.find((r) => r.id === value);
    const siblings = models.filter((id) => providerKeyOf(id) === current).length;
    out.set(current, {
      key: current,
      title: titleize(shortProviderTitle(row?.provider_title || row?.provider || displayProvider(value) || current)),
      modelId: value,
      count: Math.max(siblings, 1),
    });
  }
  return [...out.values()];
}
