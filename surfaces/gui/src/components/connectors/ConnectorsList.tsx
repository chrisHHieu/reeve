import { useEffect, useState } from "react";
import { getSettings, setExperimentalConnectors, type Connector, type McpServer } from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { Icon } from "../Icon";
import { FoldBody, FoldHead, SettingsPageAction, Switch } from "../settings/primitives";
import { AddConnectionModal } from "./AddConnectionModal";
import { AddMcpModal, CustomMcpGroup } from "./CustomMcp";
import { CHIP_OK, GRP, ROW } from "./ui";

// The list of connectors. The connected ones come first, on a card of their own;
// a row opens that connector's page, and a problem shows as a chip on the row, never one click in.
// MCP servers follow as groups of their own. Then every connector that can be added, each
// with Connect. "Add MCP server" is at the top of the page (the owner's ruling).

const SHOWN_FOLDED = 9; // three rows of three before "Show N more"

/** What a connector is for, so a long list can be narrowed (the settings redesign, 2026-10-20).
 *  Read off its name; one not named here is "Other". */
const KIND_OF: Record<string, string> = {
  slack: "Messages",
  telegram: "Messages",
  whatsapp: "Messages",
  whatsapp_personal: "Messages",
  teams: "Messages",
  gmail: "Mail and calendar",
  outlook: "Mail and calendar",
  google_calendar: "Mail and calendar",
  email: "Mail and calendar",
  notion: "Docs",
  google_drive: "Docs",
  google_docs: "Docs",
  google_sheets: "Docs",
  google_slides: "Docs",
  onedrive: "Docs",
  jira: "Work tracking",
  trello: "Work tracking",
  redmine: "Work tracking",
  github: "Work tracking",
  salesforce: "CRM",
};
const KINDS_IN_ORDER = ["Messages", "Mail and calendar", "Docs", "Work tracking", "CRM", "Other"];
const kindOf = (c: Connector) => KIND_OF[c.name] ?? "Other";

/** The line under a connected connector's name. */
function statusLine(c: Connector): string {
  const accounts = c.accounts?.length ?? 0;
  if (accounts > 1) return `${accounts} accounts`;
  if (c.auth === "none") return "Built in";
  return c.account || "Connected";
}

/** A connector that also brings messages in is Live once connected; one that only acts is Ready. */
const healthChip = (c: Connector) => <span className={CHIP_OK}>● {c.two_way && c.connected ? "Live" : "Ready"}</span>;

