// What a model row says about a model, the same words wherever a model is listed: the composer's
// picker, a provider's page and Settings ▸ Models (owner, 2026-10-21: the three said it three ways,
// one in raw ids with "1M ctx · vision · reasoning", one in names with "1M · $0.15 / $0.6").

import { useEffect, useState } from "react";
import { getModelCatalog, MODELS_CHANGED, type CatalogModel } from "./api";
import { formatTokens } from "./usage";
import { shortProviderTitle } from "./providerOptions";

/** What tells two models apart at a glance: the context window and the price. A free model's
 *  price is its Free tag, drawn beside its name. */
export function modelFacts(row: CatalogModel | undefined): string {
  if (!row) return "";
  const parts: string[] = [];
  if (row.context) parts.push(formatTokens(row.context));
  const input = row.cost?.input;
  const output = row.cost?.output;
  if (!row.free && typeof input === "number" && typeof output === "number") parts.push(`$${input} / $${output}`);
  return parts.join(" · ");
}

let cached: Record<string, CatalogModel> | null = null;
let pending: Promise<Record<string, CatalogModel>> | null = null;

function fetchCatalog(): Promise<Record<string, CatalogModel>> {
  pending ??= getModelCatalog()
    .then((catalog) => {
      const byId: Record<string, CatalogModel> = {};
      for (const row of catalog.models || []) byId[row.id] = row;
      cached = byId;
      return byId;
    })
    .finally(() => {
      pending = null;
    });
  return pending;
}

/** The catalogue by model id, for a model's name and facts; empty until it arrives or when the
 *  server has none, and read again when the pickable models change. */
export function useModelCatalog(): Record<string, CatalogModel> {
  const [byId, setById] = useState<Record<string, CatalogModel>>(() => cached ?? {});
  useEffect(() => {
    let alive = true;
    const load = () =>
      void fetchCatalog()
        .then((next) => alive && setById(next))
        .catch(() => {});
    load();
    window.addEventListener(MODELS_CHANGED, load);
    return () => {
      alive = false;
      window.removeEventListener(MODELS_CHANGED, load);
    };
  }, []);
  return byId;
}

/** A model its catalogue marks deprecated: the provider may stop serving it soon, so its row says so
 *  wherever it is listed (models.dev listed four on OpenCode Go on 2026-10-21). */
export function retiring(row: CatalogModel | undefined): boolean {
  return row?.status === "deprecated";
}

export const RETIRING_TIP = "Marked deprecated by its provider: it may stop being served soon.";

/** The name a model goes by, the one the composer's picker shows: the catalogue's, else Settings'
 *  own label, else its id. */
export function modelName(id: string, labels: Record<string, string> | undefined, row: CatalogModel | undefined): string {
  return row?.name || labels?.[id] || id.slice(id.indexOf(":") + 1) || id;
}

/** The name a provider goes by, the one the composer's picker shows: the catalogue's, else the
 *  provider's own title without its parenthesis ("Claude subscription (Pro / Max)"). */
export function providerName(key: string, title: string | undefined, catalog: Record<string, CatalogModel>): string {
  for (const row of Object.values(catalog))
    if ((row.provider || "") === key && row.provider_title) return shortProviderTitle(row.provider_title);
  return shortProviderTitle(title || key);
}
