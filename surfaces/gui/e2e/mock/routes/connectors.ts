import { CONNECTORS } from "../data";
import type { On } from "../router";
import type { MockState } from "../state";

// The connectors: Slack's people, rosters and user token; GitHub's tool groups and scopes; the
// accounts of Gmail, Calendar and Notion; the catalog; and the local sign-in, which finishes at
// once here, so the page's poll finds it done.

/** Disconnect and make-default for one connector's accounts. The next account becomes the
 *  default when the default one goes. */
function accountRoutes<A extends { default: boolean }>(on: On, connector: string, accounts: A[], idOf: (a: A) => string) {
  const base = `/v1/connectors/${connector}/accounts/([^/]+)`;
  on("POST", new RegExp(`${base}/disconnect$`), ({ match }) => {
    const i = accounts.findIndex((a) => idOf(a) === decodeURIComponent(match[1]));
    if (i < 0) return { ok: false, error: "account not connected" };
    const [gone] = accounts.splice(i, 1);
    if (gone.default && accounts[0]) accounts[0].default = true;
    return { ok: true, remaining_accounts: accounts.length };
  });
  on("POST", new RegExp(`${base}/default$`), ({ match }) => {
    const id = decodeURIComponent(match[1]);
    if (!accounts.some((a) => idOf(a) === id)) return { ok: false, error: "account not connected" };
    for (const a of accounts) a.default = idOf(a) === id;
    return { ok: true, default_account: id };
  });
}

/** Directory rows matching `q` on any of `fields`, or all of them with no `q`. */
const matching = <T>(rows: T[], q: string, fields: (row: T) => string[]) => rows.filter((row) => !q || fields(row).some((f) => f.includes(q)));

