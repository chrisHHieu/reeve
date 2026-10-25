import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import {
  formatRemoteEnvironmentDestination,
  formatRemoteEnvironmentReadout,
  getRecentWorkspaces,
  listRemoteDirectories,
  listRemoteEnvironments,
  openWorkspace,
  type RecentWorkspace,
  type RemoteDirectoryEntry,
  type RemoteEnvironmentProfile,
  type RemoteEnvironmentSummary,
} from "../api";
import { chooseFolder } from "../desktop";
import { baseName, looksLikeFolderPath, normalizeFolderPath } from "../paths";
import { Icon } from "./Icon";

// What a new conversation is set up with before its first message: the folder it works in and the
// machine it runs on, as two quiet chips in the strip under the composer. They are a
// conversation's choices, not a message's, so they look apart from the composer's controls, and
// after the first message the strip states them instead.
//
// The folder's panel is a search over the recent folders with the current one checked, "Open
// folder…", and a temporary folder for work with no project. A full path typed into the search is
// offered as a row of its own, so a folder that was never opened is a keystroke away.

/** What a step answers: done, or the server's own words for why not, said in the panel. */
type Outcome = Promise<{ ok: boolean; error?: string }>;

export interface SessionSetupRowProps {
  /** The folder the person picked for this draft, by name; never a temporary folder's. */
  folderName: string | null;
  /** The bound folder's full path, which the panel checks its list against. */
  folderPath?: string | null;
  /** The draft works in a temporary folder. */
  tempFolder?: boolean;
  onPickFolder: (path: string, branch?: string | null) => void;
  /** Make and bind the conversation's temporary folder, by the same route a send uses. */
  onUseTemp?: () => Outcome;
  /** Settings ▸ Remote: where "Add SSH host…" goes. */
  onOpenRemoteSettings: () => void;
  /** The SSH environment the draft is bound to (null: this computer), and the folder on it. */
  environment: RemoteEnvironmentSummary | null;
  remoteWorkspace?: string | null;
  /** Bind the draft to a saved host and a folder there. A vanished profile or a relative path
   *  comes back as the server's refusal. */
  onPickRemote?: (environment: RemoteEnvironmentSummary, workspace: string) => Outcome;
  /** Back to this computer. */
  onClearEnvironment?: () => Outcome;
  /** Why the bound host failed to start a session (unreachable, refused, unsupported): shown
   *  under the row, menu open or not. */
  remoteError?: string;
}

