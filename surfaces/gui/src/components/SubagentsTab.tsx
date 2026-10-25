import { useEffect, useMemo, useRef, useState } from "react";
import {
  deleteSubagent,
  getModelCatalog,
  getRecentWorkspaces,
  getSettings,
  getSubagents,
  getSubagentsExternal,
  getSubagentExternalModels,
  runSubagentExternalAction,
  saveSubagent,
  setSubagentEnabled,
  setSubagentModel,
  setSubagentModelSelection,
  type CatalogModel,
  type RecentWorkspace,
  type SubagentAgent,
  type SubagentExternalStatus,
  type SubagentScope,
  type SubagentTools,
} from "../api";
import { displayModel, displayModelFull } from "../modelName";
import { agentColor } from "../subagents";
import { Icon } from "./Icon";
import { Select, SettingsPageAction, Switch } from "./settings/primitives";

// Settings ▸ Subagents — the management home for `task`-spawned agent definitions. The
// design follows `docs/mockups/subagents.html`: an Installed list over a Built-in list,
// and a form page (New / Edit) opened from a row. The GUI writes PER-AGENT MARKDOWN
// (never config.toml): user scope saves to the state dir's `agents/`, project scope to
// `<workspace>/.reeve/agents/`. Every tool group maps onto the server's canonical
// permission vocabulary — the checkbox labels are labels over that map, not a second list.
//
// Built-ins are read-only: a row opens a DETAIL view (description, allowed tools, system
// prompt), and the one editable field — the model — is pinned through the dedicated
// store (`PUT /v1/subagents/{name}/model`, `subagent_models.json`), never by writing a
// markdown override that would replace the shipped definition.

const CARD = "rounded-2xl bg-panel";
const CARD_SOFT = "rounded-2xl bg-panel/60";
const FIELD_LABEL = "text-[13px] font-medium text-ink";
const FIELD_HELP = "text-[12px] text-muted mt-1.5 leading-relaxed";
const INPUT =
  "w-full min-w-0 px-3 py-2 rounded-lg border border-line bg-panel text-[13px] text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accentSoft";
const BTN_PRIMARY =
  "inline-flex items-center gap-1.5 h-[30px] text-[12.5px] px-3 rounded-lg bg-accent text-[color:var(--canvas)] font-medium shrink-0 hover:brightness-105 disabled:opacity-40";
const BTN_GHOST =
  "inline-flex items-center gap-1.5 text-[13px] px-3 py-2 rounded-lg text-muted hover:text-ink shrink-0 disabled:opacity-40";
const ICONBTN =
  "w-8 h-8 grid place-items-center rounded-lg text-muted hover:text-ink shrink-0";
const BADGE =
  "text-[11px] font-medium px-1.5 py-px rounded-[5px] bg-paper text-muted whitespace-nowrap";
const CHIP =
  "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-paper text-muted text-[11px] font-medium select-none";

// The mockup's swatches, in order. Empty means "no explicit color" — the list dot then
// falls back to the same name hash the task card uses (`agentColor`).
const SWATCHES = [
  "#f4c542",
  "#e8746a",
  "#e89a4a",
  "#2f9e5f",
  "#3aa9a0",
  "#3b5cf6",
  "#8b6bf2",
  "#e06bb4",
];

// The GUI's tool groups. The server owns the group → canonical-permission mapping; these
// names are the vocabulary both sides share.
const TOOL_GROUPS = [
  "read",
  "grep",
  "list",
  "shell-read",
  "write",
  "edit",
  "shell",
  "web",
  "skills",
  "memory",
  "task",
] as const;

// Groups that can change files or run commands — dotted in the picker.
const DANGEROUS_GROUPS: ReadonlySet<string> = new Set(["write", "edit", "shell"]);

// `shell-read` is the shell for commands that only read (`git log`, `ls`, `rg`), inside the
// session's folders.
const GROUP_LABELS: Record<string, string> = { "shell-read": "Read-only shell" };

const groupLabel = (group: string) => GROUP_LABELS[group] ?? group.charAt(0).toUpperCase() + group.slice(1);

const NAME_RE = /^[a-z0-9][a-z0-9-]*(?:\/[a-z0-9][a-z0-9-]*)*$/;

// The tab's scope choice: global (`user`, no workspace parameter) or one workspace's project
// files (the `workspace` query param the API already accepts). The dropdown is the reference design
// reference's: a pill with the current label over a `User` row plus a `Workspaces` group.
type ScopePick = { scope: SubagentScope; workspace?: string };

const basename = (path: string) =>
  path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path;

const pickLabel = (pick: ScopePick, workspaces: RecentWorkspace[]) => {
  if (pick.scope === "user") return "User";
  if (!pick.workspace) return "Project";
  return (
    workspaces.find((w) => w.path === pick.workspace)?.name ?? basename(pick.workspace)
  );
};

const EMPTY_TOOLS: SubagentTools = { preset: "all", allow: [], deny: [] };

type EditorMode = "new" | "edit";

interface EditorState {
  mode: EditorMode;
  name: string;
  scope: SubagentScope;
  /** The project-scope target (the `workspace` query param); absent for user scope. */
  workspace?: string;
  description: string;
  color: string;
  model: string;
  tools: SubagentTools;
  prompt: string;
  injectAgents: boolean;
}

const newEditor = (pick: ScopePick): EditorState => ({
  mode: "new",
  name: "",
  scope: pick.scope,
  workspace: pick.workspace,
  description: "",
  color: "",
  model: "",
  tools: { ...EMPTY_TOOLS },
  prompt: "",
  injectAgents: true,
});

const editorFor = (
  row: SubagentAgent,
  mode: EditorMode,
  workspace?: string,
): EditorState => ({
  mode,
  name: row.name,
  scope: row.source === "project" ? "project" : "user",
  workspace,
  description: row.description ?? "",
  color: typeof row.color === "string" ? row.color : "",
  model: row.model ?? "",
  tools: row.tools ?? { ...EMPTY_TOOLS },
  prompt: row.prompt ?? "",
  // Absent on an older backend = the default (inject).
  injectAgents: row.inject_agents !== false,
});

const isInstalled = (row: SubagentAgent) =>
  row.source === "user" || row.source === "project";
const scopeLabel = (row: SubagentAgent) =>
  row.source === "project" ? "Project" : "User";

