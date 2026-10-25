# What Reeve does, what leaves your machine, and where your data lives

This notice describes the **default behavior of this source tree**: permissions, data
locations, credentials and outbound requests. It is not a license (see "Licensing" at the
end) and it does not describe a hosted service. Line-level references are given so each
claim can be checked against the code.

## 1. AI output and what an approval does (and does not) cover

- Model output can be wrong, unsafe or based on files you did not intend it to read. It is
  not a verification, an authorization, or a statement of fact. Check important work.
- **There is no operating-system sandbox.** Tools, shells and terminals run as *your* OS
  user, with that account's file, process and network rights
  (`harness/tools/shell.py`, `harness/tools/terminal.py`, `harness/integrations/cua_server.py`). The
  permission system (`harness/safety/permissions.py`, `harness/safety/boundary.py`) is in-process
  policy — it stops accidents and casual overreach, not a determined adversary.
- Approval covers the **call you saw**, on the axis the card names:
  - A path outside the session's folders is an *ask*; approving writes there once, or the
    folder can be added as a read-only/writable root.
  - A folder you added as **read-only** refuses writes in every mode — approving something
    else never re-opens it.
  - Reeve's own settings (`config.toml`, `secrets.json`, `reeve.db`,
    `workspace_trust.json`, `unattended.json`), files that execute later (`.git/hooks/`,
    CI configs, `.vscode/tasks.json`, `.reeve/`) and authority that outlives the
    session (saving skills, creating/updating/deleting scheduled tasks) are floors: no
    automatic path clears them, and no mode grants them.
- **An approval does not cover** these, because they happen without a per-call card:
  connecting to and listing tools from MCP servers (individual MCP tool calls are gated by
  their own `requires_approval`); the auxiliary model calls in §2; compaction summarizer
  calls; update checks and downloads; the one-time dictation model download; telemetry
  once signed in; tools auto-allowed by a **scheduled task** you armed
  (`server/manager/automation.py`); and external CLI subagents (`codex`/`claude`
  executables), which run with their own approval prompts disabled
  (`harness/orchestration/subagent_external.py`).
- Modes: `read-only` refuses consequential actions, `workspace-write` asks, and
  `danger-full-access` runs everything the floors above still allow. The plan overlay
  hard-denies consequential actions on top of any mode. `docs/config.example.toml`
  documents the config surface.

## 2. What leaves your machine

