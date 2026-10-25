import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  getSettings,
  getTrustedWorkspaces,
  setDefaultMode,
  setDefaultShell,
  setOnboarded,
  setPdfSettings,
  setScratchBase,
  setToolDiscovery,
  setVisuals,
  setWorkspaceTrusted,
  type ModelSettings,
  type PdfSettings,
  type ShellChoice,
  type VisualsSetting,
  type WorkspaceCommandTrust,
} from "../api";
import { useOpenFinishedTools } from "../cards/toolRowPrefs";
import { checkForUpdate, getAutostart, getCloseBehavior, getKeepAwake, installUpdate, isDesktop, pickFolder, setAutostart, setCloseBehavior, setKeepAwake } from "../desktop";
import { PERMISSION_OPTIONS, modeValue } from "./composer/modes";
import { ConnectorsSection } from "./connectors/ConnectorsSection";
import { Icon } from "./Icon";
import { InstructionsSection } from "./InstructionsSection";
import { MemorySection } from "./MemorySection";
import { PluginsTab } from "./PluginsTab";
import { RemoteEnvironmentsSection } from "./RemoteEnvironmentsSection";
import { AppearanceSettings } from "./settings/AppearanceSettings";
import { ModelsTab } from "./settings/ModelsTab";
import { announceSaved, ChoiceCards, Segmented, Select, SETTINGS_PAGE_ACTION_ID, Stepper, SettingsCard, SettingsRow, SettingsSavedToast, Switch, TextButton } from "./settings/primitives";
import { resolveSettingsTab, searchSettings, SETTINGS_GROUP_LABELS, SETTINGS_INDEX, type SettingsGroupId, type SettingsTab } from "./settings/settingsIndex";
import { VoiceInputSettings } from "./settings/VoiceInputSettings";
import { SkillsTab } from "./SkillsTab";
import { SubagentsTab } from "./SubagentsTab";
import { useMapBasemap, type MapBasemap } from "./visual/mapPrefs";
import { WebSearchSection } from "./WebSearchSection";

// The Settings page: a rail of pages, searchable down to a single setting, and the open page. The
// pages live in their own files; the General and Advanced pages are here.

type SettingsViewProps = {
  /** A page's key. A page merged into another ("voice", "context") opens where it is now. */
  initialTab?: string;
  model?: string;
  workspace?: string;
  onCreateSkill?: (description: string) => void;
  onBack?: () => void;
};

