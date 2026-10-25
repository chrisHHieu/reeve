import { fetch, httpBase } from "./base";

// SSH remote environments — phase 1, with password auth (askpass server-side).
// Profiles are local config: identity_file is a path the server expands at invocation,
// and a password is never in the JSON or on the wire — `remembered`/`has_password` are
// booleans, and a remembered secret lives in the OS credential store. `testRemoteEnvironment`
// runs the real probe server-side; a transient password sent with it is used for that call
// only. Here it is only a request.
export interface RemoteEnvironmentProfile {
  id: string;
  name: string;
  host: string;
  user: string | null;
  port: number;
  identity_file: string | null;
  /** The user asked the OS credential store to keep this host's password. */
  remembered: boolean;
  /** A remembered password is actually readable right now (the honest sibling flag). */
  has_password: boolean;
  created_at: string;
  updated_at: string;
}

export interface RemoteEnvironmentInput {
  name: string;
  host: string;
  user?: string;
  port?: number;
  identity_file?: string;
  /** Transient unless `remember` is set; when set it goes to the OS credential store. */
  password?: string;
  remember?: boolean;
}

export interface RemoteEnvironmentTestResult {
  ok: boolean;
  platform: "linux" | "darwin" | null;
  arch: "x64" | "arm64" | null;
  home: string | null;
  error: string | null;
}

// The editor's draft keeps port as a string so a half-typed number is representable.
export interface RemoteEnvironmentDraft {
  name: string;
  host: string;
  user: string;
  port: string;
  identity_file: string;
  // Never sent back in a response; cleared for every fresh edit. `remember` mirrors
  // the profile flag, so an unchanged save keeps the stored secret. `stored` means a
  // remembered secret is actually readable (drives the "leave blank to keep it" copy).
  password: string;
  remember: boolean;
  stored: boolean;
}

export function emptyRemoteEnvironmentDraft(): RemoteEnvironmentDraft {
  return {
    name: "",
    host: "",
    user: "",
    port: "",
    identity_file: "",
    password: "",
    remember: false,
    stored: false,
  };
}

export function draftFromProfile(profile: RemoteEnvironmentProfile): RemoteEnvironmentDraft {
  return {
    name: profile.name,
    host: profile.host,
    user: profile.user ?? "",
    port: String(profile.port ?? 22),
    identity_file: profile.identity_file ?? "",
    password: "",
    remember: profile.remembered === true,
    stored: profile.has_password === true,
  };
}

/** The session-wire shape of a bound environment. `identity_file`
 *  is a controller-local path and deliberately never crosses this boundary; the id lets
 *  a surface match a binding against the full profile list. */
export interface RemoteEnvironmentSummary {
  id: string;
  name: string;
  user: string | null;
  host: string;
  port: number;
}

/** `user@host` for display; port shown only when it is not the SSH default. */
export function formatRemoteEnvironmentDestination(
  profile: Pick<RemoteEnvironmentProfile, "user" | "host" | "port">,
): string {
  const destination = profile.user ? `${profile.user}@${profile.host}` : profile.host;
  return profile.port && profile.port !== 22 ? `${destination}:${profile.port}` : destination;
}

/** The session readout: `user@host:path`, where the path is the
 *  absolute workspace on the host — the one string that says which machine a session's
 *  files live on. Falls back to the bare destination when no workspace is bound yet.
 *
 *  Only an absolute POSIX path is a remote workspace. Anything else — a Windows drive
 *  path, a local spelling a caller mistakenly passed as the remote path — is a fact
 *  about the WRONG machine, so it is never appended: the destination alone is the
 *  honest readout. This is the last line of defense for every readout built here
 *  (composer chips/footer, setup row, topbar). */
export function formatRemoteEnvironmentReadout(
  environment: Pick<RemoteEnvironmentSummary, "user" | "host" | "port"> | null | undefined,
  workspace: string | null | undefined,
): string {
  if (!environment) return "";
  const destination = formatRemoteEnvironmentDestination(environment);
  const path = typeof workspace === "string" ? workspace.trim() : "";
  return path.startsWith("/") ? `${destination}:${path}` : destination;
}

