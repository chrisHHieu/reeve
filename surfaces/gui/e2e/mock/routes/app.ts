import { HEALTH, SETTINGS } from "../data";
import { inSession, type On } from "../router";
import type { MockState } from "../state";

// The app's own settings and places: health, the settings a page changes (its own copy, so no
// change reaches another test), web search, PDF limits, the folders a session can start in, and
// the session list.

export function appRoutes(on: On, s: MockState) {
  const { sidebarState, webSearchState, sessions } = s;
  const settings: Record<string, any> = structuredClone(SETTINGS);

  on("*", "/v1/health", () => HEALTH);
  on("*", "/v1/settings", () => (sidebarState.organize ? { ...settings, sidebar_organize: sidebarState.organize } : settings));
  on("POST", "/v1/settings/sidebar-organize", ({ body }) => {
    sidebarState.organize = body.sidebar_organize;
    return { ok: true, sidebar_organize: sidebarState.organize };
  });
  on("POST", "/v1/settings/context-bar", ({ body }) => {
    Object.assign(settings, body);
    return { ok: true, context_bar: settings.context_bar };
  });
  on("POST", "/v1/settings/shell", ({ body }) => {
    settings.shell = body.shell;
    return { ok: true, shell: settings.shell };
  });
  on("POST", "/v1/settings/default-mode", ({ body }) => ({ ok: true, ...settings, default_mode: body.mode }));
  on("POST", "/v1/settings/pdf", ({ body }) => {
    Object.assign(settings, body);
    return { ok: true, pdf_fallback: settings.pdf_fallback, pdf_max_pages: settings.pdf_max_pages, pdf_max_mb: settings.pdf_max_mb };
  });
  // The page count the composer's limit checks. The mock reads no real PDF: a test writes the
  // count into the file as "%%pages=N".
  on("POST", "/v1/attachments/inspect-pdf", ({ body }) => {
    const dataUrl = String(body.data_url || "");
    const pages = /%%pages=(\d+)/.exec(atob(dataUrl.split(",")[1] || "") || "");
    return { ok: true, pages: pages ? Number(pages[1]) : 1, bytes: dataUrl.length };
  });

  // Web search: the server keeps the mode and whether a key is saved, never the key, and a test
  // search answers as the real chain would, falling back when there is no key.
  on("POST", "/v1/web-search/test", ({ body }) => webSearchState.hasKey
    ? { ok: true, query: body.query, provider: "exa", results: 3, ms: 1180, first: { title: "Widgets, explained", url: "https://widgets.example/explained" } }
    : { ok: true, query: body.query, provider: "duckduckgo", results: 3, ms: 2400, fallback_from: "exa", fallback_reason: "exa mcp: rate limited" });
  on("POST", "/v1/web-search", ({ body }) => {
    webSearchState.mode = body.provider === "duckduckgo" ? "duckduckgo" : "auto";
    if (body.api_key) webSearchState.hasKey = true;
    if (body.remove_key) webSearchState.hasKey = false;
    return { ok: true, provider: body.provider };
  });
  on("*", "/v1/web-search", () => {
    const keyed = webSearchState.hasKey;
    return {
      mode: webSearchState.mode, destination: webSearchState.mode === "auto" ? "exa → duckduckgo" : "duckduckgo",
      has_key: keyed, key_source: keyed ? "settings" : null, key_set_at: keyed ? "2026-10-05" : null, providers: ["duckduckgo", "exa"],
    };
  });

  // Where a session starts: no recent folders, a picked one, an opened one on main, or a new
  // temporary folder made when the message is sent, ready for git unless told otherwise.
  on("*", "/v1/workspaces/recent", () => ({ workspaces: [] }));
  on("POST", "/v1/workspaces/pick", () => ({ ok: true, path: "/tmp/picked-folder" }));
  on("POST", "/v1/workspaces/open", ({ body }) => ({ ok: true, path: body.path, git_branch: "main" }));
  on("POST", "/v1/workspaces/temp", ({ body }) => ({ ok: true, path: `/tmp/reeve-temp/${body.session_id}`, git: body.git !== false }));
  on("POST", inSession("save-as-project"), ({ body }) => ({ ok: true, path: body.path }));
  on("*", "/v1/sessions", () => ({ sessions }));
}
