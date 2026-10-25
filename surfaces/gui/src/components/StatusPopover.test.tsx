// The status popover's behaviour worth pinning: the trigger pip tells the truth about the
// engine and the services (error beats warning), the head is the verdict, what needs a
// person comes first whatever kind it is, with the action its state calls for and the raw
// message behind Details, what works is groups that open, and the last good snapshot
// survives when the engine drops.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

vi.mock("../api", () => ({
  getMcpServers: vi.fn(async () => []),
  getConnectors: vi.fn(async () => []),
  listSkills: vi.fn(async () => ({ skills: [], installs: {} })),
  getLspStatus: vi.fn(async () => ({
    enabled: true,
    servers: [],
    known_languages: ["python", "typescript"],
  })),
  connectMcp: vi.fn(async () => ({ ok: true, started: true })),
  reloadMcp: vi.fn(async () => ({ ok: true })),
}));

import {
  connectMcp,
  getConnectors,
  getLspStatus,
  getMcpServers,
  listSkills,
  reloadMcp,
  type Connector,
  type McpServer,
  type SkillRow,
} from "../api";
import { StatusPopover } from "./StatusPopover";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// `clearAllMocks` keeps mockResolvedValue overrides alive, so each test starts from the
// all-clear baseline explicitly — a leaked "Slack reconnecting" from a previous test would
// silently flip every later pip to warn.
beforeEach(() => {
  vi.mocked(getMcpServers).mockResolvedValue([]);
  vi.mocked(getConnectors).mockResolvedValue([]);
  vi.mocked(listSkills).mockResolvedValue({ skills: [], installs: {} });
  vi.mocked(getLspStatus).mockResolvedValue({
    enabled: true,
    servers: [],
    known_languages: ["python", "typescript"],
  });
});

function mcp(overrides: Partial<McpServer>): McpServer {
  return {
    name: "server",
    enabled: true,
    transport: "stdio",
    requires_approval: false,
    status: "connected",
    tool_count: 3,
    config: {},
    ...overrides,
  };
}

function connector(overrides: Partial<Connector>): Connector {
  return {
    name: "notion",
    title: "Notion",
    icon: "",
    blurb: "",
    auth: "oauth",
    two_way: false,
    channels: false,
    available: true,
    fields: [],
    instructions: [],
    connected: true,
    account: null,
    enabled: true,
    brand_color: "#6b7280",
    logo: "",
    allowed_users: [],
    tools: [],
    ...overrides,
  };
}

function skill(name: string, overrides: Partial<SkillRow> = {}): SkillRow {
  return {
    name,
    description: "",
    instructions: "",
    scope: "project",
    source: "local",
    enabled: true,
    path: `/w/skills/${name}`,
    ...overrides,
  };
}

function renderPopover(overrides: { connected?: boolean } = {}) {
  const onOpenConnectors = vi.fn();
  const onOpenSkills = vi.fn();
  render(
    <StatusPopover
      connected={overrides.connected ?? true}
      model="gpt-5.6-sol"
      onOpenConnectors={onOpenConnectors}
      onOpenSkills={onOpenSkills}
    />,
  );
  return { onOpenConnectors, onOpenSkills };
}

async function open() {
  fireEvent.click(screen.getByTestId("status-trigger"));
  await screen.findByTestId("status-popover");
}

const openGroup = (id: string) => fireEvent.click(screen.getByTestId(`status-group-toggle-${id}`));