/** Client-side mirror of the server's validation; returns the first problem, or null. */
export function validateRemoteEnvironmentDraft(draft: RemoteEnvironmentDraft): string | null {
  if (!draft.name.trim()) return "Name is required.";
  const host = draft.host.trim();
  if (!host) return "SSH host is required.";
  if (host.startsWith("-") || /[\s\0]/.test(host)) {
    return "SSH host must be a hostname, address, or SSH config alias without whitespace.";
  }
  const user = draft.user.trim();
  if (user && (!/^[A-Za-z0-9._-]+$/.test(user) || user.startsWith("-"))) {
    return "SSH user contains unsupported characters.";
  }
  const port = draft.port.trim();
  if (port !== "") {
    const parsed = Number(port);
    if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
      return "SSH port must be an integer between 1 and 65535.";
    }
  }
  if (/[\0\r\n]/.test(draft.identity_file)) {
    return "SSH identity file cannot contain newlines or NUL bytes.";
  }
  return null;
}

function inputFromDraft(draft: RemoteEnvironmentDraft): RemoteEnvironmentInput {
  const port = draft.port.trim();
  // user/identity_file always ride along (as "" when cleared) so a PATCH can clear them;
  // the server normalizes "" to null. An empty port means the SSH default. password is
  // sent verbatim (never trimmed — spaces are opaque bytes); with `remember` false the
  // server uses it for nothing but leaves the file alone.
  return {
    name: draft.name.trim(),
    host: draft.host.trim(),
    user: draft.user.trim(),
    identity_file: draft.identity_file.trim(),
    port: port ? Number(port) : 22,
    password: draft.password,
    remember: draft.remember,
  };
}

export interface RemoteEnvironmentMutationResult {
  ok: boolean;
  environment?: RemoteEnvironmentProfile;
  error?: string;
}

export async function listRemoteEnvironments(): Promise<RemoteEnvironmentProfile[]> {
  const res = await fetch(`${httpBase()}/v1/remote-environments`);
  const body = await res.json();
  return Array.isArray(body?.environments) ? body.environments : [];
}

export async function createRemoteEnvironment(
  draft: RemoteEnvironmentDraft,
): Promise<RemoteEnvironmentMutationResult> {
  const res = await fetch(`${httpBase()}/v1/remote-environments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(inputFromDraft(draft)),
  });
  return res.json();
}

export async function updateRemoteEnvironment(
  id: string,
  draft: RemoteEnvironmentDraft,
): Promise<RemoteEnvironmentMutationResult> {
  const res = await fetch(`${httpBase()}/v1/remote-environments/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(inputFromDraft(draft)),
  });
  return res.json();
}

export async function deleteRemoteEnvironment(
  id: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/remote-environments/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  return res.json();
}

export async function testRemoteEnvironment(
  id: string,
  password?: string,
): Promise<RemoteEnvironmentTestResult> {
  const res = await fetch(
    `${httpBase()}/v1/remote-environments/${encodeURIComponent(id)}/test`,
    {
      method: "POST",
      ...(password
        ? {
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password }),
          }
        : {}),
    },
  );
  const body = await res.json();
  if (typeof body?.ok !== "boolean") {
    return {
      ok: false,
      platform: null,
      arch: null,
      home: null,
      error: body?.error || "The connection test could not run.",
    };
  }
  return body;
}

/** One folder in a remote directory listing (the workspace picker's browser). */
export interface RemoteDirectoryEntry {
  name: string;
  path: string;
}

export interface RemoteDirectoryListing {
  ok: boolean;
  /** The path the HOST resolved — what "Use this folder" would bind. */
  path: string;
  entries: RemoteDirectoryEntry[];
  error?: string;
}

/** List a host directory's immediate subdirectories. An omitted path means the host's
 *  $HOME (the picker's default); the server resolves the path on the host and returns
 *  the physical result, so `..` can never walk the listing outside the requested tree. */
export async function listRemoteDirectories(
  environmentId: string,
  path?: string,
): Promise<RemoteDirectoryListing> {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  const res = await fetch(
    `${httpBase()}/v1/remote-environments/${encodeURIComponent(environmentId)}/directories${query}`,
  );
  const body = await res.json().catch(() => null);
  if (body?.ok !== true) {
    return {
      ok: false,
      path: path ?? "",
      entries: [],
      error: body?.error || "Could not list that folder on the host.",
    };
  }
  return {
    ok: true,
    path: typeof body.path === "string" ? body.path : (path ?? ""),
    entries: Array.isArray(body.entries) ? body.entries : [],
  };
}
