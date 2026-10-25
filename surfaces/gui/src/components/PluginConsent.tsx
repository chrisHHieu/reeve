// What a person agrees to before a plugin installs or updates (design §8.2):
// what it brings, by name; anything that would run code, verbatim; what is left out; the
// settings it needs (a secret is a password field); the plugins it installs with it; and,
// for an update, what changed — with a change to what runs code called out.
import { useState } from "react";
import { cancelInstall, confirmInstall, type Consent, type PluginDiff } from "../api/plugins";
import { Switch } from "./settings/primitives";

/** A description as a sentence, so the facts after it read as their own. */
export function sentence(text: string): string {
  const trimmed = text.trim();
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}

function List({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="plugin-consent-list">
      <div className="gate-label">{title}</div>
      {items.map((item) => (
        <code key={item} className="block text-[13px] text-ink">
          {item}
        </code>
      ))}
    </div>
  );
}

function Changes({ diff }: { diff: PluginDiff }) {
  const rows = (["skills", "commands", "agents"] as const).flatMap((key) => [
    ...(diff[key]?.added ?? []).map((name) => `+ ${name}`),
    ...(diff[key]?.removed ?? []).map((name) => `− ${name}`),
  ]);
  return (
    <div data-testid="plugin-consent-diff">
      {diff.executable_changed && (
        <div className="plugin-consent-warn" role="alert">
          What this plugin runs has changed — read it again below.
        </div>
      )}
      <List title="What changes" items={rows.length ? rows : ["No skill, command or agent changes"]} />
    </div>
  );
}

export function PluginConsent({
  token,
  consent,
  diff,
  review,
  onDone,
  onClose,
}: {
  token: string;
  consent: Consent;
  diff?: PluginDiff;
  /** A second yes for an installed plugin's code (changed, or new to this workspace). */
  review?: boolean;
  onDone: () => void;
  onClose: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const verb = review ? "Allow" : diff ? "Update" : "Install";

  const confirm = async () => {
    setBusy(true);
    setError("");
    // Typed as the plugin declared them: the server checks a number as a number.
    const types = Object.fromEntries(consent.user_config.map((o) => [o.key, o.type]));
    const settings = Object.fromEntries(
      Object.entries(values)
        .filter(([, v]) => v !== "")
        .map(([k, v]) => [k, types[k] === "number" ? Number(v) : types[k] === "boolean" ? v === "true" : v]),
    );
    try {
      await confirmInstall(token, settings);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };
  const cancel = () => {
    void cancelInstall(token).catch(() => undefined);
    onClose();
  };
  const pending = consent.dependencies.filter((d) => !d.installed);

  return (
    <div className="gate-overlay" role="dialog" aria-modal="true" aria-labelledby="plugin-consent-title">
      <div className="gate max-w-[620px] max-h-[86vh] overflow-y-auto" data-testid="plugin-consent">
        <h2 id="plugin-consent-title">
          {review
            ? `Allow ${consent.display_name || consent.name} to run its code?`
            : `${verb} ${consent.display_name || consent.name}${consent.version ? ` ${consent.version}` : ""}?`}
        </h2>
        <p className="gate-sub">
          {sentence(consent.description || "A plugin for Reeve.")}
          {consent.author ? ` By ${consent.author}.` : ""}
          {consent.source ? ` From ${consent.source}${consent.sha ? ` @ ${consent.sha.slice(0, 12)}` : ""}.` : ""}
          {consent.current_version ? ` Installed now: ${consent.current_version}.` : ""}
        </p>
        {diff && <Changes diff={diff} />}
        <List title="Skills" items={consent.skills} />
        <List title="Commands" items={consent.commands.map((c) => `/${c}`)} />
        <List title="Subagents" items={consent.agents} />
        {consent.executable.length > 0 && (
          <div className="plugin-consent-code" data-testid="plugin-consent-code">
            <div className="gate-label">Runs code on this computer</div>
            {consent.executable.map((item, i) => (
              <pre key={i} className="plugin-consent-pre">
                <span className="plugin-consent-kind">{item.kind}</span>
                {"\n"}
                {item.detail}
              </pre>
            ))}
          </div>
        )}
        <List title="Not loaded" items={consent.skipped} />
        <List title="Installs with it" items={pending.map((d) => d.id)} />
        {consent.user_config.length > 0 && (
          <div className="plugin-consent-settings">
            <div className="gate-label">Settings</div>
            {consent.user_config.map((option) => (
              <label key={option.key} className="plugin-consent-field">
                <span>
                  {option.title}
                  {option.required ? " *" : ""}
                </span>
                {option.type === "boolean" ? (
                  <Switch
                    label={option.title}
                    checked={values[option.key] === "true"}
                    onChange={(next) => setValues({ ...values, [option.key]: next ? "true" : "false" })}
                  />
                ) : (
                  <input
                    type={option.sensitive ? "password" : option.type === "number" ? "number" : "text"}
                    autoComplete="off"
                    aria-label={option.title}
                    title={option.description}
                    value={values[option.key] ?? ""}
                    onChange={(e) => setValues({ ...values, [option.key]: e.target.value })}
                  />
                )}
              </label>
            ))}
          </div>
        )}
        <p className="plugin-consent-note">
          A plugin's text goes to the model as instructions, and it never gains a permission you
          have not given.
        </p>
        {error && <div className="gate-error" role="alert">{error}</div>}
        <div className="gate-foot justify-end gap-2">
          <button className="btn" onClick={cancel} disabled={busy}>
            Cancel
          </button>
          <button className="btn primary" onClick={() => void confirm()} disabled={busy} data-testid="plugin-consent-confirm">
            {busy ? "Saving…" : verb}
          </button>
        </div>
      </div>
    </div>
  );
}