export function ConnectorsList({
  connectors,
  mcpServers,
  onOpen,
  onChanged,
}: {
  connectors: Connector[];
  mcpServers: McpServer[];
  onOpen: (name: string) => void;
  onChanged: () => void;
}) {
  const [search, setSearch] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [addingMcp, setAddingMcp] = useState(false);
  const [kind, setKind] = useState<string | null>(null);

  const wanted = search.trim().toLowerCase();
  const matches = (c: Connector) => !wanted || c.title.toLowerCase().includes(wanted) || c.name.includes(wanted);
  const connected = connectors.filter((c) => c.connected && matches(c));
  const addable = connectors.filter((c) => !c.connected && c.available && matches(c));
  const kinds = KINDS_IN_ORDER.filter((k) => addable.some((c) => kindOf(c) === k));
  const addableShown = kind ? addable.filter((c) => kindOf(c) === kind) : addable;
  const servers = mcpServers.filter((s) => !wanted || s.name.toLowerCase().includes(wanted));
  const connectingTo = connecting ? connectors.find((c) => c.name === connecting) : null;
  const folded = !wanted && !kind;

  // The card opens the connector's page before it is connected; Connect goes straight to the
  // sheet. Two buttons side by side, not one inside the other: the inner one used to be a span with
  // role="button" that Tab never reached, and focus had nowhere to go back to when the sheet closed.
  const card = (c: Connector) => (
    <div key={c.name} data-testid={`connector-${c.name}`} className="connector-card">
      <button type="button" className="connector-card-main" aria-label={`Open ${c.title}`} aria-describedby={`available-${c.name}-blurb`} onClick={() => onOpen(c.name)}>
        <span className="connector-card-head">
          <ConnectorBadge connector={c} size={32} title={c.title} />
          <span className="connector-row-title">{c.title}</span>
        </span>
        <span className="connector-card-blurb" id={`available-${c.name}-blurb`}>
          {c.blurb}
        </span>
      </button>
      <button type="button" className="settings-text-btn connector-card-connect" aria-describedby={`available-${c.name}-blurb`} onClick={() => setConnecting(c.name)}>
        Connect
      </button>
    </div>
  );

  const kindButton = (value: string | null, label: string) => (
    <button
      key={label}
      type="button"
      className={kind === value ? "connector-kind on" : "connector-kind"}
      aria-pressed={kind === value}
      data-testid={value ? `connector-kind-${value}` : undefined}
      onClick={() => setKind(kind === value ? null : value)}
    >
      {label}
    </button>
  );

  return (
    <div>
      {/* In Settings a page's main action sits next to its title. */}
      <SettingsPageAction>
        <button className="settings-text-btn accent" onClick={() => setAddingMcp(true)} data-testid="add-custom-server">
          <Icon name="plus" size={13} /> Add MCP server
        </button>
      </SettingsPageAction>
      <div className="settings-toolbar">
        <input placeholder="Search connectors" aria-label="Search connectors" value={search} spellCheck={false} onChange={(e) => setSearch(e.target.value)} className="settings-search-input" />
      </div>

      {connected.length > 0 && (
        <>
          <h2 className="settings-card-heading connector-group-heading">
            Connected <span className="settings-heading-count">{connected.length}</span>
          </h2>
          <div className={GRP}>
            {connected.map((c) => (
              <button key={c.name} type="button" data-testid={`connector-${c.name}`} className="connector-row connector-row-main" onClick={() => onOpen(c.name)}>
                <ConnectorBadge connector={c} size={34} title={c.title} />
                <span className="min-w-0 flex-1">
                  <span className="connector-row-title">{c.title}</span>
                  <span className="connector-row-sub">{statusLine(c)}</span>
                </span>
                {healthChip(c)}
                <Icon name="chevronRight" size={14} className="connector-row-chev" />
              </button>
            ))}
          </div>
        </>
      )}

      <CustomMcpGroup servers={servers} onOpen={(name) => onOpen("mcp:" + name)} onChanged={onChanged} />

      <h2 className="settings-card-heading connector-group-heading">
        Add a connector <span className="settings-heading-count">{addable.length}</span>
      </h2>
      {kinds.length > 1 && (
        <div className="connector-kinds" role="group" aria-label="Show connectors for">
          {kindButton(null, "All")}
          {kinds.map((k) => kindButton(k, k))}
        </div>
      )}
      <div className="connector-grid">{(folded ? addableShown.slice(0, SHOWN_FOLDED) : addableShown).map(card)}</div>
      {folded && addableShown.length > SHOWN_FOLDED && (
        <div className={GRP + " connector-grid-more"}>
          <FoldBody open={showAll}>
            <div className="connector-grid connector-more">{addableShown.slice(SHOWN_FOLDED).map(card)}</div>
          </FoldBody>
          <FoldHead open={showAll} onToggle={() => setShowAll(!showAll)} testId="connectors-show-all">
            {showAll ? "Show fewer" : `Show ${addableShown.length - SHOWN_FOLDED} more`}
          </FoldHead>
        </div>
      )}
      {addableShown.length === 0 && <div className="connector-line connector-muted">Nothing matches.</div>}

      {connectingTo && <AddConnectionModal c={connectingTo} onClose={() => setConnecting(null)} onChanged={onChanged} />}
      {addingMcp && <AddMcpModal onClose={() => setAddingMcp(false)} onChanged={onChanged} />}

      <ExperimentalConnectors onChanged={onChanged} />
    </div>
  );
}

// Experimental connectors are listed only once the person asks for them, and each still says what
// it risks and asks for that to be acknowledged before it connects.
function ExperimentalConnectors({ onChanged }: { onChanged: () => void }) {
  const [on, setOn] = useState<boolean | null>(null);
  useEffect(() => {
    getSettings()
      .then((settings) => setOn(settings.experimental_connectors === true))
      .catch(() => setOn(false));
  }, []);
  if (on === null) return null;
  return (
    <>
      <h2 className="settings-card-heading connector-group-heading">Experimental</h2>
      <div className={GRP}>
        <div className={ROW + " gap-3"}>
          <span className="min-w-0 flex-1">
            <span className="text-[13px]">Show experimental connectors</span>
            <span className="block text-[12px] text-muted">Connections made by unofficial means, which can cost you the account they link. Each says its risk before it connects.</span>
          </span>
          <Switch
            checked={on}
            label="Show experimental connectors"
            testId="experimental-connectors-toggle"
            onChange={(next) => {
              setOn(next);
              void setExperimentalConnectors(next).then(onChanged);
            }}
          />
        </div>
      </div>
    </>
  );
}
