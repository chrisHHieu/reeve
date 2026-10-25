import { useEffect, useState } from "react";
import { addMcpServer, connectMcp, deleteMcpServer, getMcpTools, patchMcpServer, signoutMcp, type McpServer } from "../../api";
import { sinceLabel } from "../../ago";
import { ConnectorIcon } from "../../connectors/ConnectorIcon";
import { defaultBrandColor } from "../../connectors/registry";
import { ConfirmDialog } from "../../ui";
import { Icon } from "../Icon";
import { FoldBody, FoldHead, SettingsCard, SettingsRow, Switch, TextButton } from "../settings/primitives";
import { BrowserBackendPanel } from "./BrowserBackend";
import { CHIP_ERR, CHIP_OFF, CHIP_OK, CHIP_WARN, GRP, PILL_ACCENT } from "./ui";

// MCP servers on the Connectors page (there is no separate MCP tab any more). They
// come at the end of the Connected section in groups of their own, never mixed in with this app's
// connectors, so a server someone added stays visibly one this app does not vouch for. A stdio
// server is never called "Connected": Live means a connection is open now, Ready that its Test
// passed once (the line under it says "tested <when>", which the server keeps).

/** The state chip of a server's row. */
export function mcpChip(server: McpServer) {
  const chip = (className: string, text: string) => <span className={className}>● {text}</span>;
  if (!server.enabled) return chip(CHIP_OFF, "Off");
  if (server.status === "authorizing") return chip(CHIP_WARN, server.auth === "oauth" ? "Signing in…" : "Testing…");
  if (server.status === "connected") return chip(CHIP_OK, "Live");
  if (server.auth_hint || server.status === "needs_auth") return chip(CHIP_WARN, "Needs sign-in");
  if (server.status === "error") return chip(CHIP_ERR, "Error");
  return server.last_test_at ? chip(CHIP_OK, "Ready") : chip(CHIP_OFF, "Not tested");
}

/** The line under a server's name: its transport, then its tool count when live or its host, then
 *  when it was last tested. A live server shows the test too: it is the visible sign that pressing
 *  Test did something (it connects again and counts the tools again). */
export function mcpStatusLine(server: McpServer): string {
  const parts = [server.transport];
  if (server.status === "connected" && server.tool_count != null) parts.push(`${server.tool_count} tools`);
  else if (server.transport === "http" && server.config?.url) {
    try {
      parts.push(new URL(server.config.url).host);
    } catch {
      // an address that does not parse is shown without a host
    }
  }
  const tested = sinceLabel(server.last_test_at);
  if (tested) parts.push(`tested ${tested}`);
  return parts.join(" · ");
}

/** The servers that ship with Reeve, by the names people read and what each one is for (2026-10-10:
 *  the list showed `computer-use` and `chrome-devtools`, the config keys). The key is the server's id. */
export const MCP_BUILTINS: Record<string, { title: string; blurb: string }> = {
  browser: {
    title: "Browser",
    blurb: "A browser Reeve uses to open pages, click, fill forms and read what is there. Each conversation gets its own.",
  },
  "computer-use": {
    title: "Computer use",
    blurb: "Lets Reeve see this computer's screen and use its mouse and keyboard, when you allow it.",
  },
  "chrome-devtools": {
    title: "Chrome DevTools",
    blurb: "Inspects pages the way a developer does: console, network and performance. A second browser beside Browser.",
  },
};

/** What a server is called: a built-in's own title, otherwise the name it was added under. */
export function mcpTitle(server: Pick<McpServer, "name" | "builtin">): string {
  return (server.builtin && MCP_BUILTINS[server.name]?.title) || server.name;
}

/** A server's mark: the registry's for an id it knows (the built-ins in their own colours, so they
 *  read apart), the plug otherwise. */
export function McpGlyph({ name, size = 34 }: { name: string; size?: number }) {
  return (
    <span className="mcp-glyph" style={{ width: size, height: size }}>
      <ConnectorIcon connector={{ logo: name, brand_color: defaultBrandColor(name) }} size={Math.round(size * 0.5)} />
    </span>
  );
}

