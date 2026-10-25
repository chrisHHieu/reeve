import type { Page } from "@playwright/test";
import { AUTOMATION, AUTOMATION_CLEAN, AUTOMATION_RUNS, CONNECTIONS, CONNECTORS, EXTRA_SESSIONS, INBOX_ITEMS, OPS_SESSION, PINNED_SESSION, PRIMARY_ROOT, PROVIDERS, SLACK_SESSION } from "./data";

// What one page's mock remembers from one request to the next, copied from data.ts for each page:
// a test's changes reach the next read through the real UI and never another test. The two values
// the routes replace outright live under `staged`, so every module sees the same one.

type MailAccount = { email: string; default: boolean; scopes: string; needs_reauth: boolean };
type NamedAccount = { account_id: string; name: string; default: boolean };

/** A copy of one catalog entry, with `over` laid on it. */
function catalogCopy(name: string, over: Record<string, unknown>) {
  return { ...CONNECTORS.connectors.find((entry: any) => entry.name === name), ...over };
}

/** A connector whose accounts the local sign-in adds at once (gmail:account:<email> and the like):
 *  connected while it has one, the default account's `label` shown as its account. */
function signedInAccounts<A extends { default: boolean }>(accounts: A[], label: (a: A) => string) {
  return {
    connected: accounts.length > 0,
    enabled: accounts.length > 0,
    account: accounts.find((a) => a.default) ? label(accounts.find((a) => a.default)!) : null,
    accounts: accounts.map((a) => ({ ...a })),
  };
}

