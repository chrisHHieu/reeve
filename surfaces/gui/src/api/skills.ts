import { fetch, httpBase } from "./base";

// -- Skills ----------------------------------------------------
// Scope = folder location: "global" (every session) or "project" (one workspace).
// The session endpoints resolve the effective menu (Settings disables + session mutes).

export interface SkillRow {
  name: string;
  description: string;
  instructions: string;
  // "compat" = discovered in a community-standard root (.agents/.claude/.opencode) —
  // read-only here (P1-2).
  scope: "global" | "project" | "compat";
  // From a shared community folder: read-only entirely (no edit/delete/toggle).
  compat?: boolean;
  source: string; // "local" | "uploaded"
  /** How often the worker loaded it and when last (harness/skills/store.py). */
  uses?: number;
  last_used_at?: number | null;
  enabled: boolean;
  path: string;
  files?: number; // bundled resources beyond SKILL.md (rich skills are visible)
  // P1-5 frontmatter passthrough, surfaced in the row detail.
  when_to_use?: string;
  license?: string;
  compatibility?: string;
  metadata?: Record<string, string>;
  // P1-7 invocation policy: model_invocable false = user-only (kept out of the model's
  // catalog); user_invocable false = model-only (kept out of the composer's "/" popup).
  model_invocable?: boolean;
  user_invocable?: boolean;
}

export interface SessionSkillRow {
  /** How often the worker loaded it, and when last (harness/skills/store.py). */
  uses?: number;
  last_used_at?: number | null;
  name: string;
  description: string;
  /** A short line written for people (the skill's `summary:`), shown in place of the
   *  model-facing description where there is one. */
  summary?: string;
  scope: "global" | "project" | "compat" | "plugin";
  enabled: boolean; // false = muted for this session only
  model_invocable?: boolean;
  user_invocable?: boolean;
}

export interface SkillUploadPreview {
  ok: boolean;
  error?: string;
  token?: string;
  name?: string;
  description?: string;
  instructions?: string;
  files?: string[];
}

// Community installs (batch 3/P2) — `npx skills add` staged under a token, promoted to the
// global root on confirm. `installs` (from GET /v1/skills) is the lockfile keyed by name.
export interface SkillInstallInfo {
  source: string;
  // "github" | "url" | "git" (the reference design's sourceType vocabulary)
  sourceType: string;
  skillPath: string;
  computedHash: string;
}

export interface SkillInstallPreviewSkill {
  name: string;
  description: string;
  description_length: number;
  description_capped: boolean;
  license: string;
  metadata: Record<string, string>;
  files: string[];
  size: number;
  skillPath: string;
  computedHash: string;
  // Non-empty when the name is already taken by a shared-folder copy (installing
  // would shadow it); the confirm endpoint refuses until that copy is moved aside.
  blocked: string;
  // True when a product-global copy exists — confirm needs {overwrite: true}.
  replaces: boolean;
}

export interface SkillInstallPreview {
  ok: boolean;
  error?: string;
  token?: string;
  source?: string;
  sourceType?: string;
  agent?: string;
  command?: string[];
  skills?: SkillInstallPreviewSkill[];
  warnings?: string[];
  installable?: boolean;
}

const skillUrl = (path = "") => `${httpBase()}/v1/skills${path}`;
const jsonPost = (body: unknown, method = "POST") => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export async function listSkills(
  workspace?: string,
): Promise<{ skills: SkillRow[]; installs: Record<string, SkillInstallInfo> }> {
  const qs = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(skillUrl(qs));
  const body = await res.json();
  return { skills: body.skills ?? [], installs: body.installs ?? {} };
}

export async function createSkill(body: {
  name: string;
  description: string;
  instructions: string;
  scope?: "global" | "project";
  workspace?: string;
}): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(skillUrl(), jsonPost(body));
  return res.json();
}

export async function updateSkill(
  name: string,
  patch: { description?: string; instructions?: string; enabled?: boolean; workspace?: string },
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(skillUrl(`/${encodeURIComponent(name)}`), jsonPost(patch, "PATCH"));
  return res.json();
}

export async function revealSkill(name: string): Promise<{ ok: boolean; error?: string }> {
  // "Show folder": the backend opens the skill's folder in the OS file manager.
  const res = await fetch(skillUrl(`/${encodeURIComponent(name)}/reveal`), jsonPost({}));
  return res.json();
}

export async function deleteSkill(
  name: string,
  workspace?: string,
): Promise<{ ok: boolean; error?: string }> {
  const qs = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(skillUrl(`/${encodeURIComponent(name)}${qs}`), { method: "DELETE" });
  return res.json();
}

export async function moveSkill(
  name: string,
  scope: "global" | "project",
  workspace?: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(skillUrl(`/${encodeURIComponent(name)}/move`), jsonPost({ scope, workspace }));
  return res.json();
}

export async function stageSkillUpload(
  dataB64: string,
  filename = "",
): Promise<SkillUploadPreview> {
  const res = await fetch(skillUrl("/upload"), jsonPost({ data_b64: dataB64, filename }));
  return res.json();
}

export async function confirmSkillUpload(
  token: string,
  scope: "global" | "project" = "global",
  workspace?: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(skillUrl("/upload/confirm"), jsonPost({ token, scope, workspace }));
  return res.json();
}

export async function stageSkillInstall(body: {
  source: string;
  skills?: string[];
  agent?: string;
}): Promise<SkillInstallPreview> {
  const res = await fetch(skillUrl("/install"), jsonPost(body));
  return res.json();
}

export async function confirmSkillInstall(
  token: string,
  overwrite = false,
): Promise<{ ok: boolean; error?: string; skills?: { name: string }[] }> {
  const res = await fetch(skillUrl("/install/confirm"), jsonPost({ token, overwrite }));
  return res.json();
}


export async function sessionSkills(
  sessionId: string,
  workspace?: string,
): Promise<SessionSkillRow[]> {
  const qs = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/skills${qs}`,
  );
  return (await res.json()).skills ?? [];
}

export async function setSessionSkill(
  sessionId: string,
  skill: string,
  enabled: boolean,
  opts: { clear?: boolean; workspace?: string } = {},
): Promise<{ skills?: SessionSkillRow[]; ok?: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/skills`,
    jsonPost({
      skill,
      enabled,
      ...(opts.clear ? { clear: true } : {}),
      ...(opts.workspace ? { workspace: opts.workspace } : {}),
    }),
  );
  return res.json();
}
