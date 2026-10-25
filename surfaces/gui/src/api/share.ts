import { fetch, httpBase } from "./base";

// `/share` has NO hosted backend here — deliberately. The harness uploads the
// exported session JSON to the endpoint the person configured in `[share] endpoint` and
// returns the URL to copy; without one the status says `configured: false` and the slash
// row stays disabled with that reason. Nothing pretends a cloud exists.

export interface SessionShare {
  url: string;
  share_id?: string;
  shared_at?: string;
  endpoint?: string;
}

export interface SessionShareStatus {
  configured: boolean;
  /** True only when a revoke endpoint is configured AND this share has an id. */
  can_revoke: boolean;
  share: SessionShare | null;
}

export async function getSessionShare(sessionId: string): Promise<SessionShareStatus> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/share`,
  );
  return res.json();
}

/** Upload the session's export to the configured endpoint; returns the URL to copy. */
export async function shareSession(
  sessionId: string,
): Promise<{ ok: boolean; url?: string; share_id?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/share`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return res.json();
}

/** Revoke a stored share — only meaningful when the endpoint supports it. */
export async function unshareSession(
  sessionId: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/unshare`,
    { method: "POST", headers: { "Content-Type": "application/json" } },
  );
  return res.json();
}
