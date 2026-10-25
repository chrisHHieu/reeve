import { useEffect, useState } from "react";
import {
  deleteAutoMemoryFile,
  getAutoMemory,
  getRecentWorkspaces,
  promoteAutoMemoryFile,
  readAutoMemoryFile,
  setMemorySettings,
  writeAutoMemoryFile,
  type AutoMemoryFile,
  type MemoryScope,
  type MemorySettings,
  type RecentWorkspace,
} from "../api";
import { FileIcon } from "./FileIcon";
import { Icon } from "./Icon";
import { SettingsCard, SettingsRow, Switch } from "./settings/primitives";

// Claude-Code-shaped auto memory: the agent's own markdown notes, kept machine-local
// (outside the workspace) so they never show up in `git status`. MEMORY.md is an index
// loaded every session; topic files are read on demand. This card is /memory's analogue:
// toggle, browse, search, open, edit, delete.
const BTN_ACCENT =
  "text-[13px] px-3 py-2 rounded-lg bg-accent text-[color:var(--canvas)] shrink-0 disabled:opacity-40";

const AM_HELP =
  "What Reeve keeps about a project between sessions: how you want the work done, " +
  "the work in progress and why, what went wrong here and how it was fixed, where things " +
  "outside the project live, and what it learned about you. A note that names a file is " +
  "marked for re-checking when that file changes. Plain markdown, stored outside your workspace.";

// What an earlier version saved about the person, kept for review.
const USER_HELP =
  "Notes an earlier version saved about you in the background. They are no longer loaded " +
  "into any conversation or sent to a model. Copy anything you want every project to follow " +
  "into Instructions, then delete the rest: this list disappears when it is empty.";

const TYPE_LABEL: Record<string, string> = {
  user: "about you",
  feedback: "feedback",
  project: "project",
  lesson: "lesson",
  reference: "reference",
  // Types older notes still carry.
  decision: "decision",
  "dead-end": "dead end",
  "how-to": "how-to",
  correction: "you asked",
};

function fmtWhen(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const day = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return `${day} ${time}`;
}

