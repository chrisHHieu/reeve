// Plugins, marketplaces and slash commands. An install is two calls: stage
// (what to consent to, plus a token) and confirm (the token). Nothing here sends plugin
// content; a failure carries the server's sentence.
import { fetch, httpBase } from "./base";

export interface PluginRow {
  id: string;
  name: string;
  version: string;
  root: string;
  scope: string;
  display_name: string;
  description: string;
  components: Record<string, number>;
  skipped: string[];
  dependencies?: string[];
  /** Its code: none, running, or waiting for a person's yes (design §9.3). */
  code?: "none" | "running" | "needs_consent";
}

export interface PluginError {
  id: string;
  root?: string;
  error: string;
}

export interface InstalledPlugin {
  id: string;
  version: string;
  scopes: string[];
  enabled: boolean;
  enabled_by: string;
  install_path: string;
  /** What went wrong setting it up (its Node packages, design §8.5) — it installed anyway. */
  warnings?: string[];
  /** Its marketplace no longer lists it: it does not load unless `kept` (design §6.1). */
  flagged?: { marketplace: string; flaggedAt: string; kept?: boolean };
  /** An update auto-update found and left for a person (its code changed, design §8.4). */
  update_waiting?: { version: string; reason: string; checkedAt: string };
}

export interface PluginsView {
  plugins: PluginRow[];
  errors: PluginError[];
  installed: InstalledPlugin[];
}

export interface ConsentOption {
  key: string;
  title: string;
  description: string;
  type: "string" | "number" | "boolean" | "directory" | "file";
  sensitive: boolean;
  required: boolean;
}

export interface Consent {
  id: string;
  name: string;
  display_name: string;
  version: string;
  current_version?: string;
  description: string;
  author: string;
  source: string;
  sha: string;
  skills: string[];
  commands: string[];
  agents: string[];
  executable: { kind: string; detail: string }[];
  skipped: string[];
  user_config: ConsentOption[];
  dependencies: { id: string; installed: boolean; consent?: Consent }[];
}

export interface PluginDiff {
  skills: { added: string[]; removed: string[] };
  commands: { added: string[]; removed: string[] };
  agents: { added: string[]; removed: string[] };
  executable_changed: boolean;
}

export interface Staged {
  token: string;
  consent: Consent;
  diff?: PluginDiff;
}

export interface Marketplace {
  name: string;
  plugins: number;
  error: string;
  installLocation: string;
  lastUpdated?: string;
  /** Updates that run no new code apply themselves (off unless turned on). */
  autoUpdate?: boolean;
}

export interface OfferedPlugin {
  id: string;
  name: string;
  description: string;
  version: string;
  installed: boolean;
}

/** What a trusted project declares or suggests that is not in place here (design §7.3). */
export interface ProjectPlugins {
  trusted: boolean;
  plugins: { id: string; scope: string; state: "missing" | "needs_consent" | "ready" | "not_loaded" }[];
  marketplaces: { name: string; source: string; file: string; known: boolean }[];
  pending: number;
}

export interface CommandRow {
  name: string;
  description: string;
  argument_hint: string;
  source: "plugin" | "user" | "project" | "compat";
  plugin?: string;
  allowed_tools?: string[];
}

function scoped(path: string, opts: { session?: string; workspace?: string }): string {
  const qs = new URLSearchParams();
  if (opts.session) qs.set("session", opts.session);
  if (opts.workspace) qs.set("workspace", opts.workspace);
  const q = qs.toString();
  return `${httpBase()}${path}${q ? `?${q}` : ""}`;
}

/** JSON, or an Error carrying the server's own sentence (a 400's `detail`). */
async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body as { detail?: string }).detail || `HTTP ${res.status}`);
  return body as T;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});

export async function listPlugins(opts: { session?: string; workspace?: string } = {}): Promise<PluginsView> {
  const body = await call<Partial<PluginsView>>(scoped("/v1/plugins", opts));
  return { plugins: body.plugins ?? [], errors: body.errors ?? [], installed: body.installed ?? [] };
}

export async function projectPlugins(opts: { session?: string; workspace?: string } = {}): Promise<ProjectPlugins> {
  const body = await call<Partial<ProjectPlugins>>(scoped("/v1/plugins/project", opts));
  return { trusted: !!body.trusted, plugins: body.plugins ?? [], marketplaces: body.marketplaces ?? [], pending: body.pending ?? 0 };
}