export function connectorRoutes(on: On, s: MockState) {
  const { parked, slackState, githubState, GITHUB_SETS, gmailState, gcalState, notionState, mcpState } = s;

  // A message from someone not allowed yet: allowing them adds them, dismissing does not;
  // either way the message leaves the list.
  on("POST", /\/v1\/connectors\/slack\/unauthorized\/([^/]+)$/, ({ match, body }) => {
    const i = parked.findIndex((message) => message.id === match[1]);
    if (i < 0) return { ok: false, error: "unknown item" };
    const [message] = parked.splice(i, 1);
    if ((body.action === "allow" || body.action === "allow_deliver") && !slackState.allowed_users.includes(message.user_id)) {
      slackState.allowed_users.push(message.user_id);
    }
    return { ok: true };
  });
  // The flat Socket Mode list of who may talk to Reeve; a pick from the directory brings a name.
  on("POST", /\/v1\/connectors\/slack\/(allow|disallow)$/, ({ match, body }) => {
    const allowed = slackState.allowed_users;
    const i = allowed.indexOf(body.user_id);
    if (match[1] === "allow") {
      if (i < 0) allowed.push(body.user_id);
      if (body.name) slackState.names[body.user_id] = body.name;
    } else if (i >= 0) allowed.splice(i, 1);
    return { ok: true, allowed_users: [...allowed], team_id: null };
  });
  // The workspace's people and channels for the pickers (users.list and conversations.list).
  on("GET", "/v1/connectors/slack/directory", ({ query }) => {
    const people = [
      { id: "U9MAYA", name: "Maya Chen", handle: "maya", guest: false },
      { id: "U8SAMOK", name: "Sam Okafor", handle: "sam", guest: false },
      { id: "U7CAL", name: "Contractor Cal", handle: "cal", guest: true },
    ];
    return { ok: true, members: matching(people, (query.get("q") || "").toLowerCase(), (p) => [p.name.toLowerCase(), p.handle]) };
  });
  on("GET", "/v1/connectors/slack/channels", ({ query }) => {
    const channels = [
      { id: "C9LAUNCH", name: "launch-team", is_private: false, is_member: true },
      { id: "C8LEADS", name: "leads", is_private: true, is_member: true },
      { id: "C7LOBBY", name: "lobby", is_private: false, is_member: false },
    ];
    return { ok: true, channels: matching(channels, (query.get("q") || "").toLowerCase(), (c) => [c.name]) };
  });
  // The user token (search, reminders, status), checked and kept for the workspace. An empty one removes it.
  on("POST", "/v1/connectors/slack/user-token", ({ body }) => {
    if (!body.token) {
      slackState.hasUserToken = false;
      return { ok: true, removed: true };
    }
    if (!String(body.token).startsWith("xoxp-")) return { ok: false, error: "a Slack user token starts with xoxp-" };
    slackState.hasUserToken = true;
    return { ok: true, user: "sam", team: "acme.example" };
  });

  // GitHub. Disconnected, its page shows what it offers before connecting. The groups chosen are
  // kept, and the reply names the scopes the token still lacks for them.
  const neededScopes = () => GITHUB_SETS.filter((set) => set.locked || githubState.enabled.includes(set.id)).flatMap((set) => set.needs);
  on("POST", "/v1/connectors/github/disconnect", () => {
    githubState.connected = false;
    return { ok: true };
  });
  on("POST", "/v1/connectors/github/toolsets", ({ body }) => {
    githubState.enabled = Array.isArray(body.enabled) ? body.enabled : [];
    return { ok: true, enabled: githubState.enabled, needs_scopes: neededScopes().filter((scope) => !githubState.granted.includes(scope)) };
  });

  accountRoutes(on, "gmail", gmailState.accounts, (a) => a.email);
  accountRoutes(on, "google_calendar", gcalState.accounts, (a) => a.email);
  // What Gmail never shows the agents: senders and labels.
  on("PATCH", "/v1/connectors/gmail/filters", ({ body }) => {
    if (Array.isArray(body.senders)) gmailState.filters.senders = body.senders;
    if (Array.isArray(body.labels)) gmailState.filters.labels = body.labels;
    return { ok: true, filters: { ...gmailState.filters } };
  });
  accountRoutes(on, "notion", notionState.accounts, (a) => a.account_id);

  // The catalog, each entry as its state has it now.
  const live: Record<string, () => unknown> = {
    gmail: s.gmailConnector, google_calendar: s.gcalConnector, notion: s.notionConnector, outlook: s.outlookConnector,
    monday: () => s.mcpConnector("monday"), jira: () => s.mcpConnector("jira"),
  };
  on("*", "/v1/connectors", () => ({
    connectors: [s.slackConnector(), s.githubConnector(), ...CONNECTORS.connectors.map((entry: any) => live[entry.name]?.() ?? { ...entry })],
  }));

  // The local sign-in. GitHub's asks again for every scope the chosen groups need ("Grant
  // access"); the accounts' connectors each add the next account, the first one the default.
  const nextEmail = (taken: number, queue: string[]) => queue[taken] || `acct${taken}@x.com`;
  const signIns: Record<string, () => unknown> = {
    monday: () => (mcpState.monday = true),
    jira: () => (mcpState.jira = true),
    gmail: () => gmailState.accounts.push({
      email: nextEmail(gmailState.accounts.length, s.GMAIL_NEXT), default: gmailState.accounts.length === 0, scopes: "gmail.readonly gmail.send", needs_reauth: false,
    }),
    google_calendar: () => gcalState.accounts.push({
      email: nextEmail(gcalState.accounts.length, s.GCAL_NEXT), default: gcalState.accounts.length === 0, scopes: "calendar", needs_reauth: false,
    }),
    notion: () => {
      const taken = notionState.accounts.length;
      notionState.accounts.push({ ...(s.NOTION_NEXT[taken] || { account_id: `ws-${taken + 1}`, name: "extra" }), default: taken === 0 });
    },
  };
  on("POST", /\/v1\/connectors\/([^/]+)\/mcp-connect$/, ({ match }) => {
    const name = match[1];
    if (name === "github") {
      githubState.granted = Array.from(new Set([...githubState.granted, ...neededScopes()]));
      githubState.connected = true;
      return {
        ok: true,
        device: { user_code: "ABCD-1234", verification_uri: "https://github.com/login/device", open_url: "https://github.com/login/device", needs_code_entry: true, expires_in: 900 },
      };
    }
    if (!signIns[name]) return { ok: false, error: `${name} has no MCP connect path` };
    signIns[name]();
    return { ok: true, started: true };
  });
}
