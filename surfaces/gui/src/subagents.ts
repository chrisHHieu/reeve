// The `task` agent definitions as data for the GUI (`GET /v1/subagents`, ledger 2.2/2.10).
//
// The task card draws the agent's title in the agent's own color. 
// `app/src/utils/agent.ts`: an explicit `color` from the definition (a hex value or a CSS
// theme literal) wins; otherwise the name hashes into a stable palette, so the same agent
// wears the same color in every session, every reload and every machine.
//
// The index only carries colors an agent DECLARED. Agents without one are not absent —
// `agentColor` still paints them from the palette. That keeps a config edit (adding a
// color) the only thing that changes an already-known agent's tint.

import { createContext, useContext } from "react";

export interface SubagentInfo {
  name: string;
  /** `color` from the agent definition: a hex value, a CSS theme literal, or empty. */
  color?: string | null;
}

export type AgentColorIndex = Readonly<Record<string, string>>;

export const EMPTY_AGENT_COLORS: AgentColorIndex = {};

/** The fallback palette, as literals this app can always paint. */
const PALETTE = [
  "#2563eb",
  "#0d9488",
  "#7c3aed",
  "#0891b2",
  "#c026d3",
  "#16a34a",
  "#d97706",
  "#dc2626",
  "#4f46e5",
  "#0f766e",
  "#9333ea",
  "#b45309",
];

/** The hash: `hash = hash * 31 + charCode`, unsigned, modulo the palette. */
function tone(name: string): string {
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}

/** The color for one agent: its own `color` when it declared one, else the stable hash. */
export function agentColor(name: string, custom?: string | null): string {
  const own = typeof custom === "string" ? custom.trim() : "";
  if (own) return own;
  return tone((name || "task").toLowerCase());
}

/** Index `GET /v1/subagents` by lowercased name — explicit colors only; the hash fills
 *  the rest. Lowercased because the card looks up the `subagent_type` the model wrote,
 *  which need not match the definition's casing. */
export function buildAgentColors(agents: readonly SubagentInfo[]): AgentColorIndex {
  const out: Record<string, string> = {};
  for (const agent of agents ?? []) {
    const name = typeof agent?.name === "string" ? agent.name.trim() : "";
    const color = typeof agent?.color === "string" ? agent.color.trim() : "";
    if (name && color) out[name.toLowerCase()] = color;
  }
  return out;
}

export const AgentColorsContext = createContext<AgentColorIndex>(EMPTY_AGENT_COLORS);

export function useAgentColors(): AgentColorIndex {
  return useContext(AgentColorsContext);
}