export const stageInstall = (id: string, scope = "user", workspace?: string) =>
  call<Staged>(`${httpBase()}/v1/plugins/install`, json("POST", { id, scope, ...(workspace ? { workspace } : {}) }));

export const confirmInstall = (token: string, userConfig: Record<string, unknown> = {}) =>
  call<{ id: string; version: string; scope: string }>(
    `${httpBase()}/v1/plugins/install/${encodeURIComponent(token)}/confirm`,
    json("POST", { user_config: userConfig }),
  );

export const cancelInstall = (token: string) =>
  call<{ ok: boolean }>(`${httpBase()}/v1/plugins/install/${encodeURIComponent(token)}`, json("DELETE"));

export const setPluginEnabled = (id: string, enabled: boolean, scope = "user", workspace?: string) =>
  call<{ ok: boolean }>(
    `${httpBase()}/v1/plugins/${encodeURIComponent(id)}`,
    json("PATCH", { enabled, scope, ...(workspace ? { workspace } : {}) }),
  );

/** Its current code for a yes: changed since the last one, or new to this workspace. */
export const stageConsent = (id: string, workspace?: string) =>
  call<Staged>(
    `${httpBase()}/v1/plugins/${encodeURIComponent(id)}/consent`,
    json("POST", workspace ? { workspace } : {}),
  );

/** Keep using a plugin its marketplace withdrew. */
export const keepPlugin = (id: string) =>
  call<{ id: string; kept: boolean }>(`${httpBase()}/v1/plugins/${encodeURIComponent(id)}/keep`, json("POST"));

export const setMarketplaceAutoUpdate = (name: string, autoUpdate: boolean) =>
  call<{ name: string; autoUpdate: boolean }>(
    `${httpBase()}/v1/plugin-marketplaces/${encodeURIComponent(name)}`,
    json("PATCH", { autoUpdate }),
  );

export const stageUpdate = (id: string) =>
  call<Staged | { up_to_date: true; version: string }>(
    `${httpBase()}/v1/plugins/${encodeURIComponent(id)}/update`,
    json("POST"),
  );

export const uninstallPlugin = (id: string, keepData = false) =>
  call<{ id: string; data_removed: boolean }>(
    `${httpBase()}/v1/plugins/${encodeURIComponent(id)}?keep_data=${keepData ? "true" : "false"}`,
    json("DELETE"),
  );

export const listMarketplaces = async (): Promise<Marketplace[]> =>
  (await call<{ marketplaces?: Marketplace[] }>(`${httpBase()}/v1/plugin-marketplaces`)).marketplaces ?? [];

export const addMarketplace = (source: string) =>
  call<{ name: string; plugins: number }>(`${httpBase()}/v1/plugin-marketplaces`, json("POST", { source }));

export const refreshMarketplace = (name: string) =>
  call<{ name: string; plugins: number }>(
    `${httpBase()}/v1/plugin-marketplaces/${encodeURIComponent(name)}/refresh`,
    json("POST"),
  );

export const removeMarketplace = (name: string) =>
  call<{ ok: boolean }>(`${httpBase()}/v1/plugin-marketplaces/${encodeURIComponent(name)}`, json("DELETE"));

export const marketplacePlugins = async (name: string): Promise<OfferedPlugin[]> =>
  (await call<{ plugins?: OfferedPlugin[] }>(`${httpBase()}/v1/plugin-marketplaces/${encodeURIComponent(name)}/plugins`))
    .plugins ?? [];

/** `/reload-plugins`: rebuild this conversation with the plugins as they are now. Between
 * turns only; every view of the session gets `plugins_reloaded` and reconnects. */
export const reloadSessionPlugins = (sessionId: string) =>
  call<{ ok: boolean; plugins?: number; errors?: number; views?: number; error?: string }>(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/reload-plugins`,
    json("POST"),
  );

export async function listCommands(opts: { session?: string; workspace?: string } = {}): Promise<CommandRow[]> {
  const res = await fetch(scoped("/v1/commands", opts));
  return (await res.json()).commands ?? [];
}

/** One command with its body — fetched when a person picks it, never with the list. */
export async function getCommand(
  name: string,
  opts: { session?: string; workspace?: string } = {},
): Promise<(CommandRow & { body: string }) | null> {
  const res = await fetch(scoped(`/v1/commands/${encodeURIComponent(name)}`, opts));
  if (!res.ok) return null;
  return res.json();
}
