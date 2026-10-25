/** App-level pure helpers, split out of App.tsx (behaviour unchanged). */
import type { ApprovalDecision, Item, SessionInfo } from "../types";

export const newId = () =>
  (crypto as any).randomUUID ? crypto.randomUUID().slice(0, 12) : Math.random().toString(36).slice(2, 14);

// Tools whose success means a new/changed file should show up under Artifacts right away.
export const FILE_WRITE_TOOLS = new Set(["write_file", "apply_patch", "apply_unified_diff", "replace_in_file"]);

export const RAIL_HIDDEN_KEY = "reeve:rail-hidden:v1";
export const NAV_COLLAPSED_KEY = "reeve:nav-collapsed:v1";
// Per-device rail widths (the reference design persists its session/file-tree widths globally per server
// scope; the panel's open state is global there too, so one key each — not per session).
// v2 (2026-10-13): the pane's default became "all the room the conversation does not need";
// a width dragged under the old default would hide the new one for good.
export const RAIL_WIDTH_KEY = "reeve:rail-width:v2";
export const RAIL_TREE_WIDTH_KEY = "reeve:rail-tree-width:v1";
export const FM_TREE_WIDTH_KEY = "reeve:fm-tree-width:v1";

export function sessionTs(s: SessionInfo): number {
  return Date.parse(s.updated_at || "") || Number(s.updated_at) || 0;
}

/** The composer facts a session record carries — the model and permission mode
 *  the session was last saved with. The reload path reads them from the same
 *  `GET /v1/sessions` row the sidebar already holds, so a boot, resume or sidebar pick
 *  never paints (or sends) the app defaults while the WS `ready` frame is in flight.
 *  Absent/empty fields are DROPPED, never applied as defaults: a record without a fact
 *  must fall through to the frame, not overwrite it with "".
 */
/** A row's workspace ON THE BOUND HOST (the path half of `user@host:path`), or null
 *  for a local session. The server sends `remote_workspace` explicitly; the fallback
 *  covers a pre-field row, where a bound session's `workspace` IS the host's POSIX
 *  path. Surfaces render remote readouts from this, never from the local workspace. */
export function sessionRemoteWorkspace(
  info:
    | Pick<SessionInfo, "remote_workspace" | "environment_id" | "workspace">
    | null
    | undefined,
): string | null {
  if (!info) return null;
  if (info.remote_workspace) return info.remote_workspace;
  return info.environment_id && info.workspace ? info.workspace : null;
}

export function sessionComposerState(
  info: Pick<SessionInfo, "model" | "mode"> | null | undefined,
): { model?: string; mode?: string } {
  const out: { model?: string; mode?: string } = {};
  if (!info) return out;
  if (info.model) out.model = info.model;
  if (info.mode) out.mode = info.mode;
  return out;
}


export function resolveLastApproval(items: Item[], decision: ApprovalDecision): Item[] {
  const copy = [...items];
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    if (it.kind === "approval" && !it.resolved) {
      copy[i] = { ...it, resolved: decision };
      break;
    }
  }
  return copy;
}

export function resolveLastToolReq(items: Item[], resolved: "installed" | "skipped"): Item[] {
  const copy = [...items];
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    if (it.kind === "toolreq" && !it.resolved) {
      copy[i] = { ...it, resolved };
      break;
    }
  }
  return copy;
}

/** What the surface tells the user a retry is FOR. The engine sends the provider's own
 *  words in `error`; a dropped connection is the one case worth naming in plain language,
 *  because "RemoteProtocolError" means nothing to the person reading it. */
export function retryReason(d: { error?: string; discard_partial?: boolean }): string {
  const raw = (d.error || "").trim();
  if (/empty response/i.test(raw)) {
    // Name the upstream when the endpoint gave one: on an aggregator the same model id
    // covers several machines, and WHICH one answered with nothing is the only part of
    // this the user (or we) can act on.
    const servedBy = /\(served by ([^)]+)\)/.exec(raw)?.[1];
    return servedBy ? `The model returned nothing (${servedBy})` : "The model returned nothing";
  }
  return d.discard_partial ? "Connection dropped mid-answer" : "The request failed";
}

export function resolveLastPlan(items: Item[], resolved: "approved" | "rejected"): Item[] {
  const copy = [...items];
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    if (it.kind === "planreq" && !it.resolved) {
      copy[i] = { ...it, resolved };
      break;
    }
  }
  return copy;
}

export function resolveLastQuestion(items: Item[], answer: string): Item[] {
  const copy = [...items];
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    if (it.kind === "question" && !it.resolved) {
      copy[i] = { ...it, resolved: answer };
      break;
    }
  }
  return copy;
}