export function createMockState(page: Page) {
  const webSearchState = { mode: "auto" as "auto" | "duckduckgo", hasKey: false };
  const sidebarState: { organize?: string } = {};

  // Slack, in Socket Mode on one workspace. A session already listens on one channel (the
  // Listening row), and one message waits from someone not yet allowed, which Allow and
  // Dismiss act on.
  const subscriptions: any[] = [
    { session_id: "wp-1", session_title: "Weekly plan 1", agent: "reeve", channel: "slack:C0AAA111", channel_name: "reeve-test" },
  ];
  const parked: any[] = [
    {
      id: "pk1", platform: "slack", chat_id: "C0AAA111", chat_name: "#reeve-test", user_id: "U0NEW", user_name: "Maya",
      chat_type: "channel", text: "hey reeve, can you summarize this thread?", ts: Date.now() / 1000 - 120,
    },
  ];
  const slackState = {
    connected: true,
    account: "acme.example",
    allowed_users: ["U_ME"] as string[],
    // The names of the allowed, as the server keeps them (a pick from the directory sends the
    // name along) and serves them as `allowed_user_names`. Without them the people picker's
    // spec only passed when it read a name off the picker's list before the list closed.
    names: {} as Record<string, string>,
    hasUserToken: false,
  };
  const slackConnector = () => ({
    name: "slack", title: "Slack", icon: "#", blurb: "Two-way Slack messaging.", auth: "socket_app",
    two_way: true, channels: true, available: true, brand_color: "#611f69", logo: "slack", fields: [], instructions: [],
    connected: slackState.connected, account: slackState.account, enabled: slackState.connected,
    allowed_users: [...slackState.allowed_users],
    allowed_user_names: Object.fromEntries(slackState.allowed_users.map((id) => [id, slackState.names[id] ?? null])),
    tools: [], mode: "", has_user_token: slackState.hasUserToken,
    unauthorized: parked.map((message) => ({ ...message })),
  });

  // GitHub, on its own MCP server, with groups of tools to switch. A group needing a
  // scope the sign-in was not granted says so, and offers to grant it.
  const GITHUB_SETS = [
    { id: "repos", label: "Repositories", description: "Files.", locked: true, needs: [] as string[] },
    { id: "issues", label: "Issues", description: "Issues.", locked: true, needs: [] as string[] },
    { id: "actions", label: "Actions", description: "Runs and logs.", locked: false, needs: [] as string[] },
    { id: "gists", label: "Gists", description: "Read and write gists.", locked: false, needs: ["gist"] },
    { id: "notifications", label: "Notifications", description: "Read and dismiss notifications.", locked: false, needs: ["notifications"] },
  ];
  const githubState = { connected: true, granted: ["repo", "read:org"] as string[], enabled: ["repos", "issues", "actions"] as string[] };
  const githubTool = (name: string, label: string, description: string) =>
    ({ name, label, kind: "write", description, enabled: true, requires_approval: true });
  const githubConnector = () => ({
    name: "github", title: "GitHub", icon: "⌘", blurb: "Work with issues, pull requests, repository files, and CI status.",
    auth: "token", two_way: false, channels: false, available: true, brand_color: "#1f2328", logo: "github", one_click: true,
    fields: [{ key: "token", label: "Personal access token", secret: true, required: true, help: "", placeholder: "" }],
    instructions: [], connected: githubState.connected, account: null, enabled: githubState.connected, allowed_users: [], mode: "mcp",
    tools: [githubTool("github_clone", "Clone a repo", "Clone a repository."), githubTool("github_pull", "Update a clone", "Fast-forward a clone.")],
    toolsets: GITHUB_SETS.map((set) => ({
      id: set.id, label: set.label, description: set.description, locked: set.locked,
      enabled: set.locked || githubState.enabled.includes(set.id),
      needs_scopes: set.needs.filter((scope) => !githubState.granted.includes(scope)),
    })),
  });

  // The accounts' connectors start with none. Gmail also keeps the filters of what it reads.
  const gmailState = { accounts: [] as MailAccount[], filters: { senders: [] as string[], labels: [] as string[] } };
  const gcalState = { accounts: [] as MailAccount[] };
  const notionState = { accounts: [] as NamedAccount[] };
  const outlookState = { accounts: [] as NamedAccount[] };
  // Who the next sign-in turns out to be.
  const GMAIL_NEXT = ["sam@gmail.com", "work@acme.example", "third@x.com"];
  const GCAL_NEXT = [...GMAIL_NEXT];
  const NOTION_NEXT = [
    { account_id: "ws-1", name: "Sam's Workspace" },
    { account_id: "ws-2", name: "Ops Space" },
  ];
  const gmailConnector = () => catalogCopy("gmail", {
    ...signedInAccounts(gmailState.accounts, (a) => a.email),
    filters: { senders: [...gmailState.filters.senders], labels: [...gmailState.filters.labels] },
  });
  const gcalConnector = () => catalogCopy("google_calendar", signedInAccounts(gcalState.accounts, (a) => a.email));
  const notionConnector = () => catalogCopy("notion", signedInAccounts(notionState.accounts, (a) => a.name));
  const outlookConnector = () => catalogCopy("outlook", signedInAccounts(outlookState.accounts, (a) => a.name));
  // The vendor-MCP connectors. Their browser sign-in finishes at once, for the dialog's
  // poll to find.
  const mcpState = { monday: false, jira: false };
  const mcpConnector = (name: "monday" | "jira") =>
    catalogCopy(name, { connected: mcpState[name], enabled: mcpState[name], mode: mcpState[name] ? "mcp" : "" });

  // The lists the app changes: a session archived, renamed or deleted, an inbox item answered, a
  // folder added or made read-only (a POST of a path already there replaces it), a provider's key
  // saved (verifying one changes nothing), an automation run, switched or deleted.
  const sessions: any[] = [PINNED_SESSION, ...EXTRA_SESSIONS, OPS_SESSION, SLACK_SESSION].map((row) => ({ ...row }));
  const inbox: any[] = INBOX_ITEMS.map((item) => ({ ...item }));
  const roots: any[] = [{ ...PRIMARY_ROOT }];
  const providers: any[] = PROVIDERS.map((row) => ({ ...row }));
  const automations: any[] = [{ ...AUTOMATION }, { ...AUTOMATION_CLEAN }];
  const automationRuns: any[] = AUTOMATION_RUNS.map((run) => ({ ...run }));
  // MCP servers the person added; none until a spec adds one by URL.
  const mcpServers: any[] = [];
  // The session's connections, so muting one in Access is this test's alone.
  const connections = {
    connected: CONNECTIONS.connected.map((row) => ({ ...row })),
    recommended: CONNECTIONS.recommended.map((row) => ({ ...row })),
    attention: CONNECTIONS.attention,
  };
  // The project menu: which session is bound to which project, and the projects memory knows.
  const projectBindings: Record<string, string> = {};
  const projectNames: Record<string, { name: string; key: string }[]> = {
    memory: [{ name: "reeve", key: "/k/reeve" }, { name: "personal-ops", key: "/k/ops" }],
  };

  // Review's source control. The folder is not a repository until a scenario makes it
  // one ("edit the upload client"), so no other spec's layout changes.
  const scmState: {
    git: boolean;
    files: { path: string; status: string; add: number; del: number; staged: boolean }[];
    commits: { hash: string; short: string; author: string; when: string; subject: string; refs: string[] }[];
    hunks: { path: string; index: number; header: string; action: string }[];
    ahead: number;
    behind: number;
    branch: string;
    branches: string[];
    pr: any | null;
  } = { git: false, files: [], commits: [], hunks: [], ahead: 1, behind: 1, branch: "main", branches: ["main", "feature/statements"], pr: null };
  // The session's goal: the bar's pause, resume, edit and clear go through its route, and the
  // scripted agent pushes `goal` and `goal_activation` frames for the live side.
  const goalState: { goal: any | null } = { goal: null };
  // Sharing has an endpoint configured, so uploading can be tested; a spec that plays
  // "no endpoint" answers the GET itself.
  const shareState: { configured: boolean; share: any | null } = { configured: true, share: null };

  // Skills, mirroring their folders: Settings' edits, the enabled switch, an upload's
  // stage-preview-confirm and the composer's menu all read and change this one list. A
  // community install keeps its lockfile here too.
  const skill = (name: string, description: string, instructions: string, source: string, files: number) =>
    ({ name, description, instructions, scope: "global", source, enabled: true, path: `/state/skills/${name}`, files });
  const skills: any[] = [
    skill("weekly-report", "Monday status report", "1. Collect updates\n2. Write it up", "local", 0),
    skill("html-to-markdown", "Convert an HTML document or fragment to clean markdown.", "Convert the given HTML to markdown, preserving structure.", "uploaded", 2),
  ];
  const skillInstalls: Record<string, any> = {};

  return {
    page,
    staged: { stagedSkill: null as any, stagedInstall: null as any },
    webSearchState, sidebarState, subscriptions, parked, slackState, slackConnector,
    GITHUB_SETS, githubState, githubConnector,
    gmailState, GMAIL_NEXT, gmailConnector, gcalState, GCAL_NEXT, gcalConnector,
    notionState, NOTION_NEXT, notionConnector, outlookState, outlookConnector, mcpState, mcpConnector,
    sessions, inbox, roots, providers, automations, automationRuns, mcpServers, connections, projectBindings, projectNames,
    scmState, goalState, shareState, skills, skillInstalls,
  };
}

export type MockState = ReturnType<typeof createMockState>;
