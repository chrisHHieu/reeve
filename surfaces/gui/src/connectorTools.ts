// The connector-tool index: which CONNECTOR a built-in tool belongs to, and how to brand it.
//
// Reeve ships two shapes of external tool surface, and only one of them was branded:
//   · `mcp__<server>__<tool>` — a vendor MCP server. The transcript already grouped these
//     and wore the server's logo.
//   · `<connector>_<action>` — a hand-written connector tool (`gmail_get_message`,
//     `github_create_issue`, …). These fell through to the generic "Tool" row: no logo, no
//     grouping, and twenty-eight Gmail reads read as twenty-eight anonymous steps.
//
// `/v1/connectors` already publishes each connector's `logo`/`brand_color` AND its `tools[]`
// (name + label), so the GUI can recover the missing half without a new endpoint: build a
// `tool name → connector` map once, and every consumer (the row's leading glyph, the group
// header, the label) reads it. Unknown tools simply are not in the map and keep the old
// behaviour — this is additive.

import { createContext, useContext } from "react";
import type { Connector } from "./api";

export interface ConnectorToolEntry {
  /** The connector id, e.g. "gmail". */
  connector: string;
  /** The connector's display title, e.g. "Gmail". */
  title: string;
  /** Stable logo id keyed into the frontend registry (falls back to the connector id). */
  logo: string;
  /** The connector's brand color, e.g. "#ea4335". Empty → registry default. */
  brandColor: string;
  /** The tool's own label, e.g. "Read message". Falls back to the wire name. */
  label: string;
}

export type ConnectorToolIndex = ReadonlyMap<string, ConnectorToolEntry>;

export const EMPTY_CONNECTOR_TOOLS: ConnectorToolIndex = new Map();

/** Index `/v1/connectors` by the WIRE tool name, for O(1) lookup from a row. */
export function buildConnectorToolIndex(connectors: readonly Connector[]): ConnectorToolIndex {
  const index = new Map<string, ConnectorToolEntry>();
  for (const connector of connectors ?? []) {
    const title = connector.title || connector.name;
    const logo = connector.logo || connector.name;
    const brandColor = connector.brand_color || "";
    for (const tool of connector.tools ?? []) {
      const name = tool?.name;
      if (!name) continue;
      index.set(name, {
        connector: connector.name,
        title,
        logo,
        brandColor,
        label: tool.label || name,
      });
    }
  }
  return index;
}

export const ConnectorToolsContext = createContext<ConnectorToolIndex>(EMPTY_CONNECTOR_TOOLS);

export function useConnectorTools(): ConnectorToolIndex {
  return useContext(ConnectorToolsContext);
}
