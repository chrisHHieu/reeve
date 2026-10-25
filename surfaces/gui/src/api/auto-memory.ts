import { fetch, httpBase } from "./base";

// -- Auto memory: machine-local markdown notes (the agent writes them itself) -

export interface AutoMemoryFile {
  name: string; // "MEMORY.md" (the index) or a topic file like "feedback_testing.md"
  title: string; // first heading, else the frontmatter description, else the filename stem
  description?: string; // frontmatter `description`; "" for the index
  type: string; // user | feedback | project | reference (topic files); "" for the index
  size: number;
  modified_at: string; // ISO
  injected: boolean; // true for MEMORY.md — the index rides in the system prompt
}

/** Which memory directory: a project's notes (the default) or who the user is, one
 *  directory for every project. */
export type MemoryScope = "project" | "user";

const scopeParam = (scope: MemoryScope) => (scope === "user" ? "&scope=user" : "");

export async function getAutoMemory(
  workspace: string,
  scope: MemoryScope = "project",
): Promise<{ files: AutoMemoryFile[]; dir?: string; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/memory/auto?workspace=${encodeURIComponent(workspace)}${scopeParam(scope)}`,
  );
  return res.json();
}

export async function readAutoMemoryFile(
  workspace: string,
  name: string,
  scope: MemoryScope = "project",
): Promise<{ name: string; exists: boolean; content: string; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/memory/auto/file?workspace=${encodeURIComponent(workspace)}&name=${encodeURIComponent(name)}${scopeParam(scope)}`,
  );
  return res.json();
}

export async function writeAutoMemoryFile(
  workspace: string,
  name: string,
  content: string,
  mode: "append" | "overwrite" = "overwrite",
  scope: MemoryScope = "project",
): Promise<{ ok: boolean; warning?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/memory/auto/file`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace, name, content, mode, scope }),
  });
  return res.json();
}

/** Delete a memory and its MEMORY.md line. */
export async function deleteAutoMemoryFile(
  workspace: string,
  name: string,
  scope: MemoryScope = "project",
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/memory/auto/file?workspace=${encodeURIComponent(workspace)}&name=${encodeURIComponent(name)}${scopeParam(scope)}`,
    { method: "DELETE" },
  );
  return res.json();
}

/** Copy one machine-local memory into the project's `.agents/memory/` (committed,
 *  reviewable). A one-way export the user asks for — the local file stays canonical. */
export async function promoteAutoMemoryFile(
  workspace: string,
  name: string,
): Promise<{ ok: boolean; target?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/memory/auto/promote`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace, name }),
  });
  return res.json();
}
export interface ProviderField {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string;
  placeholder: string;
  default?: string; // pre-filled editable value (e.g. an OpenAI-compatible vendor's endpoint)
  // Non-empty → segmented choice, not a text input. tag = tiny badge ("Easiest");
  // desc = one-liner atop the method panel; command = copyable terminal command.
  choices?: { value: string; label: string; tag?: string; desc?: string; command?: string }[];
  show_when?: Record<string, string> | null; // render only while these fields hold these values
}

export interface ProviderInfo {
  name: string;
  title: string;
  needs_key: boolean;
  fields: ProviderField[];
  configured: boolean;
  values: Record<string, string>; // non-secret stored values (e.g. base_url), for prefilling
  suggested_models: string[]; // bare model-name suggestions for the "add model" datalist
  blurb?: string; // one-line note under the title ("Uses X's OpenAI-compatible API…")
  key_set_at?: string | null; // ISO date the key was last (re)saved — absent for env-only config
  last_used_at?: number | null; // epoch secs the provider last served a completion
  // OAuth providers (auth === "oauth"): browser sign-in instead of a key form.
  auth?: string | null;
  signed_in?: boolean;
  account?: string | null; // signed-in account label (email or id)
  authorizing?: boolean;
  last_error?: string | null;
}
