// Settings ▸ Web search — where the agent's searches go, the key they use, and a test.
//
// Rebuilt 2026-10-10 because the owner could not read the page: three rows of prose asked them
// to assemble "is search working, through what, on whose key" themselves. Now:
//   · a state head says it in one word and one line, with the test beside it; a test's answer
//     opens under it, and a rejected key says so with the way to replace it;
//   · the engine is radio rows, each saying what it means in plain words: Exa, Tavily (an engine
//     now, set up here like Exa and no longer a connector) and DuckDuckGo only;
//   · each keyed engine has an account card (Exa's while Exa is in use, Tavily's while Tavily is,
//     or while its key is being added) with Add, Replace and a Remove that asks;
//   · Firecrawl, a connector with search tools of its own, is listed with its state and opens its
//     connector page.
// A rejected key does not fail a search (it falls through to DuckDuckGo), so without the test a
// wrong key looks exactly like a right one.

import { useEffect, useState } from "react";
import { getConnectors, type Connector } from "../api";
import {
  getWebSearch,
  setWebSearch,
  testWebSearch,
  type WebSearchChange,
  type WebSearchMode,
  type WebSearchSettings,
  type WebSearchTest,
} from "../api/webSearch";
import { ConnectorBadge } from "../connectors/ConnectorIcon";
import { ConfirmDialog } from "../ui";
import { Icon } from "./Icon";
import { EngineKeyCard } from "./settings/EngineKeyCard";
import { FoldBody, SETTINGS_INPUT, SettingsCard, TextButton } from "./settings/primitives";
import {
  SEARCH_CONNECTORS,
  engineOf,
  keyDetail,
  keyRejected,
  keyTitle,
  searchState,
  tavilyKeyDetail,
  tavilyKeyTitle,
  testVerdict,
} from "./settings/webSearchView";

type Keyed = "exa" | "tavily";

const ENGINES: { value: WebSearchMode; title: string; line: string; tag?: string }[] = [
  {
    value: "auto",
    title: "Exa",
    tag: "Recommended",
    line: "Reads the pages it finds, with their dates. DuckDuckGo steps in when Exa fails or finds nothing.",
  },
  {
    value: "tavily",
    title: "Tavily",
    line: "Made for agents: page excerpts with their dates. DuckDuckGo steps in when Tavily fails.",
  },
  { value: "duckduckgo", title: "DuckDuckGo only", line: "No key needed. Shorter results, and no dates." },
];

const SAVED_NOTE: Record<WebSearchMode, string> = {
  auto: "Searches go to Exa first.",
  tavily: "Searches go to Tavily first.",
  duckduckgo: "Searches go to DuckDuckGo only.",
};

// Each engine wears its own mark (registry: `exa` from exa.ai's published SVG, `tavily` from
// tavily.com's, `duckduckgo` from simple-icons) in its brand colour, so the page names a service
// by sight as well as by word.
const ENGINE_MARK: Record<WebSearchMode, { logo: string; brand_color: string; title: string }> = {
  auto: { logo: "exa", brand_color: "#0143D9", title: "Exa" },
  tavily: { logo: "tavily", brand_color: "#1f1e1e", title: "Tavily" },
  duckduckgo: { logo: "duckduckgo", brand_color: "#DE5833", title: "DuckDuckGo" },
};
const MARK_OF_ENGINE = { exa: ENGINE_MARK.auto, tavily: ENGINE_MARK.tavily, duckduckgo: ENGINE_MARK.duckduckgo };

const DEFAULT_QUERY = "latest technology news";