export function SettingsView({ initialTab, model, workspace, onCreateSkill, onBack }: SettingsViewProps) {
  const index = SETTINGS_INDEX;
  const opening = resolveSettingsTab(initialTab);
  const [tab, setTab] = useState<SettingsTab>(opening.tab && index.some((entry) => entry.key === opening.tab) ? opening.tab : "general");
  const [query, setQuery] = useState("");
  // A connector another page opened (Web search ▸ More search tools): Connectors starts on its page.
  const [openConnector, setOpenConnector] = useState<string | null>(null);
  const [anchor, setAnchor] = useState<string | null>(opening.anchor);
  const railScroll = useRef<HTMLDivElement>(null);
  const indicator = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [indPlaced, setIndPlaced] = useState(false);
  const [indReady, setIndReady] = useState(false);

  // The rail lists what the search matches, under the groups the matches are in.
  const hits = useMemo(() => searchSettings(index, query), [index, query]);
  const groups = useMemo(() => {
    const byGroup = new Map<SettingsGroupId, typeof hits>();
    for (const hit of hits) byGroup.set(hit.entry.group, [...(byGroup.get(hit.entry.group) ?? []), hit]);
    return [...byGroup];
  }, [hits]);

  const active = index.find((entry) => entry.key === tab) ?? index[0];

  // The rail's highlight travels to the open page: one measurement of the active item, then a
  // transform. Before it is placed (and in the phone strip, where it is hidden) the active item
  // wears the highlight itself, so nothing ever shows unselected.
  useLayoutEffect(() => {
    const place = () => {
      const scroller = railScroll.current;
      const ind = indicator.current;
      const item = scroller?.querySelector<HTMLElement>(".settings-rail-item.active");
      if (!scroller || !ind || !item || !item.offsetHeight) return false;
      const top = item.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
      ind.style.height = `${item.offsetHeight}px`;
      ind.style.transform = `translateY(${top}px)`;
      return true;
    };
    setIndPlaced(place());
    const scroller = railScroll.current;
    if (!scroller || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setIndPlaced(place()));
    observer.observe(scroller);
    return () => observer.disconnect();
  }, [tab, hits]);

  useEffect(() => {
    if (!indPlaced || indReady) return;
    const frame = requestAnimationFrame(() => setIndReady(true));
    return () => cancelAnimationFrame(frame);
  }, [indPlaced, indReady]);

  useEffect(() => {
    content.current?.scrollTo({ top: 0 });
  }, [tab]);

  // Esc goes back to the app, unless something on the page is using it: a field being typed in,
  // or an open menu or dialog (those close on it themselves, and stop it there).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || !onBack) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      if (document.querySelector("[role='dialog'], [role='menu'], [role='listbox']")) return;
      onBack();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onBack]);

  // A search hit names one setting: once its page has drawn it, it is scrolled to and flashed. The
  // page may still be loading, so it is looked for a few times before giving up.
  useEffect(() => {
    if (!anchor) return;
    let cancelled = false;
    let timer: number | undefined;
    const find = (attempt: number) => {
      if (cancelled) return;
      const nodes = document.querySelectorAll<HTMLElement>("[data-settings-title]");
      const match = [...nodes].find((node) => node.dataset.settingsTitle === anchor);
      if (match) {
        const row = match.closest<HTMLElement>(".settings-row") ?? match.closest<HTMLElement>(".settings-card-block") ?? match;
        row.scrollIntoView({ block: "center" });
        row.classList.add("settings-flash");
        window.setTimeout(() => row.classList.remove("settings-flash"), 1700);
        setAnchor(null);
        return;
      }
      if (attempt < 10) timer = window.setTimeout(() => find(attempt + 1), 120);
      else setAnchor(null);
    };
    find(0);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [anchor, tab]);

  return (
    <main className="settings-shell" data-testid="settings-shell">
      <div data-drag-region className="settings-drag" />

      <aside className="settings-rail" aria-label="Settings">
        <div className="settings-rail-top">
          {/* The way out first, where the eye starts (settings redesign, 2026-10-20); Esc too. */}
          <button type="button" className="settings-rail-back" data-testid="settings-back" onClick={() => onBack?.()}>
            <Icon name="arrowLeft" size={15} />
            <span>Back to app</span>
            <kbd className="settings-rail-kbd">Esc</kbd>
          </button>
          <div className="settings-search">
            <Icon name="search" size={14} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search settings…"
              aria-label="Search settings"
              spellCheck={false}
              autoComplete="off"
            />
          </div>
        </div>

        <div ref={railScroll} className={"settings-rail-scroll" + (indPlaced ? " has-ind" : "") + (indReady ? " ready" : "")}>
          <div ref={indicator} className="settings-rail-ind" aria-hidden="true" hidden={!indPlaced} />
          {hits.length === 0 ? (
            <div className="settings-rail-empty">No settings match “{query}”.</div>
          ) : (
            groups.map(([groupId, groupHits]) => (
              <div className={"settings-rail-group is-" + groupId} key={groupId}>
                {SETTINGS_GROUP_LABELS[groupId] ? <div className="settings-rail-group-label">{SETTINGS_GROUP_LABELS[groupId]}</div> : null}
                {groupHits.map((hit) => (
                  <button
                    key={hit.entry.key}
                    className={"settings-rail-item" + (tab === hit.entry.key ? " active" : "")}
                    aria-current={tab === hit.entry.key ? "page" : undefined}
                    onClick={() => {
                      setTab(hit.entry.key);
                      setOpenConnector(null);
                      if (hit.anchor) setAnchor(hit.anchor);
                    }}
                  >
                    <span className="settings-rail-icon">
                      <Icon name={hit.entry.icon} size={15} />
                    </span>
                    <span className="settings-rail-label">{hit.entry.label}</span>
                    {hit.anchor ? <span className="settings-rail-match">{hit.anchor}</span> : null}
                  </button>
                ))}
              </div>
            ))
          )}
        </div>
      </aside>

      <div ref={content} className="settings-content hairline-scroll">
        {/* Keyed by tab: a page change mounts a fresh page, which fades in (DESIGN.md motion). */}
        <div className="settings-content-inner settings-enter" key={tab}>
          <div className="settings-page-head">
            <div>
              <h1 className="settings-section-title">{active?.label ?? "Settings"}</h1>
              {active?.sub ? <p className="settings-section-sub">{active.sub}</p> : null}
            </div>
            <div className="settings-page-action" id={SETTINGS_PAGE_ACTION_ID} />
          </div>

          <div className="settings-stack">
            {tab === "general" ? (
              <GeneralSection />
            ) : tab === "appearance" ? (
              <AppearanceSettings />
            ) : tab === "instructions" ? (
              <InstructionsSection />
            ) : tab === "models" ? (
              <ModelsTab />
            ) : tab === "advanced" ? (
              <AdvancedSection />
            ) : tab === "remote" ? (
              <RemoteEnvironmentsSection />
            ) : tab === "skills" ? (
              <SkillsTab onCreateSkill={onCreateSkill} />
            ) : tab === "plugins" ? (
              <PluginsTab workspace={workspace} />
            ) : tab === "subagents" ? (
              <SubagentsTab workspace={workspace} />
            ) : tab === "connectors" ? (
              <ConnectorsSection footer={<ToolDiscoveryCard />} initialDetail={openConnector} />
            ) : tab === "memory" ? (
              <MemorySection model={model} />
            ) : (
              <WebSearchSection
                onOpenConnector={(name) => {
                  setOpenConnector(name);
                  setTab("connectors");
                }}
              />
            )}
          </div>
        </div>
      </div>

      {/* "Saved" and its kin, said once for every page. */}
      <SettingsSavedToast />
    </main>
  );
}

