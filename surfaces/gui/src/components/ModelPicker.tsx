import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { addModel, getModelCatalog, MODELS_CHANGED, type CatalogModel } from "../api";
import { displayModel, displayModelFull } from "../modelName";
import { formatTokens } from "../usage";
import { modelFacts, retiring, RETIRING_TIP } from "../modelFacts";
import { providerKeyOf, providerOptions, shortProviderTitle } from "../providerOptions";
import { ProviderMark } from "../providers/ProviderSetup";
import { Icon } from "./Icon";

// The composer's model picker. ONE control in the composer's row: it names the model and,
// where the model has them, its thinking level ("Gemini 3.8 Flash · High").
//
// The panel is read left to right, then down (owner, 2026-10-13): the providers down the
// left, that provider's models on the right, and at the foot the model in use with its own
// thinking levels. Every click applies at once and the panel stays open until it is left
// (a click outside, Escape, or Enter on a row). It used to close the moment a model was
// picked, while the thinking row at its foot belonged to the model in use; reaching the new
// model's levels meant opening the panel a second time. The provider was a menu inside the
// panel whose pick also switched the model to that provider's first one; it only filters
// now. And the side pane that described whichever row the pointer was over, beside a
// thinking row about another model, is gone: context and price are on each row, the rest is
// in the foot, which only ever describes the model in use.
//
// The list is `/v1/models/catalog` (the curated matrix plus models.dev facts for every
// CONFIGURED provider). It degrades to the plain `models` list when the catalogue is
// unreachable, so a session can always switch models.
//
// The list is the models turned on in Settings ▸ Models (`models`), the counts beside each
// provider too, so the picker and that page say the same thing (owner, 2026-10-21: Settings said
// 6 and the picker listed 33). "Show all" and a search reach every model the catalogue knows;
// picking one turns it on.
//
// The provider filter is view state: with no explicit pick it follows the current model, so
// a session opens narrowed to its own provider. Typing searches every provider whatever is
// picked. "All" starts with the last few models picked here (Recent), remembered in this
// browser only.

type Props = {
  value: string;
  models: string[];
  modelLabels?: Record<string, string>;
  onChange: (id: string) => void;
  // The catalogue can add a model the user picked but had not ticked; the surface
  // refetches settings so the new id is in the picker from then on.
  onModelsChanged?: () => void;
  // The session's current thinking level ("" = the model's own default) and its setter.
  // Rendered only for a model whose catalogue row states levels — the UI never invents one.
  thinking?: string;
  onThinkingChange?: (level: string) => void;
  disabled?: boolean;
  // CONTROLLED open state (the composer's `/model` row opens the popover imperatively).
  // Absent → the picker owns its open state, the standalone/test shape.
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  // The active conversation. A change retires an explicit provider pick, so the new
  // session's loaded model decides the filter again (the composer passes its resetKey).
  sessionKey?: string;
};

/** The provider key a catalogue row belongs to: its own field, else the id's prefix. */
export function rowProvider(row: CatalogModel): string {
  return row.provider || providerKeyOf(row.id);
}

/** The rows the model list may show: all of them, or exactly one provider's. */
export function rowsForProvider(
  rows: CatalogModel[],
  provider: string | null,
): CatalogModel[] {
  return provider ? rows.filter((row) => rowProvider(row) === provider) : rows;
}

const RECENT_KEY = "reeve:recent-models:v1";
const RECENT_MAX = 3;