export function WebSearchSection({ onOpenConnector }: { onOpenConnector?: (name: string) => void } = {}) {
  const [settings, setSettings] = useState<WebSearchSettings | null>(null);
  const [loadError, setLoadError] = useState("");
  const [tools, setTools] = useState<Connector[]>([]);
  const [entering, setEntering] = useState<Keyed | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ on: Keyed; text: string } | null>(null);
  const [error, setError] = useState<{ on: Keyed; text: string } | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<Keyed | null>(null);
  const [ownQuery, setOwnQuery] = useState(false);
  const [query, setQuery] = useState(DEFAULT_QUERY);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<WebSearchTest | null>(null);

  const refresh = () =>
    getWebSearch()
      .then((next) => {
        setSettings(next);
        setLoadError("");
      })
      .catch(() => setLoadError("Could not read the web search settings."));

  useEffect(() => {
    void refresh();
    getConnectors()
      .then((all) => setTools(all.filter((c) => SEARCH_CONNECTORS.includes(c.name))))
      .catch(() => setTools([]));
  }, []);

  /** Save `change`; `on` is the account a note or an error belongs to. */
  const save = async (change: WebSearchChange, done: string, on: Keyed = "exa") => {
    setBusy(true);
    setError(null);
    setNote(null);
    const res = await setWebSearch(change).catch(() => ({ ok: false, error: "the server did not answer" }));
    setBusy(false);
    if (!res.ok) {
      setError({ on, text: res.error || "Could not save." });
      return false;
    }
    setEntering(null);
    setTest(null); // a test of the old setting says nothing about the new one
    setNote({ on, text: done });
    await refresh();
    return true;
  };

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    const q = (ownQuery && query.trim()) || DEFAULT_QUERY;
    const result = await testWebSearch(q).catch(
      (): WebSearchTest => ({ ok: false, query: q, error: "the server did not answer" }),
    );
    setTesting(false);
    setTest(result);
  };

  const startEntering = (on: Keyed) => {
    setNote(null);
    setError(null);
    setEntering(on);
  };

  if (loadError) return <div className="settings-note">{loadError}</div>;
  if (!settings) return <div className="settings-note">Loading…</div>;

  const state = searchState(settings, test);
  const verdict = test ? testVerdict(test) : null;
  const engine = engineOf(settings);
  const mark = MARK_OF_ENGINE[engine];
  const rejectedBy: Keyed | undefined = keyRejected(test)
    ? test?.fallback_from === "tavily"
      ? "tavily"
      : "exa"
    : undefined;
  const tavilyHasKey = Boolean(settings.tavily?.key_source);
  const showExa = engine === "exa";
  const showTavily = settings.mode === "tavily" || entering === "tavily";
  const kept = [
    !showExa && settings.has_key ? "Exa" : "",
    !showTavily && settings.tavily?.has_key ? "Tavily" : "",
  ].filter(Boolean);

  return (
    <div className="ws-page">

      <SettingsCard
        title="Search engine"
        description="Every web search Reeve makes goes here. The next search in every conversation uses it, with no restart."
        testId="web-search-engine"
      >
        <div role="radiogroup" aria-label="Search engine" className="ws-choices" data-testid="web-search-mode">
          {ENGINES.map((choice) => {
            const on = settings.mode === choice.value;
            // Tavily answers only with a key: with none, choosing it opens its account card to add
            // one, and saving the key there chooses it.
            const needsKey = choice.value === "tavily" && !tavilyHasKey;
            return (
              <div key={choice.value} className="ws-choice-row">
                <button
                  type="button"
                  role="radio"
                  aria-checked={on}
                  className={"ws-choice" + (on ? " on" : "")}
                  disabled={busy}
                  data-testid={`web-search-mode-${choice.value}`}
                  onClick={() => {
                    if (on) return;
                    if (needsKey) startEntering("tavily");
                    else void save({ mode: choice.value }, SAVED_NOTE[choice.value], choice.value === "tavily" ? "tavily" : "exa");
                  }}
                >
                  <span className="ws-radio" aria-hidden="true" />
                  <ConnectorBadge connector={ENGINE_MARK[choice.value]} size={28} title={ENGINE_MARK[choice.value].title} />
                  <span className="ws-choice-text">
                    <span className="ws-choice-title">
                      {choice.title}
                      {choice.tag ? <span className="settings-tag">{choice.tag}</span> : null}
                    </span>
                    <span className="ws-choice-line">
                      {needsKey ? "Needs a Tavily API key. The free plan covers about 1,000 searches a month." : choice.line}
                    </span>
                  </span>
                </button>
                {needsKey && entering !== "tavily" ? (
                  <span className="ws-choice-action">
                    <TextButton testId="web-search-tavily-start" onClick={() => startEntering("tavily")}>
                      Add key
                    </TextButton>
                  </span>
                ) : null}
              </div>
            );
          })}
        </div>
      </SettingsCard>

      <FoldBody open={showExa} testId="web-search-exa">
        <div className="ws-fold-pad">
          <EngineKeyCard
            title="Exa account"
            testPrefix="web-search"
            label="Exa API key"
            keyTitle={keyTitle(settings)}
            keyDetail={keyDetail(settings)}
            hasKey={settings.has_key}
            open={entering === "exa"}
            busy={busy}
            note={note?.on === "exa" ? note.text : undefined}
            error={error?.on === "exa" ? error.text : undefined}
            keysUrl="https://dashboard.exa.ai/api-keys"
            keysLabel="Get a key at dashboard.exa.ai"
            onOpen={() => startEntering("exa")}
            onClose={() => setEntering(null)}
            onSave={(key) => void save({ mode: settings.mode, apiKey: key }, "Key saved.")}
            onRemove={() => setConfirmRemove("exa")}
          />
        </div>
      </FoldBody>

      <FoldBody open={showTavily} testId="web-search-tavily">
        <div className="ws-fold-pad">
          <EngineKeyCard
            title="Tavily account"
            testPrefix="web-search-tavily"
            label="Tavily API key"
            keyTitle={tavilyKeyTitle(settings)}
            keyDetail={tavilyKeyDetail(settings)}
            hasKey={Boolean(settings.tavily?.has_key)}
            open={entering === "tavily"}
            busy={busy}
            note={note?.on === "tavily" ? note.text : undefined}
            error={error?.on === "tavily" ? error.text : undefined}
            keysUrl="https://app.tavily.com/home"
            keysLabel="Get a key at app.tavily.com"
            onOpen={() => startEntering("tavily")}
            onClose={() => setEntering(null)}
            // Adding Tavily's key is choosing Tavily: nobody pastes one to leave it unused.
            onSave={(key) => void save({ mode: "tavily", tavilyKey: key }, "Key saved. Searches go to Tavily first.", "tavily")}
            onRemove={() => setConfirmRemove("tavily")}
          />
        </div>
      </FoldBody>

      {kept.length ? (
        <p className="ws-kept" data-testid="web-search-key-kept">
          Your {kept.join(" and ")} key{kept.length > 1 ? "s are" : " is"} kept, ready for when you switch back.
        </p>
      ) : null}

      <section className="settings-card-block" data-testid="web-search-card">
        <h2 className="settings-card-heading">Status</h2>
        <div className="settings-panel ws-status">
          <div className="ws-status-head">
            <ConnectorBadge connector={mark} size={40} title={mark.title} />
            <div className="ws-status-text">
              <div className="ws-status-word" data-testid="web-search-state-word">
                {state.word}
              </div>
              <div className={"connector-state " + state.tone} data-testid="web-search-state">
                <span className="connector-state-dot" aria-hidden="true" />
                {state.line}
              </div>
            </div>
            <TextButton testId="web-search-test" disabled={testing} onClick={() => void runTest()}>
              {testing ? "Searching…" : "Test search"}
            </TextButton>
          </div>

          <FoldBody open={Boolean(verdict)}>
            {verdict ? (
              <div
                className={"ws-verdict tone-" + verdict.tone}
                role="status"
                data-testid="web-search-verdict"
                data-tone={verdict.tone}
              >
                <Icon name={verdict.tone === "ok" ? "check" : "warning"} size={14} />
                <div className="ws-verdict-text">
                  <div>{verdict.text}</div>
                  {test?.first ? (
                    <div className="ws-verdict-first" title={test.first.url}>
                      First result: {test.first.title || test.first.url}
                    </div>
                  ) : null}
                </div>
                {rejectedBy ? (
                  <TextButton testId="web-search-fix-key" onClick={() => startEntering(rejectedBy)}>
                    Replace key
                  </TextButton>
                ) : null}
              </div>
            ) : null}
          </FoldBody>

          <div className="ws-own-query">
            <button
              type="button"
              className="ws-link"
              aria-expanded={ownQuery}
              data-testid="web-search-own-query"
              onClick={() => setOwnQuery(!ownQuery)}
            >
              <Icon name="chevronRight" size={12} />
              Test with your own search
            </button>
            <FoldBody open={ownQuery}>
              <div className="ws-own-query-field">
                <input
                  className={SETTINGS_INPUT}
                  aria-label="Test query"
                  value={query}
                  disabled={testing}
                  data-testid="web-search-test-query"
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !testing) void runTest();
                  }}
                />
              </div>
            </FoldBody>
          </div>
        </div>
      </section>

      {tools.length ? (
        <SettingsCard
          title="More search tools"
          description="Connectors that bring search tools of their own. Reeve can use them beside web search; searches above never go through them."
          testId="web-search-more"
        >
          <div className="ws-tools">
            {tools.map((c) => (
              <button
                key={c.name}
                type="button"
                className="connector-row connector-row-main"
                data-testid={`web-search-tool-${c.name}`}
                onClick={() => onOpenConnector?.(c.name)}
              >
                <ConnectorBadge connector={c} size={34} title={c.title} />
                <span className="min-w-0 flex-1">
                  <span className="connector-row-title">{c.title}</span>
                  <span className="connector-row-sub">{c.blurb}</span>
                </span>
                <span className={"connector-state " + (c.connected ? "ok" : "off")}>
                  <span className="connector-state-dot" aria-hidden="true" />
                  {c.connected ? "Connected" : "Not connected"}
                </span>
                <Icon name="chevronRight" size={14} className="connector-row-chev" />
              </button>
            ))}
          </div>
        </SettingsCard>
      ) : null}

      {confirmRemove === "exa" ? (
        <ConfirmDialog
          title="Remove your Exa key?"
          body="Searches go back to Exa's free shared access, about 150 a day for everyone on this network, with DuckDuckGo behind it."
          confirmLabel="Remove key"
          busyLabel="Removing…"
          onCancel={() => setConfirmRemove(null)}
          onConfirm={async () => {
            await save({ mode: settings.mode, removeKey: true }, "Key removed.");
            setConfirmRemove(null);
          }}
        />
      ) : null}
      {confirmRemove === "tavily" ? (
        <ConfirmDialog
          title="Remove your Tavily key?"
          body="Tavily cannot search without it, so searches go back to Exa, with DuckDuckGo behind it."
          confirmLabel="Remove key"
          busyLabel="Removing…"
          onCancel={() => setConfirmRemove(null)}
          onConfirm={async () => {
            await save({ mode: settings.mode, removeTavilyKey: true }, "Key removed. Searches go to Exa first.");
            setConfirmRemove(null);
          }}
        />
      ) : null}
    </div>
  );
}
