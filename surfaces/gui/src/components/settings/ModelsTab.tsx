import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { getProviderModels, getSettings, removeModel, setDefaultModel, type CatalogModel, type ModelSettings, type ProviderInfo } from "../../api";
import { openExternal } from "../../desktop";
import { modelName, providerName, useModelCatalog } from "../../modelFacts";
import { KEY_HELP, ProviderCards, ProviderFields, ProviderMark, providerConnected, relTime, useProviderSetup, type ProviderSetupState } from "../../providers/ProviderSetup";
import { useDismiss } from "../../useDismiss";
import { Icon } from "../Icon";
import { ModelChecklist } from "../ModelChecklist";
import { ModelRow } from "../ModelRow";
import { AddProviderDialog, SignInSteps } from "./AddProviderDialog";
import { announceSaved, Select, SettingsCard, SettingsPageAction, SettingsRow, TextButton } from "./primitives";

// Settings ▸ Models. The page lists the providers that are connected, the default model and the
// models the composer offers; a provider opens on its own page, with its connection and its models.
// The key form and the provider cards are onboarding's, so the two never drift apart.

/** A provider's own model list, asked for when its page opens. The server answers with its curated
 *  rows when the provider cannot list (`source: "curated"`); an older server's 404 leaves it empty. */
type LiveModels = { loading: boolean; ok: boolean; models: string[]; source?: string; error?: string };

function useLiveModels(provider: string | null) {
  const [live, setLive] = useState<LiveModels>({ loading: false, ok: false, models: [] });
  const load = useCallback(
    (refresh = false) => {
      if (!provider) return;
      setLive((now) => ({ ...now, loading: true }));
      getProviderModels(provider, refresh)
        .then((r) => setLive({ loading: false, ok: !!r.ok, models: r.models ?? [], source: r.source, error: r.error }))
        .catch(() => setLive({ loading: false, ok: false, models: [], source: "curated" }));
    },
    [provider],
  );
  return { live, load };
}

function sourceNote(live: LiveModels): string {
  if (live.loading) return "Loading the provider's own model list…";
  if (live.source === "live") return "Live from the provider's API.";
  if (live.source === "curated") return `Couldn't list from the provider${live.error ? ` (${live.error})` : ""} — showing the curated models.`;
  return "";
}

export function ModelsTab() {
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const reload = () =>
    getSettings()
      .then(setSettings)
      .catch(() => setSettings(null));
  // keepOpen: a provider's page stays open after Check again, or after a replaced key passes.
  const ps = useProviderSetup({ onSaved: reload, keepOpen: true });
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    reload();
  }, []);

  if (!settings) return <div className="text-[13px] text-muted">Loading…</div>;

  // Add provider is a window over the page; a connected provider picked there opens its own page.
  const dialog = adding ? (
    <AddProviderDialog
      modelLabels={settings.model_labels}
      onClose={() => setAdding(false)}
      onConnected={() => {
        void ps.refreshProviders();
        reload();
      }}
      onOpenProvider={(name) => {
        setAdding(false);
        ps.openProvider(name);
      }}
    />
  ) : null;

  if (ps.sel !== null) return <ProviderPage ps={ps} settings={settings} onSettings={setSettings} />;

  const connected = ps.providers.filter(providerConnected).length;
  return (
    <>
      <SettingsPageAction>
        <button type="button" className="settings-text-btn accent" data-testid="models-add-provider" onClick={() => setAdding(true)}>
          <Icon name="plus" size={13} /> Add provider
        </button>
      </SettingsPageAction>
      <DefaultModelCard settings={settings} providers={ps.providers} onChanged={reload} />
      <section className="settings-card-block">
        {connected > 0 ? (
          <>
            <h2 className="settings-card-heading">
              Connected <span className="settings-heading-count">{connected}</span>
            </h2>
            <ProviderCards ps={ps} tp="set" lastUsed list filter={providerConnected} />
          </>
        ) : (
          <div className="settings-panel settings-empty" data-testid="models-empty">
            <Icon name="sparkle" size={20} className="settings-empty-mark" />
            <div className="settings-empty-title">No provider yet</div>
            <p className="settings-empty-text">Sign in with a plan you already pay for, add an API key, or run models on this computer.</p>
            <button type="button" className="settings-primary-btn" onClick={() => setAdding(true)}>
              Add provider
            </button>
          </div>
        )}
      </section>
      <ComposerPickerCard settings={settings} providers={ps.providers} onChanged={reload} />
      {dialog}
    </>
  );
}