| Path | What is sent | When |
| --- | --- | --- |
| Model providers | The full provider-ready conversation: system prompt, memory, transcripts, file contents, tool arguments and results, images/PDFs as base64 | Every turn, to the provider you configured (`harness/llm/providers/*`) |
| Auxiliary model calls | Auto-title (first user messages) and the compaction summarizer — through your configured provider. No background pass reads a conversation to write memory (removed) | Automatically, around turns (`server/manager/memory_jobs.py`, `engine/compaction.py`) |
| Web search | The query text: Exa (API key) or keyless Exa MCP, or Tavily (API key) when it is the chosen engine, falling back to DuckDuckGo | When the model calls `web_search` (`harness/integrations/web/*`) |
| Browser tab, a page outside this computer | Before the tab frames it, one GET for that address through the web fetch address guard, without cookies; only the response headers are read (does the site allow being shown inside another app), the body never | When you open an outside address in the Browser tab; a local address (a dev server) is not asked about (`harness/integrations/web/frameable.py`) |
| Browser tab in the desktop app | The page is a browser of its own inside the window (a `<webview>`), so it loads what any browser loads for that site, with the site's own cookies. Those live in their own profile (`persist:reeve-browser`, under `Reeve Desktop`), never the app's; **Clear cookies and site data** in the tab's menu clears them. The page gets nothing of Reeve: no preload, no Node, sandboxed, web addresses only | When you open an address in the Browser tab (`surfaces/gui/electron/browser.ts`) |
| Browser tab: a picture of the page, its look, Save the page's HTML | Reeve's own browser loads the same address once, in a fresh profile that is deleted after (signed out of everything), and takes a picture, measures its styles, or saves its HTML to your Downloads folder | Only when you press the camera, the lightbulb or the menu item (`harness/interface/server/manager/design_export.py`) |
| Files tab, an HTML file you open | Nothing by default. The page is served from its own folder by a second local listener (the preview door, `127.0.0.1` on its own port, started when first needed) with a policy that names only that address, so it cannot fetch from or send to the internet. **Allow…** on that page lifts the policy for it until you close the file or turn it off; the page can then load code from any site and send data there, including the files of the conversation's folder it can read (pages, styles, scripts, data, pictures, fonts and media; never dotfiles, keys or source files) | Only after you answer "Let this page use the internet?" (`surfaces/gui/src/components/rail/PageFrame.tsx`) |
| Site icons of search results | A GET for the icon address Tavily reported, else the site's `/favicon.ico`, once per site, through the same address guard as web fetch; the image is kept under the state folder (`favicons/`) and the GUI reads it from the local server | When a search returns a site whose icon is not kept yet (`harness/integrations/web/favicons.py`) |
| Web fetch | A GET to the model-chosen URL; page text/images may then be sent to the model | When the model calls `web_fetch`; the SSRF guard blocks loopback/private/link-local/CGNAT and pins DNS per hop (`harness/integrations/web/guard.py`) |
| MCP servers | Tool calls and results; stdio servers are local processes, HTTP/SSE servers receive configured headers (including resolved secrets) | When the model calls an MCP tool |
| Connectors | Slack/Telegram/email and the integration tool sets talk to their own APIs with your tokens; inbound messages arrive over Socket Mode or long-poll; there is no hosted relay | Continuously while a connector is enabled (`harness/integrations/connectors/*`) |
| WhatsApp (personal) | **Off until you acknowledge its risk and link a phone.** A device linked to your WhatsApp account on WhatsApp's multi-device protocol, through an unofficial library: it talks to WhatsApp's servers as WhatsApp Web does, receives your chats' history once and new messages while Reeve runs, and sends only what you approve, one message at a time. WhatsApp can ban a number it sees doing this | While the account is linked and Reeve is open (`harness/integrations/whatsapp/*`) |
| Share | The session row and full transcript, to the `[share] endpoint` **you** configure | Only when you press Share; disabled with no endpoint (`harness/session/share.py`) |
| Updates | `latest.yml` check and installer download; the file is checked against the sha512 the manifest names | 15 s after boot and every 30 min (`surfaces/gui/electron/updates.ts`, the `publish` setting in `surfaces/gui/package.json`) |
| Dictation | A one-time model download from huggingface.co; transcription runs locally on CPU | When you first use dictation (`stt/src/lib.rs`) |
| Map tiles | Requests for the style, fonts and vector tiles of the area a `map` visual shows, at its zoom, to OpenFreeMap (`tiles.openfreemap.org`, OpenStreetMap data); no conversation text. **Settings > Visuals > Map background: Country outlines** turns it off, and a map is then drawn from the bundled outlines with nothing fetched | When a map visual is drawn, panned or zoomed (`surfaces/gui/src/components/visual/MapBlock.tsx`) |
| Browser / computer use | A Playwright MCP backend (isolated by default, or attached to your signed-in browser via CDP), the built-in Chrome DevTools MCP (**off until you switch it on**; it is fetched from the npm registry by `npx` when first started, and runs with Google's usage statistics and CrUX lookups disabled, so a performance trace does not send the page's URL to Google), and a stdio MCP server that can screenshot, read the UI tree, click/type and read the clipboard | When those MCP servers are armed and the model calls them |

No Sentry/Bugsnag/PostHog or crash-reporting SDK is present. Anything you point Reeve at
(a custom base URL, an MCP server, a webhook connector) has its own behavior.

## 3. Data on disk