export function SubagentsTab({ workspace }: { workspace?: string }) {
  const [rows, setRows] = useState<SubagentAgent[] | null>(null);
  const [search, setSearch] = useState("");
  const [scopePick, setScopePick] = useState<ScopePick>({ scope: "user" });
  const [editor, setEditor] = useState<EditorState | null>(null);
  // The read-only view a built-in row opens.
  const [detail, setDetail] = useState<SubagentAgent | null>(null);
  const [armedDelete, setArmedDelete] = useState<string | null>(null);
  const [listError, setListError] = useState("");
  const [formError, setFormError] = useState("");
  const [models, setModels] = useState<{ id: string; label: string }[]>([]);
  const [modelLabels, setModelLabels] = useState<Record<string, string>>({});
  // Whether a delegation call may pick its own LLM route (the reference design's
  // `modelSelectionSettings`) — the tab's one global switch. `null` until the settings
  // read answers, so the control never renders a guessed state.
  const [modelSelection, setModelSelection] = useState<boolean | null>(null);
  // The composer picker's own catalogue (`/v1/models/catalog`): the grouped model menu
  // mirrors ModelPicker's data, so the two surfaces can never offer different models.
  const [catalog, setCatalog] = useState<CatalogModel[]>([]);
  const [workspaces, setWorkspaces] = useState<RecentWorkspace[]>([]);

  // The API's workspace param: present only for a workspace's project scope.
  const targetWorkspace =
    scopePick.scope === "project" ? scopePick.workspace : undefined;

  const refresh = () =>
    getSubagents(targetWorkspace)
      .then((next) => {
        setRows(next);
        setListError("");
      })
      .catch(() => {
        setRows([]);
        setListError("Could not load subagents — is the server running?");
      });

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetWorkspace]);

  // The Sidebar/projects list's own source (`/v1/workspaces/recent`): the app's known
  // workspaces, never a fabricated "default". The open session's folder joins the list
  // so its project scope stays reachable even before it shows up in recents.
  useEffect(() => {
    getRecentWorkspaces()
      .then(setWorkspaces)
      .catch(() => setWorkspaces([]));
  }, []);

  const workspaceOptions = useMemo(() => {
    if (!workspace || workspaces.some((w) => w.path === workspace)) return workspaces;
    return [{ path: workspace, name: basename(workspace), exists: true }, ...workspaces];
  }, [workspaces, workspace]);

  // The model list the composer's own picker uses (Settings ▸ Models), so the two
  // surfaces can never offer different models. The catalogue adds the provider grouping
  // the menu mirrors from ModelPicker; without it the settings list is grouped by id
  // prefix, so the menu still renders.
  useEffect(() => {
    getSettings()
      .then((s) => {
        setModels(
          (s.models ?? []).map((id) => ({
            id,
            label: displayModel(id, s.model_labels),
          })),
        );
        setModelLabels(s.model_labels ?? {});
        // The `modelSelectionSettings`: missing means on (the feature shipped on).
        setModelSelection(s.subagent_model_selection !== false);
      })
      .catch(() => setModels([]));
    getModelCatalog()
      .then((c) => setCatalog(c.models ?? []))
      .catch(() => setCatalog([]));
  }, []);

  const saveModelSelection = async (next: boolean) => {
    setModelSelection(next);
    const res = await setSubagentModelSelection(next);
    if (res.ok === false) {
      setModelSelection(!next);
      setListError(res.error || "Could not change model selection.");
      return;
    }
    setModelSelection(res.subagent_model_selection !== false);
  };

  // External CLI providers (codex / claude-code): install + auth markers for the badges
  // and the Sign in / Install actions. Never required — an in-process list just doesn't
  // carry a `provider`.
  const [external, setExternal] = useState<Record<string, SubagentExternalStatus>>({});
  // `${provider}:${action}` while a detached action runs; the poll below flips the badge.
  const [externalBusy, setExternalBusy] = useState("");

  const loadExternal = () =>
    getSubagentsExternal()
      .then((list) =>
        setExternal(Object.fromEntries(list.map((s) => [s.provider, s]))),
      )
      .catch(() => setExternal({}));

  useEffect(() => {
    void loadExternal();
  }, []);

  // An install is an npm download and a login waits on a browser round-trip, so poll while
  // one is in flight; give up after two minutes and leave the last state on screen.
  useEffect(() => {
    if (!externalBusy) return;
    const stop = window.setTimeout(() => setExternalBusy(""), 120_000);
    const timer = window.setInterval(() => void loadExternal(), 2500);
    return () => {
      window.clearInterval(timer);
      window.clearTimeout(stop);
    };
  }, [externalBusy]);

  // The provider account's own model list, fetched when a provider detail opens (Claude's
  // `/v1/models` via the CLI's OAuth token). Absent = not asked; `models` empty/error =
  // fall back to the static aliases. Never polled: the list changes once in a blue moon.
  const [accountModels, setAccountModels] = useState<
    Record<
      string,
      {
        loading: boolean;
        error?: string;
        source?: string;
        models?: { id: string; name: string }[];
      }
    >
  >({});

  useEffect(() => {
    const provider = detail?.provider;
    if (!provider || accountModels[provider]) return;
    setAccountModels((current) => ({ ...current, [provider]: { loading: true } }));
    getSubagentExternalModels(provider)
      .then((res) =>
        setAccountModels((current) => ({
          ...current,
          [provider]: res.ok
            ? { loading: false, models: res.models ?? [], source: res.source }
            : {
                loading: false,
                error: res.error || "could not read the account's models",
              },
        })),
      )
      .catch(() =>
        setAccountModels((current) => ({
          ...current,
          [provider]: { loading: false, error: "could not read the account's models" },
        })),
      );
  }, [detail, accountModels]);

  const runExternal = async (provider: string, action: "login" | "install") => {
    setExternalBusy(`${provider}:${action}`);
    setListError("");
    const res = await runSubagentExternalAction(provider, action);
    if (res.ok === false) {
      setExternalBusy("");
      setListError(res.error || `Could not ${action} ${provider}.`);
    }
  };

  const all = rows ?? [];
  const installed = useMemo(() => all.filter(isInstalled), [all]);
  const builtins = useMemo(() => all.filter((row) => !isInstalled(row)), [all]);

  const needle = search.trim().toLowerCase();
  const matches = (row: SubagentAgent) =>
    !needle ||
    row.name.toLowerCase().includes(needle) ||
    (row.description ?? "").toLowerCase().includes(needle);
  const visibleInstalled = installed.filter(matches);
  const visibleBuiltins = builtins.filter(matches);

  const backToList = () => {
    setEditor(null);
    setDetail(null);
    setFormError("");
  };

  const openNew = () => {
    setFormError("");
    setEditor(newEditor(scopePick));
  };

  const openRow = (row: SubagentAgent, mode: EditorMode) => {
    setFormError("");
    setArmedDelete(null);
    setEditor(
      editorFor(row, mode, row.source === "project" ? targetWorkspace : undefined),
    );
  };

  // A built-in row opens the read-only detail — there is no override form.
  const openBuiltin = (row: SubagentAgent) => {
    setListError("");
    setArmedDelete(null);
    setDetail(row);
  };

  const duplicate = (row: SubagentAgent) => {
    const taken = new Set(all.map((r) => r.name));
    let name = `${row.name}-copy`;
    let n = 2;
    while (taken.has(name)) name = `${row.name}-copy-${n++}`;
    setFormError("");
    setEditor({
      ...editorFor(row, "new", row.source === "project" ? targetWorkspace : undefined),
      name,
    });
  };

  const deleteEntry = async (name: string, scope: SubagentScope, ws?: string) => {
    const res = await deleteSubagent(name, scope, ws);
    if (res.ok === false) return res.error || "Could not delete that subagent.";
    refresh();
    return "";
  };

  const remove = async (row: SubagentAgent) => {
    if (armedDelete !== row.name) {
      setArmedDelete(row.name);
      return;
    }
    setArmedDelete(null);
    const error = await deleteEntry(
      row.name,
      row.source === "project" ? "project" : "user",
      row.source === "project" ? targetWorkspace : undefined,
    );
    setListError(error);
  };

  // Inline model select on an installed row: a real save of the same definition with the
  // new model. The file already carries a description and a prompt (the API's
  // validation).
  const changeModel = async (row: SubagentAgent, model: string | null) => {
    const res = await saveSubagent(
      row.name,
      {
        scope: row.source === "project" ? "project" : "user",
        description: row.description ?? "",
        color: typeof row.color === "string" ? row.color : "",
        model: model || null,
        tools: row.tools ?? { ...EMPTY_TOOLS },
        prompt: row.prompt ?? "",
        inject_agents: row.inject_agents !== false,
      },
      row.source === "project" ? targetWorkspace : undefined,
    );
    if (res.ok === false) {
      setListError(res.error || "Could not change the model.");
      return;
    }
    setListError("");
    refresh();
  };

  // The built-in model pin: optimistic on the row (and the open detail), then the
  // dedicated store route. A failure restores the previous value and surfaces inline.
  const changeBuiltinModel = async (row: SubagentAgent, model: string | null) => {
    const previous = row.model ?? "";
    const next = model || null;
    setRows((current) =>
      (current ?? []).map((r) => (r.name === row.name ? { ...r, model: next } : r)),
    );
    setDetail((current) =>
      current && current.name === row.name ? { ...current, model: next } : current,
    );
    setListError("");
    const res = await setSubagentModel(row.name, next, targetWorkspace);
    if (res.ok === false) {
      setRows((current) =>
        (current ?? []).map((r) =>
          r.name === row.name ? { ...r, model: previous || null } : r,
        ),
      );
      setDetail((current) =>
        current && current.name === row.name
          ? { ...current, model: previous || null }
          : current,
      );
      setListError(res.error || "Could not change the model.");
      return;
    }
    // The route answers the effective agent; adopt it, then re-read the list.
    if (res.agent) {
      setDetail((current) =>
        current && current.name === row.name ? res.agent! : current,
      );
    }
    refresh();
  };

  // The built-in enable switch: optimistic on the row (and the open detail), then the
  // state-dir store route. A failure restores the switch and surfaces inline.
  const toggleBuiltinEnabled = async (row: SubagentAgent, enabled: boolean) => {
    const previous = row.disabled === true;
    setRows((current) =>
      (current ?? []).map((r) =>
        r.name === row.name ? { ...r, disabled: !enabled } : r,
      ),
    );
    setDetail((current) =>
      current && current.name === row.name ? { ...current, disabled: !enabled } : current,
    );
    setListError("");
    const res = await setSubagentEnabled(row.name, enabled, targetWorkspace);
    if (res.ok === false) {
      setRows((current) =>
        (current ?? []).map((r) =>
          r.name === row.name ? { ...r, disabled: previous } : r,
        ),
      );
      setDetail((current) =>
        current && current.name === row.name ? { ...current, disabled: previous } : current,
      );
      setListError(res.error || "Could not change that subagent.");
      return;
    }
    if (res.agent) {
      setDetail((current) =>
        current && current.name === row.name ? res.agent! : current,
      );
    }
    refresh();
  };

  const save = async () => {
    if (!editor) return;
    const res = await saveSubagent(
      editor.name.trim(),
      {
        scope: editor.scope,
        description: editor.description.trim(),
        color: editor.color,
        model: editor.model || null,
        tools: editor.tools,
        prompt: editor.prompt,
        inject_agents: editor.injectAgents,
      },
      editor.scope === "project" ? editor.workspace : undefined,
    );
    if (res.ok === false) {
      setFormError(res.error || "Could not save that subagent.");
      return;
    }
    setFormError("");
    setEditor(null);
    refresh();
  };

  if (editor) {
    return (
      <SubagentForm
        editor={editor}
        models={models}
        modelLabels={modelLabels}
        catalog={catalog}
        rows={all}
        workspaces={workspaceOptions}
        error={formError}
        onChange={setEditor}
        onScopePick={setScopePick}
        onCancel={backToList}
        onSave={save}
        onDelete={async () => {
          const error = await deleteEntry(
            editor.name,
            editor.scope,
            editor.scope === "project" ? editor.workspace : undefined,
          );
          if (error) {
            setFormError(error);
            return;
          }
          backToList();
        }}
      />
    );
  }

  if (detail) {
    return (
      <BuiltinDetail
        row={detail}
        models={models}
        modelLabels={modelLabels}
        catalog={catalog}
        error={listError}
        onBack={backToList}
        onChangeModel={(model) => void changeBuiltinModel(detail, model)}
        external={detail.provider ? external[detail.provider] : undefined}
        externalBusy={externalBusy}
        accountModels={detail.provider ? accountModels[detail.provider] : undefined}
        onExternalAction={(provider, action) => void runExternal(provider, action)}
      />
    );
  }

  return (
    <section>
      {/* The page's one way to add, beside its title (settings redesign, 2026-10-20); with none
          installed yet the empty list offers it instead. */}
      {installed.length > 0 ? (
        <SettingsPageAction>
          <button type="button" className={BTN_PRIMARY} onClick={openNew}>
            <Icon name="plus" size={13} /> New subagent
          </button>
        </SettingsPageAction>
      ) : null}
      {/* Toolbar: the scope picker, then the search. */}
      <div className="flex flex-wrap items-center gap-2.5 mb-3">
        <ScopePicker
          value={scopePick}
          workspaces={workspaceOptions}
          onChange={setScopePick}
          title="Where subagents are read from and saved"
        />
        <span className="w-px h-5 bg-line" />
        <b className="text-[13px]">Subagents</b>
        <span className="text-[13px] text-faint tabular-nums">{all.length}</span>
        <span className="flex-1" />
        {/* Shrinks with the pane (a fixed 256px pushed a phone-width page sideways). */}
        <div className="relative min-w-0 flex-[0_1_16rem]">
          <Icon
            name="search"
            size={14}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"
          />
          <input
            className="w-full min-w-0 pl-8 pr-3 py-1.5 rounded-lg border border-line bg-panel text-[13px] outline-none focus:border-accent focus:ring-2 focus:ring-accentSoft"
            placeholder="Search subagents…"
            aria-label="Search subagents"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>

      {listError ? (
        <div role="alert" className="text-[13px] text-danger mb-3">
          {listError}
        </div>
      ) : null}

      <div className="flex items-center gap-2 mt-5 mb-2.5">
        <h3 className="text-[13px] font-semibold">Installed</h3>
        <span className="text-[13px] text-faint tabular-nums">
          {installed.length}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          className={ICONBTN}
          title="Refresh"
          aria-label="Refresh subagents"
          onClick={() => {
            refresh();
            void loadExternal();
          }}
        >
          <Icon name="refresh" size={15} />
        </button>
      </div>

      {visibleInstalled.length > 0 ? (
        <div className={CARD + " divide-y divide-line"}>
          {visibleInstalled.map((row) => (
            <div
              key={row.name}
              className="flex items-center gap-3.5 px-4 py-3 hover:text-ink group"
            >
              <span
                className="w-2.5 h-2.5 rounded-full shrink-0"
                style={{ background: agentColor(row.name, row.color) }}
              />
              <button
                type="button"
                className="min-w-0 flex-1 text-left"
                onClick={() => openRow(row, "edit")}
                aria-label={`Edit ${row.name}`}
              >
                <span className="flex items-center gap-2 font-semibold text-[13px]">
                  {row.name}
                  <span className={BADGE}>
                    {row.tool_count == null ? "All tools" : `${row.tool_count} tools`}
                  </span>
                  <span className={BADGE + " border-dashed"}>{scopeLabel(row)}</span>
                </span>
                <span className="block text-[13px] text-muted truncate">
                  {row.description}
                </span>
              </button>
              <span className="flex items-center gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100">
                <button
                  type="button"
                  className={ICONBTN}
                  title="Duplicate"
                  aria-label={`Duplicate ${row.name}`}
                  onClick={() => duplicate(row)}
                >
                  <Icon name="copy" size={14} />
                </button>
                <button
                  type="button"
                  className={ICONBTN + " hover:text-danger w-auto px-2"}
                  title="Delete"
                  aria-label={`Delete ${row.name}`}
                  onClick={() => void remove(row)}
                  onBlur={() => setArmedDelete(null)}
                >
                  {armedDelete === row.name ? (
                    <span className="text-[11px] whitespace-nowrap">Confirm delete</span>
                  ) : (
                    <Icon name="trash" size={14} />
                  )}
                </button>
              </span>
              <ModelMenu
                value={row.model ?? null}
                models={models}
                modelLabels={modelLabels}
                catalog={catalog}
                label={row.name}
                onSelect={(model) => void changeModel(row, model)}
              />
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-2xl bg-panel px-5 py-8 text-center text-muted">
          <div className="w-10 h-10 rounded-xl grid place-items-center bg-paper mx-auto text-muted">
            <Icon name="copy" size={18} />
          </div>
          <h3 className="mt-2.5 mb-1 text-[14px] font-semibold text-ink">
            {needle ? "No matching subagents" : "No subagents of your own yet"}
          </h3>
          <p className="m-0 mb-3.5 text-[13px]">
            {needle
              ? "Nothing here matches that search."
              : "A subagent is a helper Reeve hands a task to: its own instructions, tools and model. The built-in ones below are ready now."}
          </p>
          {!needle ? (
            <button type="button" className={BTN_PRIMARY + " mx-auto"} onClick={openNew}>
              <Icon name="plus" size={13} /> New subagent
            </button>
          ) : null}
        </div>
      )}

      {visibleBuiltins.length > 0 ? (
        <>
          <div className="flex items-center gap-2 mt-6 mb-2.5">
            <h3 className="text-[13px] font-semibold">Built-in subagents</h3>
            <span className="text-[13px] text-faint tabular-nums">
              {builtins.length}
            </span>
          </div>
          <div className={CARD_SOFT + " divide-y divide-line"}>
            {visibleBuiltins.map((row) => (
              <div
                key={row.name}
                className={
                  "flex items-center gap-3.5 px-4 py-3 hover:text-ink group cursor-pointer" +
                  (row.disabled ? " opacity-60" : "")
                }
                onClick={() => openBuiltin(row)}
              >
                <button
                  type="button"
                  className="min-w-0 flex-1 flex items-center gap-3.5 text-left"
                  onClick={() => openBuiltin(row)}
                  aria-label={`View ${row.name}`}
                >
                  <span className="w-9 h-9 rounded-lg grid place-items-center bg-panel border border-line text-muted shrink-0">
                    <Icon name="copy" size={16} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2 font-semibold text-[13px]">
                      {row.name}
                      <span className={BADGE}>
                        {row.tool_count == null ? "All tools" : `${row.tool_count} tools`}
                      </span>
                      {row.provider ? (
                        <span className={BADGE + " border-dashed"}>{row.provider} CLI</span>
                      ) : null}
                      {row.disabled ? <span className={BADGE}>Off</span> : null}
                    </span>
                    <span className="block text-[13px] text-muted truncate">
                      {row.description}
                    </span>
                  </span>
                </button>
                {row.provider ? (
                  <CliStatusChip
                    status={external[row.provider]}
                    model={row.model ?? null}
                  />
                ) : (
                  <ModelMenu
                    value={row.model ?? null}
                    models={models}
                    modelLabels={modelLabels}
                    catalog={catalog}
                    label={row.name}
                    onSelect={(model) => void changeBuiltinModel(row, model)}
                  />
                )}
                {/* The enable switch: the row stays so this control
                    can flip back; nothing may delegate to a disabled agent. */}
                <span className="shrink-0" onClick={(event) => event.stopPropagation()}>
                  <Switch
                    checked={row.disabled !== true}
                    label={
                      row.disabled === true
                        ? `Enable ${row.name}`
                        : `Disable ${row.name}`
                    }
                    testId={`subagent-enabled-${row.name}`}
                    onChange={(next) => void toggleBuiltinEnabled(row, next)}
                  />
                </span>
              </div>
            ))}
          </div>
        </>
      ) : null}

      {/* Every CLI Reeve can delegate to, installed or not: the only place an Install /
          Sign in is reachable for a provider whose agent type does not exist yet (a
          missing binary hides the agent row entirely). The list re-reads per request, so
          a freshly installed CLI shows its agent type on Refresh — no restart needed. */}
      {Object.values(external).length > 0 ? (
        <>
          <div className="flex items-center gap-2 mt-6 mb-2.5">
            <h3 className="text-[13px] font-semibold">CLI providers</h3>
            <span className="text-[13px] text-faint tabular-nums">
              {Object.values(external).length}
            </span>
          </div>
          <div className={CARD_SOFT + " divide-y divide-line"}>
            {Object.values(external).map((s) => (
              <div key={s.provider} className="flex items-center gap-3.5 px-4 py-3">
                <span className="w-9 h-9 rounded-lg grid place-items-center bg-panel border border-line text-muted shrink-0">
                  <Icon name="terminal" size={16} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2 font-semibold text-[13px]">
                    {s.provider}
                    <span className={BADGE + (s.installed ? "" : " text-danger")}>
                      {!s.installed ? "Not installed" : s.signed_in ? "Ready" : "Sign-in needed"}
                    </span>
                  </span>
                  <span className="block text-[12px] text-muted truncate" title={s.path || s.detail}>
                    {!s.installed
                      ? "Not on PATH on the machine running the sidecar."
                      : s.signed_in
                        ? `${s.detail} — the agent type is available to task.`
                        : `${s.detail} — sign in so the CLI can run.`}
                  </span>
                </span>
                {!s.installed ? (
                  <button
                    type="button"
                    className={BTN_PRIMARY}
                    disabled={externalBusy === `${s.provider}:install`}
                    onClick={() => void runExternal(s.provider, "install")}
                  >
                    <Icon name="plus" size={13} />
                    {externalBusy === `${s.provider}:install` ? "Installing…" : "Install CLI"}
                  </button>
                ) : !s.signed_in ? (
                  <button
                    type="button"
                    className={BTN_PRIMARY}
                    disabled={externalBusy === `${s.provider}:login`}
                    onClick={() => void runExternal(s.provider, "login")}
                  >
                    <Icon name="externalLink" size={13} />
                    {externalBusy === `${s.provider}:login` ? "Waiting for the browser…" : "Sign in"}
                  </button>
                ) : null}
              </div>
            ))}
          </div>
        </>
      ) : null}

      {/* One switch for every delegation call (the `modelSelectionSettings`): in-process
          children get a provider and model, codex / claude-code take one from their own
          account. Off, each child runs on its agent type's model. At the page's foot as an
          option, not above the list (settings redesign, 2026-10-20). */}
      <div className="flex items-center gap-2 mt-6 mb-2.5">
        <h3 className="text-[13px] font-semibold">Options</h3>
      </div>
      <div className={CARD + " flex items-center gap-3 px-4 py-3"} data-testid="subagent-model-selection">
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-semibold">Let a subagent run on another model</div>
          <div className="text-[12px] text-muted leading-snug mt-0.5">
            When Reeve hands off a task it may pick a cheaper or stronger model for it: a provider and model for
            its own subagents, or one from the CLI's own account for codex / claude-code. Off, every subagent uses
            its own set model. From the next conversation you start.
          </div>
        </div>
        {modelSelection !== null && (
          <Switch
            checked={modelSelection}
            label="Let subagents pick their own model"
            testId="subagent-model-selection-toggle"
            onChange={(next) => void saveModelSelection(next)}
          />
        )}
      </div>
    </section>
  );
}

// -- external CLI providers (codex / claude-code) ---------------------------------------

// An external agent's row chip: install/auth state at a glance. The row itself opens the
// detail, where Sign in / Install and the CLI model field live.
function CliStatusChip({
  status,
  model,
}: {
  status?: SubagentExternalStatus;
  model: string | null;
}) {
  const label = !status
    ? "Checking…"
    : !status.installed
      ? "Not installed"
      : !status.signed_in
        ? "Sign-in needed"
        : model
          ? model
          : "CLI default";
  return (
    <span
      className={BADGE + " max-w-[190px] truncate" + (status && !status.installed ? " text-danger" : "")}
      title={status?.detail ?? "Checking the CLI…"}
    >
      {label}
    </span>
  );
}

// The external agents' model picker: the SAME popup language as the built-in rows'
// ModelMenu (grouped list, capped scroller, pinned default row), but its rows are the
// models the CLI's own account/catalog answered with — plus the CLI's known aliases —
// and the pinned bottom row is "CLI default" (blank = the CLI's own choice). Selecting a
// row pins it through the same model route; free-form ids are typed nowhere because the
// menu IS the list of what that CLI reports.
function CliModelMenu({
  value,
  label,
  models,
  loading,
  error,
  source,
  onSelect,
}: {
  value: string | null;
  label: string;
  /** The account's / catalog's models; empty while loading or after a failed read. */
  models: { id: string; name: string }[];
  loading?: boolean;
  error?: string;
  source?: string;
  onSelect: (model: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  // True when the trigger sits too low for the capped menu to open downward.
  const [dropUp, setDropUp] = useState(false);
  const [focusTarget, setFocusTarget] = useState<string | "first" | "last" | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      trigger.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    if (!open || !focusTarget) return;
    const entering = focusTarget === "first" || focusTarget === "last";
    const items = menu.current?.querySelectorAll<HTMLButtonElement>(
      entering
        ? '[role="menuitemradio"]'
        : `[data-model-id="${focusTarget}"]`,
    );
    if (!items || items.length === 0) return;
    (focusTarget === "last" ? items[items.length - 1] : items[0]).focus();
    setFocusTarget(null);
  }, [open, focusTarget]);

  // One group: what the CLI actually reported. Nothing else is offered — a hardcoded
  // alias table is exactly the kind of copy that drifts, so a failed read shows the
  // reason instead (the pinned `CLI default` is always there).
  const groups = useMemo(
    () => (models.length > 0 ? [{ title: source ?? "Models", rows: models }] : []),
    [models, source],
  );

  const choose = (model: string | null) => {
    setOpen(false);
    trigger.current?.focus();
    // No-op when the row is already the pin (the menu always shows the current value).
    if ((model ?? "") !== (value ?? "")) onSelect(model);
  };

  // The menu is capped at 60vh; a trigger that low in the window would still run its
  // bottom rows (and the default) off-screen, so anchor the menu above it instead.
  const place = () => {
    const el = trigger.current;
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const cap = Math.min(360, window.innerHeight * 0.6);
    const below = window.innerHeight - r.bottom;
    return below < cap + 8 && r.top > below;
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!["ArrowDown", "ArrowUp"].includes(e.key)) return;
    e.preventDefault();
    if (!open) {
      setDropUp(place());
      setOpen(true);
      setFocusTarget(e.key === "ArrowUp" ? "last" : "first");
      return;
    }
    const items = menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]');
    if (!items || items.length === 0) return;
    const at = Array.prototype.indexOf.call(items, document.activeElement);
    const last = items.length - 1;
    const next =
      e.key === "ArrowDown" ? (at < 0 ? 0 : (at + 1) % items.length) : at < 0 ? last : (at - 1 + items.length) % items.length;
    items[next]?.focus();
  };

  return (
    <div
      className="relative shrink-0"
      onClick={(e) => e.stopPropagation()}
      onKeyDown={onKeyDown}
    >
      <button
        type="button"
        ref={trigger}
        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-line bg-panel text-[12px] text-muted hover:text-ink hover:border-lineStrong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent max-w-[190px]"
        aria-label={`${label} CLI model`}
        aria-haspopup="menu"
        aria-expanded={open}
        title={value ?? "CLI default"}
        onClick={() => {
          if (open) {
            setOpen(false);
          } else {
            setDropUp(place());
            setOpen(true);
          }
        }}
      >
        <span className="min-w-0 truncate font-mono">{value ?? "CLI default"}</span>
        <Icon name="chevronDown" size={12} className="shrink-0" />
      </button>

      {open ? (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div
            ref={menu}
            role="menu"
            aria-label={`${label} CLI model menu`}
            className={
              "absolute right-0 z-20 w-64 flex flex-col max-h-[min(360px,60vh)] rounded-xl border border-line bg-panel shadow-[var(--elev-2)] " +
              (dropUp ? "bottom-full mb-1.5" : "top-full mt-1.5")
            }
          >
            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1.5">
              {loading ? (
                <div className="px-2.5 py-2 text-[12px] text-faint">
                  Reading the account's models…
                </div>
              ) : null}
              {groups.length === 0 ? (
                loading ? null : (
                  <div className="px-2.5 py-2 text-[12px] text-faint">
                    {error ? `No models — ${error}` : "No models reported"}
                  </div>
                )
              ) : (
                groups.map((group) => (
                  <div key={group.title}>
                    <div className="flex items-center gap-2 px-2.5 pt-2 pb-1">
                      <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-faint">
                        {group.title}
                      </span>
                      <span className="text-[10px] font-mono px-1.5 py-0.5 rounded-full border border-line bg-paper text-muted">
                        {group.rows.length}
                      </span>
                    </div>
                    {group.rows.map((row) => {
                      const on = row.id === value;
                      return (
                        <button
                          key={row.id}
                          type="button"
                          role="menuitemradio"
                          aria-checked={on}
                          data-model-id={row.id}
                          className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-left text-[13px] text-ink hover:bg-paper focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                          title={row.name}
                          onClick={() => choose(row.id)}
                        >
                          <span className="min-w-0 flex-1 truncate font-mono">
                            {row.id}
                          </span>
                          {row.name && row.name !== row.id ? (
                            <span className="text-[11px] text-faint truncate max-w-[45%]">
                              {row.name}
                            </span>
                          ) : null}
                          {on ? (
                            <Icon name="check" size={13} className="text-accent shrink-0" />
                          ) : null}
                        </button>
                      );
                    })}
                  </div>
                ))
              )}
            </div>
            <div className="shrink-0 border-t border-line p-1.5">
              <button
                type="button"
                role="menuitemradio"
                aria-checked={!value}
                className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-left text-[13px] text-ink hover:bg-paper focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                onClick={() => choose(null)}
              >
                <span className="min-w-0 flex-1 truncate">CLI default</span>
                {!value ? (
                  <Icon name="check" size={13} className="text-accent shrink-0" />
                ) : null}
              </button>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

// -- the model menu (the reference design reference: grouped providers, Inherit pinned) ---------------

// The provider an id belongs to, ModelPicker's own rule: the part before ":", "openai"
// when there is none.
function providerOf(id: string): string {
  const at = id.indexOf(":");
  return at > 0 ? id.slice(0, at) : "openai";
}

type MenuRow = { id: string; name: string; provider: string; provider_title?: string; vision?: boolean | null };

// A compact grouped model menu mirroring the composer's `ModelPicker` data + grouping
// (`/v1/models/catalog`, falling back to the session's own selectable list): only the
// current value's provider starts expanded under its title, every other provider is a
// submenu row, the list is capped and scrolls inside the menu, and `Inherit default` is
// pinned below the scroll area with a check on the current value. Used on every settings
// row, the form included, with roving focus for keyboard-first editing.
function ModelMenu({
  value,
  models,
  modelLabels,
  catalog,
  label,
  ariaLabel,
  triggerClassName,
  onSelect,
}: {
  value: string | null;
  models: { id: string; label: string }[];
  modelLabels?: Record<string, string>;
  catalog: CatalogModel[];
  label: string;
  /** The trigger's accessible name; defaults to `${label} model`. */
  ariaLabel?: string;
  /** Extra trigger classes — the form sizes it like an input. */
  triggerClassName?: string;
  onSelect: (model: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  // True when the trigger sits too low for the capped menu to open downward. Measured
  // when the menu opens, so render never depends on layout.
  const [dropUp, setDropUp] = useState(false);
  // Where focus goes after the next render: "first"/"last" enter the open menu, a model
  // id lands on that row. Expanding a provider removes the row that had focus, so a
  // keyboard user would otherwise lose their place.
  const [focusTarget, setFocusTarget] = useState<string | "first" | "last" | null>(
    null,
  );
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  // Every provider but the current value's starts collapsed; closing forgets an explicit
  // expansion so the next open is short again.
  useEffect(() => {
    if (!open) setExpanded(null);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      trigger.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    if (!open || !focusTarget) return;
    const entering = focusTarget === "first" || focusTarget === "last";
    const items = menu.current?.querySelectorAll<HTMLButtonElement>(
      entering
        ? '[role="menuitem"], [role="menuitemradio"]'
        : `[data-model-id="${focusTarget}"]`,
    );
    if (!items || items.length === 0) return;
    (focusTarget === "last" ? items[items.length - 1] : items[0]).focus();
    setFocusTarget(null);
  }, [open, expanded, focusTarget]);

  // The catalogue rows when the catalogue answered; else the session's list, grouped by
  // the id prefix — the same degradation the composer's picker has. A pinned model the
  // catalogue does not carry keeps its own row (with its check), so the current value
  // stays visible instead of silently missing from its own menu.
  const source: MenuRow[] = useMemo(() => {
    const base: MenuRow[] = catalog.length
      ? catalog
      : models.map((m) => ({
          id: m.id,
          name: m.label,
          provider: providerOf(m.id),
        }));
    if (value && !base.some((row) => row.id === value)) {
      return [
        {
          id: value,
          name: displayModelFull(value, modelLabels),
          provider: providerOf(value),
        },
        ...base,
      ];
    }
    return base;
  }, [catalog, models, value, modelLabels]);

  const groups = useMemo(() => {
    const out = new Map<string, MenuRow[]>();
    for (const row of source) {
      const key = row.provider_title || row.provider;
      const bucket = out.get(key);
      if (bucket) bucket.push(row);
      else out.set(key, [row]);
    }
    return [...out.entries()];
  }, [source]);

  // Only the current model's provider starts expanded; with no pin every provider stays
  // collapsed, so the menu opens short and only grows when a group is asked for.
  const current = value ? source.find((r) => r.id === value) : undefined;
  const activeProvider =
    expanded ?? (current ? current.provider_title || current.provider : null);

  const choose = (model: string | null) => {
    setOpen(false);
    trigger.current?.focus();
    onSelect(model);
  };

  // The menu is capped at 60vh; a trigger that low in the window would still run its
  // bottom rows (and Inherit) off-screen, so anchor the menu above it instead.
  const place = () => {
    const el = trigger.current;
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const cap = Math.min(360, window.innerHeight * 0.6);
    const below = window.innerHeight - r.bottom;
    return below < cap + 8 && r.top > below;
  };

  // Arrow keys walk the menu's rows; from the trigger they open it and land inside.
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    if (!open) {
      setDropUp(place());
      setOpen(true);
      setFocusTarget(e.key === "ArrowUp" || e.key === "End" ? "last" : "first");
      return;
    }
    const items = menu.current?.querySelectorAll<HTMLButtonElement>(
      '[role="menuitem"], [role="menuitemradio"]',
    );
    if (!items || items.length === 0) return;
    const at = Array.prototype.indexOf.call(items, document.activeElement);
    const last = items.length - 1;
    const next =
      e.key === "Home"
        ? 0
        : e.key === "End"
          ? last
          : e.key === "ArrowDown"
            ? at < 0
              ? 0
              : (at + 1) % items.length
            : at < 0
              ? last
              : (at - 1 + items.length) % items.length;
    items[next]?.focus();
  };

  return (
    <div
      className="relative shrink-0"
      onClick={(e) => e.stopPropagation()}
      onKeyDown={onKeyDown}
    >
      <button
        type="button"
        ref={trigger}
        className={
          "inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-line bg-panel text-[12px] text-muted hover:text-ink hover:border-lineStrong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent " +
          (triggerClassName ?? "max-w-[190px]")
        }
        aria-label={ariaLabel ?? `${label} model`}
        aria-haspopup="menu"
        aria-expanded={open}
        title={value ? displayModelFull(value, modelLabels) : "Inherit default"}
        onClick={() => {
          if (open) {
            setOpen(false);
          } else {
            setDropUp(place());
            setOpen(true);
          }
        }}
      >
        <span className="min-w-0 truncate">
          {value ? displayModel(value, modelLabels) : "Inherit default"}
        </span>
        <Icon name="chevronDown" size={12} className="shrink-0" />
      </button>

      {open ? (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div
            ref={menu}
            role="menu"
            aria-label={`${label} model menu`}
            className={
              "absolute right-0 z-20 w-64 flex flex-col max-h-[min(360px,60vh)] rounded-xl border border-line bg-panel shadow-[var(--elev-2)] " +
              (dropUp ? "bottom-full mb-1.5" : "top-full mt-1.5")
            }
          >
            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1.5">
              {groups.length === 0 ? (
                <div className="px-2.5 py-2 text-[12px] text-faint">No models configured</div>
              ) : (
                groups.map(([title, rows]) =>
                  title === activeProvider ? (
                    <div key={title}>
                      <div className="flex items-center gap-2 px-2.5 pt-2 pb-1">
                        <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-faint">
                          {title}
                        </span>
                        <span className="text-[10px] font-mono px-1.5 py-0.5 rounded-full border border-line bg-paper text-muted">
                          {rows.length}
                        </span>
                      </div>
                      {rows.map((row) => {
                        const on = row.id === value;
                        return (
                          <button
                            key={row.id}
                            type="button"
                            role="menuitemradio"
                            aria-checked={on}
                            data-model-id={row.id}
                            className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-left text-[13px] text-ink hover:bg-paper focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                            onClick={() => choose(row.id)}
                          >
                            <span className="min-w-0 flex-1 truncate">{row.name}</span>
                            {row.vision ? (
                              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded-full border border-line bg-paper text-muted shrink-0">
                                Vision
                              </span>
                            ) : null}
                            {on ? (
                              <Icon name="check" size={13} className="text-accent shrink-0" />
                            ) : null}
                          </button>
                        );
                      })}
                    </div>
                  ) : (
                    <button
                      key={title}
                      type="button"
                      role="menuitem"
                      className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-left text-[13px] text-ink hover:bg-paper focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                      onClick={() => {
                        setExpanded(title);
                        setFocusTarget(rows[0]?.id ?? null);
                      }}
                    >
                      <span className="min-w-0 flex-1 truncate">{title}</span>
                      <Icon name="chevronRight" size={13} className="text-muted shrink-0" />
                    </button>
                  ),
                )
              )}
            </div>
            <div className="shrink-0 border-t border-line p-1.5">
              <button
                type="button"
                role="menuitemradio"
                aria-checked={!value}
                className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-left text-[13px] text-ink hover:bg-paper focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                onClick={() => choose(null)}
              >
                <span className="min-w-0 flex-1 truncate">Inherit default</span>
                {!value ? (
                  <Icon name="check" size={13} className="text-accent shrink-0" />
                ) : null}
              </button>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

// -- the built-in detail (read-only; the model is the one editable field) --------------

function BuiltinDetail({
  row,
  models,
  modelLabels,
  catalog,
  error,
  external,
  externalBusy,
  accountModels,
  onBack,
  onChangeModel,
  onExternalAction,
}: {
  row: SubagentAgent;
  models: { id: string; label: string }[];
  modelLabels?: Record<string, string>;
  catalog: CatalogModel[];
  error: string;
  /** Install/auth markers for `row.provider` (absent for in-process built-ins). */
  external?: SubagentExternalStatus;
  /** `${provider}:${action}` while a detached login/install runs. */
  externalBusy?: string;
  /** The provider account's own models, still loading or failed — aliases cover it. */
  accountModels?: {
    loading: boolean;
    error?: string;
    source?: string;
    models?: { id: string; name: string }[];
  };
  onBack: () => void;
  onChangeModel: (model: string | null) => void;
  onExternalAction?: (provider: string, action: "login" | "install") => void;
}) {
  const [promptOpen, setPromptOpen] = useState(false);
  const tools = row.tools ?? { ...EMPTY_TOOLS };
  const allowed = tools.preset === "all" ? [] : tools.allow;
  const denied = tools.preset === "all" ? tools.deny : [];
  const prompt = (row.prompt ?? "").trim();
  const busy = (action: string) => externalBusy === `${row.provider}:${action}`;

  return (
    <section>
      <div className="flex items-center gap-1.5 text-[13px] text-faint mb-4">
        <button type="button" className="hover:text-ink" onClick={onBack}>
          Subagents
        </button>
        <span aria-hidden="true">›</span>
        <span>{row.name}</span>
      </div>

      <div className="flex items-start gap-4">
        <span className="w-11 h-11 rounded-xl2 grid place-items-center bg-panel border border-line text-muted shrink-0">
          <Icon name="copy" size={20} />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="flex items-center gap-2 text-[20px] font-semibold tracking-[-0.01em]">
            {row.name}
            <span className={BADGE + " border-dashed"}>Built-in</span>
            <span className={BADGE}>
              {row.tool_count == null ? "All tools" : `${row.tool_count} tools`}
            </span>
            {row.provider ? (
              <span className={BADGE + " border-dashed"}>{row.provider} CLI</span>
            ) : null}
          </h2>
          <p className="text-[13px] text-muted mt-1.5 leading-relaxed max-w-[560px]">
            {row.description}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1.5 shrink-0">
          <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-faint">
            {row.provider ? "CLI model" : "Model"}
          </span>
          {row.provider ? (
            <CliModelMenu
              value={row.model ?? null}
              label={row.name}
              models={accountModels?.models ?? []}
              loading={accountModels?.loading}
              error={accountModels?.error}
              source={accountModels?.source}
              onSelect={onChangeModel}
            />
          ) : (
            <ModelMenu
              value={row.model ?? null}
              models={models}
              modelLabels={modelLabels}
              catalog={catalog}
              label={row.name}
              onSelect={onChangeModel}
            />
          )}
          <span className="text-[11px] text-faint">
            {row.provider
              ? "Blank = the CLI's own default"
              : "The only editable field"}
          </span>
        </div>
      </div>

      {row.provider ? (
        <div className={CARD + " mt-6 px-4 py-4"}>
          <div className="flex items-start gap-3">
            <span className="min-w-0 flex-1">
              <h3 className="text-[13px] font-semibold">{row.provider} CLI status</h3>
              <p className="text-[12px] text-muted mt-1.5 leading-relaxed">
                Runs as a real {row.provider} process: it uses that CLI&rsquo;s own model and
                sign-in (its subscription or API key). The CLI model above only overrides
                the <span className="font-mono">--model</span> flag. Both CLIs run headless
                with full permissions — no approval prompts, so treat a delegation as full
                trust.
              </p>
              <p className="text-[12px] text-muted mt-2">
                {external
                  ? external.installed
                    ? external.signed_in
                      ? `Installed and signed in — ${external.detail}`
                      : `Installed — ${external.detail}`
                    : "Not on PATH on the machine running the sidecar."
                  : "Checking…"}
              </p>
              <p className="text-[12px] text-muted mt-1">
                {accountModels?.loading
                  ? "Reading your account's model list…"
                  : accountModels?.models?.length
                    ? `${accountModels.models.length} model${
                        accountModels.models.length === 1 ? "" : "s"
                      } on ${accountModels.source ?? "your account"} — listed in the menu above.`
                    : `Couldn't read your account's model list${
                        accountModels?.error ? ` (${accountModels.error})` : ""
                      } — the CLI's aliases are still offered.`}
              </p>
            </span>
            {external && !external.installed && onExternalAction ? (
              <button
                type="button"
                className={BTN_PRIMARY}
                disabled={busy("install")}
                onClick={() => onExternalAction(row.provider!, "install")}
              >
                <Icon name="plus" size={13} />
                {busy("install") ? "Installing…" : "Install CLI"}
              </button>
            ) : null}
            {external && external.installed && !external.signed_in && onExternalAction ? (
              <button
                type="button"
                className={BTN_PRIMARY}
                disabled={busy("login")}
                onClick={() => onExternalAction(row.provider!, "login")}
              >
                <Icon name="externalLink" size={13} />
                {busy("login") ? "Waiting for the browser…" : "Sign in"}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {error ? (
        <div role="alert" className="text-[13px] text-danger mt-4">
          {error}
        </div>
      ) : null}

      <div className={CARD + " mt-6 divide-y divide-line"}>
        <div className="px-4 py-4">
          <h3 className="text-[13px] font-semibold">Allowed tools</h3>
          {tools.preset === "all" && denied.length === 0 ? (
            <p className="text-[13px] text-muted mt-1.5 leading-relaxed">
              Every tool — this definition denies no group of its own. Out-of-root reads
              and the harness defaults still apply.
            </p>
          ) : (
            <>
              <div className="flex flex-wrap gap-1.5 mt-2.5">
                {allowed.map((group) => (
                  <span key={group} className={CHIP}>
                    {group}
                  </span>
                ))}
              </div>
              {denied.length > 0 ? (
                <p className="text-[12px] text-muted mt-2.5">
                  Denied:{" "}
                  {denied.map((group) => (
                    <span key={group} className="font-mono mr-1.5">
                      {group}
                    </span>
                  ))}
                </p>
              ) : null}
            </>
          )}
        </div>

        <div className="px-4 py-4">
          <button
            type="button"
            className="w-full flex items-center gap-2 text-left"
            aria-expanded={promptOpen}
            onClick={() => setPromptOpen((v) => !v)}
          >
            <span className="text-[13px] font-semibold">System prompt</span>
            <span className="text-[11px] text-faint">read-only</span>
            <span className="flex-1" />
            <Icon
              name={promptOpen ? "chevronDown" : "chevronRight"}
              size={14}
              className="text-muted"
            />
          </button>
          {promptOpen ? (
            prompt ? (
              <pre className="mt-3 px-3 py-2.5 rounded-lg border border-line bg-paper text-[12px] text-ink whitespace-pre-wrap break-words leading-relaxed">
                {prompt}
              </pre>
            ) : (
              <p className="text-[13px] text-muted mt-2.5 leading-relaxed">
                This built-in ships no system prompt of its own — it runs on the
                provider&rsquo;s base prompt.
              </p>
            )
          ) : null}
        </div>
      </div>
    </section>
  );
}

// -- the form page (New / Edit) --------------------------------------------------------

function SubagentForm({
  editor,
  models,
  modelLabels,
  catalog,
  rows,
  workspaces,
  error,
  onChange,
  onScopePick,
  onCancel,
  onSave,
  onDelete,
}: {
  editor: EditorState;
  models: { id: string; label: string }[];
  modelLabels?: Record<string, string>;
  catalog: CatalogModel[];
  rows: SubagentAgent[];
  workspaces: RecentWorkspace[];
  error: string;
  onChange: (next: EditorState) => void;
  onScopePick: (next: ScopePick) => void;
  onCancel: () => void;
  onSave: () => void;
  onDelete: () => void;
}) {
  const [armed, setArmed] = useState(false);
  const set = (patch: Partial<EditorState>) => onChange({ ...editor, ...patch });
  // The editor is the source of truth (a duplicate keeps its row's scope); the picker
  // just renders it. Changing it in New mode also moves the tab's own scope.
  const formScope: ScopePick =
    editor.scope === "project"
      ? { scope: "project", workspace: editor.workspace }
      : { scope: "user" };
  const name = editor.name.trim();
  const nameError =
    editor.mode === "new" && name !== "" && !NAME_RE.test(name)
      ? "Lowercase letters, digits, and dashes — e.g. code-reviewer or team/helper."
      : "";
  const clash =
    editor.mode === "new" &&
    name !== "" &&
    rows.some((row) => row.name === name && row.source === editor.scope);
  const canSave =
    name !== "" &&
    !nameError &&
    editor.description.trim() !== "" &&
    editor.prompt.trim() !== "";
  const title = editor.mode === "edit" ? "Edit subagent" : "New subagent";

  const toggleGroup = (group: string) => {
    const allow = editor.tools.allow.includes(group)
      ? editor.tools.allow.filter((g) => g !== group)
      : [...editor.tools.allow, group];
    set({ tools: { ...editor.tools, allow } });
  };

  return (
    <section>
      <div className="flex items-center gap-1.5 text-[13px] text-faint mb-3.5">
        <button type="button" className="hover:text-ink" onClick={onCancel}>
          Subagents
        </button>
        <span aria-hidden="true">›</span>
        <span>{title}</span>
      </div>
      <h2 className="text-[20px] font-semibold tracking-[-0.01em] mb-1.5">{title}</h2>
      <p className="text-[13px] text-muted mb-5 leading-relaxed">
        Fill in the name, tools, and system prompt, then save to return to the list.
      </p>

      <div className={CARD}>
        <div className="flex items-center px-4 py-3 border-b border-line">
          <span className="flex-1" />
          <span className="text-[12px] text-faint mr-2.5">Scope</span>
          <ScopePicker
            value={formScope}
            workspaces={workspaces}
            onChange={(next) => {
              set({ scope: next.scope, workspace: next.workspace });
              onScopePick(next);
            }}
            disabled={editor.mode !== "new"}
            title={editor.mode === "new" ? undefined : "The scope is fixed after saving"}
          />
        </div>

        <div className="px-4 py-5 grid gap-5">
          <div className="grid grid-cols-1 sm:grid-cols-[1.4fr_0.9fr_0.9fr] gap-4">
            <div>
              <label className={FIELD_LABEL + " block mb-1.5"} htmlFor="subagent-name">
                Name
              </label>
              <input
                id="subagent-name"
                className={INPUT + (nameError ? " border-danger" : "")}
                value={editor.name}
                placeholder="code-reviewer"
                spellCheck={false}
                disabled={editor.mode !== "new"}
                onChange={(e) => set({ name: e.target.value })}
              />
              <div className="text-[12px] text-faint mt-1.5 leading-relaxed">
                lowercase, dashes. Used as the file name and the @mention.
              </div>
              {nameError ? (
                <div className="text-[12px] text-danger mt-1">{nameError}</div>
              ) : null}
            </div>

            <div>
              <span className={FIELD_LABEL + " block mb-1.5"}>Color</span>
              <div className="flex items-center gap-2 h-9" role="group" aria-label="Color">
                {SWATCHES.map((color) => {
                  const on = editor.color.toLowerCase() === color;
                  return (
                    <button
                      key={color}
                      type="button"
                      className={
                        "w-[18px] h-[18px] rounded-full border-0 cursor-pointer " +
                        (on ? "ring-2 ring-ink ring-offset-2 ring-offset-panel" : "")
                      }
                      style={{ background: color }}
                      aria-label={`Color ${color}`}
                      aria-pressed={on}
                      onClick={() => set({ color: on ? "" : color })}
                    />
                  );
                })}
              </div>
              <div className="text-[12px] text-faint mt-1.5 leading-relaxed">
                The dot on the task card and in this list.
              </div>
            </div>

            <div>
              <span className={FIELD_LABEL + " block mb-1.5"}>Model</span>
              {/* The app's own grouped picker (the composer picker's catalogue), never a
                  native select over one provider's list. */}
              <ModelMenu
                value={editor.model || null}
                models={models}
                modelLabels={modelLabels}
                catalog={catalog}
                label="subagent"
                ariaLabel="Model"
                triggerClassName="w-full h-9 justify-between text-[13px]"
                onSelect={(model) => set({ model: model ?? "" })}
              />
              <div className="text-[12px] text-faint mt-1.5 leading-relaxed">
                Inherited from the session unless set.
              </div>
            </div>
          </div>

          <div>
            <label
              className={FIELD_LABEL + " block mb-1.5"}
              htmlFor="subagent-description"
            >
              Description
            </label>
            <input
              id="subagent-description"
              className={INPUT}
              value={editor.description}
              placeholder="Short description shown to the model"
              onChange={(e) => set({ description: e.target.value })}
            />
            <div className="text-[12px] text-faint mt-1.5 leading-relaxed">
              One line. This is what the main agent reads when choosing a subagent.
            </div>
          </div>

          <div>
            <div className={FIELD_LABEL + " mb-1.5"}>Allowed tools</div>
            <div className="max-w-[280px]">
              <Select<SubagentTools["preset"]>
                label="Allowed tools"
                testId="subagent-tools"
                value={editor.tools.preset}
                options={[
                  { value: "all", label: "Default all permissions" },
                  ...(editor.tools.preset !== "all" && editor.tools.preset !== "custom"
                    ? [{ value: editor.tools.preset, label: "Read-only search" }]
                    : []),
                  { value: "custom", label: "Custom allowed tools" },
                ]}
                onChange={(preset) => {
                  set({
                    tools: {
                      ...editor.tools,
                      preset,
                      // An explicit deny only means something under "all"; switching to
                      // a deny-all preset would silently keep it and win over an allow.
                      deny: preset === "all" ? editor.tools.deny : [],
                    },
                  });
                }}
              />
            </div>
            <div className={FIELD_HELP}>
              Control which tools this subagent can use. Everything else is denied.
            </div>
            {editor.tools.preset === "custom" ? (
              <div
                className="mt-2.5 rounded-xl2 border border-line bg-panel p-2"
                role="group"
                aria-label="Allowed tool groups"
              >
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-1">
                  {TOOL_GROUPS.map((group) => {
                    const on = editor.tools.allow.includes(group);
                    const dangerous = DANGEROUS_GROUPS.has(group);
                    return (
                      <label
                        key={group}
                        className="flex items-center gap-2 px-2 py-1.5 rounded-lg text-[13px] text-ink hover:text-ink cursor-pointer select-none has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent"
                      >
                        <input
                          type="checkbox"
                          className="accent-accent w-3.5 h-3.5 shrink-0"
                          checked={on}
                          onChange={() => toggleGroup(group)}
                        />
                        <span className="min-w-0 flex-1 truncate">{groupLabel(group)}</span>
                        {dangerous ? (
                          <span
                            className="w-1.5 h-1.5 rounded-full bg-danger shrink-0"
                            title="Can change files or run commands"
                            aria-hidden="true"
                          />
                        ) : null}
                      </label>
                    );
                  })}
                </div>
              </div>
            ) : null}
          </div>

          <div>
            <label className={FIELD_LABEL + " block mb-1.5"} htmlFor="subagent-prompt">
              System prompt
            </label>
            <textarea
              id="subagent-prompt"
              className={INPUT + " min-h-[128px] resize-y leading-relaxed"}
              spellCheck={false}
              value={editor.prompt}
              placeholder="You are a code reviewer. You read a change and report what is wrong with it — you do not fix it."
              onChange={(e) => set({ prompt: e.target.value })}
            />
            <div className="text-[12px] text-faint mt-1.5 leading-relaxed">
              The body of the markdown file. Sent in place of the provider&rsquo;s base
              prompt.
            </div>
          </div>

          <div className="flex items-center gap-3 pt-3 border-t border-line">
            <span className="flex-1">
              <span className="block font-semibold text-[13px]">Inject AGENTS.md</span>
              <span className="block text-[13px] text-muted mt-0.5">
                Append the project's AGENTS.md instructions to this subagent's prompt.
              </span>
            </span>
            <Switch
              checked={editor.injectAgents}
              label="Inject AGENTS.md"
              onChange={(next) => set({ injectAgents: next })}
            />
          </div>

          {clash ? (
            <div className="flex items-start gap-2.5 rounded-xl2 border border-warnInk/30 bg-warnSoft text-warnInk px-3 py-2.5 text-[13px]">
              <span aria-hidden="true">ⓘ</span>
              <span>
                A {editor.scope === "project" ? "project" : "user"}-scoped subagent named{" "}
                <b>{name}</b> already exists. Saving replaces its file.
              </span>
            </div>
          ) : null}
        </div>

        <div className="flex items-center gap-2 px-4 py-3 border-t border-line">
          {editor.mode === "edit" ? (
            <button
              type="button"
              className={BTN_GHOST + " text-danger"}
              onClick={() => (armed ? onDelete() : setArmed(true))}
              onBlur={() => setArmed(false)}
            >
              <Icon name="trash" size={13} /> {armed ? "Confirm delete" : "Delete"}
            </button>
          ) : null}
          <span className="flex-1" />
          {error ? (
            <span role="alert" className="text-[13px] text-danger mr-1">
              {error}
            </span>
          ) : null}
          <button type="button" className={BTN_GHOST} onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className={BTN_PRIMARY}
            disabled={!canSave}
            onClick={onSave}
          >
            Save
          </button>
        </div>
      </div>
    </section>
  );
}

// -- the scope picker (the reference design reference: pill + menu) ---------------------------------

// A dropdown pill for the tab's scope: the current label over a menu with `User` (monitor
// glyph, global scope) and a `Workspaces` group (folder glyph) of the app's workspaces.
// Choosing a workspace targets its project files via the `workspace` query param.
function ScopePicker({
  value,
  workspaces,
  onChange,
  disabled,
  title,
}: {
  value: ScopePick;
  workspaces: RecentWorkspace[];
  onChange: (next: ScopePick) => void;
  disabled?: boolean;
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      trigger.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const choose = (next: ScopePick) => {
    onChange(next);
    setOpen(false);
    trigger.current?.focus();
  };

  const userScope = value.scope === "user";

  return (
    <div className="relative">
      <button
        type="button"
        ref={trigger}
        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-full border border-line bg-panel text-[13px] text-muted hover:text-ink hover:border-lineStrong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-60 disabled:hover:border-line disabled:hover:text-muted"
        title={title}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name="monitor" size={13} />
        <b className="font-semibold text-ink">
          {pickLabel(value, workspaces)}
        </b>
        <Icon name="chevronDown" size={12} />
      </button>

      {open ? (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div
            role="menu"
            aria-label="Scope"
            className="absolute left-0 top-full mt-1.5 w-52 z-20 p-1.5 rounded-xl border border-line bg-panel shadow-[var(--elev-2)]"
          >
            <button
              type="button"
              role="menuitemradio"
              aria-checked={userScope}
              className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-left text-[13px] text-ink hover:bg-paper focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              onClick={() => choose({ scope: "user" })}
            >
              <Icon name="monitor" size={14} className="text-muted shrink-0" />
              <span className="min-w-0 flex-1 truncate">User</span>
              {userScope ? (
                <Icon name="check" size={14} className="text-accent shrink-0" />
              ) : null}
            </button>
            <div className="px-2.5 pt-2 pb-1 text-[11px] font-semibold uppercase tracking-[0.06em] text-faint">
              Workspaces
            </div>
            {workspaces.length === 0 ? (
              <div className="px-2.5 py-1.5 text-[12px] text-faint">
                No workspaces open
              </div>
            ) : (
              workspaces.map((w) => {
                const selected = !userScope && value.workspace === w.path;
                return (
                  <button
                    key={w.path}
                    type="button"
                    role="menuitemradio"
                    aria-checked={selected}
                    className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-left text-[13px] text-ink hover:bg-paper focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    onClick={() => choose({ scope: "project", workspace: w.path })}
                  >
                    <Icon name="folder" size={14} className="text-muted shrink-0" />
                    <span className="min-w-0 flex-1 truncate" title={w.path}>
                      {w.name}
                    </span>
                    {selected ? (
                      <Icon name="check" size={14} className="text-accent shrink-0" />
                    ) : null}
                  </button>
                );
              })
            )}
          </div>
        </>
      ) : null}
    </div>
  );
}