/** One provider's page: its name heads it under a "Models /" crumb, then how it is connected and
 *  the models it brings. A kept key shows as kept, with Check again and Replace key; Remove key is
 *  in the ⋯ menu. */
function ProviderPage({
  ps,
  settings,
  onSettings,
}: {
  ps: ProviderSetupState;
  settings: ModelSettings;
  onSettings: Dispatch<SetStateAction<ModelSettings | null>>;
}) {
  const provider = ps.sel as string;
  const info = ps.info;
  const { live, load } = useLiveModels(provider);
  const [replacing, setReplacing] = useState(false);

  useEffect(() => {
    setReplacing(false);
    load();
    // A provider opened from far down the list starts at its top, as a new page does.
    document.querySelector(".settings-content")?.scrollTo({ top: 0 });
  }, [provider, load]);

  // A replacement key that passed its check is the kept key again.
  useEffect(() => {
    if (ps.verify.state === "ok") setReplacing(false);
  }, [ps.verify.state]);

  const oauth = info?.auth === "oauth";
  const connected = info ? providerConnected(info) || (!info.needs_key && ps.keylessOk.has(info.name)) : false;
  const models = live.models.length > 0 ? live.models : info?.suggested_models || [];
  // OpenAI's ids are bare in the picker; every other provider's carry its name.
  const ownsId = (id: string) => (provider === "openai" ? !id.includes(":") || id.startsWith("openai:") : id.startsWith(`${provider}:`));
  const inPicker = settings.models.filter(ownsId).length;
  const used = info?.last_used_at ? relTime(info.last_used_at) : null;
  const sub = connected
    ? [used ? `Used ${used}` : "", inPicker ? `${inPicker} model${inPicker === 1 ? "" : "s"} in the composer` : ""].filter(Boolean).join(" · ")
    : info?.blurb || "";
  const state = connected ? (oauth ? "Signed in" : info?.needs_key ? "Connected" : "Running") : "Not connected";
  const showForm = !ps.credentialed || replacing || oauth;
  const note = sourceNote(live);

  const refresh = (
    <TextButton testId="models-refresh" disabled={live.loading} onClick={() => load(true)}>
      <Icon name="refresh" size={12} /> Refresh
    </TextButton>
  );
  const source = note ? (
    <p className="settings-provider-note" data-testid="models-source">
      {note}
    </p>
  ) : null;
  const removeKey =
    ps.credentialed && !oauth
      ? () => {
          if (window.confirm(`Remove the ${info?.title} key from this computer?`)) void ps.removeKey();
        }
      : undefined;
  const keyState =
    ps.verify.state === "testing"
      ? "Checking…"
      : ps.verify.state === "ok"
        ? "Saved on this computer · checked just now"
        : ps.verify.state === "error"
          ? `The check failed: ${ps.verify.msg}`
          : "Saved on this computer";

  return (
    <div className="settings-provider settings-enter" key={provider}>
      <header className="settings-head-override provider-head">
        <nav className="provider-crumb" aria-label="Breadcrumb">
          <button type="button" data-testid="set-back" onClick={ps.backToGallery}>
            Models
          </button>
          <span aria-hidden="true">/</span>
          <span>{info?.title}</span>
        </nav>
        <div className="provider-head-row">
          <ProviderMark name={info?.name || ""} title={info?.title || ""} size={40} />
          <div className="provider-head-text">
            <div className="provider-head-name">
              <h1 className="provider-title">{info?.title}</h1>
              <span className={"provider-chip" + (connected ? " is-ok" : "")} data-testid="provider-state">
                {state}
              </span>
            </div>
            {sub ? <p className="provider-head-sub">{sub}</p> : null}
          </div>
          <ProviderMenu title={info?.title || ""} keysUrl={KEY_HELP[provider]} onRemove={removeKey} />
        </div>
      </header>

      <SettingsCard title={oauth ? "Account" : "Connection"} tile={showForm} testId="provider-connection">
        {showForm ? (
          <div className="settings-provider-fields">
            {oauth && !info?.signed_in ? <SignInSteps title={info?.title || ""} /> : null}
            <ProviderFields ps={ps} tp="set" hideBlurb />
            {provider === "openai" && settings.source === "env" && (
              <p className="settings-provider-note">
                A key is set via <code>OPENAI_API_KEY</code> in this server's environment. You can override it above; the stored key is used only when the
                environment variable is absent.
              </p>
            )}
            {replacing ? (
              <TextButton onClick={() => setReplacing(false)} testId="set-replace-cancel">
                Keep the saved key
              </TextButton>
            ) : null}
          </div>
        ) : (
          <SettingsRow title="API key" detail={keyState} testId="provider-key">
            <TextButton testId="set-check" disabled={ps.verify.state === "testing"} onClick={() => void ps.runTestAndSave()}>
              Check again
            </TextButton>
            <TextButton testId="set-replace" onClick={() => setReplacing(true)}>
              Replace key
            </TextButton>
          </SettingsRow>
        )}
      </SettingsCard>

      {info?.configured ? (
        <SettingsCard
          title="Models"
          description="On ones show in the composer's picker. The Default tag marks the model new conversations start with."
          action={refresh}
          tile
          testId="provider-models"
        >
          {source}
          <div className="settings-provider-models">
            <ModelChecklist
              provider={provider}
              knownProviders={ps.providers.map((p) => p.name)}
              suggested={models}
              curated={settings.models}
              defaultModel={settings.model}
              labels={settings.model_labels}
              onChanged={(next) => onSettings((now) => (now ? { ...now, models: next.models, model: next.model } : now))}
            />
          </div>
        </SettingsCard>
      ) : models.length > 0 ? (
        // What a key would bring is part of deciding to get one, so a provider not connected yet
        // still shows its models, read-only.
        <SettingsCard
          title="Models it brings"
          description={
            live.source === "live"
              ? "Fetched live from the provider. Connect it above to turn them on."
              : "Curated, agent-capable models this provider serves. Connect it above to turn them on."
          }
          action={refresh}
          tile
          testId="model-preview"
        >
          {source}
          <PreviewModels ids={models.map((m) => (provider === "openai" ? m : `${provider}:${m}`))} labels={settings.model_labels} />
        </SettingsCard>
      ) : null}
    </div>
  );
}

