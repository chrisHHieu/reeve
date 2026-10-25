import { OPS_METRICS } from "../data";
import type { On } from "../router";
import type { MockState } from "../state";

// The rest of the server: onboarding's flag, Metrics, the language servers, the MCP servers the
// person adds, and channel subscriptions.

export function serverRoutes(on: On, s: MockState) {
  const { mcpServers, subscriptions } = s;

  on("POST", "/v1/settings/onboarded", ({ body }) => ({ ok: true, onboarded: !!body.value }));
  // One payload whatever the range: the specs look at the page, and the store's own tests pin
  // how runs are bucketed.
  on("*", "/v1/metrics", () => OPS_METRICS);
  // No language server is running: they start on the first `lsp` call, so the Status tab shows
  // its empty state.
  on("*", "/v1/lsp", () => ({ enabled: true, servers: [], known_languages: ["python", "typescript", "go", "rust"] }));

  // MCP servers added by URL, through their sign-in: added, it needs signing in; connecting, it
  // is authorizing; one read later it is connected with six tools. One named locked-* is a
  // guarded server: its anonymous probe is refused until the entry is switched to oauth.
  // `_flip` marks a server that has been read once while authorizing; the list never shows it.
  on("GET", "/v1/mcp", () => {
    for (const server of mcpServers) {
      if (server.status === "authorizing" && server._flip) {
        if (server.name.startsWith("locked") && server.auth !== "oauth") {
          Object.assign(server, { status: "error", auth_hint: true, last_error: "authentication required — sign in to connect" });
        } else {
          // The probe that connects it also dates it.
          Object.assign(server, { status: "connected", tool_count: 6, last_test_at: 1700000000 });
        }
      }
      if (server.status === "authorizing") server._flip = true;
    }
    return { servers: mcpServers.map(({ _flip, ...shown }) => shown) };
  });
  on("POST", "/v1/mcp", ({ body }) => {
    const oauth = body.config?.auth === "oauth";
    mcpServers.push({
      name: body.name, enabled: true, transport: body.config?.url ? "http" : "stdio", requires_approval: true,
      auth: oauth ? "oauth" : null, status: oauth ? "needs_auth" : "configured",
      auth_hint: false, last_test_at: null, last_error: null, tool_count: null, config: body.config || {},
    });
    return { ok: true, name: body.name };
  });
  const server = (match: RegExpMatchArray) => mcpServers.find((row) => row.name === decodeURIComponent(match[1]));
  on("POST", /\/v1\/mcp\/([^/]+)\/connect$/, ({ match }) => {
    const row = server(match);
    if (row) Object.assign(row, { status: "authorizing", auth_hint: false, last_error: null, _flip: false });
    return { ok: true, started: true };
  });
  on("POST", /\/v1\/mcp\/([^/]+)\/signout$/, ({ match }) => {
    const row = server(match);
    if (row) Object.assign(row, { status: "needs_auth", tool_count: null, _flip: false });
    return { ok: true };
  });
  // Switching a server to oauth is the fix for one that needs signing in: the connect after it
  // runs the browser flow.
  on("PATCH", /\/v1\/mcp\/([^/]+)$/, ({ match, body }) => {
    const row = server(match);
    if (row) {
      if (body.enabled !== undefined) row.enabled = body.enabled;
      if (body.auth === "oauth") Object.assign(row, { auth: "oauth", auth_hint: false, status: "needs_auth" });
      row.config = { ...row.config, ...body };
    }
    return { ok: !!row, name: match[1] };
  });
  on("DELETE", /\/v1\/mcp\/([^/]+)$/, ({ match }) => {
    const i = mcpServers.findIndex((row) => row.name === decodeURIComponent(match[1]));
    if (i >= 0) mcpServers.splice(i, 1);
    return { ok: i >= 0 };
  });
  on("*", "/v1/unrouted", () => []);

  // Subscriptions, resolved as resolve_channel does: a Copy-link URL gives the channel's id, and
  // a bare #name is refused with the server's hint, since a name cannot be looked up.
  on("GET", "/v1/subscriptions", () => ({ subscriptions }));
  on("POST", "/v1/subscriptions", ({ body }) => {
    const raw = String(body.channel || "").trim();
    if (raw.startsWith("#")) {
      return { ok: false, error: "Channel names can't be looked up — paste the channel ID (channel name ▸ About) or the channel's Copy-link URL." };
    }
    const link = raw.match(/slack\.com\/archives\/([A-Za-z0-9]+)/);
    const channel = link ? `slack:${link[1].toUpperCase()}` : raw;
    subscriptions.push({ session_id: body.session_id, session_title: "", agent: "", channel });
    return { ok: true, channel };
  });
  on("POST", "/v1/subscriptions/remove", ({ body }) => {
    const i = subscriptions.findIndex((row) => row.session_id === body.session_id && row.channel === body.channel);
    if (i >= 0) subscriptions.splice(i, 1);
    return { ok: true };
  });
}
