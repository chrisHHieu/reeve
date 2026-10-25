import { fetch, httpBase } from "./base";
import type { Connector } from "./connectors";

/** One-click connect for an MCP-backed connector (monday, asana, jira): the sidecar
 * opens the vendor's sign-in in the browser (local OAuth, no account needed);
 * poll getConnectors until the card flips to connected. */
/** A device grant in progress: what the user must do, and where. `needs_code_entry`
 *  is false only when the vendor pre-fills the code into the URL (GitHub does not). */
export interface DeviceCodePrompt {
  user_code: string;
  verification_uri: string;
  open_url: string;
  needs_code_entry: boolean;
  expires_in: number;
}

export async function connectMcpBacked(
  name: string,
  variant?: string,
): Promise<{ ok: boolean; error?: string; device?: DeviceCodePrompt }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(name)}/mcp-connect`,
    variant
      ? {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ variant }),
        }
      : { method: "POST" },
  );
  return res.json();
}

/** The sign-in page of the one-click connect that is waiting, so a page that never opened (or
 * was closed) can be opened again. Empty when no sign-in is waiting. */
export async function getConnectAuthorizeUrl(name: string): Promise<string> {
  try {
    const res = await fetch(`${httpBase()}/v1/mcp/${encodeURIComponent(name)}/authorize-url`);
    if (!res.ok) return "";
    const body = (await res.json()) as { authorizing?: boolean; authorize_url?: string };
    return body.authorizing ? body.authorize_url || "" : "";
  } catch {
    return "";
  }
}

export interface ConnectorTool {
  name: string;
  label: string;
  kind: "read" | "write" | string;
  description: string;
  enabled: boolean;
  requires_approval: boolean;
  /** A long catalog groups itself (read / write / admin); absent: a flat list. */
  group?: string;
}

export async function getConnectors(): Promise<Connector[]> {
  const res = await fetch(`${httpBase()}/v1/connectors`);
  return (await res.json()).connectors ?? [];
}

export async function connectConnector(
  name: string,
  fields: Record<string, string>,
): Promise<{ ok: boolean; account?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/connectors/${encodeURIComponent(name)}/connect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fields }),
  });
  return res.json();
}

// Linking a personal WhatsApp number: start offers a QR code, or the code to type
// for `phone`; the status says where it stands, with the QR drawn server-side.
export interface WhatsAppPairStatus {
  running: boolean;
  paired?: boolean;
  connected?: boolean;
  number?: string;
  history_chunks?: number;
  qr_svg?: string; // a data: URI
  qr_error?: string;
  error?: string;
}

export async function startWhatsAppPairing(
  acknowledged: boolean,
  phone = "",
): Promise<{ ok: boolean; code?: string; error?: string; risk_notice?: string }> {
  const res = await fetch(`${httpBase()}/v1/connectors/whatsapp_personal/pair`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ acknowledged, phone }),
  });
  return res.json();
}

export async function whatsAppPairStatus(): Promise<WhatsAppPairStatus> {
  const res = await fetch(`${httpBase()}/v1/connectors/whatsapp_personal/pair`);
  return res.json();
}

// What the linked device keeps on this computer: how much, how long (null keeps every
// message), and clearing it without unlinking.
export interface WhatsAppKept {
  messages: number;
  chats: number;
  oldest: number | null; // unix seconds
  bytes: number;
  keep_days: number | null;
  folder?: string;
}

type KeptAnswer = WhatsAppKept & { ok: boolean; error?: string };

export async function whatsAppKept(): Promise<WhatsAppKept> {
  const res = await fetch(`${httpBase()}/v1/connectors/whatsapp_personal/kept`);
  return res.json();
}

export async function setWhatsAppKeep(keepDays: number | null): Promise<KeptAnswer> {
  const res = await fetch(`${httpBase()}/v1/connectors/whatsapp_personal/kept`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keep_days: keepDays }),
  });
  return res.json();
}

export async function clearWhatsAppKept(): Promise<KeptAnswer> {
  const res = await fetch(`${httpBase()}/v1/connectors/whatsapp_personal/kept`, { method: "DELETE" });
  return res.json();
}

export async function disconnectConnector(name: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/connectors/${encodeURIComponent(name)}/disconnect`, {
    method: "POST",
  });
  return res.json();
}

export async function updateConnectorTools(
  name: string,
  enabled: Record<string, boolean>,
): Promise<{ ok: boolean; error?: string; tools?: Record<string, boolean> }> {
  const res = await fetch(`${httpBase()}/v1/connectors/${encodeURIComponent(name)}/tools`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  return res.json();
}

/** Add (or, with an empty token, remove) the optional Slack user token for search and reminders. */
export async function setSlackUserToken(
  token: string,
): Promise<{ ok: boolean; error?: string; user?: string; removed?: boolean }> {
  const res = await fetch(`${httpBase()}/v1/connectors/slack/user-token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  return res.json();
}

export interface ConnectorToolset {
  id: string;
  label: string;
  description: string;
  enabled: boolean;
  /** Served by the vendor regardless; the switch is shown but cannot turn it off. */
  locked: boolean;
  /** OAuth scopes the sign-in still lacks for this set (empty: nothing to grant). */
  needs_scopes: string[];
}

/** Choose which of the vendor server's tool groups a connection serves (GitHub). */
export async function setConnectorToolsets(
  name: string,
  enabled: string[],
): Promise<{ ok: boolean; error?: string; enabled?: string[]; needs_scopes?: string[] }> {
  const res = await fetch(`${httpBase()}/v1/connectors/${encodeURIComponent(name)}/toolsets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  return res.json();
}

export interface AuditEvent {
  id: number;
  timestamp: string;
  session_id: string;
  agent: string;
  workspace: string;
  connector: string;
  tool: string;
  stage: string;
  status: string;
  approval: string;
  args: Record<string, any>;
  result_preview: string;
  reason: string;
  resource: string;
}

export async function getAudit(params: {
  limit?: number;
  session_id?: string;
  connector?: string;
  tool?: string;
} = {}): Promise<AuditEvent[]> {
  const q = new URLSearchParams();
  if (params.limit) q.set("limit", String(params.limit));
  if (params.session_id) q.set("session_id", params.session_id);
  if (params.connector) q.set("connector", params.connector);
  if (params.tool) q.set("tool", params.tool);
  const res = await fetch(`${httpBase()}/v1/audit${q.toString() ? "?" + q.toString() : ""}`);
  return (await res.json()).events ?? [];
}

export interface BrowserState {
  open: boolean;
  url: string;
  title: string;
  status: string;
  last_action: string;
  last_result: string;
  last_error: string;
  screenshot_data_url: string;
  updated_at: string | null;
  controls: any[];
}

export async function getBrowserState(): Promise<BrowserState> {
  const res = await fetch(`${httpBase()}/v1/browser/state`);
  return res.json();
}

export async function takeBrowserScreenshot(): Promise<BrowserState & { ok?: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/browser/screenshot`, { method: "POST" });
  return res.json();
}

export async function closeBrowser(): Promise<{ ok?: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/browser/close`, { method: "POST" });
  return res.json();
}
