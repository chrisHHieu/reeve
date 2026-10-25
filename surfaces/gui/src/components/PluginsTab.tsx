// Settings ▸ Plugins (design §11.1), the way Claude Code's `/plugin` is laid
// out: what is installed (with its switch), what the added marketplaces offer, the
// marketplaces themselves, and what failed. Installing and updating go through the
// consent screen; nothing here sends plugin content, only ids and choices.
import { useCallback, useEffect, useState } from "react";
import {
  addMarketplace,
  keepPlugin,
  listMarketplaces,
  listPlugins,
  marketplacePlugins,
  projectPlugins,
  refreshMarketplace,
  removeMarketplace,
  setMarketplaceAutoUpdate,
  setPluginEnabled,
  stageConsent,
  stageInstall,
  stageUpdate,
  uninstallPlugin,
  type InstalledPlugin,
  type Marketplace,
  type OfferedPlugin,
  type PluginsView,
  type ProjectPlugins,
  type Staged,
} from "../api/plugins";
import { PluginConsent } from "./PluginConsent";
import { SETTINGS_INPUT, SettingsCard, SettingsRow, Switch, TextButton } from "./settings/primitives";
import { Disclosure } from "../ui";
import { Icon } from "./Icon";

const COMPONENT_WORDS: Record<string, [string, string]> = {
  skills: ["skill", "skills"],
  commands: ["command", "commands"],
  agents: ["agent", "agents"],
};

/** "2 skills · 1 command" — the components a plugin brought, in words. */
export function componentsLine(components: Record<string, number>): string {
  const parts = Object.entries(components)
    .filter(([, n]) => n > 0)
    .map(([key, n]) => {
      const [one, many] = COMPONENT_WORDS[key] ?? [key, key];
      return `${n} ${n === 1 ? one : many}`;
    });
  return parts.length ? parts.join(" · ") : "Nothing to load";
}

/** Why a switch cannot change a plugin, when a higher scope decides it (design §6.3). */
export function lockedReason(p: InstalledPlugin): string | null {
  if (p.enabled_by === "managed") return "Set by your administrator";
  if (p.enabled_by === "project") return "Set by this project's plugin settings";
  if (p.enabled_by === "local") return "Set by this project's local plugin settings";
  return null;
}

