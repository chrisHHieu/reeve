import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { Connector } from "../api";

/**
 * Picking which endpoint of a multi-endpoint vendor MCP server to sign in to.
 *
 * Postman publishes the same server three times — Essentials (41 tools, none of which
 * delete), Full (140 tools, 17 of which delete) and Code generation (23, read-only). The
 * choice has to be made BEFORE the browser opens, because each endpoint is its own OAuth
 * resource: the sign-in is for that endpoint specifically.
 *
 * THERE ARE TWO ONE-CLICK SURFACES, and the tests run against both. The first version of
 * this shipped the picker on the Connectors card only, and the add-connection modal — the
 * button most people actually press — went on connecting straight to the default with no
 * choice on screen. Nothing failed: the card's own tests were green, because the card was
 * fine. So every case below runs twice, once per surface.
 *
 * `connectMcpBacked` is intercepted so the assertions are about what each surface SENDS.
 * A pane that renders three tidy radios and then posts the default anyway looks perfect
 * and connects the user to the wrong tool set.
 */
const calls: Array<[string, string | undefined]> = [];
vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  connectMcpBacked: (name: string, variant?: string) => {
    calls.push([name, variant]);
    return Promise.resolve({ ok: true });
  },
  connectConnector: () => Promise.resolve({ ok: true }),
  connectManaged: () => Promise.resolve({ ok: true }),
  getConnectors: () => Promise.resolve(polled),
}));

// What the next poll of /v1/connectors will answer.
let polled: any[] = [];

const { ConnectSetup } = await import("./connectors/ConnectSetup");
const { McpOneClick } = await import("./connectors/AddConnectionModal");

const postman = (over: Partial<Connector> = {}): Connector =>
  ({
    name: "postman",
    title: "Postman",
    icon: "◈",
    blurb: "",
    auth: "oauth",
    two_way: false,
    channels: false,
    available: true,
    fields: [],
    instructions: [],
    connected: false,
    account: null,
    enabled: false,
    brand_color: "#ff6c37",
    logo: "postman",
    one_click: true,
    allowed_users: [],
    tools: [],
    mcp_variant: "minimal",
    mcp_variants: [
      { key: "minimal", label: "Essentials", summary: "41 tools — nothing that deletes." },
      { key: "full", label: "Full", summary: "140 tools — including deleting workspaces." },
      { key: "code", label: "Code generation", summary: "23 tools, read-only." },
    ],
    ...over,
  }) as unknown as Connector;

// The two places a user can press Connect on an MCP-backed connector.
const SURFACES: Array<[string, (c: Connector) => JSX.Element]> = [
  ["Connectors card", (c) => <ConnectSetup c={c} onConnected={() => {}} />],
  ["add-connection modal", (c) => <McpOneClick c={c} onConnected={() => {}} />],
];

beforeEach(() => {
  calls.length = 0;
});
afterEach(cleanup);

describe.each(SURFACES)("choosing an endpoint — %s", (_name, mount) => {
  it("offers every endpoint, with what each one costs", () => {
    render(mount(postman()));
    const box = screen.getByTestId("mcp-variants");
    expect(box.textContent).toContain("Essentials");
    expect(box.textContent).toContain("Full");
    expect(box.textContent).toContain("Code generation");
    // The summaries are the decision, not decoration: one option can delete a workspace.
    expect(box.textContent).toContain("deleting workspaces");
  });

  it("starts on the safe one", () => {
    render(mount(postman()));
    expect((screen.getByDisplayValue("minimal") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByDisplayValue("full") as HTMLInputElement).checked).toBe(false);
  });

  it("sends the endpoint the user actually chose", () => {
    render(mount(postman()));
    fireEvent.click(screen.getByDisplayValue("full"));
    fireEvent.click(screen.getByText(/Connect Postman/));
    expect(calls).toEqual([["postman", "full"]]);
  });

  it("sends the default when the user changes nothing", () => {
    render(mount(postman()));
    fireEvent.click(screen.getByText(/Connect Postman/));
    expect(calls).toEqual([["postman", "minimal"]]);
  });

  it("shows the endpoint a connected card is already on", () => {
    render(mount(postman({ mcp_variant: "code", connected: true })));
    expect((screen.getByDisplayValue("code") as HTMLInputElement).checked).toBe(true);
  });

  it("leaves single-endpoint connectors exactly as they were", () => {
    // Every other MCP connector sends no variant at all. A control here would be a
    // question with one answer, and the request must stay the shape it has always been.
    const monday = postman({
      name: "monday",
      title: "monday.com",
      mcp_variants: [],
      mcp_variant: "",
    });
    render(mount(monday));
    expect(screen.queryByTestId("mcp-variants")).toBeNull();
    fireEvent.click(screen.getByText(/Connect monday/));
    expect(calls).toEqual([["monday", undefined]]);
  });
});

/**
 * A sign-in that fails has to reach the screen.
 *
 * `list_mcp` hides connector-backed servers from the MCP tab, so a failed one-click had
 * nowhere at all to surface: the dialog set "Check your browser…", polled `connected`
 * forever, and the reason sat in the server's `_mcp_errors` unread. What the user saw was
 * a button telling them to go look at a browser window that had never opened.
 */
describe("a one-click that fails says so", () => {
  it("stops waiting and shows the reason the server recorded", async () => {
    polled = [{ name: "postman", connected: false, mcp_error: "DCR rejected the redirect" }];
    vi.useFakeTimers();
    try {
      render(<McpOneClick c={postman()} onConnected={() => {}} />);
      fireEvent.click(screen.getByText(/Connect Postman/));
      await vi.advanceTimersByTimeAsync(2500);
      expect(screen.getByText("DCR rejected the redirect")).toBeTruthy();
      // and the button stops claiming a browser is open
      expect(screen.queryByText("Check your browser…")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps waiting while the sign-in is still in flight", async () => {
    // An error left over from a PREVIOUS attempt must not abort the one running now.
    polled = [
      { name: "postman", connected: false, mcp_error: "an older failure", mcp_authorizing: true },
    ];
    vi.useFakeTimers();
    try {
      render(<McpOneClick c={postman()} onConnected={() => {}} />);
      fireEvent.click(screen.getByText(/Connect Postman/));
      await vi.advanceTimersByTimeAsync(2500);
      expect(screen.queryByText("an older failure")).toBeNull();
      expect(screen.getByText("Check your browser…")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("closes on success, not on the error path", async () => {
    polled = [{ name: "postman", connected: true }];
    const done = vi.fn();
    vi.useFakeTimers();
    try {
      render(<McpOneClick c={postman()} onConnected={done} />);
      fireEvent.click(screen.getByText(/Connect Postman/));
      await vi.advanceTimersByTimeAsync(2500);
      expect(done).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
