import { fetch, httpBase } from "./base";

// -- Per-session connections (Sources bar + drawer) -----------------------
// An effective-enabled source for a session, with a short human detail (e.g. "#reeve-test · DMs",
// "3 tools"). `enabled` reflects the session override or the default so the drawer toggle shows
// correct state.
export interface SessionConnectedConnector {
  connector: string;
  enabled: boolean;
  detail: string;
  // "connector" (the built-in registry) or "mcp" (a server from mcp.json). MCP rows were
  // missing entirely until 2026-08-26, so the panel claimed a session could touch less than
  // it could. `manageable: false` says there is no per-session toggle for this row — muting
  // an MCP server is a global act, and a switch that pretended otherwise would be the next
  // lying surface. Optional: an older server sends neither field.
  kind?: "connector" | "mcp";
  manageable?: boolean;
}

// A recommended connector not yet connected (drives the `⚠ N` attention count).
export interface SessionRecommendedConnector {
  connector: string;
  reason: string;
  tier: string;
  connected: boolean;
}

export interface SessionConnections {
  connected: SessionConnectedConnector[];
  recommended: SessionRecommendedConnector[];
  attention: number; // ⚠ count = recommended connectors not yet connected
}

export async function getSessionConnections(sessionId: string): Promise<SessionConnections> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/connections`);
  return res.json();
}

/**
 * Set a per-session connection override (mute/unmute a connector for THIS session). Pass
 * `clear: true` to drop the override and inherit the default again.
 */
export async function setSessionConnection(
  sessionId: string,
  connector: string,
  enabled: boolean,
  clear = false,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/connections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ connector, enabled, ...(clear ? { clear: true } : {}) }),
  });
  return res.json();
}