export function PluginsTab({ workspace }: { workspace?: string }) {
  const [view, setView] = useState<PluginsView | null>(null);
  const [project, setProject] = useState<ProjectPlugins | null>(null);
  const [markets, setMarkets] = useState<Marketplace[]>([]);
  const [offered, setOffered] = useState<Record<string, OfferedPlugin[]>>({});
  const [staged, setStaged] = useState<(Staged & { review?: boolean }) | null>(null);
  const [source, setSource] = useState("");
  const [note, setNote] = useState("");
  const [failed, setFailed] = useState(false);
  const [tab, setTab] = useState<"installed" | "browse" | null>(null);

  const load = useCallback(async () => {
    try {
      const [v, m] = await Promise.all([listPlugins({ workspace }), listMarketplaces()]);
      setView(v);
      setMarkets(m);
      if (workspace) setProject(await projectPlugins({ workspace }).catch(() => null));
      const lists = await Promise.all(m.filter((x) => !x.error).map(async (x) => [x.name, await marketplacePlugins(x.name)] as const));
      setOffered(Object.fromEntries(lists));
    } catch {
      setFailed(true);
    }
  }, [workspace]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (work: () => Promise<unknown>, done?: string) => {
    setNote("");
    try {
      await work();
      if (done) setNote(done);
      await load();
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err));
    }
  };
  const install = (id: string) =>
    act(async () => setStaged(await stageInstall(id, "user", workspace)));
  const update = (id: string) =>
    act(async () => {
      const result = await stageUpdate(id);
      if ("up_to_date" in result) setNote(`${id} is up to date (${result.version}).`);
      else setStaged(result);
    });

  if (failed) return <p className="settings-section-sub">Could not read the plugin list.</p>;
  if (view === null) return <p className="settings-section-sub">Loading…</p>;

  const installedIds = new Set(view.installed.map((p) => p.id));
  const knownMarkets = new Set(markets.map((m) => m.name));
  const projectRows = project?.trusted ? [...project.plugins.filter((p) => p.state !== "ready"), ...project.marketplaces.filter((m) => !m.known)] : [];
  const sessionPlugins = view.plugins.filter((p) => p.scope === "session");
  const loaded = new Map(view.plugins.map((p) => [p.id, p]));
  const installedCount = view.installed.length + sessionPlugins.length;
  // Two tabs (settings redesign, 2026-10-20): what is here, and where more comes from. The page
  // opens on what is here once there is something, else on where to find it.
  const shown = tab ?? (installedCount > 0 ? "installed" : "browse");

  return (
    <>
      <div className="settings-tabs" role="tablist" aria-label="Plugins">
        <button
          type="button"
          role="tab"
          aria-selected={shown === "installed"}
          className={"settings-tab" + (shown === "installed" ? " on" : "")}
          data-testid="plugins-tab-installed"
          onClick={() => setTab("installed")}
        >
          Installed <span className="settings-tab-count">{installedCount}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={shown === "browse"}
          className={"settings-tab" + (shown === "browse" ? " on" : "")}
          data-testid="plugins-tab-browse"
          onClick={() => setTab("browse")}
        >
          Marketplaces <span className="settings-tab-count">{markets.length}</span>
        </button>
      </div>
      {note && (
        <p className="plugin-note" role="status">
          {note}
        </p>
      )}
      {/* What this folder asks for is to act on whichever tab is open. */}
      {project && projectRows.length > 0 && (
        <SettingsCard
          title="This project"
          description="What this folder's plugin settings ask for that is not in place on this computer. Nothing is installed or added until you choose to."
          testId="plugins-project"
        >
          {project.marketplaces
            .filter((m) => !m.known)
            .map((m) => (
              <SettingsRow key={`m-${m.name}`} title={`Marketplace ${m.name}`} wide detail={`${m.source} · suggested in ${m.file}`}>
                <TextButton
                  tone="accent"
                  testId={`project-market-add-${m.name}`}
                  onClick={() =>
                    void act(async () => {
                      const added = await addMarketplace(m.source);
                      setNote(`Added ${added.name} (${added.plugins} plugins).`);
                    })
                  }
                >
                  Add
                </TextButton>
              </SettingsRow>
            ))}
          {project.plugins
            .filter((p) => p.state !== "ready")
            .map((p) => {
              const market = p.id.split("@").pop() ?? "";
              return (
                <SettingsRow
                  key={p.id}
                  title={p.id}
                  wide
                  testId={`project-plugin-${p.id}`}
                  detail={
                    p.state === "missing"
                      ? knownMarkets.has(market)
                        ? "Not installed on this computer"
                        : `Not installed — add the ${market} marketplace first`
                      : p.state === "needs_consent"
                        ? "Installed; its code waits for this workspace's yes (Review, below)"
                        : "Installed, but not loaded — see Errors"
                  }
                >
                  {p.state === "missing" && knownMarkets.has(market) && (
                    <TextButton
                      tone="accent"
                      testId={`project-install-${p.id}`}
                      onClick={() => void act(async () => setStaged(await stageInstall(p.id, "project", workspace)))}
                    >
                      Install
                    </TextButton>
                  )}
                </SettingsRow>
              );
            })}
        </SettingsCard>
      )}
      {shown === "installed" && (
      <SettingsCard testId="plugins-installed">
        {view.installed.length === 0 && sessionPlugins.length === 0 ? (
          <SettingsRow
            title="No plugins yet"
            flat
            detail="Install one from Marketplaces, or start Reeve with --plugin-dir <folder>. Claude Code plugins load as they are."
          />
        ) : null}
        {view.installed.map((p) => {
          const locked = lockedReason(p);
          const info = loaded.get(p.id);
          return (
            <SettingsRow
              key={p.id}
              testId={`plugin-${p.id}`}
              title={info?.display_name || p.id}
              wide
              detail={
                <div className="plugin-detail">
                  <div>
                    {p.version ? `v${p.version} · ` : ""}
                    {info ? componentsLine(info.components) : p.enabled ? "Not loaded — see Errors" : "Off"}
                  </div>
                  {locked && <div className="plugin-root">{locked}</div>}
                  {info?.code === "running" && <div className="plugin-root">Runs code you agreed to</div>}
                  {p.flagged && !p.flagged.kept && (
                    <div className="plugin-error" data-testid={`plugin-withdrawn-${p.id}`}>
                      {p.flagged.marketplace} no longer lists it, so it is not loaded. Keep using it only if you trust
                      it as it is.
                    </div>
                  )}
                  {p.flagged?.kept && <div className="plugin-root">Withdrawn by {p.flagged.marketplace} — kept by you</div>}
                  {(p.warnings ?? []).map((w) => (
                    <div key={w} className="plugin-error">
                      Installed, but {w}.
                    </div>
                  ))}
                  {p.update_waiting && (
                    <div className="plugin-error" data-testid={`plugin-update-waiting-${p.id}`}>
                      Update{p.update_waiting.version ? ` to v${p.update_waiting.version}` : ""} waiting for you:{" "}
                      {p.update_waiting.reason}.
                    </div>
                  )}
                  {info?.code === "needs_consent" && (
                    <div className="plugin-error" data-testid={`plugin-code-waiting-${p.id}`}>
                      Code not running — it changed, or this workspace has not agreed to it yet.
                    </div>
                  )}
                  {info && info.skipped.length > 0 && (
                    <SkippedList items={info.skipped} />
                  )}
                </div>
              }
            >
              {info?.code === "needs_consent" && (
                <TextButton
                  tone="accent"
                  testId={`plugin-review-${p.id}`}
                  onClick={() =>
                    void act(async () =>
                      setStaged({
                        ...(await stageConsent(p.id, p.enabled_by === "project" ? workspace : undefined)),
                        review: true,
                      }),
                    )
                  }
                >
                  Review
                </TextButton>
              )}
              {p.flagged && !p.flagged.kept && (
                <TextButton
                  onClick={() => void act(() => keepPlugin(p.id), `Keeping ${p.id} — it loads in new conversations.`)}
                  testId={`plugin-keep-${p.id}`}
                >
                  Keep using
                </TextButton>
              )}
              <TextButton
                tone={p.update_waiting ? "accent" : undefined}
                onClick={() => void update(p.id)}
                testId={`plugin-update-${p.id}`}
              >
                {p.update_waiting ? "Review update" : "Update"}
              </TextButton>
              <TextButton
                tone="danger"
                onClick={() =>
                  void act(() => uninstallPlugin(p.id), `Removed ${p.id}.`)
                }
                testId={`plugin-remove-${p.id}`}
              >
                Uninstall
              </TextButton>
              <Switch
                label={`Use ${p.id}`}
                checked={p.enabled}
                disabled={locked !== null}
                testId={`plugin-switch-${p.id}`}
                onChange={(next) => void act(() => setPluginEnabled(p.id, next, "user", workspace))}
              />
            </SettingsRow>
          );
        })}
        {sessionPlugins.map((p) => (
          <SettingsRow
            key={p.id}
            testId={`plugin-${p.name}`}
            title={p.display_name || p.name}
            wide
            detail={
              <div className="plugin-detail">
                <div>
                  {p.version ? `v${p.version} · ` : ""}
                  {componentsLine(p.components)} · from this session's plugin folders
                </div>
                {p.description ? <div className="plugin-description">{p.description}</div> : null}
                <div className="plugin-root" title={p.root}>
                  {p.root}
                </div>
                {p.skipped.length > 0 && (
                  <SkippedList items={p.skipped} />
                )}
              </div>
            }
          />
        ))}
      </SettingsCard>
      )}

      {shown === "browse" && markets.length > 0 && (
        <SettingsCard title="Discover" testId="plugins-discover">
          {markets.flatMap((m) =>
            (offered[m.name] ?? []).map((p) => (
              <SettingsRow
                key={p.id}
                testId={`offered-${p.id}`}
                title={p.name}
                wide
                detail={`${p.description || "No description"}${p.version ? ` · v${p.version}` : ""} · ${m.name}`}
              >
                {installedIds.has(p.id) ? (
                  <span className="plugin-installed">Installed</span>
                ) : (
                  <TextButton tone="accent" onClick={() => void install(p.id)} testId={`plugin-install-${p.id}`}>
                    Install
                  </TextButton>
                )}
              </SettingsRow>
            )),
          )}
        </SettingsCard>
      )}

      {shown === "browse" && (
      <SettingsCard testId="plugins-marketplaces">
        {markets.map((m) => (
          <SettingsRow
            key={m.name}
            testId={`market-${m.name}`}
            title={m.name}
            wide
            detail={
              m.error ? (
                <span className="plugin-error">{m.error}</span>
              ) : (
                <>
                  <div>{`${m.plugins} plugins · ${m.installLocation}`}</div>
                  <div className="plugin-root">
                    {m.autoUpdate
                      ? "Auto-update on: updates that run no new code apply themselves; the rest wait for you."
                      : "Auto-update off"}
                  </div>
                </>
              )
            }
          >
            <Switch
              label={`Auto-update ${m.name}`}
              checked={m.autoUpdate === true}
              testId={`market-auto-${m.name}`}
              onChange={(next) => void act(() => setMarketplaceAutoUpdate(m.name, next))}
            />
            <TextButton onClick={() => void act(() => refreshMarketplace(m.name), `Refreshed ${m.name}.`)}>Refresh</TextButton>
            <TextButton tone="danger" onClick={() => void act(() => removeMarketplace(m.name), `Removed ${m.name}.`)}>
              Remove
            </TextButton>
          </SettingsRow>
        ))}
        <SettingsRow title="Add a marketplace" wide detail="owner/repo on GitHub, a git URL, or a folder on this computer">
          <input
            className={SETTINGS_INPUT}
            aria-label="Marketplace source"
            placeholder="owner/repo"
            value={source}
            onChange={(e) => setSource(e.target.value)}
          />
          <TextButton
            tone="accent"
            disabled={!source.trim()}
            testId="market-add"
            onClick={() =>
              void act(async () => {
                const added = await addMarketplace(source.trim());
                setSource("");
                setNote(`Added ${added.name} (${added.plugins} plugins).`);
              })
            }
          >
            Add
          </TextButton>
        </SettingsRow>
      </SettingsCard>
      )}

      {shown === "installed" && view.errors.length > 0 && (
        <SettingsCard title="Errors" testId="plugins-errors">
          {view.errors.map((e, i) => (
            <SettingsRow key={`${e.id}-${i}`} title={e.id} wide detail={<span className="plugin-error">{e.error}</span>} />
          ))}
        </SettingsCard>
      )}

      {staged && (
        <PluginConsent
          token={staged.token}
          consent={staged.consent}
          diff={staged.diff}
          review={staged.review}
          onClose={() => setStaged(null)}
          onDone={() => {
            setStaged(null);
            // What was just installed is shown where it now lives.
            if (!staged.review) setTab("installed");
            void load();
            setNote(
              staged.review
                ? `${staged.consent.id}'s code runs in new conversations (/reload-plugins brings it into an open one).`
                : `${staged.diff ? "Updated" : "Installed"} ${staged.consent.id} — it loads in new conversations (/reload-plugins brings it into an open one).`,
            );
          }}
        />
      )}
    </>
  );
}

/** What a plugin left out, behind a line that opens it. A native <details> snapped open; this one
 * grows and fades like every other show/hide in Settings (2026-10-10). */
function SkippedList({ items }: { items: string[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="plugin-skipped">
      <button type="button" className="plugin-skipped-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Icon name="chevronRight" size={12} />
        {items.length} not loaded
      </button>
      <Disclosure open={open} className="fold-fade">
        <ul>
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </Disclosure>
    </div>
  );
}
