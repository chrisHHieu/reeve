import { fetch, httpBase } from "./base";
import type { RecentChannel } from "./channel-subscriptions";
import type { ModelSettings } from "./settings";

export async function getRecentChannels(): Promise<RecentChannel[]> {
  const res = await fetch(`${httpBase()}/v1/channels/recent`);
  return (await res.json()).channels ?? [];
}

export async function subscribeChannel(
  sessionId: string,
  channel: string,
): Promise<{ ok: boolean; channel?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/subscriptions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, channel }),
  });
  return res.json();
}

export async function unsubscribeChannel(
  sessionId: string,
  channel: string,
): Promise<{ ok: boolean; removed?: boolean }> {
  const res = await fetch(`${httpBase()}/v1/subscriptions/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, channel }),
  });
  return res.json();
}

export async function getSettings(): Promise<ModelSettings> {
  const res = await fetch(`${httpBase()}/v1/settings`);
  return res.json();
}

export async function setModelKey(
  apiKey: string,
): Promise<{ ok: boolean; error?: string; has_key?: boolean; source?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/model-key`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
  return changedModels(res);
}

// Fired after anything that changes which models can be picked: a provider connected,
// removed or signed out, a model added or removed, the default moved. The composer's picker
// stays mounted behind Settings and caches its catalogue, so without this a provider
// connected in Settings showed nowhere until the app was restarted (owner, 2026-10-18).
export const MODELS_CHANGED = "reeve:models-changed";
export function announceModelsChanged() {
  window.dispatchEvent(new CustomEvent(MODELS_CHANGED));
}

/** The response's body, with the model list announced as changed once the server answered. */
export async function changedModels<T>(res: Response): Promise<T> {
  const body = await res.json();
  announceModelsChanged();
  return body;
}

export async function setDefaultModel(
  model: string,
): Promise<{ ok: boolean; error?: string; model?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/default-model`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  return changedModels(res);
}

export async function addModel(model: string): Promise<ModelSettings & { ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/models/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  return changedModels(res);
}

export async function removeModel(model: string): Promise<ModelSettings & { ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/settings/models/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  return changedModels(res);
}

// One row of the model picker's catalogue: the curated matrix plus the models.dev
// facts (context, modalities, reasoning, cost). Optional fields mean "the catalogues
// did not say" — the UI renders an em dash, never a false No.
export interface CatalogModel {
  id: string;
  name: string;
  provider: string;
  provider_title?: string;
  curated?: boolean;
  selected?: boolean;
  context?: number | null;
  output?: number | null;
  vision?: boolean | null;
  tools?: boolean | null;
  pdf?: boolean | null;
  reasoning?: boolean | null;
  // The thinking levels the model accepts, low→high ([] = no control shown). Derived by
  // the server from the provider's own reasoning options; `reasoning_default` is null
  // when the provider's own default rides.
  reasoning_levels?: string[] | null;
  reasoning_default?: string | null;
  temperature?: boolean | null;
  inputs?: string[] | null;
  cost?: { input?: number; output?: number; cache_read?: number; cache_write?: number } | null;
  free?: boolean;
  status?: string | null;
  release_date?: string | null;
  source?: string | null;
}

export async function getModelCatalog(): Promise<{ models: CatalogModel[]; default: string }> {
  const res = await fetch(`${httpBase()}/v1/models/catalog`);
  return res.json();
}

export async function setOnboarded(value: boolean): Promise<{ ok: boolean; onboarded: boolean }> {
  const res = await fetch(`${httpBase()}/v1/settings/onboarded`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
  return res.json();
}
