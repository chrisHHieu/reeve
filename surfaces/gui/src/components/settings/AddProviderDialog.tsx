// Settings ▸ Models ▸ Add provider (settings redesign, 2026-10-20): a window over the page with the
// providers grouped by how they connect, and the chosen one's connect step in the same window.
// "Add provider" used to scroll to a folded list at the page's foot, and a provider opened as a
// page of its own (owner: "chạy tuốt xuống dưới").
//
// The window keeps its own provider state (useProviderSetup, keepOpen), so picking one here never
// turns the page behind it into that provider's page. A passing check, or a finished sign-in,
// closes the window and the provider is in the page's Connected list.
import { useEffect, useMemo, useRef, useState } from "react";
import type { ProviderInfo } from "../../api";
import { ProviderFields, ProviderMark, providerConnected, useProviderSetup } from "../../providers/ProviderSetup";
import { Icon } from "../Icon";
import { announceSaved } from "./primitives";

/** How long the window stays after a provider connects, so "Tested & saved" is read. */
const CLOSE_AFTER_MS = 900;
/** How many of a provider's models the connect step shows before it is connected. */
const PREVIEW = 6;

type Group = { id: string; label: string; has: (p: ProviderInfo) => boolean; how: (p: ProviderInfo) => string };

const GROUPS: Group[] = [
  { id: "plan", label: "Sign in with a plan you pay for", has: (p) => p.auth === "oauth", how: () => "Sign in" },
  { id: "key", label: "With an API key", has: (p) => p.auth !== "oauth" && !!p.needs_key, how: () => "API key" },
  { id: "local", label: "On this computer", has: (p) => p.auth !== "oauth" && !p.needs_key, how: () => "No key needed" },
];

