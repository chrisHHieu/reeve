// What to draw for a connector that is known only by a reference: an id such as "github", or an
// MCP server's name. The answer is a ConnectorVisual ({logo, brand_color, label}) for ConnectorIcon
// and ConnectorBadge.
//
// A connector's true colour is on its descriptor from `/v1/connectors` (`brand_color`), so the list
// is indexed by id and the descriptor's logo and colour are passed on; nothing here keeps a table
// of colours. A reference the list does not have is tried as a logo id of the registry (an MCP
// server gets the MCP mark) with the neutral colour, which is the registry's own fallback.

import type { Connector } from "../api";
import type { ConnectorVisual } from "./ConnectorIcon";

export type ConnectorMap = Record<string, Connector>;

/** `/v1/connectors` by connector id (`name`). */
export function indexConnectors(list: Connector[]): ConnectorMap {
  return Object.fromEntries(list.map((connector) => [connector.name, connector]));
}

/** A reference with its first letter in capitals, for when there is no descriptor to give a title. */
export function humanize(ref: string): string {
  return ref ? ref[0].toUpperCase() + ref.slice(1) : "";
}

/** The descriptor's title when the connector is known, else the reference made readable. */
export function labelFor(ref: string, byName: ConnectorMap): string {
  return byName[ref]?.title || humanize(ref);
}

/** The logo, colour and label for a reference: a known connector's own (its true colour), the MCP
 *  mark for an MCP server, otherwise the reference as a logo id, which ends at the plug if the
 *  registry does not know it either. */
export function visualFor(ref: string, kind: string, byName: ConnectorMap): ConnectorVisual {
  const known = byName[ref];
  if (known) return { logo: known.logo || ref, brand_color: known.brand_color, title: known.title, label: known.title };
  return { logo: kind === "mcp" ? "mcp" : ref, label: humanize(ref) };
}
