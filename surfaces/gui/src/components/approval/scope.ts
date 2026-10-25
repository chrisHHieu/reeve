// Where an action asked about acts, in words a person can consent to, for the approval card
// and the parked one (InboxItemCard) alike.
//
// There are three answers, not two. "Stays on this computer" is a promise, and it used to be the
// answer for anything this file had not heard of, every MCP tool included (their category is "mcp",
// not "connector"): a third-party server opening a ticket in someone else's tracker was described as
// staying on the person's machine, on the one screen where that decides consent. A tool nobody here
// can vouch for now says so.

import type { ApprovalSubject } from "../../humanize";

export interface ScopeNote {
  text: string;
  /** Known to act outside this computer. */
  external: boolean;
  /** Where it acts cannot be established. Not the same as `external`: one is a fact, the other the
   *  lack of one. Both get the careful look; only `external` may name a destination. */
  unknown: boolean;
}

export const FILE_WRITES = new Set(["write_file", "replace_in_file", "apply_patch", "apply_unified_diff"]);
/** Sends that leave the machine for a chat: the warm border and a named destination. */
export const EXTERNAL = new Set(["send_message", "send_file"]);

// What this screen can state as fact: writes the permission engine has already held to the
// session's folders, and an automation kept on this machine. Anything else is unknown on purpose:
// adding a name here claims to know where it acts, so the set only grows with a reason.
const LOCAL = new Set([...FILE_WRITES, "create_scheduled_task"]);

const CHAT_NAMES: Record<string, string> = { slack: "Slack", telegram: "Telegram" };

/** The host a domain grant from a fetch card would cover, spelled as the server mints it in
 *  `allow_domain_for_session`: lower case, `www.` dropped. The button must say exactly what it
 *  grants. "" when the URL does not parse. */
export function grantHost(url: any): string {
  try {
    return new URL(String(url ?? "")).hostname.toLowerCase().replace(/^www\./, "");
  } catch {
    return "";
  }
}

export function scopeNote({ name, args, category, presented }: ApprovalSubject): ScopeNote {
  const here = (text: string): ScopeNote => ({ text, external: false, unknown: false });
  const out = (destination: string): ScopeNote => ({ text: `leaves this computer → ${destination}`, external: true, unknown: false });

  // A skill's answer is WHERE it goes: the place to find, edit or switch it off.
  if (name === "save_skill") return here("saves to Settings ▸ Skills");
  if (category === "connector") return { text: "acts on a connected service", external: true, unknown: false };
  // A request out reaches the network by being made: never "stays on this computer".
  if (name === "web_fetch") return out(grantHost(args?.url) || "the web");
  if (name === "web_search") return out("your search provider");
  if (EXTERNAL.has(name)) {
    const platform = String(args?.target ?? "").split(":")[0];
    return out(CHAT_NAMES[platform] || platform || "a connected chat");
  }
  // A tool that declared it reaches out is believed: that is what its contract is for, and the only
  // way a tool unknown here can get a definite answer rather than the careful one.
  if (presented?.kind === "send" || presented?.kind === "fetch") return out("a service outside this computer");
  // "Runs", not "stays": the process is local, but a command can reach the network as any program
  // can, and the old words read as a promise nobody made. What vouches for a command is the server's
  // read-only check.
  if (name === "run_shell") return here("runs on this computer");
  if (LOCAL.has(name)) return here("stays on this computer" + (name === "write_file" && args?.overwrite ? " · overwrites the existing file" : ""));
  // Reeve's own MCP sidecars are local by how they are built, so unlike a third party's tool their
  // place can be named.
  if (name.startsWith("mcp__")) {
    const server = name.split("__")[1];
    if (server === "computer-use") return here("controls mouse and keyboard on this computer");
    if (server === "browser") return here("acts in the local headless browser");
  }
  return { text: "can’t tell where this acts", external: false, unknown: true };
}

/** A call's arguments as `key=value` pairs on one line, each value cut at 96 characters: what a
 *  reader falls back to when a tool says nothing of itself. */
export function shortArgs(args: any): string {
  if (!args || typeof args !== "object") return "";
  return Object.entries(args)
    .map(([key, value]) => {
      const text = typeof value === "string" ? value : JSON.stringify(value);
      return `${key}=${(text.length > 96 ? text.slice(0, 95) + "..." : text).replace(/\n/g, " ")}`;
    })
    .join("  ");
}