/** A provider page's ⋯: what is done to the provider rather than set on it. */
function ProviderMenu({ title, keysUrl, onRemove }: { title: string; keysUrl?: { url: string; label: string }; onRemove?: () => void }) {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const root = useDismiss(open, close);
  if (!keysUrl && !onRemove) return null;
  const pick = (act: () => void) => () => {
    setOpen(false);
    act();
  };
  return (
    <div className="provider-menu" ref={root}>
      <button
        type="button"
        className="settings-icon-btn"
        aria-label={`More for ${title}`}
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid="provider-menu"
        onClick={() => setOpen(!open)}
      >
        <Icon name="moreHorizontal" size={15} />
      </button>
      {open ? (
        <div className="provider-menu-pop" role="menu" aria-label={`More for ${title}`}>
          {keysUrl ? (
            <button type="button" role="menuitem" onClick={pick(() => openExternal(keysUrl.url))}>
              Manage keys at {keysUrl.label} ↗
            </button>
          ) : null}
          {onRemove ? (
            <button type="button" role="menuitem" className="is-danger" data-testid="set-remove-key" onClick={pick(onRemove)}>
              Remove key…
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// The first few models are shown and the rest are one press away: a live list can run to
// hundreds, and a scroll box inside the scrolling page scrolled twice.
const PREVIEW_SHOWN = 8;

function PreviewModels({ ids, labels }: { ids: string[]; labels?: Record<string, string> }) {
  const [all, setAll] = useState(false);
  return (
    <div className="settings-model-preview">
      {(all ? ids : ids.slice(0, PREVIEW_SHOWN)).map((id) => (
        <div key={id} className="settings-model-preview-row" title={id}>
          {labels?.[id] || id.slice(id.indexOf(":") + 1)}
        </div>
      ))}
      {ids.length > PREVIEW_SHOWN && (
        <button type="button" className="settings-model-preview-more" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show ${ids.length - PREVIEW_SHOWN} more`}
        </button>
      )}
    </div>
  );
}

/** For a picker model: its provider (a bare id is OpenAI's), that provider's title, and the
 *  model's own name. */
function pickerNames(settings: ModelSettings, providers: ProviderInfo[], catalog: Record<string, CatalogModel>) {
  const known = providers.map((p) => p.name);
  const provOf = (id: string) => {
    const colon = id.indexOf(":");
    return colon > 0 && known.includes(id.slice(0, colon)) ? id.slice(0, colon) : "openai";
  };
  const titleOf = (provider: string) => providerName(provider, providers.find((p) => p.name === provider)?.title, catalog);
  const labelOf = (id: string) => modelName(id, settings.model_labels, catalog[id]);
  return { provOf, titleOf, labelOf };
}

type CardProps = { settings: ModelSettings; providers: ProviderInfo[]; onChanged: () => void };

/** The model new conversations start on, one menu at the head of the page. */
function DefaultModelCard({ settings, providers, onChanged }: CardProps) {
  const catalog = useModelCatalog();
  if (settings.models.length === 0) return null;
  const { provOf, titleOf, labelOf } = pickerNames(settings, providers, catalog);
  const choose = (id: string) =>
    void setDefaultModel(id).then(() => {
      onChanged();
      announceSaved("Default changed");
    });
  return (
    <SettingsCard testId="default-model-card">
      <SettingsRow title="Default model" detail="New conversations start on it. The composer can switch any time.">
        <Select
          label="Default model"
          value={settings.model}
          searchable={settings.models.length > 8}
          options={settings.models.map((id) => ({ value: id, label: labelOf(id), hint: titleOf(provOf(id)) }))}
          testId="default-model"
          optionTestId={(id) => `default-model-${id}`}
          onChange={choose}
        />
      </SettingsRow>
    </SettingsCard>
  );
}

/** The models the composer offers, under their providers; each is a row with its switch, the
 *  default wearing its tag. Turning one off takes it out of the picker; a provider's page adds. */
function ComposerPickerCard({ settings, providers, onChanged }: CardProps) {
  const [query, setQuery] = useState("");
  const catalog = useModelCatalog();
  const { provOf, titleOf, labelOf } = pickerNames(settings, providers, catalog);
  const needle = query.trim().toLowerCase();
  const shown = needle ? settings.models.filter((id) => `${labelOf(id)} ${id}`.toLowerCase().includes(needle)) : settings.models;
  const groups = new Map<string, string[]>();
  for (const id of shown) groups.set(provOf(id), [...(groups.get(provOf(id)) ?? []), id]);
  const search =
    settings.models.length > 8 ? (
      <input
        className="settings-search-input settings-heading-search"
        value={query}
        spellCheck={false}
        placeholder="Find a model"
        aria-label="Find a model in the picker"
        data-testid="composer-picker-search"
        onChange={(event) => setQuery(event.target.value)}
      />
    ) : undefined;
  return (
    <SettingsCard testId="composer-picker" title="In the composer's picker" description="The models offered when starting a session. Add more from a provider's page." action={search}>
      {shown.length === 0 ? <div className="settings-row settings-note">No model matches “{query}”.</div> : null}
      {[...groups].map(([provider, ids]) => (
        <div key={provider} className="settings-picker-group" data-testid={`composer-picker-group-${provider}`}>
          <div className="settings-picker-group-head">
            <ProviderMark name={provider} title={titleOf(provider)} size={18} />
            {titleOf(provider)}
          </div>
          {ids.map((id) => (
            <ModelRow
              key={id}
              id={id}
              name={labelOf(id)}
              row={catalog[id]}
              on
              isDefault={id === settings.model}
              onToggle={() => void removeModel(id).then((r) => r.ok && onChanged())}
            />
          ))}
        </div>
      ))}
    </SettingsCard>
  );
}