`REEVE_STATE_DIR` (default `%APPDATA%\Reeve`, `~/.config/Reeve`) holds:
`secrets.json`, `config.toml`, `reeve.db` (sessions/workspaces/audit),
`conversations/<id>.jsonl` (full transcript) and `<id>.events.jsonl` (full event log,
including tool arguments and results), `automation.db`, `metrics.db`, `journal.db`,
`teams.db`, inbox/wake/subscription/router JSON, the memory as markdown files —
`projects/<project>/memory/*` (each project's notes, with `.anchors.json`: the files a
note stands on, as digests; and `.repairs.json`: which named test or lint failures were
repaired, by edits to which files, as names and digests) and `memory/*` (notes an earlier version saved about you;
no conversation loads them or sends them to a model any more, and Settings ▸
Memory lists them until you delete them) —
`whatsapp/<account>/` (a linked WhatsApp device's keys in `session.db`, and every chat and
message it has seen in `messages.db`; disconnecting deletes the folder),
`SOUL.md`, `skills/`, `logs/reeve-server.log` (the server's
own INFO record of background work; rotated
at 2 MB, three backups). The workspace keeps
`<root>/.reeve/` with `undo/` before-images, `intents/`, spilled `output/*.log`,
`plans/`, and its own `config.toml`/`mcp.json`/`skills/`. Each conversation's own folder
(under the Conversations folder in Settings ▸ General) keeps `attachments/`: a copy of every
file you attached that is not a picture, so its card in the message opens it.

Transcripts, event logs, `parked.json` (unauthorized inbound message bodies), spill files,
attachment copies,
undo before-images and the sidecar log can contain **secrets and full user content**.
Deleting the app does not delete backups, other state dirs, remote hosts, or anything
already sent to a provider or connector.

## 4. Credentials

- `secrets.json` is **plaintext JSON**; protection is OS permissions only (0600 on POSIX,
  a user-only ACL via `icacls` on Windows; `harness/safety/secrets.py`). It holds provider API
  keys, Codex/cloud/MCP OAuth tokens (including refresh tokens), and connector tokens.
  (An older build's `memory_embedding` profile may remain; nothing reads it since memory
  stopped using embeddings.)
  `${VAR}` references resolve from the process environment or a `.env` file beside it.
- No connector's OAuth client id or secret ships in the source. One-click sign-in reads them
  from the process environment or that same `.env` (the keys are listed in the README); without
  them a connector offers only its manual way. The Claude and ChatGPT plan sign-ins use the
  vendors' own public client ids, which are in the source.
- `[share] token` lives plaintext in `config.toml`.
- The only OS credential store used is Windows Credential Manager, for remembered remote
  SSH passwords (`harness/config/credentials.py`); other platforms store nothing and ask again.
- The GUI's API token is generated per launch, injected into the window and never
  persisted by the desktop shell; a standalone `reeve-server` writes a `sidecar-<port>.token`
  (0600) and deletes it on exit. If the token environment variable is empty the local API
  is **unauthenticated** (`harness/interface/server/app.py`) — do not expose the port beyond
  loopback.

## 5. Licensing

Reeve's own code is under the MIT License (`LICENSE`), the dictation host in `stt/` included.
There is no `THIRD-PARTY-NOTICES.md` yet: the dependencies in `pyproject.toml` and
`surfaces/gui/package.json` (Python packages, the TUI stack, the React GUI, and anything bundled
into a packaged build) keep their own licences. Do not treat this file as a license.

Programs a packaged build carries, not in either manifest: a portable Node runtime (for the
browser MCP server) and ripgrep 15.2.0 (`sidecar/rg/`, MIT or Unlicense, its licence files
beside it), which `grep` and `list_files` run. Both are downloaded by the build scripts in
`packaging/`; ripgrep's archive is checked against the hash its release publishes.

The GUI's line icons (`surfaces/gui/src/components/Icon.tsx`) are Lucide's geometry, copied from
lucide-static 0.469.0 (ISC), not a dependency.

Fonts bundled for design pages (`harness/tools/design/fonts/`): Plus Jakarta Sans, Be Vietnam Pro,
Newsreader, Lora, Space Grotesk, IBM Plex Sans, JetBrains Mono, Bricolage Grotesque and Archivo, as
woff2 from Fontsource 5.3.0, each under the SIL Open Font License 1.1 with its licence beside it
(`OFL.txt`). A design page copies the families it uses beside itself.

Data bundled with the app, not in either manifest: the `map` visual's country outlines
(`surfaces/gui/src/components/visual/worldTopo.json`, from world-atlas 2.0.2, ISC, itself
Natural Earth 1:50m, public domain) and country names
(`harness/tools/visual/countries.json`, from i18n-iso-countries 7.14.0, MIT), both built by
`scripts/build_world_map.mjs`. Maps are drawn with MapLibre GL JS (BSD-3-Clause); the online
basemap is OpenFreeMap's (© OpenMapTiles, data © OpenStreetMap contributors, ODbL), credited on
the map itself.
