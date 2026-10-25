// The Status popover: one topbar button that answers "is everything my agent has working?"
// without a trip to Settings.
//
// It is the state of everything attached to the agent: the model's connection, MCP servers
// (GET /v1/mcp), connectors and their live health (GET /v1/connectors), skills (GET
// /v1/skills) and language servers (GET /v1/lsp). Its mark is a puzzle piece: the plug it had
// already means "Connectors" everywhere else, and this is more than connectors (owner,
// 2026-10-13).
//
// The head is the verdict ("2 things need your attention" / "Everything is working"). What is
// broken comes first, whatever kind it is, with one sentence, one action, and the raw message
// behind "Details". What works is four lines that open. It used to be four tabs that opened on
// MCP, so a broken connector was a tab away, under a red dot beside the word "Connected".
//
// Data refreshes when the popover opens and every 5s while it stays open (the cadence the
// Connectors page already uses). The last good snapshot SURVIVES a sidecar drop, said to be
// old: this is the popover you open because things look dead, so it must not be the one
// surface that depends on the thing that is down.
//
// The trigger dot is computed from the same snapshot the body renders, so trigger and body
// can never disagree. It hides while the popover is open — the popover is the answer.

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  connectMcp,
  getConnectors,
  getLspStatus,
  getMcpServers,
  listSkills,
  reloadMcp,
  type Connector,
  type LspStatus,
  type McpServer,
  type SkillRow,
} from "../api";
import { Icon } from "./Icon";
import { FileIcon } from "./FileIcon";
import { Disclosure } from "../ui";
import { ConnectorIcon } from "../connectors/ConnectorIcon";
import { CopyButton } from "./primitives/CopyButton";
import { Tooltip } from "../ui/Tooltip";
import { mcpStatusLine } from "./connectors/CustomMcp";
import { baseName } from "../paths";

const POLL_MS = 5000;
// The trigger's dot must be truthful before the popover is ever opened, so the snapshot is
// fetched on mount too; while closed it ages on a slower cadence (the sidebar's own polls
// are 5s/15s, and five cheap GETs every 30s is noise next to them).
const CLOSED_POLL_MS = 30000;

type Level = "ok" | "warn" | "err" | "off";
type Group = "connectors" | "mcp" | "skills" | "lsp";

const DOT: Record<Level, string> = {
  ok: "bg-ok",
  warn: "bg-warnInk",
  err: "bg-danger",
  off: "bg-faint",
};

const worst = (levels: Level[]): Level =>
  levels.includes("err") ? "err"
  : levels.includes("warn") ? "warn"
  : levels.includes("ok") ? "ok"
  : "off";

function mcpLevel(s: McpServer): Level {
  if (!s.enabled) return "off";
  if (s.status === "error") return "err";
  if (s.status === "authorizing" || s.status === "needs_auth" || s.auth_hint) return "warn";
  if (s.status === "connected") return "ok";
  return "off";
}

// Connectors carry two layers of health: the stored config (`connected`) and the agent-side
// MCP tunnel (`mcp_error` / `mcp_authorizing`). The row shows the worst of them.
function connectorLevel(c: Connector): Level {
  if (c.mcp_error) return "err";
  if (c.mcp_authorizing) return "warn";
  return "ok";
}

function ago(ms: number): string {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  return `${m}m ago`;
}

/** How many skills an opened group lists before it points at Settings: forty rows of them
 *  would push everything else out of the popover. */
const SKILLS_SHOWN = 6;

const LANGUAGE_NAMES: Record<string, string> = {
  typescript: "TypeScript",
  javascript: "JavaScript",
  csharp: "C#",
  cpp: "C++",
  php: "PHP",
};

const languageName = (language: string) => LANGUAGE_NAMES[language.toLowerCase()] ?? titleCase(language);

const titleCase = (text: string) => (text ? text[0].toUpperCase() + text.slice(1) : text);

/** One thing that needs the person, whatever kind it is. */
interface Problem {
  key: string;
  logo: ReactNode;
  name: string;
  kind: string;
  level: Level;
  /** One sentence a person can act on. */
  says: string;
  /** The message as it came, for a bug report. */
  raw: string;
  action: { label: string; run: () => void };
}