export function CustomMcpGroup({ servers, onOpen, onChanged }: { servers: McpServer[]; onOpen: (name: string) => void; onChanged: () => void }) {
  // A test or sign-in settles on the server within seconds, but this page polls nothing of MCP's
  // by itself, and the chip stayed on Testing for good (2026-08-21, a server added by URL behind a
  // guard). While any row is in that state, the parent is asked to refresh every second.
  const settling = servers.some((s) => s.status === "authorizing");
  useEffect(() => {
    if (!settling) return;
    const timer = setInterval(onChanged, 1000);
    return () => clearInterval(timer);
  }, [settling, onChanged]);
  if (!servers.length) return null;

  // Reeve's own (`browser`, `computer-use`, and `chrome-devtools`, which starts off) are a group of
  // their own, so nothing a person added reads as something the app vouches for.
  const builtins = servers.filter((s) => s.builtin);
  const added = servers.filter((s) => !s.builtin);

  // Every server's row is shaped like a connector's: mark, name, one line, state, chevron. A built-in
  // has its switch on the row as well (2026-10-10: turning Chrome DevTools on was a page away), next
  // to the row's button rather than inside it.
  const row = (server: McpServer) => (
    <div key={server.name} className="connector-row" data-testid={`mcp-row-${server.name}`}>
      <button type="button" className="connector-row-main" onClick={() => onOpen(server.name)}>
        <McpGlyph name={server.name} />
        <span className="min-w-0 flex-1">
          <span className="connector-row-title">{mcpTitle(server)}</span>
          <span className="connector-row-sub">{mcpStatusLine(server)}</span>
        </span>
        {server.builtin && !server.enabled ? null : mcpChip(server)}
        <Icon name="chevronRight" size={14} className="connector-row-chev" />
      </button>
      {server.builtin && (
        <span className="connector-row-switch">
          <Switch
            checked={server.enabled}
            label={`${mcpTitle(server)} on`}
            testId={`mcp-switch-${server.name}`}
            onChange={async (on) => {
              await patchMcpServer(server.name, { enabled: on });
              onChanged();
            }}
          />
        </span>
      )}
    </div>
  );

  return (
    <>
      {builtins.length > 0 && (
        <section className="settings-card-block">
          <h2 className="settings-card-heading connector-group-heading">Built into Reeve</h2>
          <div className={GRP} data-testid="builtin-mcp-group">
            {builtins.map(row)}
          </div>
        </section>
      )}
      {added.length > 0 && (
        <section className="settings-card-block">
          <h2 className="settings-card-heading connector-group-heading">
            Your MCP servers <span className="settings-heading-count">{added.length}</span>
          </h2>
          <div className={GRP} data-testid="custom-mcp-group">
            {added.map(row)}
          </div>
        </section>
      )}
    </>
  );
}

// -- adding a server (a URL tab and a JSON tab, in the page's modal) -------------------

const EXAMPLE = `{
  "filesystem": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
    "enabled": true
  }
}`;

const FIELD = "w-full text-[13px] px-3 py-2 rounded-lg border border-line bg-paper text-ink outline-none focus:border-accent";

// A name to start from, read off the URL: the first part of the host that says something (skipping
// mcp, api, data, www…): mcp.linear.app gives "linear", data.acme.link gives "acme"; else the part
// before the top-level domain. Typing over it is always possible.
const SAYS_NOTHING = new Set(["www", "mcp", "api", "data", "remote", "server", "agent", "app"]);
function nameFromUrl(raw: string): string {
  let host: string;
  try {
    host = new URL(raw).hostname.toLowerCase();
  } catch {
    return "";
  }
  const parts = host.split(".").filter(Boolean).slice(0, -1); // without the top-level domain
  if (!parts.length) return "";
  return (parts.find((part) => !SAYS_NOTHING.has(part)) || parts[parts.length - 1]).replace(/[^a-z0-9-]/g, "");
}

/** The servers in pasted JSON: an `mcpServers` block or a `{ name: config }` map; null for one bare
 *  config, which has no name to go by. */
function serversIn(parsed: any): [string, Record<string, any>][] | null {
  const map = parsed?.mcpServers || parsed;
  if (!map || typeof map !== "object" || map.command || map.url) return null;
  const entries = Object.entries(map) as [string, Record<string, any>][];
  return entries.length ? entries : null;
}