// -- General ---------------------------------------------------------------------
// How Reeve behaves on this computer. How it looks is Settings → Appearance.
export function GeneralSection() {
  const [autostart, setAuto] = useState(false);
  const [onClose, setOnClose] = useState<"tray" | "quit">("tray");
  const [keepAwake, setKeep] = useState(false);
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [scratchDraft, setScratchDraft] = useState("");
  const [scratchMsg, setScratchMsg] = useState<string | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceCommandTrust[] | null>(null);
  const desktop = isDesktop();

  const refreshSettings = () =>
    getSettings()
      .then((s) => {
        setSettings(s);
        setScratchDraft((draft) => draft || s.scratch_base || "");
      })
      .catch(() => setSettings(null));

  const refreshWorkspaces = () =>
    getTrustedWorkspaces()
      .then(setWorkspaces)
      .catch(() => setWorkspaces([]));

  useEffect(() => {
    refreshSettings();
    refreshWorkspaces();
    if (isDesktop()) {
      getAutostart().then((v) => setAuto(!!v));
      getKeepAwake().then((v) => setKeep(!!v));
      getCloseBehavior().then((v) => setOnClose(v === "quit" ? "quit" : "tray"));
    }
  }, []);

  const changeOnClose = async (next: "tray" | "quit") => {
    const saved = await setCloseBehavior(next);
    setOnClose(saved === "quit" ? "quit" : "tray");
    announceSaved();
  };

  const toggleAuto = async (v: boolean) => {
    setAuto(!!(await setAutostart(v)));
    announceSaved();
  };
  const toggleKeep = async (v: boolean) => {
    setKeep(!!(await setKeepAwake(v)));
    announceSaved();
  };
  const saveScratch = async () => {
    setScratchMsg(null);
    const res = await setScratchBase(scratchDraft.trim());
    if (res.ok) {
      setScratchMsg(null);
      announceSaved("Folder saved");
      refreshSettings();
    } else {
      setScratchMsg(res.error || "Could not use that location.");
    }
  };
  const changeScratch = async () => {
    const picked = await pickFolder();
    if (!picked) return;
    setScratchMsg(null);
    const res = await setScratchBase(picked.trim());
    if (res.ok) {
      announceSaved("Folder saved");
      setScratchDraft(picked);
      refreshSettings();
    } else {
      setScratchMsg(res.error || "Could not use that location.");
    }
  };
  const revoke = async (path: string) => {
    if (!window.confirm(`Revoke command trust for ${path}?`)) return;
    await setWorkspaceTrusted(path, false);
    refreshWorkspaces();
  };
  const runSetupAgain = async () => {
    await setOnboarded(false);
    window.dispatchEvent(new CustomEvent("reeve:open-onboarding"));
  };
  const scratchChanged = !!settings && scratchDraft.trim() !== (settings.scratch_base || "").trim();

  return (
    <>
      <NewConversationsCard />

      <SettingsCard title="Conversations" testId="conversations-card">
        <OpenFinishedToolsRow />
      </SettingsCard>

      <SettingsCard title="Files" testId="files-card">
        <SettingsRow
          title="Conversations folder"
          detail="Each conversation without a project gets its own folder here. Existing conversations keep theirs."
          wide={!desktop}
        >
          {settings && desktop ? (
            // The desktop picks a folder and keeps it on the pick: no typing, no Save (settings
            // redesign, 2026-10-20). A browser has no picker, so it keeps the field below.
            <>
              <div className="flex items-center gap-2 w-full min-w-0">
                <span className="settings-path" data-testid="scratch-base-path" title={settings.scratch_base || "~/Reeve"}>
                  {settings.scratch_base || "~/Reeve"}
                </span>
                <TextButton onClick={() => void changeScratch()} testId="scratch-base-change">
                  Change…
                </TextButton>
              </div>
              {scratchMsg && (
                <div className="settings-note" role="alert">
                  {scratchMsg}
                </div>
              )}
            </>
          ) : settings ? (
            <>
              <div className="flex items-center gap-2 w-full min-w-0">
                <input
                  className="settings-search-input flex-1 min-w-0 font-mono text-[12px]"
                  type="text"
                  placeholder="~/Reeve"
                  value={scratchDraft}
                  spellCheck={false}
                  autoComplete="off"
                  aria-label="Conversations folder"
                  data-testid="scratch-base"
                  onChange={(e) => setScratchDraft(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && void saveScratch()}
                />
                <TextButton tone="accent" onClick={() => void saveScratch()} disabled={!scratchChanged || !scratchDraft.trim()}>
                  Save
                </TextButton>
              </div>
              {scratchMsg && (
                <div className="settings-note" role="alert">
                  {scratchMsg}
                </div>
              )}
            </>
          ) : (
            <div className="settings-note">Loading…</div>
          )}
        </SettingsRow>
        <SettingsRow
          title="Trusted workspaces"
          detail={
            workspaces === null
              ? "Loading…"
              : workspaces.length === 0
                ? "None. A trusted project may manage its own command allowances in .reeve/config.toml."
                : "These projects may manage their own command allowances in .reeve/config.toml."
          }
          testId="trusted-workspaces-card"
        />
        {(workspaces ?? []).map((workspace) => (
          <SettingsRow
            key={workspace.workspace}
            title={workspace.workspace}
            detail={
              (workspace.requested_commands.length
                ? `${workspace.requested_commands.length} project command allowance${
                    workspace.requested_commands.length === 1 ? "" : "s"
                  }`
                : "No project command allowances declared") + (workspace.exists ? "" : " · Folder unavailable")
            }
          >
            <TextButton tone="danger" onClick={() => void revoke(workspace.workspace)}>
              Revoke
            </TextButton>
          </SettingsRow>
        ))}
      </SettingsCard>

      {desktop && (
      <SettingsCard title="This computer">
        {desktop && (
          <SettingsRow title="Open at login" detail="Start Reeve in the background when you sign in.">
            <Switch checked={autostart} label="Open at login" onChange={(next) => void toggleAuto(next)} />
          </SettingsRow>
        )}
        {desktop && (
          <SettingsRow
            title="When you close the window"
            detail="In the tray, work in progress and automations go on; Quit in the tray stops them."
          >
            <Select
              value={onClose}
              label="When you close the window"
              options={[
                { value: "tray", label: "Keep running in the tray" },
                { value: "quit", label: "Quit Reeve" },
              ]}
              onChange={(next) => void changeOnClose(next)}
              testId="close-behavior"
            />
          </SettingsRow>
        )}
        {desktop && (
          <SettingsRow title="Keep this computer awake" detail="So automations run on time.">
            <Switch checked={keepAwake} label="Keep this computer awake" onChange={(next) => void toggleKeep(next)} />
          </SettingsRow>
        )}
        {desktop && (
          <SettingsRow title="Updates" detail="Reeve also checks when it starts.">
            <UpdateInline />
          </SettingsRow>
        )}
      </SettingsCard>
      )}
      {/* Not a setting: a way to go through the first-run steps again, said once under the page. */}
      <div className="settings-foot">
        <span>First-run setup: choose a model and a first automation again.</span>
        <TextButton onClick={() => void runSetupAgain()}>Run setup again</TextButton>
      </div>
    </>
  );
}

const DEFAULT_MODE_HELP =
  "The permission mode a new conversation opens in. A conversation already started keeps its own, and the picker in the composer still changes just that one. Full access runs everything without asking, files that run later on their own (git hooks, CI config) included. Only Reeve's own settings stay out of reach.";

const VISUALS_HELP =
  "Charts, tables, maps, timelines and calculators drawn inside an answer. Automatic: the model shows one when you would look through or adjust what it shows, and answers in a sentence when a sentence decides it. Only when I ask: it shows one when you ask for a chart, a table or the like. Off: answers are text and Markdown tables only. Applies to the next conversation you start.";

const VISUALS_OPTIONS: { value: VisualsSetting; label: string }[] = [
  { value: "auto", label: "Automatic" },
  { value: "asked", label: "Only when I ask" },
  { value: "off", label: "Off" },
];

const MAP_HELP =
  "Online map: streets, places and terrain under a map, so a single address opens at street level. The map tiles come from OpenFreeMap (OpenStreetMap data), so the area you are looking at is requested from their servers. Country outlines: the outlines bundled with Reeve and nothing fetched; a map shows countries but no streets or places.";

const MAP_OPTIONS: { value: MapBasemap; label: string }[] = [
  { value: "online", label: "Online map" },
  { value: "offline", label: "Country outlines" },
];

const SHELL_HELP =
  "The shell a command runs in when Reeve does not name one, and the one the terminal panel opens. Reeve can still run a command in any shell this computer has. Git Bash understands &&, grep and the commands most models write; PowerShell 5.1 does not have &&. Automatic is your system's own shell. Applies to the next conversation you start and to new terminals.";

/** The default shell (amendment of 2026-10-14). Shown only where there is a choice:
 * a machine with one shell has nothing to pick. */
export function ShellRow() {
  const [shell, setShell] = useState<string | null>(null);
  const [choices, setChoices] = useState<ShellChoice[]>([]);

  useEffect(() => {
    getSettings()
      .then((s) => {
        setChoices(s.shells ?? []);
        setShell(s.shell ?? "");
      })
      .catch(() => setShell(""));
  }, []);

  const save = async (next: string) => {
    const before = shell;
    setShell(next);
    const res = await setDefaultShell(next).catch(() => ({ ok: false }));
    if (!res.ok) setShell(before);
    else announceSaved();
  };

  if (shell === null || choices.length < 2) return null;
  return (
    <SettingsRow
      title="Shell"
      description={SHELL_HELP}
      detail="What commands run in unless Reeve picks another."
      testId="shell-card"
    >
      {/* Three choices read as a row; more than that is a menu (a machine with PowerShell 7, WSL
          and Git Bash had a row wider than the page). */}
      {choices.length > 2 ? (
        <Select
          label="Default shell"
          value={shell}
          options={[{ value: "", label: "Automatic" }, ...choices.map((c) => ({ value: c.kind, label: c.label }))]}
          onChange={(next) => void save(next)}
          testId="shell-select"
          optionTestId={(value) => `shell-${value || "auto"}`}
        />
      ) : (
        <Segmented
          radio
          label="Default shell"
          value={shell}
          options={[{ value: "", label: "Automatic" }, ...choices.map((c) => ({ value: c.kind, label: c.label }))]}
          onChange={(next) => void save(next)}
          optionTestId={(value) => `shell-${value || "auto"}`}
        />
      )}
    </SettingsRow>
  );
}

/** What a new conversation starts with: its permission mode, its shell, whether answers draw
 * visuals, and the map background those visuals use. */
const OPEN_FINISHED_HELP =
  "Off: tools stay one line, while they run and after; click one to see its output, live while it runs. On: a command still running after half a second opens to stream its output, each tool opens when it finishes to show what it did, and a delegated task's card stays open. Either way a tool that fails opens, a row you opened or closed stays as you left it, and conversations you come back to start folded.";

function OpenFinishedToolsRow() {
  const [on, setOn] = useOpenFinishedTools();
  return (
    <SettingsRow
      title="Open tools when they finish"
      description={OPEN_FINISHED_HELP}
      detail="Off keeps every tool to one line until you open it."
      testId="open-finished-tools"
    >
      <Switch
        checked={on}
        label="Open tools when they finish"
        testId="open-finished-tools-switch"
        onChange={(next) => {
          setOn(next);
          announceSaved();
        }}
      />
    </SettingsRow>
  );
}

function NewConversationsCard() {
  return (
    <SettingsCard title="New conversations">
      <DefaultModeRow />
      <ShellRow />
      <VisualsRows />
    </SettingsCard>
  );
}

function DefaultModeRow() {
  const [mode, setMode] = useState<string | null>(null);

  useEffect(() => {
    getSettings()
      .then((s) => setMode(modeValue(s.default_mode || "workspace-write")))
      .catch(() => setMode("workspace-write"));
  }, []);

  const save = async (next: string) => {
    const before = mode;
    setMode(next);
    const res = await setDefaultMode(next).catch(() => ({ ok: false }));
    if (!res.ok) setMode(before);
    else announceSaved();
  };

  if (mode === null) return null;
  return (
    // The choice that matters most on the page, so each answer says what it lets happen (settings
    // redesign, 2026-10-20), in the words the composer's picker uses.
    <SettingsRow
      title="Permission"
      description={DEFAULT_MODE_HELP}
      detail="What a new conversation may do without asking. One already started keeps its own."
      testId="default-mode-card"
      wide
    >
      <ChoiceCards
        label="New conversations start in"
        value={mode}
        options={PERMISSION_OPTIONS.map((o) => ({ value: o.value, label: o.label, detail: o.description ?? "", caution: o.caution }))}
        onChange={(next) => void save(next)}
        optionTestId={(value) => `default-mode-${value}`}
      />
    </SettingsRow>
  );
}

function VisualsRows() {
  const [visuals, setVisualsState] = useState<VisualsSetting | null>(null);
  const [basemap, setBasemap] = useMapBasemap();

  useEffect(() => {
    getSettings()
      .then((s) => setVisualsState(s.visuals ?? "auto"))
      .catch(() => setVisualsState("auto"));
  }, []);

  const save = async (next: VisualsSetting) => {
    const before = visuals;
    setVisualsState(next);
    const res = await setVisuals(next).catch(() => ({ ok: false }));
    if (!res.ok) setVisualsState(before);
    else announceSaved();
  };

  if (visuals === null) return null;
  return (
    <>
      <SettingsRow
        title="Visuals in answers"
        description={VISUALS_HELP}
        detail="Charts, tables and maps drawn from your data."
        testId="visuals-card"
      >
        <Segmented
          radio
          label="Show visuals in answers"
          value={visuals}
          options={VISUALS_OPTIONS}
          onChange={(next) => void save(next as VisualsSetting)}
          optionTestId={(value) => `visuals-${value}`}
        />
      </SettingsRow>
      <SettingsRow
        title="Map background"
        description={MAP_HELP}
        detail="Online tiles, or outlines that work offline."
        testId="map-basemap"
      >
        <Segmented
          radio
          label="Map background"
          value={basemap}
          options={MAP_OPTIONS}
          onChange={(next) => {
            setBasemap(next as MapBasemap);
            announceSaved();
          }}
          optionTestId={(value) => `map-basemap-${value}`}
        />
      </SettingsRow>
    </>
  );
}

/** Kept for callers that show the default-mode choice alone. */
export function DefaultModeCard() {
  return (
    <SettingsCard title="Permissions">
      <DefaultModeRow />
    </SettingsCard>
  );
}

/** Kept for callers that show the visuals choices alone. */
export function VisualsCard() {
  return (
    <SettingsCard title="Visuals">
      <VisualsRows />
    </SettingsCard>
  );
}

const TOOL_DISCOVERY_HELP =
  "MCP and connector tools stop being sent on every request and are reached on demand: the model searches for one (tool_search), which shows the best matches' parameters, then runs it by name. On an install with several MCP servers or connectors this removes most of the tool definitions from every request, at the cost of one extra round-trip the first time a tool is used. Every call still goes through the same permission and approval path. Applies to the next session you start.";

/** Connectors ▸ Tool loading (moved from General: it is about MCP tools). */
export function ToolDiscoveryCard() {
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    getSettings()
      .then((s) => setEnabled(s.tool_discovery === true))
      .catch(() => setEnabled(false));
  }, []);

  const save = async (next: boolean) => {
    setEnabled(next);
    await setToolDiscovery(next);
    announceSaved(next ? "Turned on" : "Turned off");
  };

  if (enabled === null) return null;
  return (
    <SettingsCard title="Tool loading">
      <SettingsRow
        title="Load MCP tools on demand"
        description={TOOL_DISCOVERY_HELP}
        detail="Large toolsets stay out of each request until Reeve searches for one. Experimental."
        testId="tool-discovery-card"
      >
        <Switch
          checked={enabled}
          label="Search MCP tools on demand"
          testId="tool-discovery-toggle"
          onChange={(next) => void save(next)}
        />
      </SettingsRow>
    </SettingsCard>
  );
}