export function AddProviderDialog({
  onClose,
  onConnected,
  onOpenProvider,
  modelLabels,
}: {
  onClose: () => void;
  /** A provider was saved or signed in: the page behind refreshes its lists. */
  onConnected: () => void;
  /** A provider already connected was picked: its own page opens instead. */
  onOpenProvider: (name: string) => void;
  /** The names models go by elsewhere in Settings ("GLM-5.2 · Z AI"), by their full id. */
  modelLabels?: Record<string, string>;
}) {
  const dps = useProviderSetup({ keepOpen: true, onSaved: onConnected });
  const [query, setQuery] = useState("");
  const search = useRef<HTMLInputElement>(null);
  const info = dps.info;

  const finish = () => {
    announceSaved("Connected");
    onClose();
  };

  // A key that passed its check (or a keyless provider detected) closes the window.
  useEffect(() => {
    if (dps.verify.state !== "ok") return;
    const timer = window.setTimeout(finish, CLOSE_AFTER_MS);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dps.verify.state]);

  // A sign-in finishes in the browser; the provider list says so on its next read.
  const signedIn = info?.auth === "oauth" && !!info.signed_in;
  useEffect(() => {
    if (!signedIn) return;
    onConnected();
    const timer = window.setTimeout(finish, CLOSE_AFTER_MS);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn]);

  // Esc steps back from a provider to the list, then closes.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      event.preventDefault();
      if (dps.sel) dps.backToGallery();
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dps, onClose]);

  useEffect(() => {
    if (!dps.sel) search.current?.focus();
  }, [dps.sel]);

  const needle = query.trim().toLowerCase();
  const shown = useMemo(
    () => dps.ordered.filter((p) => !needle || p.title.toLowerCase().includes(needle) || p.name.includes(needle)),
    [dps.ordered, needle],
  );

  return (
    <>
      <div className="add-provider-backdrop" data-floating onClick={onClose} />
      <section className="add-provider" role="dialog" aria-modal="true" aria-label={info ? `Connect ${info.title}` : "Add a provider"} data-testid="add-provider">
        {!dps.sel || !info ? (
          <>
            <div className="add-provider-head">
              <h2 className="add-provider-title">Add a provider</h2>
              <button type="button" className="settings-icon-btn" aria-label="Close" onClick={onClose}>
                <Icon name="x" size={14} />
              </button>
            </div>
            <div className="add-provider-search">
              <Icon name="search" size={14} />
              <input
                ref={search}
                value={query}
                spellCheck={false}
                placeholder={`Search ${dps.providers.length} providers`}
                aria-label="Search providers"
                data-testid="add-provider-search"
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
            <div className="add-provider-groups">
              {GROUPS.map((group) => {
                const list = shown.filter(group.has);
                if (list.length === 0) return null;
                return (
                  <div key={group.id} className="add-provider-group">
                    <div className="add-provider-group-label">{group.label}</div>
                    <div className="add-provider-grid">
                      {list.map((p) => {
                        const connected = providerConnected(p);
                        return (
                          <button
                            key={p.name}
                            type="button"
                            className={"add-provider-item" + (connected ? " is-connected" : "")}
                            // A provider already connected opens its page; the rest open their step.
                            data-testid={connected ? `add-connected-${p.name}` : `set-provider-${p.name}`}
                            onClick={() => (connected ? onOpenProvider(p.name) : dps.openProvider(p.name))}
                          >
                            <ProviderMark name={p.name} title={p.title} size={34} />
                            <span className="add-provider-item-text">
                              <span className="add-provider-item-name">{p.title}</span>
                              {connected ? (
                                <span className="add-provider-item-how is-ok">✓ Connected</span>
                              ) : (
                                <span className="add-provider-item-how">{dps.statusFor(p)}</span>
                              )}
                            </span>
                            {!connected ? <span className="add-provider-item-kind">{group.how(p)}</span> : null}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
              {shown.length === 0 ? <p className="settings-note">No provider matches “{query.trim()}”.</p> : null}
            </div>
          </>
        ) : (
          <>
            <div className="add-provider-head">
              <button type="button" className="settings-icon-btn" aria-label="Back to the providers" data-testid="set-back" onClick={dps.backToGallery}>
                <Icon name="arrowLeft" size={14} />
              </button>
              <span className="flex-1" />
              <button type="button" className="settings-icon-btn" aria-label="Close" onClick={onClose}>
                <Icon name="x" size={14} />
              </button>
            </div>
            <div className="add-provider-who">
              <ProviderMark name={info.name} title={info.title} size={40} />
              <div className="min-w-0">
                <h2 className="add-provider-title">Connect {info.title}</h2>
                {info.blurb ? <p className="add-provider-blurb">{info.blurb}</p> : null}
              </div>
            </div>
            {info.auth === "oauth" ? <SignInSteps title={info.title} /> : null}
            <div className="add-provider-form">
              <ProviderFields ps={dps} tp="set" hideBlurb testLabel="Connect" />
            </div>
            {(info.suggested_models ?? []).length > 0 ? (
              <div className="add-provider-models" data-testid="model-preview">
                <div className="add-provider-group-label">
                  Models it brings <span className="add-provider-models-hint">· in the composer's picker once connected</span>
                </div>
                <div className="add-provider-model-list">
                  {(info.suggested_models ?? []).slice(0, PREVIEW).map((id) => (
                    <span key={id} className="add-provider-model" title={id}>
                      {modelLabels?.[info.name === "openai" ? id : `${info.name}:${id}`] || id}
                    </span>
                  ))}
                  {(info.suggested_models ?? []).length > PREVIEW ? (
                    <span className="add-provider-model is-more">+{(info.suggested_models ?? []).length - PREVIEW} more</span>
                  ) : null}
                </div>
              </div>
            ) : null}
          </>
        )}
      </section>
    </>
  );
}

/** What signing in with a plan does, before the button that starts it. */
export function SignInSteps({ title }: { title: string }) {
  const plan = title.replace(/\s*subscription.*$/i, "");
  return (
    <ol className="signin-steps">
      <li>Your browser opens on {plan}'s sign-in page.</li>
      <li>Sign in and allow Reeve.</li>
      <li>Back here, its models are ready. The sign-in stays on this computer.</li>
    </ol>
  );
}
