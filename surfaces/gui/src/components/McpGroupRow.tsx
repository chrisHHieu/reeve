// A run of consecutive calls to ONE server, folded under a header (the reference design header style B —
// a per-tool breakdown, "get_portfolio ×2, get_transactions ×1", rather than a bare count).
// Collapsed by default; opening it reveals each call, so a tool called repeatedly is never
// hidden. A single call is left as a normal row.
//
// It covers BOTH server shapes: a vendor MCP server (`mcp__<server>__<tool>`) and a connector
// (`gmail_get_message`, `github_create_issue`, …). The identity — title, logo, brand color,
// and whether it is MCP — comes from `serverIdentity`, backed by the `/v1/connectors` index;
// the header wears the server's logo and shimmers while any call is in flight.

import { useState, memo } from "react";
import { sameItems } from "./rowMemo";
import type { ToolItem } from "../cards/cardModels";
import { mcpToolBreakdown, serverIdentity, serverName, type ServerIdentity } from "../toolMeta";
import { useConnectorTools } from "../connectorTools";
import { ConnectorIcon } from "../connectors/ConnectorIcon";
import { Icon } from "./Icon";
import { ToolCallRow } from "./ToolCallRow";
import { Disclosure } from "../ui/Disclosure";

function McpGroupRowInner({
  tools,
  onReveal,
  identity: provided,
}: {
  tools: ToolItem[];
  onReveal?: (path: string) => void;
  /** Resolved by the caller when it already knows the server; derived from `tools[0]` otherwise. */
  identity?: ServerIdentity;
}) {
  const [open, setOpen] = useState(false);
  const connectorTools = useConnectorTools();
  const identity = provided ?? serverIdentity(tools[0].name, connectorTools);
  const isMcp = identity?.isMcp ?? true;
  const running = tools.some((t) => t.status === "…");
  const breakdown = mcpToolBreakdown(
    tools.map((t) => t.name),
    connectorTools,
  );
  // The server's name is the header in every state (owner, 2026-10-13): the shimmer says a
  // call is in flight, so "Calling" and "Called" said nothing.
  const title = identity ? serverName(identity) : "Server";
  const calls = `${tools.length} call${tools.length === 1 ? "" : "s"}`;
  const toggle = () => setOpen((v) => !v);
  return (
    <div
      className={"tool-row " + (isMcp ? "mcp-group" : "connector-group")}
      data-state={running ? "running" : "ok"}
      data-testid={isMcp ? "mcp-group" : "connector-group"}
    >
      <div
        className="tool-row-line"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={toggle}
        onKeyDown={(event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          toggle();
        }}
      >
        <span className="tool-row-leading">
          <ConnectorIcon
            connector={{
              logo: identity?.logo ?? "mcp",
              brand_color: identity?.brandColor,
            }}
            size={14}
          />
        </span>
        <span className={"tool-row-title" + (running ? " shimmer-text" : "")}>{title}</span>
        {isMcp && <span className="mcp-tag">{identity?.plugin ? `MCP · plugin ${identity.plugin}` : "MCP"}</span>}
        {breakdown && <span className="tool-row-summary">{breakdown}</span>}
        <span className={"tool-row-chevron" + (open ? " open" : "")} aria-hidden>
          <Icon name="chevronRight" size={12} />
        </span>
        <span className="tool-row-meta">
          <span className="tool-row-suffix" data-testid="server-calls">
            {calls}
          </span>
        </span>
      </div>
      {/* The calls open and close as a motion, the way one row's body does. */}
      <Disclosure open={open}>
        <div className="explore-group-body">
          {tools.map((t) => (
            <ToolCallRow key={t.id} tool={t} onOpenFile={onReveal} />
          ))}
        </div>
      </Disclosure>
    </div>
  );
}

export const McpGroupRow = memo(
  McpGroupRowInner,
  (a, b) =>
    sameItems(a.tools, b.tools) && a.onReveal === b.onReveal && a.identity?.key === b.identity?.key,
);