// -- Advanced: attachments and voice --------------------------------------------------
function AdvancedSection() {
  return (
    <>
      <TokenSavingsCard />
      <VoiceInputSettings />
    </>
  );
}

function UpdateInline() {
  const [state, setState] = useState<"idle" | "checking" | "none" | "found" | "installing" | "error">("idle");
  const [version, setVersion] = useState("");

  const check = async () => {
    setState("checking");
    try {
      const u = await checkForUpdate();
      if (u) {
        setVersion(u.version);
        setState("found");
      } else {
        setState("none");
      }
    } catch {
      setState("error");
    }
  };

  const install = async () => {
    setState("installing");
    try {
      await installUpdate();
    } catch {
      setState("error");
    }
  };

  return (
    <span className="inline-flex items-center gap-2.5">
      {state === "found" ? (
        <TextButton onClick={() => void install()} testId="settings-update-install">
          Update to v{version} and restart
        </TextButton>
      ) : (
        <TextButton
          onClick={() => void check()}
          disabled={state === "checking" || state === "installing"}
          testId="settings-update-check"
        >
          {state === "checking" ? "Checking…" : "Check for updates"}
        </TextButton>
      )}
      {(state === "none" || state === "error" || state === "installing") && (
        <span className="settings-note">
          {state === "none"
            ? "You're on the latest version."
            : state === "error"
              ? "Couldn't check right now — try again later."
              : "Downloading — Reeve restarts by itself when it's ready."}
        </span>
      )}
    </span>
  );
}
// -- Token savings (PDF attachments) --------------------------------------------
function TokenSavingsCard() {
  const [pdf, setPdf] = useState<PdfSettings | null>(null);

  useEffect(() => {
    getSettings()
      .then((s) =>
        setPdf({
          pdf_fallback: s.pdf_fallback || "text",
          pdf_max_pages: s.pdf_max_pages || 20,
          pdf_max_mb: s.pdf_max_mb || 10,
        }),
      )
      .catch(() => setPdf({ pdf_fallback: "text", pdf_max_pages: 20, pdf_max_mb: 10 }));
  }, []);

  const save = async (patch: Partial<PdfSettings>) => {
    setPdf((p) => (p ? { ...p, ...patch } : p));
    await setPdfSettings(patch);
    announceSaved();
  };

  if (!pdf) return null;
  return (
    <SettingsCard
      title="PDF attachments"
      description="PDF attachments travel with every turn of a conversation, so large documents multiply what you spend on tokens."
      testId="token-savings-card"
    >
      <SettingsRow
        title="On models that cannot read PDFs"
        description="Claude, GPT and Gemini read PDFs natively — this only applies to models that don’t (GLM, Kimi, DeepSeek, local models…). Text extraction is cheapest; page images cost more tokens and need a vision-capable model."
        detail="Text is cheapest; page images keep tables and charts."
      >
        <Segmented
          label="PDF fallback"
          testId="pdf-fallback"
          value={pdf.pdf_fallback === "images" ? "images" : "text"}
          options={[
            { value: "text", label: "Extract text" },
            { value: "images", label: "Send page images" },
          ]}
          onChange={(next) => void save({ pdf_fallback: next })}
        />
      </SettingsRow>
      <SettingsRow
        title="Max pages"
        description="PDFs over these limits are not attached — you’ll see a notice in the composer instead."
        detail="A longer PDF is not attached; the composer says so."
      >
        <Stepper
          min={1}
          max={100}
          value={pdf.pdf_max_pages}
          testId="pdf-max-pages"
          label="Max pages"
          unit="pages"
          onChange={(next) => void save({ pdf_max_pages: next })}
        />
      </SettingsRow>
      <SettingsRow title="Max size" detail="Megabytes per attached PDF.">
        <Stepper
          min={1}
          max={10}
          value={pdf.pdf_max_mb}
          testId="pdf-max-mb"
          label="Max size"
          unit="MB"
          onChange={(next) => void save({ pdf_max_mb: next })}
        />
      </SettingsRow>
    </SettingsCard>
  );
}