export function SessionSetupRow(props: SessionSetupRowProps) {
  const [openMenu, setOpenMenu] = useState<"folder" | "environment" | null>(null);
  const [recents, setRecents] = useState<RecentWorkspace[] | null>(null);
  const [remoteHosts, setRemoteHosts] = useState<RemoteEnvironmentProfile[] | null>(null);
  // The environment panel's path step: after a host is chosen, the workspace on that
  // host is asked for before anything is bound. It is a small BROWSER: the server lists the host's immediate subdirectories (defaulting to
  // $HOME), a click descends, Up climbs, a filter narrows, and "Use this folder" binds
  // the path the HOST resolved. The manual input stays as the secondary way in.
  const [selectedHost, setSelectedHost] = useState<RemoteEnvironmentProfile | null>(null);
  const [remotePath, setRemotePath] = useState("");
  const [entries, setEntries] = useState<RemoteDirectoryEntry[] | null>(null);
  const [dirLoading, setDirLoading] = useState(false);
  const [dirFilter, setDirFilter] = useState("");
  const [envError, setEnvError] = useState("");
  // The latest listing request wins; a stale response must not overwrite a newer one.
  const dirRequest = useRef(0);
  // Descend/Up/default-load set the path from the host's answer; a path the user typed
  // must survive a listing that was already in flight when they typed it.
  const pathEditedRef = useRef(false);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const folderChipRef = useRef<HTMLButtonElement>(null);
  const environmentChipRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Escape closes whichever menu is open and puts focus back on the chip that opened it
  // (never strand keyboard focus inside a dismissed panel). Arrow keys walk the folder
  // panel's options so the list is usable without a pointer.
  useEffect(() => {
    if (!openMenu) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        const chip = openMenu === "environment" ? environmentChipRef.current : folderChipRef.current;
        setOpenMenu(null);
        chip?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [openMenu]);

  // Opening a menu starts it fresh: the folders' search empty and the recents read again; the
  // machine's panel back at its list of hosts, read again too.
  const toggle = (menu: "folder" | "environment") => {
    const opening = openMenu !== menu;
    setError("");
    if (menu === "environment") {
      setEnvError("");
      setSelectedHost(null);
      setRemotePath("");
      setEntries(null);
      setDirFilter("");
      dirRequest.current += 1; // a listing still on its way must not land in a closed panel
      if (opening) {
        setRemoteHosts(null);
        listRemoteEnvironments()
          .then(setRemoteHosts)
          .catch(() => setRemoteHosts([]));
      }
    } else if (opening) {
      setQuery("");
      getRecentWorkspaces()
        .then(setRecents)
        .catch(() => setRecents([]));
    }
    setOpenMenu(opening ? menu : null);
  };

  /** One folder step at a time: the panel is busy while it runs and its error line is cleared. */
  const step = async (work: () => Promise<void>) => {
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      await work();
    } finally {
      setBusy(false);
    }
  };

  const openFolder = async (path: string) => {
    const opened = await openWorkspace(path);
    if (!opened.ok) return setError(opened.error || "could not open that folder");
    setOpenMenu(null);
    props.onPickFolder(opened.path, opened.git_branch);
  };
  const pickFolder = (path: string) => step(() => openFolder(path));
  // The system's folder dialog; the panel stays busy while it is open.
  const browse = () =>
    step(async () => {
      const picked = await chooseFolder();
      if (picked) await openFolder(picked);
    });

  const useTemp = () =>
    step(async () => {
      if (!props.onUseTemp) return;
      const made = await props.onUseTemp();
      if (made.ok) setOpenMenu(null);
      else setError(made.error || "could not create a temporary folder");
    });

  // -- the environment pick ------------------------------
  // Picking a host is only the first half: the workspace on that host is browsed (or
  // typed) before the bind, then the app PATCHes the session and the session's world is
  // rebuilt. A refusal (a vanished profile, a relative/typo path, an unreachable host
  // reported when the session starts) surfaces as the server's own words, inline.
  const loadDirectories = async (host: RemoteEnvironmentProfile, path?: string) => {
    const seq = ++dirRequest.current;
    setDirLoading(true);
    setEnvError("");
    try {
      const res = await listRemoteDirectories(host.id, path);
      if (seq !== dirRequest.current) return; // a newer navigation owns the panel now
      if (!res.ok) {
        setEntries(null);
        setEnvError(res.error || "could not list that folder on the host");
        return;
      }
      setEntries(res.entries);
      // The host resolved the path (`pwd -P`): show what the host says, not the request.
      if (!pathEditedRef.current) setRemotePath(res.path);
    } finally {
      if (seq === dirRequest.current) setDirLoading(false);
    }
  };

  const openPathStep = (host: RemoteEnvironmentProfile) => {
    setEnvError("");
    setSelectedHost(host);
    setEntries(null);
    setDirFilter("");
    const initial = props.environment?.id === host.id ? (props.remoteWorkspace ?? "") : "";
    pathEditedRef.current = false;
    setRemotePath(initial);
    // No bound path yet: the host's $HOME is the honest default (the server answers it).
    void loadDirectories(host, initial || undefined);
  };

  const descend = (entry: RemoteDirectoryEntry) => {
    if (!selectedHost || dirLoading) return;
    setDirFilter("");
    pathEditedRef.current = false;
    setRemotePath(entry.path);
    void loadDirectories(selectedHost, entry.path);
  };

  const goUp = () => {
    if (!selectedHost || dirLoading) return;
    const current = remotePath.trim().replace(/\/+$/, "");
    const parent = current.includes("/")
      ? current.slice(0, current.lastIndexOf("/")) || "/"
      : "/";
    setDirFilter("");
    pathEditedRef.current = false;
    setRemotePath(parent);
    void loadDirectories(selectedHost, parent);
  };

  const browseTo = () => {
    if (!selectedHost || busy || dirLoading) return;
    const path = remotePath.trim();
    if (!path.startsWith("/")) {
      setEnvError("The remote workspace path must be absolute (start with /).");
      return;
    }
    setDirFilter("");
    pathEditedRef.current = false;
    void loadDirectories(selectedHost, path);
  };

  const bindRemotePath = async () => {
    if (busy || !selectedHost) return;
    const path = remotePath.trim();
    if (!path.startsWith("/")) {
      // Client-side mirror of the server's rule; the server's 400 is still the one
      // that decides (and is what shows if the check ever drifts).
      setEnvError("The remote workspace path must be absolute (start with /).");
      return;
    }
    if (!props.onPickRemote) return;
    setBusy(true);
    setEnvError("");
    try {
      const res = await props.onPickRemote(selectedHost, path);
      if (!res.ok) {
        setEnvError(res.error || "could not bind that remote workspace");
        return;
      }
      setOpenMenu(null);
      setSelectedHost(null);
      setEntries(null);
    } finally {
      setBusy(false);
    }
  };

  const pickLocal = async () => {
    if (busy) return;
    setEnvError("");
    if (!props.environment) {
      setOpenMenu(null);
      return;
    }
    if (!props.onClearEnvironment) return;
    setBusy(true);
    try {
      const res = await props.onClearEnvironment();
      if (!res.ok) {
        setEnvError(res.error || "could not switch to this computer");
        return;
      }
      setOpenMenu(null);
      setSelectedHost(null);
      setEntries(null);
    } finally {
      setBusy(false);
    }
  };

  // The panel lists the current folder even when the catalog doesn't (a restored session
  // or a typed path); a temporary folder is represented by its own row, never by its path.
  const boundNormalized =
    props.folderPath && !props.tempFolder ? normalizeFolderPath(props.folderPath) : "";
  const recentOptions = (recents || []).filter((w) => w.exists);
  if (
    boundNormalized &&
    !recentOptions.some((w) => normalizeFolderPath(w.path) === boundNormalized)
  ) {
    recentOptions.unshift({
      path: props.folderPath as string,
      name: baseName(props.folderPath as string),
      exists: true,
    });
  }
  const needle = query.trim().toLowerCase();
  const filtered = needle
    ? recentOptions.filter(
        (w) =>
          w.name.toLowerCase().includes(needle) || w.path.toLowerCase().includes(needle),
      )
    : recentOptions;
  const dirNeedle = dirFilter.trim().toLowerCase();
  const filteredEntries = (entries || []).filter((entry) =>
    entry.name.toLowerCase().includes(dirNeedle),
  );

  // Roving focus across the panel's options (the search input first): ArrowDown/Up from
  // the input enters the list at its near end; inside the list they step and clamp.
  const moveFocus = (from: HTMLElement, delta: 1 | -1 | "first" | "last") => {
    const panel = panelRef.current;
    if (!panel) return;
    const items = [
      ...panel.querySelectorAll<HTMLElement>('input, [data-folder-option="true"]'),
    ];
    const index = items.indexOf(from);
    if (index === -1) return;
    const target =
      delta === "first"
        ? items[0]
        : delta === "last"
          ? items[items.length - 1]
          : items[Math.min(items.length - 1, Math.max(0, index + delta))];
    target?.focus();
  };
  const onPanelKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      moveFocus(target, 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      moveFocus(target, -1);
    } else if (e.key === "Home") {
      e.preventDefault();
      moveFocus(target, "first");
    } else if (e.key === "End") {
      e.preventDefault();
      moveFocus(target, "last");
    }
  };

  // The pickers sit in the strip under the composer's card: quiet text, the size of the facts
  // they become after the first message. A new session's composer is in the middle of the
  // page, so their panels open downward: the folder's from the left end of the strip, the
  // machine's from the right end, where each picker is.
  const chip = "setup-chip relative inline-flex items-center gap-1.5 px-2 h-7 rounded-lg text-[12.5px] text-muted hover:text-ink cursor-pointer select-none whitespace-nowrap min-w-0";
  const option = "w-full text-left flex items-start gap-2.5 px-2.5 py-2 rounded-lg hover:bg-paper disabled:opacity-60";
  const current = " bg-accentSoft/50 hover:bg-accentSoft/50";
  const chipLabel = props.folderName || (props.tempFolder ? "Temporary folder" : "Choose folder");
  const environmentLabel = props.environment ? formatRemoteEnvironmentDestination(props.environment) : "Local";

  return (
    <div data-testid="setup-row" className="setup-row">
      <div className="flex items-center gap-1 min-w-0">
      {/* A press anywhere outside an open menu closes it. */}
      {openMenu && <div className="fixed inset-0 z-20" onClick={() => setOpenMenu(null)} />}

      {/* Environment chip — the welcome menu shape, and now the real pick: Local, or a saved SSH host whose workspace path is asked for next.
          The bind is a server PATCH; a refusal is shown in the panel in its own words. */}
      <div className="relative setup-env">
        <button
          ref={environmentChipRef}
          className={chip}
          data-testid="environment-chip"
          aria-haspopup="menu"
          aria-expanded={openMenu === "environment"}
          title={
            props.environment
              ? formatRemoteEnvironmentReadout(props.environment, props.remoteWorkspace)
              : "This computer"
          }
          onClick={() => toggle("environment")}
        >
          <Icon name="monitor" size={13} />
          <span className="setup-chip-label max-w-[160px] truncate">{environmentLabel}</span>
          <Icon name="chevronDown" size={12} className="text-faint" />
        </button>
        {openMenu === "environment" && (
          <div
            className="setup-menu absolute top-full mt-1.5 right-0 z-30 w-[320px] max-w-[calc(100vw-32px)] bg-panel border border-line rounded-xl2 shadow-xl p-1"
            data-testid="environment-panel"
            role="menu"
            aria-label="Environment"
          >
            <div className="px-2.5 pt-1.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-faint">
              Local
            </div>
            <button
              role="menuitemradio"
              aria-checked={!props.environment}
              data-testid="environment-local"
              disabled={busy}
              className={
                "w-full text-left flex items-start gap-2.5 px-2.5 py-2 rounded-lg disabled:opacity-60 " +
                (!props.environment ? "bg-accentSoft/50" : "hover:bg-paper")
              }
              onClick={() => void pickLocal()}
            >
              <Icon name="monitor" size={13} className="mt-0.5 shrink-0 text-muted" />
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-medium text-ink">Local</span>
                <span className="block text-[12px] text-faint">This computer</span>
              </span>
              {!props.environment && (
                <Icon name="check" size={13} className="mt-0.5 shrink-0 text-accent" />
              )}
            </button>

            <div className="border-t border-line mt-1 pt-1.5">
              <div className="px-2.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-faint">
                Remote
              </div>
              {remoteHosts === null ? (
                <div className="px-2.5 py-2 text-[12px] text-faint">Loading SSH hosts…</div>
              ) : remoteHosts.length === 0 ? (
                <div className="px-2.5 py-2 text-[12px] text-faint" data-testid="environment-remote-empty">
                  No SSH hosts saved yet
                </div>
              ) : selectedHost ? (
                // The workspace step: a host is not a session until a folder on it is
                // named. The browser lists what the HOST says is under the current path
                // (defaulting to $HOME); the manual input is the direct way in, and
                // "Use this folder" binds the path the host resolved.
                <div className="px-2.5 py-1.5" data-testid="environment-path-step">
                  <button
                    className="flex items-center gap-1 text-[12px] text-faint hover:text-ink mb-1"
                    data-testid="environment-path-back"
                    onClick={() => {
                      setSelectedHost(null);
                      setEntries(null);
                      setEnvError("");
                    }}
                  >
                    <Icon name="arrowLeft" size={12} /> {selectedHost.name}
                  </button>
                  <div className="text-[12px] text-faint mb-1.5">
                    {formatRemoteEnvironmentDestination(selectedHost)} · workspace path
                  </div>
                  <div
                    className="rounded-lg border border-line bg-paper/40 p-1.5"
                    data-testid="environment-browser"
                  >
                    <div className="flex items-center gap-1.5">
                      <button
                        aria-label="Up one folder"
                        title="Up one folder"
                        data-testid="environment-up"
                        className="shrink-0 p-0.5 rounded text-muted hover:text-ink disabled:opacity-40"
                        disabled={dirLoading || !remotePath || remotePath === "/"}
                        onClick={() => void goUp()}
                      >
                        <Icon name="arrowLeft" size={12} className="rotate-90" />
                      </button>
                      <span
                        className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted"
                        data-testid="environment-path-readout"
                        title={remotePath}
                      >
                        {remotePath || "…"}
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5 mt-1">
                      <Icon name="search" size={12} className="shrink-0 text-faint" />
                      <input
                        aria-label="Filter folders"
                        data-testid="environment-filter"
                        className="w-full h-7 bg-transparent text-[12px] text-ink placeholder:text-faint outline-none"
                        placeholder="Filter folders"
                        spellCheck={false}
                        disabled={!entries}
                        value={dirFilter}
                        onChange={(e) => setDirFilter(e.target.value)}
                      />
                    </div>
                    <div
                      role="listbox"
                      aria-label="Remote folders"
                      className="mt-1 max-h-40 overflow-y-auto flex flex-col gap-0.5"
                      data-testid="environment-dirs"
                    >
                      {dirLoading ? (
                        <div
                          className="px-2 py-1.5 text-[12px] text-faint"
                          data-testid="environment-dirs-loading"
                        >
                          Loading folders…
                        </div>
                      ) : entries && filteredEntries.length > 0 ? (
                        filteredEntries.map((entry) => (
                          <button
                            key={entry.path}
                            role="option"
                            aria-selected={false}
                            data-testid="environment-dir"
                            data-path={entry.path}
                            className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-paper text-[12px] text-ink"
                            onClick={() => void descend(entry)}
                          >
                            <Icon name="folder" size={12} className="shrink-0 text-muted" />
                            <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                          </button>
                        ))
                      ) : entries ? (
                        <div
                          className="px-2 py-1.5 text-[12px] text-faint"
                          data-testid="environment-dirs-empty"
                        >
                          {dirNeedle ? "No matching folders" : "No subfolders"}
                        </div>
                      ) : null}
                    </div>
                  </div>
                  <input
                    aria-label="Remote workspace path"
                    data-testid="environment-path"
                    className="mt-1.5 w-full h-8 px-2 rounded-md bg-paper border border-line text-[12px] text-ink placeholder:text-faint outline-none focus:border-accent font-mono"
                    placeholder="/home/user/project"
                    spellCheck={false}
                    value={remotePath}
                    onChange={(e) => {
                      pathEditedRef.current = true;
                      setRemotePath(e.target.value);
                      setEnvError("");
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        void browseTo();
                      }
                    }}
                  />
                  <button
                    className="mt-1.5 w-full text-left px-2.5 py-1.5 rounded-lg bg-accentSoft hover:bg-accentSoft/80 text-[12px] text-accent disabled:opacity-60"
                    data-testid="environment-path-bind"
                    disabled={busy || !remotePath.trim()}
                    onClick={() => void bindRemotePath()}
                  >
                    {busy ? "Checking the host…" : "Use this folder"}
                  </button>
                </div>
              ) : (
                remoteHosts.map((host) => {
                  const bound = props.environment?.id === host.id;
                  return (
                    <button
                      key={host.id}
                      role="menuitemradio"
                      aria-checked={bound}
                      disabled={busy}
                      title={formatRemoteEnvironmentDestination(host)}
                      data-testid="environment-remote"
                      data-bound={bound || undefined}
                      className={
                        "w-full text-left flex items-start gap-2.5 px-2.5 py-2 rounded-lg disabled:opacity-60 " +
                        (bound ? "bg-accentSoft/50 hover:bg-accentSoft/50" : "hover:bg-paper")
                      }
                      onClick={() => openPathStep(host)}
                    >
                      <Icon name="monitor" size={13} className="mt-0.5 shrink-0 text-muted" />
                      <span className="min-w-0 flex-1">
                        <span className="block text-[13px] font-medium text-ink truncate">
                          {host.name}
                        </span>
                        <span className="block text-[12px] text-faint truncate">
                          {formatRemoteEnvironmentDestination(host)}
                        </span>
                      </span>
                      {bound && (
                        <Icon name="check" size={13} className="mt-0.5 shrink-0 text-accent" />
                      )}
                    </button>
                  );
                })
              )}
              {(envError || props.remoteError) && (
                <div className="px-2.5 py-1 text-[12px] text-warnInk" data-testid="environment-error">
                  {envError || props.remoteError}
                </div>
              )}
              <div className="border-t border-line mt-1 pt-1">
                <button
                  className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-paper text-[12px] text-accent"
                  data-testid="environment-add-host"
                  onClick={() => {
                    setOpenMenu(null);
                    props.onOpenRemoteSettings();
                  }}
                >
                  Add SSH host…
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* No folder chip on a remote host: the folder is on the host, and the machine's panel is
          where it is picked. */}
      {!props.environment && (
        <div className="relative">
          <button ref={folderChipRef} className={chip} data-testid="folder-chip" aria-haspopup="dialog" aria-expanded={openMenu === "folder"} title={props.folderPath || undefined} onClick={() => toggle("folder")}>
            <Icon name="folder" size={13} />
            <span className="setup-chip-label max-w-[160px] truncate">{chipLabel}</span>
            <Icon name="chevronDown" size={12} className="text-faint" />
          </button>
          {openMenu === "folder" && (
            <div
              ref={panelRef}
              className="setup-menu absolute top-full mt-1.5 left-0 z-30 w-[320px] max-w-[calc(100vw-32px)] bg-panel border border-line rounded-xl2 shadow-xl"
              data-testid="folder-panel"
              role="dialog"
              aria-label="Choose a folder"
              onKeyDown={onPanelKeyDown}
            >
              {/* Search row: filters the recents; a full path
                  typed here is offered as an open row, so a folder never in the list is
                  still one keystroke away. Enter opens it directly. */}
              <div className="flex items-center gap-2 px-2.5 border-b border-line">
                <Icon name="search" size={13} className="shrink-0 text-faint" />
                <input
                  autoFocus
                  aria-label="Search folders"
                  className="w-full h-9 bg-transparent text-[13px] text-ink placeholder:text-faint outline-none"
                  data-testid="folder-search"
                  placeholder="Search folders, or type a path"
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value);
                    setError("");
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && looksLikeFolderPath(query)) {
                      e.preventDefault();
                      void pickFolder(query);
                    }
                  }}
                />
              </div>

              <div className="p-1">
                <div role="radiogroup" aria-label="Folders" className="flex flex-col gap-0.5">
                  {looksLikeFolderPath(query) && (
                    <button
                      role="radio"
                      aria-checked={false}
                      data-folder-option="true"
                      data-testid="folder-typed"
                      className={option}
                      disabled={busy}
                      onClick={() => void pickFolder(query)}
                    >
                      <Icon name="folder" size={13} className="mt-0.5 shrink-0 text-muted" />
                      <span className="min-w-0 flex-1 truncate text-[13px] text-ink">
                        Open “{query.trim()}”
                      </span>
                    </button>
                  )}
                  {filtered.map((w) => {
                    const isCurrent = normalizeFolderPath(w.path) === boundNormalized;
                    return (
                      <button
                        key={w.path}
                        role="radio"
                        aria-checked={isCurrent}
                        data-current={isCurrent || undefined}
                        data-folder-option="true"
                        data-testid="folder-option"
                        className={option + (isCurrent ? current : "")}
                        disabled={busy}
                        onClick={() => void pickFolder(w.path)}
                        title={w.path}
                      >
                        <Icon name="folder" size={13} className="mt-0.5 shrink-0 text-muted" />
                        <span className="min-w-0 flex-1">
                          <span className="block text-[13px] font-medium text-ink truncate">
                            {w.name || baseName(w.path)}
                          </span>
                          <span className="block text-[12px] text-faint truncate">{w.path}</span>
                        </span>
                        {isCurrent && (
                          <Icon name="check" size={13} className="mt-0.5 shrink-0 text-accent" />
                        )}
                      </button>
                    );
                  })}
                  {/* No-project choice: a temporary folder, created
                      with git ready the moment it is picked. The app's own route. */}
                  {props.onUseTemp && (
                    <button
                      role="radio"
                      aria-checked={!!props.tempFolder}
                      data-current={props.tempFolder || undefined}
                      data-folder-option="true"
                      data-testid="folder-temp"
                      className={option + (props.tempFolder ? current : "")}
                      disabled={busy}
                      onClick={() => void useTemp()}
                    >
                      <Icon name="folderPlus" size={13} className="mt-0.5 shrink-0 text-muted" />
                      <span className="min-w-0 flex-1">
                        <span className="block text-[13px] font-medium text-ink">
                          Temporary folder
                        </span>
                        <span className="block text-[12px] text-faint">
                          For work that isn't tied to a project — save it later if it matters
                        </span>
                      </span>
                      {props.tempFolder && (
                        <Icon name="check" size={13} className="mt-0.5 shrink-0 text-accent" />
                      )}
                    </button>
                  )}
                </div>
                {recents !== null && filtered.length === 0 && !looksLikeFolderPath(query) && (
                  <div className="px-2.5 py-2 text-[12px] text-faint">
                    {needle
                      ? "No matching folders — type a full path to open one"
                      : "No recent folders yet — open one to start"}
                  </div>
                )}
                {error && (
                  <div className="px-2.5 py-1 text-[12px] text-warnInk" data-testid="folder-error">
                    {error}
                  </div>
                )}
                <div className="border-t border-line mt-1 pt-1">
                  <button className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-paper text-[12px] text-accent disabled:opacity-60" data-testid="folder-browse" disabled={busy} onClick={() => void browse()}>
                    {busy ? "Opening…" : "Open folder…"}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
      </div>
      {/* The machine's panel says it itself while open; closed, the line stays under the row. */}
      {props.remoteError && openMenu !== "environment" && (
        <div className="setup-error text-[12px] text-warnInk leading-snug" role="alert" data-testid="environment-error-banner">
          {props.remoteError}
        </div>
      )}
    </div>
  );
}
