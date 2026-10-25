import { fetch, httpBase } from "./base";

// -- language servers ----------------------------------------------------------
// The `lsp` tool's pool (harness/integrations/lsp): one client per (workspace, language),
// spawned lazily on first use. The Status popover's LSP tab reads this; there is no
// settings surface for it — config lives in `[lsp]` / `[lsp.servers.*]` in config.toml.
export interface LspServer {
  language: string;
  language_id: string;
  workspace: string;
  command: string;
  // False = the process died; the next `lsp` call replaces it. The only failure state.
  alive: boolean;
}

export interface LspStatus {
  // `[lsp] enabled` from the GLOBAL config (per-workspace overrides only apply once a
  // session opens that workspace, which this route does not know).
  enabled: boolean;
  servers: LspServer[];
  // The languages the shipped table can serve — what could start on first use.
  known_languages: string[];
}

export async function getLspStatus(): Promise<LspStatus> {
  const res = await fetch(`${httpBase()}/v1/lsp`);
  return res.json();
}
