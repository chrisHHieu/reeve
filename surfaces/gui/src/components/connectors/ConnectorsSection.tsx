import { useEffect, useState, type ReactNode } from "react";
import { getConnectors, getMcpServers, type Connector, type McpServer } from "../../api";
import { SettingsBack } from "../settings/primitives";
import { AccountsDetail } from "./AccountsDetail";
import { AvailableDetail } from "./AvailableDetail";
import { CalendarDetail } from "./CalendarDetail";
import { ConnectorsList } from "./ConnectorsList";
import { ConnectorTools } from "./ConnectorTools";
import { McpServerDetail } from "./CustomMcp";
import { ConnectorHead } from "./DetailParts";
import { DisconnectRow } from "./DisconnectButton";
import { GithubDetail } from "./GithubDetail";
import { GmailDetail } from "./GmailDetail";
import { SlackDetail } from "./SlackDetail";
import { TwoWayGroup } from "./TwoWayRows";
import { WhatsAppDetail } from "./WhatsAppDetail";

// Connectors are a list and, behind it, a page per connector: the Integrations
// navigation never gains an entry per connector, and a page is reached from the list and left by
// its `‹ Connectors` crumb. A connector with no page of its own gets GenericDetail, so every row
// opens something from the day it ships.

export interface DetailProps {
  c: Connector;
  onChanged: () => void;
  /** The connector was disconnected: back to the list. */
  onGone?: () => void;
}

// The pages made for one connector; any other gets GenericDetail.
const DETAIL_PAGES: Record<string, (p: DetailProps) => JSX.Element> = {
  slack: (p) => <SlackDetail {...p} />,
  gmail: (p) => <GmailDetail {...p} />,
  google_calendar: (p) => <CalendarDetail {...p} />,
  github: (p) => <GithubDetail {...p} />,
  // On the shared account layer (accounts.py): one page for all of them. Microsoft 365
  // is one account per Microsoft sign-in.
  ...Object.fromEntries(["notion", "trello", "outlook", "onedrive", "teams"].map((name) => [name, (p: DetailProps) => <AccountsDetail {...p} />])),
  // What the linked device keeps on this computer, and for how long.
  whatsapp_personal: (p) => <WhatsAppDetail {...p} />,
};

// An MCP server's page is routed as "mcp:<name>", so it can never be taken for a connector's page
// of the same name.
const MCP_ROUTE = "mcp:";

/** `footer` goes under the list only, never under a connector's page (Settings puts Tool loading there). */
export function ConnectorsSection({ footer, initialDetail = null }: { footer?: ReactNode; initialDetail?: string | null } = {}) {
  const [detail, setDetail] = useState<string | null>(initialDetail);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const back = () => setDetail(null);

  const refresh = () => {
    getConnectors()
      .then(setConnectors)
      .catch(() => setConnectors([]));
    getMcpServers()
      .then(setMcpServers)
      .catch(() => setMcpServers([]));
  };
  // Read again every few seconds: senders and parked messages arrive over time, and a sign-in that
  // finishes in the system browser shows on the next read.
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, []);
  // Faster while an MCP test or sign-in is under way, so its chip turns to the result (Live, Error,
  // Needs sign-in) with nobody touching anything.
  const mcpSettling = mcpServers.some((s) => s.status === "authorizing");
  useEffect(() => {
    if (!mcpSettling) return;
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [mcpSettling]);

  if (!detail) {
    return (
      <>
        <ConnectorsList connectors={connectors} mcpServers={mcpServers} onOpen={setDetail} onChanged={refresh} />
        {footer}
      </>
    );
  }

  let page: ReactNode;
  if (detail.startsWith(MCP_ROUTE)) {
    const server = mcpServers.find((s) => MCP_ROUTE + s.name === detail);
    page = server ? <McpServerDetail server={server} onChanged={refresh} onGone={back} /> : null;
  } else {
    const c = connectors.find((x) => x.name === detail);
    const Page = DETAIL_PAGES[detail];
    // Not connected yet: the page that says what it is for. When a connect completes, the next
    // read marks it connected and the same route draws the connected page.
    if (!c) page = null;
    else if (!c.connected) page = <AvailableDetail c={c} onChanged={refresh} />;
    else if (Page) page = <Page c={c} onChanged={refresh} onGone={back} />;
    else page = <GenericDetail c={c} onChanged={refresh} onGone={back} />;
  }
  return (
    <div className="settings-enter" key={detail}>
      <SettingsBack label="Connectors" onClick={back} testId="connectors-breadcrumb" />
      {page ?? <div className="text-[13px] text-muted">Loading…</div>}
    </div>
  );
}

// The page of a connector with none of its own: the head, its tools, and for one that brings
// messages in its allow-list, its waiting senders and what listens to it, then Disconnect.
function GenericDetail({ c, onChanged, onGone }: DetailProps & { onGone: () => void }) {
  return (
    <div className="connector-page">
      <ConnectorHead c={c} state={c.account || (c.auth === "none" ? "Built in" : "Connected")} />
      <ConnectorTools c={c} onChanged={onChanged} />
      {c.two_way && <TwoWayGroup c={c} onChanged={onChanged} />}
      <DisconnectRow c={c} onChanged={onChanged} onGone={onGone} />
    </div>
  );
}