function readRecent(): string[] {
  try {
    const raw = JSON.parse(window.localStorage.getItem(RECENT_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

function rememberRecent(id: string): string[] {
  const next = [id, ...readRecent().filter((other) => other !== id)].slice(0, RECENT_MAX + 1);
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    // A private window: the list lives for this mount only.
  }
  return next;
}

/** The model in use, in a sentence: what it reads, how much, what it costs. */
function footFacts(row: CatalogModel): string {
  const parts: string[] = [];
  const inputs = row.inputs && row.inputs.length ? row.inputs : row.vision ? ["text", "images"] : [];
  if (inputs.length) parts.push(`reads ${inputs.join(", ")}`);
  if (row.context) parts.push(`${formatTokens(row.context)} tokens`);
  const input = row.cost?.input;
  const output = row.cost?.output;
  if (row.free) parts.push("free");
  else if (typeof input === "number" && typeof output === "number") {
    parts.push(`$${input} in, $${output} out per million`);
  }
  return parts.join(" · ");
}

// A wire level → the word a person reads. "none" is Anthropic's and Gemini's OFF, so it
// reads as "Off"; every other level is its own name, capitalized.
function _levelLabel(level: string): string {
  if (level === "default") return "Default";
  if (level === "none") return "Off";
  return level.charAt(0).toUpperCase() + level.slice(1);
}

export function ModelPicker({
  value,
  models,
  modelLabels,
  onChange,
  onModelsChanged,
  thinking,
  onThinkingChange,
  disabled,
  open: openProp,
  onOpenChange,
  sessionKey,
}: Props) {
  const [selfOpen, setSelfOpen] = useState(false);
  const open = openProp ?? selfOpen;
  const setOpen = useCallback(
    (next: boolean) => {
      if (openProp === undefined) setSelfOpen(next);
      onOpenChange?.(next);
    },
    [openProp, onOpenChange],
  );
  const [rows, setRows] = useState<CatalogModel[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [query, setQuery] = useState("");
  const [recent, setRecent] = useState<string[]>(readRecent);
  // The provider the list narrows to. An explicit pick (a provider key, or null for "All")
  // wins inside the session it was made in; otherwise the filter follows the CURRENT model,
  // so a session hydrated after a refresh opens already narrowed. A pick outlives a model
  // picked inside it, but not a model that changed elsewhere since (`at`: the value it was
  // made at). `sessionKey` retires picks made in another conversation.
  const [filterPick, setFilterPick] = useState<{
    session?: string;
    provider: string | null;
    at: string;
  } | null>(null);
  const currentProvider = providerKeyOf(value);
  const providerFilter =
    !filterPick || filterPick.session !== sessionKey
      ? currentProvider
      : filterPick.provider === null || filterPick.provider === currentProvider
        ? filterPick.provider
        : filterPick.at === value
          ? filterPick.provider
          : currentProvider;
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const rowsRef = useRef<HTMLDivElement | null>(null);
  const fetched = useRef(false);
  // Request sequence: a mount fetch and an open retry can overlap, and the slower one
  // must not clobber the newer answer (a rejected mount landing after a good open would
  // otherwise blank the catalogue for the rest of the mount).
  const seq = useRef(0);

  const load = useCallback(() => {
    const mine = ++seq.current;
    setLoading(true);
    getModelCatalog()
      .then((c) => {
        if (seq.current !== mine) return;
        // Success is cached for the lifetime of the mount; a FAILURE is not, so the next
        // open retries.
        fetched.current = true;
        setFailed(false);
        setRows(c.models || []);
      })
      .catch(() => {
        if (seq.current !== mine) return;
        setFailed(true);
        setRows([]);
      })
      .finally(() => {
        if (seq.current === mine) setLoading(false);
      });
  }, []);

  useEffect(() => {
    // Fetch on MOUNT, not only on open: the trigger's thinking level needs the current
    // model's levels without the panel being opened. A failure is NOT cached, so the open
    // effect retries — how an old/restarted backend heals without a reload.
    if (!fetched.current) load();
  }, [load]);

  // The cached catalogue is answered again whenever the pickable models change. The picker
  // stays mounted behind Settings, so a provider connected there reached it only on restart.
  useEffect(() => {
    window.addEventListener(MODELS_CHANGED, load);
    return () => window.removeEventListener(MODELS_CHANGED, load);
  }, [load]);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setEverything(false);
      return;
    }
    if (!fetched.current) load();
    const t = window.setTimeout(() => inputRef.current?.focus(), 30);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, load]);

  const source: CatalogModel[] = useMemo(() => {
    if (rows && rows.length) return rows;
    // Offline / not-yet-fetched fallback: the session's own selectable list.
    return models.map((id) => ({
      id,
      name: displayModelFull(id, modelLabels),
      provider: providerKeyOf(id),
      selected: true,
    }));
  }, [rows, models, modelLabels]);

  // The models turned on in Settings, and the one in use; everything, once asked for.
  const [everything, setEverything] = useState(false);
  const offered = useMemo(() => {
    const on = source.filter((row) => models.includes(row.id) || row.id === value);
    return on.length ? on : source;
  }, [source, models, value]);
  const listed = everything ? source : offered;
  const providers = useMemo(
    () => providerOptions(rows && rows.length ? listed : [], models, value),
    [rows, listed, models, value],
  );
  const titleOf = useCallback(
    (key: string) => providers.find((p) => p.key === key)?.title ?? key,
    [providers],
  );

  const q = query.trim().toLowerCase();
  const groups = useMemo((): [string, CatalogModel[]][] => {
    const grouped = (list: CatalogModel[]) => {
      const out = new Map<string, CatalogModel[]>();
      for (const row of list) {
        const key = row.provider_title ? shortProviderTitle(row.provider_title) : titleOf(rowProvider(row));
        const bucket = out.get(key);
        if (bucket) bucket.push(row);
        else out.set(key, [row]);
      }
      return [...out.entries()];
    };
    // Typing looks everywhere, whatever provider is picked on the left.
    if (q) {
      return grouped(
        source.filter(
          (row) =>
            row.id.toLowerCase().includes(q) ||
            row.name.toLowerCase().includes(q) ||
            (row.provider_title || row.provider).toLowerCase().includes(q),
        ),
      );
    }
    // A picked provider lists flat: a single-provider list has nothing to group.
    if (providerFilter) {
      const own = rowsForProvider(listed, providerFilter);
      return own.length ? [["", own]] : [];
    }
    const recentRows = recent
      .map((id) => listed.find((row) => row.id === id))
      .filter((row): row is CatalogModel => !!row)
      .slice(0, RECENT_MAX);
    return [...(recentRows.length ? [["Recent", recentRows] as [string, CatalogModel[]]] : []), ...grouped(listed)];
  }, [source, listed, q, providerFilter, recent, titleOf]);
  // How many more the catalogue has where the list stands: behind "Show all".
  const more = q ? 0 : rowsForProvider(source, providerFilter).length - rowsForProvider(offered, providerFilter).length;

  const currentRow = source.find((row) => row.id === value);
  // The CURRENT model's levels drive the trigger and the foot. Empty while the catalogue is
  // still loading (or unreachable) — the control simply isn't there yet, then appears.
  const currentLevels = currentRow?.reasoning_levels || [];
  // A level this model does not offer is not its level: right after a switch the session still
  // holds the old model's until the server's `thinking_changed` drops it.
  const levelOffered = !thinking || currentLevels.length === 0 || currentLevels.includes(thinking);
  const level = (levelOffered && thinking) || currentRow?.reasoning_default || "default";
  const levelWord = _levelLabel(level);
  const fullName = displayModelFull(value, modelLabels);
  const levelChoices = ["default", ...currentLevels];

  const choose = async (row: CatalogModel, close = false) => {
    if (busy) return;
    if (!models.includes(row.id)) {
      setBusy(true);
      try {
        await addModel(row.id);
        onModelsChanged?.();
      } catch {
        // The switch still happens: addModel is persistence, not permission.
      } finally {
        setBusy(false);
      }
    }
    onChange(row.id);
    setRecent(rememberRecent(row.id));
    if (close) setOpen(false);
  };

  const pickProvider = (key: string | null) => {
    setQuery("");
    setFilterPick({ session: sessionKey, provider: key, at: value });
  };

  const stepLevel = (dir: 1 | -1) => {
    if (currentLevels.length === 0) return;
    const at = Math.max(0, levelChoices.indexOf(level));
    const next = levelChoices[Math.min(levelChoices.length - 1, Math.max(0, at + dir))];
    if (next !== level) onThinkingChange?.(next === "default" ? "" : next);
  };

  // Escape closes the panel from anywhere inside it; the arrow keys rove over the model
  // rows (search field included) so a keyboard user never has to tab through the list, and
  // left/right move the thinking level when the caret is not in the search field.
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      setOpen(false);
      return;
    }
    if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && !(e.target instanceof HTMLInputElement)) {
      e.preventDefault();
      stepLevel(e.key === "ArrowRight" ? 1 : -1);
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const items = rowsRef.current?.querySelectorAll<HTMLButtonElement>(".mp-row");
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
    <div className="mp">
      <button
        className="pill"
        data-testid="model-picker-trigger"
        disabled={disabled}
        onClick={() => setOpen(!open)}
        title={currentLevels.length > 0 ? `${fullName} · thinking: ${levelWord}` : fullName}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <span className="mp-mark" aria-hidden>
          <ProviderMark name={currentProvider} title={titleOf(currentProvider)} size={16} />
        </span>
        <span className="pill-label">{displayModel(value, modelLabels)}</span>
        {currentLevels.length > 0 && (
          <span className="mp-level" data-testid="model-picker-level">
            · {levelWord}
          </span>
        )}
        <Icon name="chevronDown" size={13} className="caret" />
      </button>
      {open && (
        <>
          <div className="mp-backdrop" onClick={() => setOpen(false)} />
          <div
            className="mp-panel"
            data-testid="model-picker-panel"
            role="dialog"
            aria-label="Choose a model"
            onKeyDown={onKeyDown}
          >
            <div className="mp-side" role="radiogroup" aria-label="Provider" data-testid="model-picker-providers">
              <div className="mp-side-name">Provider</div>
              <button
                type="button"
                role="radio"
                aria-checked={providerFilter === null && !q}
                className={"mp-prov" + (providerFilter === null && !q ? " on" : "")}
                data-testid="provider-row-all"
                onClick={() => pickProvider(null)}
              >
                <span className="mp-prov-all" aria-hidden>
                  <Icon name="globe" size={13} />
                </span>
                <span className="mp-prov-name">All</span>
                <span className="mp-prov-count">{listed.length}</span>
              </button>
              {providers.map((p) => {
                const on = providerFilter === p.key && !q;
                return (
                  <button
                    key={p.key}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    className={"mp-prov" + (on ? " on" : "")}
                    data-testid={`provider-row-${p.key}`}
                    title={p.title}
                    onClick={() => pickProvider(p.key)}
                  >
                    <ProviderMark name={p.key} title={p.title} size={18} />
                    <span className="mp-prov-name">{p.title}</span>
                    <span className="mp-prov-count">{p.count}</span>
                  </button>
                );
              })}
            </div>
            <div className="mp-list">
              <div className="mp-search">
                <Icon name="search" size={13} />
                <input
                  ref={inputRef}
                  data-testid="model-picker-search"
                  placeholder="Search every model"
                  aria-label="Search every model"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
              <div className="mp-rows" ref={rowsRef} role="listbox" aria-label="Models">
                {failed && source.length > 0 && (
                  // The panel fell back to the session's own list; say why, or the missing
                  // facts read as a bug instead of a backend that has no catalogue.
                  <div className="mp-note" data-testid="model-picker-note">
                    Model catalogue unavailable — showing your models
                  </div>
                )}
                {loading && <div className="mp-empty">Loading models…</div>}
                {!loading && groups.length === 0 && (
                  <div className="mp-empty">
                    {failed ? "Model catalogue unavailable" : q ? `No model matches “${query.trim()}”` : "No models here"}
                  </div>
                )}
                {groups.map(([group, items]) => (
                  <div key={group || "flat"}>
                    {group && <div className="mp-group">{group}</div>}
                    {items.map((row) => {
                      const current = row.id === value;
                      return (
                        <button
                          key={`${group}-${row.id}`}
                          role="option"
                          aria-selected={current}
                          className={"mp-row" + (current ? " current" : "") + (models.includes(row.id) || current ? "" : " off")}
                          data-testid={group === "Recent" ? `model-recent-${row.id}` : `model-row-${row.id}`}
                          onClick={() => void choose(row)}
                          onKeyDown={(event) => {
                            // Enter picks and leaves; a click or Space picks and stays, so the
                            // thinking level can be set next.
                            if (event.key !== "Enter") return;
                            event.preventDefault();
                            void choose(row, true);
                          }}
                        >
                          <span className="mp-row-check" aria-hidden>
                            {current && <Icon name="check" size={13} className="mp-check" />}
                          </span>
                          <span className="mp-name">{row.name}</span>
                          {row.free && <span className="mp-free">Free</span>}
                          {retiring(row) && (
                            <span className="mp-retiring" title={RETIRING_TIP} data-testid={`model-retiring-${row.id}`}>
                              Retiring
                            </span>
                          )}
                          <span className="mp-facts">
                            {group === "Recent" ? (row.provider_title ? shortProviderTitle(row.provider_title) : titleOf(rowProvider(row))) : modelFacts(row)}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                ))}
                {more > 0 && (
                  <button type="button" className="mp-more" data-testid="model-picker-more" onClick={() => setEverything(!everything)}>
                    {everything ? "Only the ones turned on" : `Show ${more} more`}
                  </button>
                )}
              </div>
            </div>
            <div className="mp-foot" data-testid="model-detail">
              <div className="mp-foot-who">
                <ProviderMark name={currentProvider} title={titleOf(currentProvider)} size={16} />
                <span className="mp-foot-name">{currentRow?.name ?? fullName}</span>
                {currentRow && <span className="mp-foot-facts">{footFacts(currentRow)}</span>}
              </div>
              {currentLevels.length > 0 ? (
                <div className="mp-thinking" data-testid="model-thinking">
                  <span className="mp-thinking-label" id="mp-thinking-label">
                    Thinking
                  </span>
                  <div className="mp-seg" role="radiogroup" aria-labelledby="mp-thinking-label">
                    {levelChoices.map((l) => (
                      <button
                        key={l}
                        type="button"
                        role="radio"
                        className={l === level ? "on" : ""}
                        aria-checked={l === level}
                        aria-pressed={l === level}
                        title={l === "default" ? "The model's own default" : undefined}
                        data-testid={`thinking-${l}`}
                        onClick={() => onThinkingChange?.(l === "default" ? "" : l)}
                      >
                        {_levelLabel(l)}
                      </button>
                    ))}
                  </div>
                  <span className="mp-hint">Esc to close</span>
                </div>
              ) : (
                currentRow && (
                  <div className="mp-thinking" data-testid="model-thinking-none">
                    <span className="mp-thinking-label">Thinking</span>
                    <span className="mp-none">This model has no thinking levels</span>
                    <span className="mp-hint">Esc to close</span>
                  </div>
                )
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
