import { fetch, httpBase } from "./base";

// -- MCP servers --------------------------------------------------------------
export interface McpServer {
  name: string;
  enabled: boolean;
  // Ships with the app (browser / computer-use): arms without config; a config entry
  // of the same name overrides or disables it.
  builtin?: boolean;
  transport: string;
  requires_approval: boolean;
  // "connected" | "configured" | "disabled" | and for auth:"oauth" servers:
  // "needs_auth" (no tokens yet) | "authorizing" (browser sign-in in flight)
  status: string;
  auth?: "oauth" | null;
  // http server whose anonymous connect hit a 401/403 — offer OAuth sign-in.
  auth_hint?: boolean;
  // Epoch seconds of the last successful explicit Test (persisted server-side).
  last_test_at?: number | null;
  last_error?: string | null;
  tool_count: number | null;
  config: Record<string, any>;
}

export async function getMcpServers(): Promise<McpServer[]> {
  const res = await fetch(`${httpBase()}/v1/mcp`);
  return (await res.json()).servers ?? [];
}

export async function addMcpServer(name: string, config: Record<string, any>) {
  const res = await fetch(`${httpBase()}/v1/mcp`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, config }),
  });
  return res.json();
}

export async function patchMcpServer(name: string, changes: Record<string, any>) {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  return res.json();
}

export async function deleteMcpServer(name: string) {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}`, { method: "DELETE" });
  return res.json();
}

export async function getMcpTools(
  name: string,
): Promise<{ ok: boolean; error?: string; tools: { name: string; description: string }[] }> {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}/tools`);
  return res.json();
}

export async function reloadMcp() {
  const res = await fetch(`${httpBase()}/v1/mcp/reload`, { method: "POST" });
  return res.json();
}

/** Connect one MCP server now. For OAuth servers this opens the system browser;
 * poll getMcpServers() for the status flip (authorizing → connected / needs_auth). */
export async function connectMcp(name: string): Promise<{ ok: boolean; started?: boolean }> {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}/connect`, {
    method: "POST",
  });
  return res.json();
}

/** Drop the connection and forget the stored OAuth tokens. */
export async function signoutMcp(name: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}/signout`, {
    method: "POST",
  });
  return res.json();
}

// -- the built-in `browser` server (Playwright) ----------------------------------

export interface BrowserConfig {
  ok: boolean;
  error?: string;
  // Always "playwright": the one browser engine since 2026-09-30.
  backend: string;
  // "isolated" (private browser per session) | "attach" (drive a running browser).
  mode: string;
  endpoint: string;
  headless: boolean;
  connected: boolean;
  tool_count: number | null;
  // The exact command the current choice runs, for the detail line.
  launch: string;
}

export async function getBrowserConfig(): Promise<BrowserConfig> {
  const res = await fetch(`${httpBase()}/v1/browser/config`);
  return res.json();
}

export async function setBrowserConfig(
  changes: Record<string, any>,
): Promise<BrowserConfig> {
  const res = await fetch(`${httpBase()}/v1/browser/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  return res.json();
}
