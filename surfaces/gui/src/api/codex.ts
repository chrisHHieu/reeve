import { fetch, httpBase } from "./base";
import { changedModels } from "./inbox-routing";
import type { ProviderInfo } from "./auto-memory";

// -- Subscription provider sign-in (OAuth; tokens never reach the GUI) --------------
export interface ProviderAuthStatus {
  signed_in: boolean;
  account?: string | null;
  authorizing: boolean;
  last_error?: string | null;
  authorize_url?: string | null;
}

export async function providerSignin(name: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/providers/${encodeURIComponent(name)}/signin`, {
    method: "POST",
  });
  return res.json();
}

export async function providerAuthStatus(name: string): Promise<ProviderAuthStatus> {
  const res = await fetch(`${httpBase()}/v1/providers/${encodeURIComponent(name)}/status`);
  return res.json();
}

export async function providerSignout(name: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/providers/${encodeURIComponent(name)}/signout`, {
    method: "POST",
  });
  return changedModels(res);
}

export type CodexAuthStatus = ProviderAuthStatus;

export const codexSignin = () => providerSignin("openai-codex");
export const codexAuthStatus = () => providerAuthStatus("openai-codex");
export const codexSignout = () => providerSignout("openai-codex");

export async function getProviders(): Promise<ProviderInfo[]> {
  const res = await fetch(`${httpBase()}/v1/providers`);
  return res.json();
}

export async function setProvider(
  name: string,
  fields: Record<string, string>,
): Promise<{ ok: boolean; error?: string; provider?: string }> {
  const res = await fetch(`${httpBase()}/v1/providers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, fields }),
  });
  return changedModels(res);
}

/** Forget a provider's stored config (Settings ▸ Models "Remove key…"). */
export async function removeProvider(name: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/providers/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  return changedModels(res);
}

/** Live read-only credential check (does NOT save the key). Triggered by the user's "Test" click. */
export async function verifyProvider(
  name: string,
  fields: Record<string, string>,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/providers/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, fields }),
  });
  return res.json();
}

/** The models one provider's stored credential can see, fetched from the provider's own
 *  API in real time (`GET /v1/providers/{name}/models`). On any failure the server answers
 *  `{ok: false, source: "curated", models: <matrix rows>, error}` so the setup page still
 *  renders; `refresh` bypasses the short server cache. */
export interface ProviderModels {
  ok: boolean;
  models: string[];
  source?: string;
  error?: string;
}

export async function getProviderModels(
  name: string,
  refresh = false,
): Promise<ProviderModels> {
  const q = refresh ? "?refresh=true" : "";
  const res = await fetch(
    `${httpBase()}/v1/providers/${encodeURIComponent(name)}/models${q}`,
  );
  return res.json();
}

/** Client-side provider guess from an API key's shape (mirrors the server's detect_provider). */
export function detectProvider(apiKey: string): string | null {
  const key = (apiKey || "").trim();
  if (!key) return null;
  if (key.startsWith("sk-ant-")) return "anthropic";
  if (key.startsWith("sk-or-")) return "openrouter";
  if (key.startsWith("AIza")) return "gemini";
  if (key.startsWith("sk-") || key.startsWith("sk_")) return "openai";
  return null;
}
