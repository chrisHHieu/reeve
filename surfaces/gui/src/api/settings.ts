import { fetch, httpBase } from "./base";

// -- settings (model API key, default model, onboarding) ----------------------
export interface ModelSettings {
  provider: string;
  model: string;
  // The permission mode a new conversation starts in (Settings ▸ General). Absent on
  // older backends.
  default_mode?: string;
  models: string[];
  has_key: boolean;
  model_ready: boolean; // can the default model's provider actually run (any provider)?
  source: "env" | "store" | null;
  onboarded: boolean;
  scratch_base: string;
  secrets_path: string;  // OS-native on-disk location the server reports (not hardcoded)
  // Sidebar: sessions shown under Recent before "Show more" (default 5, 1–50).
  sessions_peek?: number;
  // Sidebar: the sessions grouped by their folder, or one list by time (default "project").
  sidebar_organize?: SidebarOrganize;
  // Composer: show the context-window fill bar (default FALSE; absent → the chip shows
  // the session total). The usage popover keeps both numbers regardless.
  context_bar?: boolean;
  // Progressive tool discovery: with this on, MCP tools leave the wire and the
  // model reaches them through tool_search/tool_describe/tool_call. Applies on the NEXT
  // session build; absent on older backends → treated as off.
  tool_discovery?: boolean;
  // When the model may show a visual: on its own judgement, only when asked, or never
  // (the tool is not offered). Applies on the NEXT session build; absent → "auto".
  visuals?: VisualsSetting;
  // The default shell for new sessions and the terminal panel ("" is the OS's own), and the
  // shells this machine may make it. Absent on older backends.
  shell?: string;
  shells?: ShellChoice[];
  // Whether a delegation call pick its own LLM route
  // (provider+model in-process; a model from its own account for codex/claude-code)?
  // Applies on the NEXT session build; absent → on (the feature shipped on).
  subagent_model_selection?: boolean;
  // Experimental connectors (use at your own risk) are listed only when this is on.
  experimental_connectors?: boolean;
  // Curated-matrix display names ({full id → "GLM-5.2 · via Together"}); custom models absent.
  model_labels?: Record<string, string>;
  // {full id → context window in tokens}, verified matrix entries only — drives the
  // composer's context-fill meter (absent id → the meter hides). Optional for older backends.
  model_context_windows?: Record<string, number>;
  // Token savings (PDF attachments): fallback for models without native PDF support,
  // and attach-time thresholds. Optional so the GUI is robust to an older backend.
  pdf_fallback?: "text" | "images";
  pdf_max_pages?: number; // default 20, 1–100
  pdf_max_mb?: number; // default 10, 1–10
  // Auto-compaction is a system behavior configured in config.toml
  // `[compaction]` — deliberately not a UI. Its values still ride /v1/settings so a
  // surface can report what is in force, but the GUI reads them nowhere.
}

export interface PdfSettings {
  pdf_fallback: "text" | "images";
  pdf_max_pages: number;
  pdf_max_mb: number;
}

/** Persist the Token-savings PDF settings (fallback mode + attach thresholds). */
export async function setPdfSettings(
  patch: Partial<PdfSettings>,
): Promise<{ ok: boolean; error?: string } & Partial<PdfSettings>> {
  const res = await fetch(`${httpBase()}/v1/settings/pdf`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return res.json();
}

/** Local page/size probe for a PDF data URL — the composer's attach-time threshold check. */
export async function inspectPdf(
  dataUrl: string,
): Promise<{ ok: boolean; pages?: number; bytes?: number; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/attachments/inspect-pdf`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data_url: dataUrl }),
  });
  return res.json();
}

/** Persist whether the composer shows the context-window fill bar. */
export async function setContextBar(
  shown: boolean,
): Promise<{ ok: boolean; context_bar?: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/context-bar`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ context_bar: shown }),
  });
  return res.json();
}

/** The permission mode new conversations start in. A conversation already started keeps
 * its own; this changes only the ones opened after it. */
export async function setDefaultMode(
  mode: string,
): Promise<{ ok: boolean; default_mode?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/default-mode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  return res.json();
}

export interface ShellChoice {
  kind: string; // "powershell" | "pwsh" | "bash"
  label: string; // "PowerShell 5.1", "PowerShell 7", "Git Bash", "bash"
}

/** The shell a command runs in when it names none ("" is the OS's own). Applies to the next
 * session and the terminal panel: a session keeps the one its `run_shell` schema names. */
export async function setDefaultShell(shell: string): Promise<{ ok: boolean; shell?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/shell`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ shell }),
  });
  if (!res.ok) return { ok: false, error: `The server did not accept that setting (HTTP ${res.status}). Restart Reeve and try again.` };
  return res.json();
}

export type VisualsSetting = "auto" | "asked" | "off";

/** When the model may show a visual. Applies to the next session build: the tool
 * and its description are part of the cached prefix. */
export async function setVisuals(visuals: VisualsSetting): Promise<{ ok: boolean; visuals?: VisualsSetting; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/visuals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ visuals }),
  });
  if (!res.ok) return { ok: false, error: `The server did not accept that setting (HTTP ${res.status}). Restart Reeve and try again.` };
  return res.json();
}

/** Progressive tool discovery: MCP tools go behind the search bridge.
 * Applies to the next session build: the tool list is part of the cached prefix. */
/** Show experimental connectors on the Connectors page (each still asks for its risk to be
 * acknowledged before it connects). */
export async function setExperimentalConnectors(value: boolean): Promise<{ ok: boolean; enabled?: boolean }> {
  const res = await fetch(`${httpBase()}/v1/settings/experimental-connectors`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
  return res.json();
}

export async function setToolDiscovery(
  enabled: boolean,
): Promise<{ ok: boolean; tool_discovery?: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/tool-discovery`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  return res.json();
}

/** Whether a delegation call may pick its own LLM route.
 * Applies to the next session build, like the tool-discovery toggle. */
export async function setSubagentModelSelection(
  enabled: boolean,
): Promise<{ ok: boolean; subagent_model_selection?: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/subagent-model-selection`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  // A server too old to know the route answers 404 with `{detail: …}` — the caller must
  // hear that instead of reading a missing field as "unchanged".
  if (!res.ok) {
    return {
      ok: false,
      error: `The server did not accept that setting (HTTP ${res.status}). Restart Reeve and try again.`,
    };
  }
  return res.json();
}

export type SidebarOrganize = "project" | "list";

/** Persist how the sidebar lays out its sessions: by project, or in one list. */
export async function setSidebarOrganize(
  value: SidebarOrganize,
): Promise<{ ok: boolean; sidebar_organize?: SidebarOrganize; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/sidebar-organize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sidebar_organize: value }),
  });
  return res.json();
}

/** Persist how many sessions a sidebar group shows before "Show more". */
export async function setSessionsPeek(
  n: number,
): Promise<{ ok: boolean; sessions_peek?: number; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/sessions-peek`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sessions_peek: n }),
  });
  return res.json();
}

export async function setScratchBase(
  path: string,
): Promise<{ ok: boolean; error?: string; scratch_base?: string }> {
  const res = await fetch(`${httpBase()}/v1/settings/scratch-base`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  return res.json();
}