export function AddMcpModal({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [tab, setTab] = useState<"url" | "json">("url");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [json, setJson] = useState(EXAMPLE);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  const done = () => {
    onChanged();
    onClose();
  };

  const addByUrl = async () => {
    setError(null);
    const chosen = name.trim();
    const address = url.trim();
    if (!chosen) return setError("Give the server a name.");
    if (!/^https?:\/\/\S+$/.test(address)) return setError("Enter the server's full URL (https://…).");
    await addMcpServer(chosen, { type: "http", url: address });
    // Tested at once, without credentials: the row shows Testing…, then Live, an error, or Needs
    // sign-in (a 401, which the detail page answers by switching to OAuth).
    await connectMcp(chosen);
    done();
  };

  const addFromJson = async () => {
    setError(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(json);
    } catch (e: any) {
      return setError("Invalid JSON: " + e.message);
    }
    const servers = serversIn(parsed);
    if (!servers) return setError('Paste a `{ "<name>": { … } }` object (or a full mcpServers block).');
    for (const [serverName, config] of servers) await addMcpServer(serverName, config);
    done();
  };

  const tabButton = (which: "url" | "json", label: string) => (
    <button
      className={"text-[12px] px-2.5 py-1 rounded-md border shrink-0 " + (tab === which ? "border-accent text-accent font-medium" : "border-line text-muted hover:text-ink")}
      onClick={() => setTab(which)}
      data-testid={`mcp-add-tab-${which}`}
    >
      {label}
    </button>
  );

  return (
    <div className="fixed inset-0 z-40" data-testid="add-mcp-modal">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="absolute left-1/2 top-24 -translate-x-1/2 w-[540px] max-w-[92vw] rounded-xl2 border border-line bg-panel shadow-xl p-5 space-y-3">
        <div className="flex items-center justify-between">
          <div className="text-[14px] font-semibold">Add custom MCP server</div>
          <button className="text-faint hover:text-ink text-[16px] leading-none" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="flex items-center gap-1.5">
          {tabButton("url", "Remote URL")}
          {tabButton("json", "JSON")}
        </div>
        {tab === "url" ? (
          <>
            <div className="text-[13px] text-muted">A server someone hosts. If it wants a sign-in, its row offers one after the first test.</div>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (shown in the connectors list)" spellCheck={false} className={FIELD} data-testid="mcp-add-name" />
            <input
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                if (!name.trim()) setName(nameFromUrl(e.target.value)); // only while no name is typed
              }}
              placeholder="https://mcp.example.com/mcp"
              spellCheck={false}
              className={FIELD + " font-mono text-[12px]"}
              data-testid="mcp-add-url"
            />
          </>
        ) : (
          <>
            <div className="text-[13px] text-muted">Paste the servers as JSON, each name with its config:</div>
            <textarea
              value={json}
              onChange={(e) => setJson(e.target.value)}
              spellCheck={false}
              rows={9}
              className="w-full font-mono text-[12px] px-3 py-2.5 rounded-lg border border-line bg-paper text-ink outline-none focus:border-accent resize-y"
            />
          </>
        )}
        <div className="flex items-center gap-3">
          <button className={PILL_ACCENT} onClick={tab === "url" ? addByUrl : addFromJson}>
            {tab === "url" ? "Add & test" : "Add"}
          </button>
          <button className="text-[13px] text-muted hover:text-ink" onClick={onClose}>
            cancel
          </button>
        </div>
        {error && <div className="text-[13px] text-danger">{error}</div>}
      </div>
    </div>
  );
}

// -- a server's own page: its tools, Test, its config, the last error, removing it -------

function TestButton({ server, onTest, onOauth }: { server: McpServer; onTest: () => void; onOauth: () => void }) {
  if (server.auth_hint && server.auth !== "oauth") {
    return (
      <TextButton tone="accent" onClick={onOauth} testId={`mcp-authfix-${server.name}`}>
        Sign in
      </TextButton>
    );
  }
  if (server.auth === "oauth" && server.status === "needs_auth") {
    return (
      <TextButton tone="accent" onClick={onTest} testId={`mcp-signin-${server.name}`}>
        Sign in
      </TextButton>
    );
  }
  const testing = server.status === "authorizing";
  return (
    <TextButton disabled={testing} onClick={onTest} testId={`mcp-test-${server.name}`}>
      {testing ? "Testing…" : "Test"}
    </TextButton>
  );
}