describe("StatusPopover", () => {
  it("wears no pip when everything known is ready, and says so in one line", async () => {
    vi.mocked(getMcpServers).mockResolvedValue([mcp({ name: "browser", status: "connected" })]);
    renderPopover();
    await waitFor(() => expect(getMcpServers).toHaveBeenCalled());
    expect(screen.queryByTestId("status-trigger-dot")).toBeNull();
    await open();
    await waitFor(() => expect(screen.getByTestId("status-head").textContent).toBe("Everything is working"));
    // Plain words, and the model it is connected to.
    const popover = screen.getByTestId("status-popover");
    expect(popover.textContent).toContain("gpt-5.6-sol");
    expect(popover.textContent).not.toContain("Sidecar");
    // Nothing is wrong, so there is no list of what is.
    expect(screen.queryByTestId("status-problems")).toBeNull();
  });

  it("with nothing attached it says what the agent can still do, and where to add something", async () => {
    vi.mocked(getLspStatus).mockResolvedValue({ enabled: true, servers: [], known_languages: [] });
    const { onOpenConnectors } = renderPopover();
    await open();
    const empty = await screen.findByTestId("status-empty");
    expect(screen.getByTestId("status-head").textContent).toBe("Connected");
    fireEvent.click(within(empty).getByText("Browse connectors"));
    expect(onOpenConnectors).toHaveBeenCalled();
  });

  it("reds the pip when the engine is down, and keeps the popover openable", async () => {
    renderPopover({ connected: false });
    const dot = await screen.findByTestId("status-trigger-dot");
    expect(dot.dataset.level).toBe("err");
    await open();
    expect(screen.getByTestId("status-head").textContent).toBe("Not connected");
    expect(screen.getByTestId("status-banner").textContent).toContain("Not connected");
  });

  it("ambers for needs-sign-in and errors beat warnings", async () => {
    vi.mocked(getMcpServers).mockResolvedValue([
      mcp({ name: "linear", status: "needs_auth", auth: "oauth", tool_count: 0 }),
    ]);
    renderPopover();
    await waitFor(() =>
      expect(screen.getByTestId("status-trigger-dot").dataset.level).toBe("warn"),
    );

    vi.mocked(getMcpServers).mockResolvedValue([
      mcp({ name: "linear", status: "needs_auth", auth: "oauth", tool_count: 0 }),
      mcp({ name: "github", status: "error", last_error: "connection refused" }),
    ]);
    await open();
    fireEvent.click(screen.getByTestId("status-refresh"));
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() =>
      expect(screen.getByTestId("status-trigger-dot").dataset.level).toBe("err"),
    );
  });

  it("what needs a person comes first, whatever kind it is, and the head counts it", async () => {
    vi.mocked(getMcpServers).mockResolvedValue([
      mcp({ name: "linear", status: "needs_auth", auth: "oauth", tool_count: 0 }),
      mcp({ name: "browser", status: "connected", builtin: true, tool_count: 14 }),
    ]);
    vi.mocked(getConnectors).mockResolvedValue([
      connector({ name: "slack", title: "Slack", mcp_error: "server offline" }),
      connector({ name: "gmail", title: "Gmail", account: "you@example.com" }),
    ]);
    const { onOpenConnectors } = renderPopover();
    await open();

    await waitFor(() =>
      expect(screen.getByTestId("status-head").textContent).toBe("2 things need your attention"),
    );
    const problems = screen.getByTestId("status-problems");
    const slack = within(problems).getByTestId("status-row-slack");
    expect(slack.textContent).toContain("Connector");
    expect(slack.textContent).toContain("Could not connect.");
    fireEvent.click(within(slack).getByText("Fix"));
    expect(onOpenConnectors).toHaveBeenCalled();

    const linear = within(problems).getByTestId("status-row-linear");
    expect(linear.textContent).toContain("MCP server");
    fireEvent.click(within(linear).getByText("Sign in"));
    expect(connectMcp).toHaveBeenCalledWith("linear");

    // What works is not in that list, and is one line per kind until it is opened.
    expect(within(problems).queryByTestId("status-row-gmail")).toBeNull();
    expect(screen.queryByTestId("status-row-gmail")).toBeNull();
    expect(screen.getByTestId("status-group-connectors").textContent).toContain("Gmail");
    openGroup("connectors");
    expect((await screen.findByTestId("status-row-gmail")).textContent).toContain("you@example.com");
  });

  it("one thing wrong is said in the singular", async () => {
    vi.mocked(getMcpServers).mockResolvedValue([mcp({ name: "sentry", status: "error", last_error: "boom" })]);
    renderPopover();
    await open();
    await waitFor(() =>
      expect(screen.getByTestId("status-head").textContent).toBe("1 thing needs your attention"),
    );
  });

  it("the raw message is whole, behind Details, and not on the row", async () => {
    const raw = "<asyncio.locks.Lock object at 0x0000 [locked]> is bound to a different event loop";
    vi.mocked(getConnectors).mockResolvedValue([
      connector({ name: "atlassian", title: "Atlassian", mcp_error: raw }),
    ]);
    renderPopover();
    await open();
    const row = await screen.findByTestId("status-row-atlassian");
    expect(row.textContent).not.toContain("asyncio");

    fireEvent.click(screen.getByTestId("status-details-atlassian"));
    const shown = await screen.findByTestId("status-raw");
    expect(shown.textContent).toContain(raw);
    expect(within(shown).getByTestId("copy-button")).toBeTruthy();
    expect(screen.getByTestId("status-details-atlassian").textContent).toBe("Hide details");
  });

  it("retries a failing MCP server via reload", async () => {
    vi.mocked(getMcpServers).mockResolvedValue([
      mcp({ name: "sentry", status: "error", last_error: "boom" }),
    ]);
    renderPopover();
    await open();
    fireEvent.click(await screen.findByText("Retry"));
    expect(reloadMcp).toHaveBeenCalled();
  });

  it("a connector's own server is one row, not two", async () => {
    vi.mocked(getMcpServers).mockResolvedValue([mcp({ name: "slack", status: "error", last_error: "server offline" })]);
    vi.mocked(getConnectors).mockResolvedValue([
      connector({ name: "slack", title: "Slack", mcp_error: "server offline" }),
    ]);
    renderPopover();
    await open();
    await waitFor(() =>
      expect(screen.getByTestId("status-head").textContent).toBe("1 thing needs your attention"),
    );
    expect(screen.getAllByTestId("status-row-slack")).toHaveLength(1);
  });

  it("MCP servers that work open as a list: tool counts, and Off for a disabled one", async () => {
    vi.mocked(getMcpServers).mockResolvedValue([
      mcp({ name: "browser", status: "connected", builtin: true, transport: "http", tool_count: 14 }),
      mcp({ name: "postgres", status: "disabled", enabled: false }),
    ]);
    renderPopover();
    await open();
    await screen.findByTestId("status-group-mcp");
    openGroup("mcp");
    expect((await screen.findByTestId("status-row-browser")).textContent).toContain("14 tools");
    expect(screen.getByTestId("status-row-postgres").textContent).toContain("Off");
    // No chip that says a working thing works.
    expect(screen.getByTestId("status-row-browser").textContent).not.toContain("Live");
  });

  it("skills: the group says how many are off, lists a few, and points at Settings for the rest", async () => {
    vi.mocked(listSkills).mockResolvedValue({
      skills: [
        skill("pdf"),
        skill("pptx", { scope: "global", enabled: false }),
        ...Array.from({ length: 7 }, (_, index) => skill(`skill-${index}`)),
      ],
      installs: {},
    });
    const { onOpenSkills } = renderPopover();
    await open();

    const group = await screen.findByTestId("status-group-skills");
    expect(group.textContent).toContain("8 on, 1 turned off");
    openGroup("skills");
    expect((await screen.findByTestId("status-row-pdf")).textContent).toContain("project");
    expect(screen.getByTestId("status-row-pptx").textContent).toContain("Disabled");
    // Nine skills, six shown.
    expect(screen.queryByTestId("status-row-skill-6")).toBeNull();
    const more = screen.getByTestId("status-skills-more");
    expect(more.textContent).toContain("and 3 more");
    fireEvent.click(within(more).getByText("Open Skills"));
    expect(onOpenSkills).toHaveBeenCalled();
  });

  it("code intelligence: an empty pool is normal and says what could start", async () => {
    vi.mocked(getMcpServers).mockResolvedValue([mcp({ name: "browser" })]);
    renderPopover();
    await open();
    const idle = await screen.findByTestId("status-lsp-idle");
    expect(idle.textContent).toContain("starts on first use");
    expect(idle.textContent).toContain("python");
    expect(screen.queryByTestId("status-trigger-dot")).toBeNull();
  });

  it("code intelligence: a dead server is the one failure, and it is in the list of what needs a person", async () => {
    vi.mocked(getLspStatus).mockResolvedValue({
      enabled: true,
      servers: [
        {
          language: "python",
          language_id: "python",
          workspace: "/w/proj",
          command: "pyright-langserver --stdio",
          alive: false,
        },
      ],
      known_languages: ["python"],
    });
    renderPopover();
    await open();
    const row = await within(await screen.findByTestId("status-problems")).findByTestId("status-row-lsp-python");
    expect(row.textContent).toContain("Python");
    expect(row.textContent).toContain("language server stopped");
    fireEvent.click(screen.getByTestId("status-details-lsp-python"));
    expect((await screen.findByTestId("status-raw")).textContent).toContain("pyright-langserver");
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() =>
      expect(screen.getByTestId("status-trigger-dot").dataset.level).toBe("err"),
    );
  });

  it("code intelligence: a live server is a row with its language's name; a disabled config says so", async () => {
    vi.mocked(getLspStatus).mockResolvedValue({
      enabled: true,
      servers: [
        {
          language: "typescript",
          language_id: "typescript",
          workspace: "/w/proj",
          command: "typescript-language-server --stdio",
          alive: true,
        },
      ],
      known_languages: ["typescript"],
    });
    renderPopover();
    await waitFor(() => expect(getLspStatus).toHaveBeenCalled());
    // A live pool is nothing to look at: no pip.
    expect(screen.queryByTestId("status-trigger-dot")).toBeNull();
    await open();
    expect((await screen.findByTestId("status-group-lsp")).textContent).toContain("TypeScript");
    openGroup("lsp");
    const row = await screen.findByTestId("status-row-lsp-typescript");
    expect(row.textContent).toContain("typescript-language-server · proj");

    vi.mocked(getLspStatus).mockResolvedValue({
      enabled: false,
      servers: [],
      known_languages: [],
    });
    vi.mocked(getMcpServers).mockResolvedValue([mcp({ name: "browser" })]);
    fireEvent.click(screen.getByTestId("status-refresh"));
    await waitFor(() =>
      expect(screen.getByTestId("status-lsp-off").textContent).toContain("[lsp] enabled = false"),
    );
  });

  it("keeps the last list when the engine stops answering, and says it is old", async () => {
    vi.mocked(getMcpServers).mockResolvedValue([mcp({ name: "browser", tool_count: 14 })]);
    renderPopover();
    await open();
    await screen.findByTestId("status-group-mcp");

    vi.mocked(getMcpServers).mockRejectedValue(new Error("down"));
    fireEvent.click(screen.getByTestId("status-refresh"));
    expect((await screen.findByTestId("status-banner")).textContent).toContain("last known state");
    expect(screen.getByTestId("status-group-mcp").textContent).toContain("browser");
  });

  it("closes on Escape and on a backdrop click", async () => {
    renderPopover();
    await open();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("status-popover")).toBeNull();

    await open();
    fireEvent.click(document.querySelector(".fixed.inset-0")!);
    expect(screen.queryByTestId("status-popover")).toBeNull();
  });
});
