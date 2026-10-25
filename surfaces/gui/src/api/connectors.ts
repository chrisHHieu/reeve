import type { ConnectorTool, ConnectorToolset } from "./cloud";
import type { RecentSender } from "./super-agent";

// -- connectors ---------------------------------------------------------------
export interface ConnectorField {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string;
  placeholder: string;
}

// One endpoint of a multi-endpoint vendor MCP server. `summary` is what the user is
// actually choosing between (how many tools, and whether any of them delete), so it is
// shown next to the option rather than hidden behind a tooltip.
export interface ConnectorVariant {
  key: string;
  label: string;
  summary: string;
}

// A message from a sender not (yet) on the allow-list — parked instead of dropped.
export interface ParkedMessage {
  id: string;
  platform: string;
  chat_id: string;
  chat_name: string | null;
  user_id: string;
  user_name: string | null;
  chat_type: string;
  text: string;
  ts: number;
}


// One connected Google account (multi-account: `gmail:account:<email>` /
// `google_calendar:account:<email>` profiles — same shape for both).
export interface GmailAccount {
  email: string;
  default: boolean;
  scopes: string;
  needs_reauth: boolean;
}

// "Never show agents" — enforced locally in the tool layer; agents see silent
// omissions, the user sees counts on tool cards + Activity rows.
export interface GmailFilters {
  senders: string[];
  labels: string[];
}

// One account of a generic multi-account connector (`<name>:account:<id>`
// profiles — Notion workspaces, PostHog projects, …). Gmail/Calendar predate
// the generic layer and keep their email-keyed shape above.
export interface AccountRow {
  account_id: string;
  name: string; // display identity captured at connect (workspace name, email, …)
  default: boolean;
}

export interface Connector {
  name: string;
  title: string;
  icon: string;
  blurb: string;
  // Pre-connect detail page copy: optional About paragraph
  // (empty → group omitted) + honest Access bullets.
  about?: string;
  access?: string[];
  auth: string;
  two_way: boolean;
  // Chat-platform capability, narrower than two_way: sessions can subscribe to channels.
  channels: boolean;
  available: boolean;
  fields: ConnectorField[];
  instructions: string[];
  connected: boolean;
  account: string | null;
  enabled: boolean;
  brand_color: string; // hex brand color, e.g. "#611f69" (fallback gray "#6b7280")
  logo: string; // stable logo id keyed into the frontend registry (empty → fallback glyph)
  aliases?: string[]; // extra typeahead terms ("calendar" surfaces Outlook)
  // Local browser one-click: a vendor-hosted MCP server, or hand-written tools whose
  // sign-in is still a local browser flow.
  one_click?: boolean;
  // Vendors that publish several endpoints of the same MCP server (Postman: Essentials /
  // Full / Code generation). The user picks one before connecting; `mcp_variant` is the
  // key in force. Only keys travel — the URLs stay server-side, so the descriptor remains
  // the one place that decides what a key means.
  mcp_variants?: ConnectorVariant[];
  mcp_variant?: string;
  // Why a one-click sign-in did not finish, and whether one is still in flight. The MCP
  // tab hides connector-backed servers, so without these the connect dialog has no way
  // to tell "still waiting on the browser" from "already failed".
  mcp_error?: string;
  mcp_authorizing?: boolean;
  // The vendor MCP server's tool groups (GitHub), only for a connection that is on the server.
  toolsets?: ConnectorToolset[];
  allowed_users: string[]; // the allow-list (managed inline in the Connectors tab)
  allowed_user_names?: Record<string, string | null>; // id → display name (people directory)
  recent?: RecentSender[]; // recently-seen senders on a connected two-way connector
  unauthorized?: ParkedMessage[]; // parked messages from unallowed senders
  tools: ConnectorTool[];
  mode?: string; // "mcp" once served by the vendor MCP server; "" for manual/token connect
  has_user_token?: boolean; // Slack only: the optional user token (search, reminders) is set
  // Experimental connectors (shown only once the person opts in): the downside, said before
  // anything connects. Connecting needs it acknowledged.
  experimental?: boolean;
  risk_notice?: string;
  // Gmail/Calendar: email-keyed rows; generic account connectors (notion,
  // attio, posthog, …): AccountRow. The detail pages narrow by connector.
  accounts?: GmailAccount[] | AccountRow[];
  filters?: GmailFilters; // Gmail only: "Never show agents" senders/labels
}