export function StatusPopover({
  connected,
  model,
  onOpenConnectors,
  onOpenSkills,
}: {
  /** The app's WS to the sidecar — false means the banner truth, not the row truth. */
  connected: boolean;
  /** The active model, shown in the header line with the sidecar state. */
  model: string;
  onOpenConnectors: () => void;
  onOpenSkills: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [mcp, setMcp] = useState<McpServer[] | null>(null);
  const [connectors, setConnectors] = useState<Connector[] | null>(null);
  const [skills, setSkills] = useState<SkillRow[] | null>(null);
  const [lsp, setLsp] = useState<LspStatus | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [stale, setStale] = useState(false);
  const [checking, setChecking] = useState(false);
  const [openGroups, setOpenGroups] = useState<Set<Group>>(new Set());
  const [detail, setDetail] = useState<string | null>(null);
  const inflight = useRef(false);
  const [, force] = useState(0);

  // `asked` is a check the person asked for. Only that one is shown as "Checking…": the poll
  // runs every five seconds, and an arrow that turned on each of them would never rest.
  const refresh = useCallback(async (asked = false) => {
    if (inflight.current) return;
    inflight.current = true;
    setChecking(asked);
    try {
      const [m, c, k, l] = await Promise.allSettled([
        getMcpServers(),
        getConnectors(),
        listSkills(),
        getLspStatus(),
      ]);
      if (m.status === "fulfilled") setMcp(m.value);
      if (c.status === "fulfilled") setConnectors(c.value);
      if (k.status === "fulfilled") setSkills(k.value.skills ?? []);
      if (l.status === "fulfilled") setLsp(l.value);
      const anyFailed = [m, c, k, l].some((r) => r.status === "rejected");
      setStale(anyFailed || !connected);
      setUpdatedAt(Date.now());
    } finally {
      inflight.current = false;
      setChecking(false);
    }
  }, [connected]);

  // Seed the dot once, then poll on the cadence the open state earns.
  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const id = window.setInterval(() => refresh(), open ? POLL_MS : CLOSED_POLL_MS);
    return () => window.clearInterval(id);
  }, [open, refresh]);

  // The "checked Xs ago" footer re-renders once a second so a stalled poll is visible. And the
  // popover opens the same way every time: folded, with the verdict on top.
  useEffect(() => {
    if (!open) {
      setOpenGroups(new Set());
      setDetail(null);
      return;
    }
    const id = window.setInterval(() => force((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // A press anywhere outside closes it. The backdrop below covers only the session header: the
  // header's backdrop-filter makes it the box a `fixed` child is laid out in, so a press on the
  // conversation or the panel reached no backdrop and the popover stayed (owner, 2026-10-20). A
  // press inside a page the panel frames reaches no listener of this document at all; the window
  // losing focus to it is the sign.
  useEffect(() => {
    if (!open) return;
    const away = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const framed = () => setOpen(false);
    window.addEventListener("pointerdown", away, true);
    window.addEventListener("blur", framed);
    return () => {
      window.removeEventListener("pointerdown", away, true);
      window.removeEventListener("blur", framed);
    };
  }, [open]);

  const rows = (connectors ?? []).filter((c) => c.connected || c.mcp_error || c.mcp_authorizing);
  // A connector's own server is the connector's row: it is not listed a second time as a server.
  const connectorNames = new Set(rows.map((c) => c.name));
  const servers = (mcp ?? []).filter((s) => !connectorNames.has(s.name));
  const languages = lsp?.enabled ? lsp.servers : [];

  const connectorLogo = (c: Connector) => <ConnectorIcon connector={{ logo: c.logo, brand_color: c.brand_color }} size={16} />;
  const serverLogo = (s: McpServer) => <ConnectorIcon connector={{ logo: s.name }} size={16} />;
  const languageLogo = (language: string) => <FileIcon lang={language} size={16} />;

  const problems: Problem[] = [
    ...rows
      .filter((c) => connectorLevel(c) !== "ok")
      .map((c): Problem => {
        const level = connectorLevel(c);
        return {
          key: c.name,
          logo: connectorLogo(c),
          name: c.title,
          kind: "Connector",
          level,
          says: level === "err" ? "Could not connect." : "Signing in. Finish it in your browser.",
          raw: c.mcp_error ?? "",
          action: { label: level === "err" ? "Fix" : "Check", run: onOpenConnectors },
        };
      }),
    ...servers
      .filter((s) => mcpLevel(s) === "err" || mcpLevel(s) === "warn")
      .map((s): Problem => {
        const level = mcpLevel(s);
        const signingIn = s.status === "authorizing";
        return {
          key: s.name,
          logo: serverLogo(s),
          name: s.name,
          kind: "MCP server",
          level,
          says:
            level === "err" ? "Could not start."
            : signingIn ? "Signing in. Finish it in your browser."
            : "Needs you to sign in.",
          raw: s.last_error || (level === "err" ? mcpStatusLine(s) : ""),
          action:
            level === "err" ? { label: "Retry", run: () => void reloadMcp().then(() => refresh(true)) }
            : signingIn ? { label: "Check", run: onOpenConnectors }
            : { label: "Sign in", run: () => void connectMcp(s.name).then(() => refresh(true)) },
        };
      }),
    ...languages
      .filter((s) => !s.alive)
      .map(
        (s): Problem => ({
          key: `lsp-${s.language}`,
          logo: languageLogo(s.language),
          name: languageName(s.language),
          kind: "Code intelligence",
          level: "err",
          says: `Its language server stopped (${baseName(s.workspace)}).`,
          raw: s.command,
          action: { label: "Check again", run: () => void refresh(true) },
        }),
      ),
  ];

  const level: Level = !connected ? "err" : worst(problems.map((p) => p.level));
  const title =
    !connected
      ? "Status: not connected"
      : level === "err"
        ? "Status: something is broken"
        : level === "warn"
          ? "Status: something needs you"
          : "Status";
  // The pip is for a problem. A green dot that says "all is well" on every session is a mark
  // nobody reads, and it hid the one that matters.
  const pip = level === "warn" || level === "err";

  const working = {
    connectors: rows.filter((c) => connectorLevel(c) === "ok"),
    mcp: servers.filter((s) => mcpLevel(s) === "ok" || mcpLevel(s) === "off"),
    skills: skills ?? [],
    lsp: languages.filter((s) => s.alive),
  };
  const nothingAttached =
    connected && mcp !== null && connectors !== null && skills !== null &&
    rows.length === 0 && servers.length === 0 && working.skills.length === 0 && languages.length === 0;
  const loading = mcp === null && connectors === null && skills === null;

  const verdict = !connected
    ? "Not connected"
    : problems.length > 0
      ? `${problems.length} ${problems.length === 1 ? "thing needs" : "things need"} your attention`
      : nothingAttached
        ? "Connected"
        : "Everything is working";

  const toggleGroup = (group: Group) =>
    setOpenGroups((current) => {
      const next = new Set(current);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });

  const group = (id: Group, name: string, count: number, logos: ReactNode[], says: string, children: ReactNode) => {
    if (count === 0) return null;
    const isOpen = openGroups.has(id);
    return (
      <div className="status-group" data-testid={`status-group-${id}`} data-open={isOpen || undefined}>
        <button
          type="button"
          className="status-group-head"
          aria-expanded={isOpen}
          data-testid={`status-group-toggle-${id}`}
          onClick={() => toggleGroup(id)}
        >
          <span className="status-group-name">{name}</span>
          <span className="status-count">{count}</span>
          <span className="status-group-says">
            {!isOpen && logos.length > 0 && <span className="status-logos">{logos.slice(0, 5)}</span>}
            {!isOpen && <span className="status-group-text">{says}</span>}
          </span>
          <span className={"status-chev" + (isOpen ? " open" : "")} aria-hidden>
            <Icon name="chevronRight" size={12} />
          </span>
        </button>
        <Disclosure open={isOpen} className="fold-fade">
          <div className="status-group-rows">{children}</div>
        </Disclosure>
      </div>
    );
  };

  const row = (key: string, logo: ReactNode, name: string, fact: string, onOpen?: () => void) => (
    <div className="status-row" key={key} data-testid={`status-row-${key}`}>
      <span className="status-logo" aria-hidden>
        {logo}
      </span>
      <span className="status-row-name">
        {name}
        {fact && <span className="status-row-fact">{fact}</span>}
      </span>
      {onOpen && (
        <button type="button" className="status-link" onClick={onOpen}>
          Open
        </button>
      )}
    </div>
  );

  return (
    <div className="relative" ref={rootRef}>
      {/* `data-floating`: a press here is a click-away, never a window drag (styles.css, `.desktop-shell`).
          A press on a drag region reaches the page as nothing, so the header needs this cover. */}
      {open && <div className="fixed inset-0 z-30" data-floating onClick={() => setOpen(false)} />}
      <Tooltip label={title}>
        <button
          type="button"
          className="topbar-icon-btn relative"
          aria-label="Status"
          aria-expanded={open}
          aria-controls="status-popover"
          data-testid="status-trigger"
          onClick={() => setOpen((v) => !v)}
        >
          <Icon name="puzzle" size={16} />
          {!open && pip && (
            <span
              data-testid="status-trigger-dot"
              data-level={level}
              className={"absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full " + DOT[level]}
            />
          )}
        </button>
      </Tooltip>

      {open && (
        <div
          id="status-popover"
          data-testid="status-popover"
          role="dialog"
          aria-label="Status"
          className="status-popover pop-in-down absolute right-0 top-full z-40 mt-2 w-[372px] overflow-hidden rounded-xl border border-lineStrong bg-panel shadow-2xl"
        >
          <div className="status-head">
            <span className="status-verdict-mark" data-level={level === "off" ? "ok" : level} aria-hidden>
              <Icon name={!connected ? "x" : problems.length > 0 ? "warning" : "check"} size={15} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="status-verdict" data-testid="status-head">
                {verdict}
              </div>
              <div className="status-sub">{connected ? `${model} · connected` : "Reeve's engine is not answering"}</div>
            </div>
            <button
              type="button"
              aria-label="Check again"
              title="Check again"
              data-testid="status-refresh"
              onClick={() => refresh(true)}
              className="status-refresh"
              data-checking={checking || undefined}
            >
              <Icon name="refresh" size={13} />
            </button>
          </div>

          <div className="status-body" data-stale={stale || undefined}>
            {stale && (
              <div className="status-note" data-testid="status-banner">
                {connected
                  ? "Some services did not answer. This is the last known state."
                  : `Not connected. This is how things stood${updatedAt ? ` ${ago(updatedAt)}` : ""}.`}
              </div>
            )}

            {problems.length > 0 && (
              <div className="status-section" data-testid="status-problems">
                <div className="status-section-name">Needs attention</div>
                {problems.map((problem) => {
                  const shown = detail === problem.key;
                  return (
                    <div className="status-problem" key={problem.key} data-testid={`status-row-${problem.key}`} data-level={problem.level}>
                      <span className="status-logo" aria-hidden>
                        {problem.logo}
                      </span>
                      <span className="status-row-name">
                        {problem.name}
                        <span className="status-row-fact">{problem.kind}</span>
                      </span>
                      <button type="button" className="status-fix" onClick={problem.action.run}>
                        {problem.action.label}
                      </button>
                      <span className="status-says">
                        {problem.says}{" "}
                        {problem.raw && (
                          <button
                            type="button"
                            className="status-link"
                            aria-expanded={shown}
                            data-testid={`status-details-${problem.key}`}
                            onClick={() => setDetail(shown ? null : problem.key)}
                          >
                            {shown ? "Hide details" : "Details"}
                          </button>
                        )}
                      </span>
                      <Disclosure open={shown} className="status-raw-fold fold-fade">
                        <div className="status-raw" data-testid="status-raw">
                          <pre>{problem.raw}</pre>
                          <div className="status-raw-foot">
                            <span>The message Reeve got, in full.</span>
                            <CopyButton text={problem.raw} />
                          </div>
                        </div>
                      </Disclosure>
                    </div>
                  );
                })}
              </div>
            )}

            {loading ? (
              <div className="status-empty">Checking…</div>
            ) : nothingAttached ? (
              <div className="status-empty" data-testid="status-empty">
                <p>The agent has no connectors, MCP servers or skills yet. It can still read, write and run commands in your folder.</p>
                <button type="button" className="status-fix" onClick={onOpenConnectors}>
                  Browse connectors
                </button>
              </div>
            ) : (
              <div className="status-section" data-testid="status-working">
                {problems.length > 0 && <div className="status-section-name">Working</div>}
                {group(
                  "connectors",
                  "Connectors",
                  working.connectors.length,
                  working.connectors.map((c) => <span key={c.name}>{connectorLogo(c)}</span>),
                  working.connectors.map((c) => c.title).join(", "),
                  working.connectors.map((c) => row(c.name, connectorLogo(c), c.title, c.account ?? "", onOpenConnectors)),
                )}
                {group(
                  "mcp",
                  "MCP servers",
                  working.mcp.length,
                  working.mcp.map((s) => <span key={s.name}>{serverLogo(s)}</span>),
                  working.mcp.map((s) => s.name).join(", "),
                  working.mcp.map((s) =>
                    row(
                      s.name,
                      serverLogo(s),
                      s.name,
                      !s.enabled ? "Off" : s.tool_count != null ? `${s.tool_count} tool${s.tool_count === 1 ? "" : "s"}` : "",
                      onOpenConnectors,
                    ),
                  ),
                )}
                {group(
                  "skills",
                  "Skills",
                  working.skills.length,
                  [],
                  (() => {
                    const off = working.skills.filter((s) => !s.enabled).length;
                    return off > 0 ? `${working.skills.length - off} on, ${off} turned off` : "all on";
                  })(),
                  <>
                    {working.skills.slice(0, SKILLS_SHOWN).map((s) =>
                      row(s.name, <Icon name="book" size={14} />, s.name, [s.scope, s.enabled ? "" : "Disabled"].filter(Boolean).join(" · "), onOpenSkills),
                    )}
                    <div className="status-row" data-testid="status-skills-more">
                      <span />
                      <span className="status-row-fact">
                        {working.skills.length > SKILLS_SHOWN ? `and ${working.skills.length - SKILLS_SHOWN} more` : ""}
                      </span>
                      <button type="button" className="status-link" onClick={onOpenSkills}>
                        Open Skills
                      </button>
                    </div>
                  </>,
                )}
                {group(
                  "lsp",
                  "Code intelligence",
                  working.lsp.length,
                  working.lsp.map((s) => <span key={`${s.language}:${s.workspace}`}>{languageLogo(s.language)}</span>),
                  working.lsp.map((s) => languageName(s.language)).join(", "),
                  working.lsp.map((s) =>
                    row(`lsp-${s.language}`, languageLogo(s.language), languageName(s.language), `${s.command.split(" ")[0]} · ${baseName(s.workspace)}`),
                  ),
                )}
                {lsp !== null && !lsp.enabled && (
                  <div className="status-quiet" data-testid="status-lsp-off">
                    Code intelligence is turned off by <span className="font-mono">[lsp] enabled = false</span> in config.toml.
                  </div>
                )}
                {lsp !== null && lsp.enabled && lsp.servers.length === 0 && (
                  <div className="status-quiet" data-testid="status-lsp-idle">
                    Code intelligence starts on first use
                    {lsp.known_languages.length > 0 ? `: ${lsp.known_languages.join(", ")}.` : "."}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="status-foot">
            <span className="min-w-0 flex-1 truncate">
              {checking && updatedAt ? "Checking…" : updatedAt ? `Checked ${ago(updatedAt)}` : "Checking…"}
            </span>
            <button type="button" className="status-link" onClick={onOpenConnectors}>
              Manage connectors
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
