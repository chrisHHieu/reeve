import { fetch, httpBase } from "./base";

// -- session roots (orphan session: scratch + added folders) -------------------
export interface RootInfo {
  path: string;
  writable: boolean;
  label: string;
  primary: boolean;
  exists: boolean;
  /** True when this root is a path on a bound SSH host: not a local folder,
   *  so folder-picking features must not offer to manage it. */
  remote?: boolean;
  /** The session took this folder itself: a call reached its project and was allowed.
   *  It is a permission the session holds, listed in the mode menu, not in Files. */
  reached?: boolean;
}

export async function getRoots(sessionId: string): Promise<RootInfo[]> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/roots`);
  return (await res.json()).roots ?? [];
}

export async function addRoot(
  sessionId: string,
  path: string,
  writable: boolean,
): Promise<{ ok: boolean; error?: string; roots?: RootInfo[] }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/roots`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, writable }),
  });
  return res.json();
}

export async function removeRoot(
  sessionId: string,
  path: string,
): Promise<{ ok: boolean; error?: string; roots?: RootInfo[] }> {
  const q = new URLSearchParams({ path });
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/roots?${q.toString()}`,
    { method: "DELETE" },
  );
  return res.json();
}