export function AutoMemoryCard({
  settings,
  onSettings,
  scope = "project",
}: {
  settings: MemorySettings;
  onSettings: (s: MemorySettings) => void;
  /** "user": who the person is — no folder to pick, no switch of its own, nothing to
   *  promote into a repo (it belongs to no project). */
  scope?: MemoryScope;
}) {
  const aboutUser = scope === "user";
  const [workspaces, setWorkspaces] = useState<RecentWorkspace[]>([]);
  const [workspace, setWorkspace] = useState("");
  const [files, setFiles] = useState<AutoMemoryFile[]>([]);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getRecentWorkspaces()
      .then((ws) => {
        setWorkspaces(ws);
        setWorkspace((cur) => cur || ws[0]?.path || "");
      })
      .catch(() => {});
  }, []);

  const load = (ws: string) => {
    if (!ws && !aboutUser) {
      setFiles([]);
      return;
    }
    getAutoMemory(ws, scope)
      .then((r) => {
        setFiles(r.files || []);
        setError(r.error || null);
      })
      .catch(() => setFiles([]));
  };
  useEffect(() => {
    load(workspace);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace]);
  useEffect(() => {
    const refresh = () => load(workspace);
    window.addEventListener("focus", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace]);

  const enabled = aboutUser || settings.auto_memory;
  const toggle = async () => {
    const next = await setMemorySettings({ auto_memory: !enabled });
    onSettings(next);
  };

  const openFile = async (name: string) => {
    const r = await readAutoMemoryFile(workspace, name, scope);
    setOpen(name);
    setDraft(r.content || "");
    setError(r.error || null);
  };
  const saveFile = async () => {
    if (!open) return;
    const r = await writeAutoMemoryFile(workspace, open, draft, "overwrite", scope);
    if (!r.ok) {
      setError(r.error || "Couldn't save this file.");
      return;
    }
    setError(null);
    setMsg(r.warning ? r.warning : "Saved.");
    setOpen(null);
    load(workspace);
    window.setTimeout(() => setMsg(null), 6000);
  };
  const removeFile = async (name: string) => {
    if (!window.confirm(`Delete ${name}?`)) return;
    await deleteAutoMemoryFile(workspace, name, scope);
    if (open === name) setOpen(null);
    load(workspace);
  };
  // memory v2 Phase 4: copy one machine-local note into the project's `.agents/memory/`,
  // where it is committed and reviewable. One-way and user-asked — the local file stays
  // canonical, and the index is refused (it names machine-local files).
  const promoteFile = async (name: string) => {
    const r = await promoteAutoMemoryFile(workspace, name);
    if (!r.ok) {
      setError(r.error || "Couldn't promote this file.");
      return;
    }
    setError(null);
    setMsg(`Copied into the project at ${r.target}`);
    window.setTimeout(() => setMsg(null), 8000);
  };
  const newFile = async () => {
    const name = window.prompt("New memory file name (e.g. no-db-mocks.md)")?.trim();
    if (!name) return;
    const r = await writeAutoMemoryFile(
      workspace,
      name,
      `# ${name.replace(/\.md$/i, "")}\n`,
      "overwrite",
      scope,
    );
    if (!r.ok) setError(r.error || "Couldn't create the file.");
    load(workspace);
  };

  const needle = query.trim().toLowerCase();
  const shown = needle
    ? files.filter(
        (f) => f.name.toLowerCase().includes(needle) || f.title.toLowerCase().includes(needle),
      )
    : files;

  // Nothing left to review: the card for what was saved about the person goes away.
  if (aboutUser && !files.some((f) => f.name !== "MEMORY.md")) return null;

  return (
    <SettingsCard testId={aboutUser ? "user-memory-card" : "auto-memory-card"} tile>
      {aboutUser ? (
        <>
          <SettingsRow title="Saved about you earlier" description={USER_HELP} />
          {/* Said on the card, not behind the help mark: it is why the list is here. */}
          <div className="px-4 pb-3 settings-note" data-testid="user-memory-note">
            These notes are no longer loaded into any conversation. Copy what you want every
            project to follow into Instructions, then delete the rest.
          </div>
        </>
      ) : (
        <SettingsRow title="Auto memory" description={AM_HELP}>
          <Switch checked={enabled} onChange={toggle} label="Auto memory" />
        </SettingsRow>
      )}

      {enabled && (
        // The projects on the left, the chosen one's notes on the right (settings redesign,
        // 2026-10-20): the recent folders are one press each, and another is typed below them.
        <div className={"px-4 pb-4 pt-3 border-t border-line" + (aboutUser ? "" : " memory-layout")}>
          {!aboutUser && (
            <nav className="memory-projects" aria-label="Projects">
              <div className="memory-projects-label">Projects</div>
              {workspaces.map((w) => (
                <button
                  key={w.path}
                  type="button"
                  title={w.path}
                  aria-current={w.path === workspace ? "true" : undefined}
                  className={"memory-project" + (w.path === workspace ? " on" : "")}
                  data-testid={`am-project-${w.name}`}
                  onClick={() => setWorkspace(w.path)}
                >
                  <Icon name="folder" size={14} />
                  <span className="memory-project-name">{w.name}</span>
                </button>
              ))}
              <input
                value={workspaces.some((w) => w.path === workspace) ? "" : workspace}
                onChange={(e) => setWorkspace(e.target.value)}
                placeholder={workspaces.length ? "Another folder…" : "Workspace folder"}
                aria-label="Another folder"
                data-testid="am-workspace-input"
                spellCheck={false}
                className="memory-project-field"
              />
            </nav>
          )}
          <div className="memory-files">
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-2 min-w-0 flex-1 max-w-[420px]">
              <span className="text-[12px] text-faint shrink-0" data-testid="am-count">
                {files.length} {files.length === 1 ? "memory" : "memories"}
              </span>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <div className="flex h-[30px] items-center gap-1.5 px-2.5 rounded-lg border border-line bg-panel text-faint transition-colors hover:border-lineStrong focus-within:border-accent">
                <Icon name="search" size={13} />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search memory files…"
                  aria-label="Search memory files"
                  data-testid="am-search"
                  className="w-[150px] bg-transparent border-0 outline-none text-[12.5px] text-ink"
                />
              </div>
              <button type="button" className="settings-text-btn" data-testid="am-new" onClick={newFile}>
                <Icon name="plus" size={13} /> New note
              </button>
            </div>
          </div>

          {error && (
            <div className="text-[12px] text-danger/90 mt-2" data-testid="am-error">
              {error}
            </div>
          )}
          {msg && <div className="text-[12px] text-muted mt-2">{msg}</div>}

          {files.length === 0 && !error && (
            <div className="text-[12px] text-muted mt-3" data-testid="am-empty">
              Nothing yet. Reeve writes a note here when it learns something the project
              itself cannot tell it. MEMORY.md is the index loaded into every new conversation;
              the notes are read when they matter.
            </div>
          )}
          {files.length > 0 && shown.length === 0 && (
            <div className="text-[12px] text-muted mt-3">No files match “{query}”.</div>
          )}

          {shown.length > 0 && (
            <div className="mt-3 border-t border-line divide-y divide-line">
              {shown.map((f) => (
                <div key={f.name} data-testid={`am-file-${f.name}`}>
                  <div className="flex items-center gap-2.5 py-2.5 hover:text-ink">
                    <FileIcon path={f.name} size={16} />
                    <button
                      className="min-w-0 flex-1 text-left"
                      title={`Open ${f.name}`}
                      onClick={() => void openFile(f.name)}
                    >
                      <div className="text-[13px] font-medium text-ink truncate">{f.title}</div>
                      <div className="text-[11.5px] text-faint truncate">
                        {f.name}
                        {f.injected ? " · index, always loaded" : ""}
                      </div>
                    </button>
                    {f.type && (
                      <span className="text-[11px] text-muted border border-line rounded-full px-1.5 py-0.5 shrink-0">
                        {TYPE_LABEL[f.type] || f.type}
                      </span>
                    )}
                    <span className="text-[11.5px] text-faint shrink-0">
                      {fmtWhen(f.modified_at)}
                    </span>
                    {f.name !== "MEMORY.md" && !aboutUser && (
                      <button
                        className="text-faint hover:text-ink shrink-0"
                        title="Copy into the project's .agents/memory (committed, shared)"
                        data-testid={`am-promote-${f.name}`}
                        onClick={() => void promoteFile(f.name)}
                      >
                        <Icon name="copy" size={14} />
                      </button>
                    )}
                    <button
                      className="text-faint hover:text-danger shrink-0"
                      title="Delete this file"
                      data-testid={`am-delete-${f.name}`}
                      onClick={() => void removeFile(f.name)}
                    >
                      <Icon name="trash" size={14} />
                    </button>
                  </div>
                  {open === f.name && (
                    <div className="pb-4" data-testid="am-editor">
                      <textarea
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        rows={10}
                        autoFocus
                        className="w-full px-3 py-2.5 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent resize-y leading-relaxed font-mono"
                      />
                      <div className="flex items-center gap-3 mt-2">
                        <button className={BTN_ACCENT} onClick={() => void saveFile()}>
                          Save
                        </button>
                        <button
                          className="text-[13px] text-muted hover:text-ink"
                          onClick={() => setOpen(null)}
                        >
                          cancel
                        </button>
                        <span className="text-[11.5px] text-faint">
                          Keep MEMORY.md to one line per memory; detail goes in topic files.
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          </div>
        </div>
      )}
    </SettingsCard>
  );
}