export function McpServerDetail({ server, onChanged, onGone }: { server: McpServer; onChanged: () => void; onGone: () => void }) {
  const [toolsOpen, setToolsOpen] = useState(false);
  const [tools, setTools] = useState<{ name: string; description: string }[] | null>(null);
  const [loadingTools, setLoadingTools] = useState(false);
  const [toolsError, setToolsError] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const shipped = MCP_BUILTINS[server.name];
  const title = mcpTitle(server);

  const test = async () => {
    await connectMcp(server.name);
    onChanged();
    // The connect runs in the background; a refresh that came before it began would miss Testing,
    // and the page's poll would miss the change after it, so one more look in a moment.
    window.setTimeout(onChanged, 600);
  };
  // Connecting without credentials got a 401 or 403, so the answer is a sign-in: the server switches
  // to OAuth (dynamic registration, nothing to set up) and the browser opens straight away.
  const switchToOauth = async () => {
    await patchMcpServer(server.name, { auth: "oauth" });
    await connectMcp(server.name);
    onChanged();
  };

  // The tools are fetched the first time the fold opens; it animates open either way.
  const toggleTools = async () => {
    const open = !toolsOpen;
    setToolsOpen(open);
    if (!open || tools) return;
    setLoadingTools(true);
    setToolsError(null);
    const found = await getMcpTools(server.name);
    setLoadingTools(false);
    if (found.ok) setTools(found.tools);
    else setToolsError(found.error || "failed to connect");
  };

  const toolCount = tools ? tools.length : server.tool_count;

  return (
    <div className="connector-page" data-testid={`mcp-detail-${server.name}`}>
      <div>
        <header className="settings-provider-head connector-head">
          <McpGlyph name={server.name} size={44} />
          <div className="settings-provider-head-text">
            <h2 className="settings-provider-title">
              {title}
              {server.builtin && <span className="settings-tag connector-head-tag">Built in</span>}
            </h2>
            <div className="connector-state">{mcpStatusLine(server)}</div>
          </div>
          <div className="connector-head-action">{mcpChip(server)}</div>
        </header>
        {shipped && <p className="settings-provider-blurb">{shipped.blurb}</p>}
      </div>

      <SettingsCard>
        <SettingsRow title="On" detail="Reeve can use its tools while it is on.">
          <Switch
            checked={server.enabled}
            label="Enable this server"
            testId={`mcp-enabled-${server.name}`}
            onChange={async (on) => {
              await patchMcpServer(server.name, { enabled: on });
              onChanged();
            }}
          />
        </SettingsRow>
        <SettingsRow
          title="Test connection"
          detail={
            server.last_error && server.status !== "connected" ? (
              <span className="connector-error-line" role="alert">
                {server.last_error}
              </span>
            ) : (
              "Starts the server and lists its tools — without opening a session."
            )
          }
        >
          <TestButton server={server} onTest={() => void test()} onOauth={() => void switchToOauth()} />
        </SettingsRow>
      </SettingsCard>

      <section className="settings-card-block">
        <div className="settings-panel">
          <FoldHead open={toolsOpen} onToggle={() => void toggleTools()} aside={toolCount != null ? `${toolCount} tools` : undefined} testId={`mcp-tools-${server.name}`}>
            Tools
          </FoldHead>
          <FoldBody open={toolsOpen}>
            <div className="connector-tool-list">
              {loadingTools ? (
                <div className="connector-line connector-muted">Starting the server to list its tools…</div>
              ) : toolsError ? (
                <div className="connector-line connector-error-line" role="alert">
                  {toolsError}
                </div>
              ) : tools && tools.length === 0 ? (
                <div className="connector-line connector-muted">This server offers no tools.</div>
              ) : (
                (tools ?? []).map((tool) => (
                  <div key={tool.name} className="connector-line" title={tool.description}>
                    <span className="min-w-0 flex-1">
                      <span className="connector-line-title connector-mono">{tool.name}</span>
                      {tool.description && <span className="connector-line-sub connector-clamp">{tool.description}</span>}
                    </span>
                  </div>
                ))
              )}
            </div>
          </FoldBody>
        </div>
      </section>

      {server.name === "browser" && (
        <SettingsCard title="Browser" tile>
          <div className="connector-pad">
            <BrowserBackendPanel onChanged={onChanged} />
          </div>
        </SettingsCard>
      )}

      <SettingsCard title="Configuration" tile>
        <pre className="connector-config">{JSON.stringify(server.config, null, 2)}</pre>
      </SettingsCard>

      <SettingsCard>
        {server.auth === "oauth" && server.status === "connected" && (
          <SettingsRow title="Sign out" detail="Forgets the sign-in on this computer; the server stays in the list.">
            <TextButton
              testId={`mcp-signout-${server.name}`}
              onClick={async () => {
                await signoutMcp(server.name);
                onChanged();
              }}
            >
              Sign out
            </TextButton>
          </SettingsRow>
        )}
        <SettingsRow
          title={server.builtin ? "Reset to default" : "Remove server"}
          detail={server.builtin ? "Puts it back the way Reeve ships it." : "Takes it out of Reeve. Its own program or service is not touched."}
        >
          <TextButton tone="danger" testId={`mcp-remove-${server.name}`} onClick={() => setRemoving(true)}>
            {server.builtin ? "Reset" : "Remove"}
          </TextButton>
        </SettingsRow>
      </SettingsCard>

      {removing && (
        <ConfirmDialog
          title={server.builtin ? `Reset ${title}?` : `Remove ${title}?`}
          body={server.builtin ? "Its settings go back to how Reeve ships it." : "Reeve stops using its tools in new conversations. You can add it again later."}
          confirmLabel={server.builtin ? "Reset" : "Remove"}
          onCancel={() => setRemoving(false)}
          onConfirm={async () => {
            await deleteMcpServer(server.name);
            setRemoving(false);
            onChanged();
            onGone();
          }}
        />
      )}
    </div>
  );
}
