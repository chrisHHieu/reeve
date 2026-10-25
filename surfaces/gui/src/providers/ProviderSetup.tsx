import { type ReactNode } from "react";
import type { ProviderField as ProviderFieldT, ProviderInfo } from "../api";
import { Icon } from "../components/Icon";
import { openExternal } from "../desktop";
import { Disclosure } from "../ui";
import { KEY_HELP } from "./keyHelp";
import { PROVIDER_LOGOS } from "./logos";
import { OAuthSignIn } from "./OAuthSignIn";
import type { ProviderSetupState } from "./useProviderSetup";

// The provider gallery and a provider's form, drawn the same for Onboarding's first step and
// Settings ▸ Models, so the two can never look different. What they do is
// useProviderSetup; each place keeps its own frame (a modal of one height, a page that scrolls) and
// passes a prefix for its test ids, so e2e can reach either one.

export { KEY_HELP } from "./keyHelp";
export { relTime, useProviderSetup, type ProviderSetupState, type Verify } from "./useProviderSetup";

const LABEL = "block text-[12px] text-muted mt-3 mb-1";
const FIELD = "w-full px-3 py-2 rounded-lg border bg-panel text-[13px] outline-none focus:border-accent";
const PILL_IN_FIELD = "absolute right-2 top-1/2 -translate-y-1/2 text-[11px] font-medium text-ok bg-okSoft rounded-full px-2 py-0.5 pointer-events-none";
const LINK = "text-muted underline decoration-line underline-offset-2 hover:text-ink";

/** A provider's mark, always on a light plate so marks of several colours read in either theme. */
export function ProviderMark({ name, title, size = 32 }: { name: string; title: string; size?: number }) {
  const url = PROVIDER_LOGOS[name];
  return (
    <span className="rounded-lg border border-line grid place-items-center shrink-0" style={{ width: size, height: size, background: "#f6f7f8" }}>
      {url ? (
        <img src={url} alt="" style={{ width: size * 0.6, height: size * 0.6 }} />
      ) : (
        <span className="text-[13px] font-semibold text-muted" style={size < 24 ? { fontSize: Math.round(size * 0.55) } : undefined}>
          {title[0]}
        </span>
      )}
    </span>
  );
}

/** A provider this computer can use already: signed in to a plan, or holding a key. One with no
 *  key (Ollama) is not connected until something has answered from it. */
export function providerConnected(p: ProviderInfo): boolean {
  return p.auth === "oauth" ? !!p.signed_in : !!(p.configured && p.needs_key);
}

export function ProviderCards({
  ps,
  tp,
  gridClass = "grid grid-cols-2 gap-2.5",
  lastUsed = false,
  filter,
  list = false,
}: {
  ps: ProviderSetupState;
  tp: string; // the test-id prefix: "ob" in onboarding, "set" in Settings
  gridClass?: string;
  lastUsed?: boolean;
  /** Only the providers this keeps (Settings puts the connected ones apart). */
  filter?: (p: ProviderInfo) => boolean;
  /** A row each, as a Settings list draws them (mark, name, state, chevron), not gallery cards. */
  list?: boolean;
}) {
  const shown = filter ? ps.ordered.filter(filter) : ps.ordered;
  if (list) {
    return (
      <div className="settings-list">
        {shown.map((p) => (
          <button key={p.name} type="button" className="settings-item" data-testid={`${tp}-provider-${p.name}`} onClick={() => ps.openProvider(p.name)}>
            <ProviderMark name={p.name} title={p.title} />
            <span className="settings-item-text">
              <span className="settings-item-name">
                <span>{p.title}</span>
              </span>
              {ps.statusFor(p, { lastUsed })}
            </span>
            <Icon name="chevronRight" size={14} className="settings-item-chev" />
          </button>
        ))}
      </div>
    );
  }
  return (
    <div className={gridClass}>
      {shown.map((p) => (
        <button
          key={p.name}
          className="flex items-center gap-2.5 rounded-2xl bg-panel px-3.5 py-3 text-left hover:border-ink/30 hover:text-ink transition-colors"
          data-testid={`${tp}-provider-${p.name}`}
          onClick={() => ps.openProvider(p.name)}
        >
          <ProviderMark name={p.name} title={p.title} />
          <span className="min-w-0 flex-1">
            <span className="block text-[13px] font-semibold leading-tight truncate">{p.title}</span>
            {ps.statusFor(p, { lastUsed })}
          </span>
          <span className="text-faint text-[14px]">›</span>
        </button>
      ))}
    </div>
  );
}

/** One provider's form: the way back, its mark and name, then ProviderFields. */
export function ProviderForm({ ps, tp, footer }: { ps: ProviderSetupState; tp: string; footer?: ReactNode }) {
  const { info, sel } = ps;
  if (!sel) return null;
  return (
    <div>
      <button className="text-[13px] text-muted hover:text-ink" onClick={ps.backToGallery} data-testid={`${tp}-back`}>
        ‹ All providers
      </button>
      <div className="flex items-center gap-3 mt-3 mb-1">
        <ProviderMark name={info?.name || ""} title={info?.title || ""} size={36} />
        <span className="min-w-0">
          <span className="block text-[14px] font-semibold leading-tight">{info?.title}</span>
          {info && ps.statusFor(info)}
        </span>
      </div>
      <ProviderFields ps={ps} tp={tp} footer={footer} />
    </div>
  );
}

/** Whether a field shows only for another field's value (`show_when`), and that value is chosen. */
const shownFor = (f: ProviderFieldT, values: Record<string, string>) => !!f.show_when && Object.entries(f.show_when).every(([key, wanted]) => (values[key] || "") === wanted);

/** A provider that offers a choice of how to sign in (the cloud providers): a joined track of
 *  options, then a soft card with only the chosen way's words, fields and its own Test & save (the
 *  owner's call, 2026-07-26). */
function MethodChoice({ ps, tp, choice, renderField }: { ps: ProviderSetupState; tp: string; choice: ProviderFieldT; renderField: (f: ProviderFieldT) => ReactNode }) {
  const method = ps.fields[choice.key] || choice.default || "";
  const chosen = choice.choices?.find((c) => c.value === method);
  const testing = ps.verify.state === "testing";
  return (
    <div>
      <label className={LABEL}>{choice.label}</label>
      <div className="inline-flex max-w-full flex-wrap gap-0.5 rounded-[10px] border border-line bg-line/40 p-[3px]" role="radiogroup" aria-label={choice.label}>
        {(choice.choices || []).map((option) => {
          const on = method === option.value;
          return (
            <button
              key={option.value}
              role="radio"
              aria-checked={on}
              className={"flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[13px] whitespace-nowrap transition-colors " + (on ? "bg-panel text-ink font-medium shadow-sm ring-1 ring-line" : "text-muted hover:text-ink")}
              data-testid={`${tp}-choice-${choice.key}-${option.value}`}
              onClick={() => ps.setFieldValue(choice.key, option.value)}
            >
              {option.label}
              {option.tag && <span className="text-[9.5px] font-semibold uppercase tracking-wide text-accent bg-accentSoft rounded-full px-1.5 py-px">{option.tag}</span>}
            </button>
          );
        })}
      </div>
      <div className="mt-2.5 rounded-xl border border-line bg-paper/60 px-4 pb-3.5 pt-3">
        {chosen?.desc && <p className="text-[12px] text-muted">{chosen.desc}</p>}
        {chosen?.command && (
          <button
            className="mt-2.5 inline-flex items-center gap-2 rounded-lg border border-line bg-panel px-2.5 py-1.5 text-[12px] font-mono text-ink hover:border-lineStrong"
            onClick={() => void navigator.clipboard?.writeText(chosen.command || "")}
            title="Copy command"
            data-testid={`${tp}-cmd-copy`}
          >
            {chosen.command}
            <span className="font-sans text-[11px] text-faint">⧉</span>
          </button>
        )}
        {(ps.info?.fields || []).filter((f) => shownFor(f, ps.fields)).map(renderField)}
        <div className="mt-3.5 flex items-center justify-between gap-3 border-t border-line pt-3">
          {ps.savedState ? (
            <span className="text-[12px] font-medium text-ok" data-testid={`${tp}-saved-pill`}>
              ✓ Tested &amp; saved
            </span>
          ) : (
            <span className="text-[12px] text-faint">Runs one read-only check, then saves.</span>
          )}
          <button
            className="shrink-0 rounded-lg border border-accent bg-accent px-4 py-1.5 text-[13px] font-medium text-[color:var(--canvas)] hover:brightness-105 disabled:opacity-40"
            onClick={() => ps.runTestAndSave()}
            disabled={testing}
            data-testid={`${tp}-test`}
          >
            {testing ? "…" : <>Test &amp; save</>}
          </button>
        </div>
      </div>
    </div>
  );
}

/** A keyed provider's own address, behind a quiet line below the key help, far enough below to
 *  read as a row for those who need it, with no words of explanation (the owner, 2026-07-18 and
 *  -19). The line opens and closes the field, which grows and fades in (2026-10-10: the link used
 *  to turn into the field in one frame, and could not be closed again). */
function CustomEndpoint({ ps, tp, field }: { ps: ProviderSetupState; tp: string; field: ProviderFieldT }) {
  return (
    <div className="mt-4">
      <button
        type="button"
        className="inline-flex items-center gap-1 text-[13px] text-muted hover:text-ink"
        aria-expanded={ps.showEndpoint}
        onClick={() => ps.setShowEndpoint(!ps.showEndpoint)}
        data-testid={`${tp}-endpoint-link`}
      >
        <Icon name="chevronRight" size={12} className={"transition-transform duration-150" + (ps.showEndpoint ? " rotate-90" : "")} />
        Custom endpoint
      </button>
      <Disclosure open={ps.showEndpoint} className="fold-fade">
        <div className="pt-2">
          <label className={LABEL}>{field.label}</label>
          <div className="relative">
            <input
              className={FIELD + " border-line"}
              type="text"
              placeholder={field.placeholder}
              value={ps.fields[field.key] || ""}
              data-testid={`${tp}-field-${field.key}`}
              onChange={(e) => ps.setFieldValue(field.key, e.target.value)}
              onBlur={() => void ps.saveField(field.key)}
            />
            {ps.fieldSaved === field.key && (
              <span className={PILL_IN_FIELD} data-testid={`${tp}-field-saved-${field.key}`}>
                ✓ Saved
              </span>
            )}
          </div>
          {field.help && <p className="text-[12px] text-faint mt-1">{field.help}</p>}
        </div>
      </Disclosure>
    </div>
  );
}

/** Everything under a provider's name: the blurb, the OAuth sign-in or the fields (the endpoint
 *  behind its quiet line), the saved pill inside the field, Test or Detect, where to get a key, and
 *  an error line that keeps its height. `footer` comes after the error line (Settings puts "Remove
 *  key…" there). */
export function ProviderFields({
  ps,
  tp,
  footer,
  hideBlurb = false,
  testLabel = "Test",
}: {
  ps: ProviderSetupState;
  tp: string;
  footer?: ReactNode;
  /** The caller shows the blurb itself (Settings puts it under the provider's name). */
  hideBlurb?: boolean;
  /** The label of the key's check-and-save ("Connect" where adding the provider is the whole step). */
  testLabel?: string;
}) {
  const { info, sel } = ps;
  if (!sel) return null;
  const all = info?.fields || [];
  const oauth = info?.auth === "oauth";
  const keyed = all.some((f) => f.secret);
  const choice = all.find((f) => f.choices && f.choices.length);
  // With no choice of method, Test sits by the required secret (the API key), or by the first field
  // of a provider with no key (Ollama's Detect).
  const testNext = all.find((f) => f.secret && f.required)?.key ?? all[0]?.key;
  const endpoint = keyed && !oauth ? all.find((f) => f.key === "base_url") : undefined;
  const testing = ps.verify.state === "testing";

  const fieldRow = (f: ProviderFieldT, testable: boolean) => (
    <div key={f.key}>
      <label className={LABEL}>{f.label}</label>
      <div className="flex gap-2">
        <div className="relative flex-1 min-w-0">
          <input
            className={FIELD + (ps.savedState && testable ? " border-ok pr-32" : " border-line")}
            type={f.secret ? "password" : "text"}
            placeholder={f.secret && ps.credentialed && !ps.dirty ? "••••••••" : f.placeholder}
            value={ps.fields[f.key] || ""}
            data-testid={`${tp}-field-${f.key}`}
            onChange={(e) => ps.setFieldValue(f.key, e.target.value)}
            onBlur={f.secret ? undefined : () => void ps.saveField(f.key)}
          />
          {ps.fieldSaved === f.key && (
            <span className={PILL_IN_FIELD} data-testid={`${tp}-field-saved-${f.key}`}>
              ✓ Saved
            </span>
          )}
          {/* The state is inside the field, never in lines under it. */}
          {ps.savedState && testable && (
            <span className={PILL_IN_FIELD} data-testid={`${tp}-saved-pill`}>
              {info?.needs_key ? <>✓ Tested &amp; saved</> : <>✓ Detected</>}
            </span>
          )}
        </div>
        {testable && (
          <button
            className="px-4 rounded-lg border border-line text-[13px] font-medium text-ink hover:border-lineStrong shrink-0 disabled:opacity-40"
            onClick={() => ps.runTestAndSave()}
            disabled={testing || (!ps.secretFilled && !ps.credentialed)}
            data-testid={`${tp}-test`}
          >
            {testing ? "…" : info?.needs_key ? testLabel : "Detect"}
          </button>
        )}
      </div>
      {f.help && <p className="text-[12px] text-faint mt-1">{f.help}</p>}
    </div>
  );

  // The plain fields: none for a plan's sign-in, which sets the provider up itself, and not those
  // shown for a method, the method choice itself, or a keyed provider's endpoint, which go below.
  const plain = oauth ? [] : all.filter((f) => !f.show_when && !(f.choices && f.choices.length) && !(f.key === "base_url" && keyed));

  return (
    <>
      {info?.blurb && !hideBlurb && <p className="text-[12px] text-faint mt-1">{info.blurb}</p>}
      {oauth && info && <OAuthSignIn info={info} ps={ps} tp={tp} />}
      {plain.map((f) => fieldRow(f, !choice && f.key === testNext))}
      {choice && <MethodChoice ps={ps} tp={tp} choice={choice} renderField={(f) => fieldRow(f, false)} />}

      {info?.needs_key && KEY_HELP[sel] && (
        <p className="text-[12px] text-faint mt-2">
          No key yet?{" "}
          <button className={LINK} onClick={() => openExternal(KEY_HELP[sel].url)}>
            Create one at {KEY_HELP[sel].label} ↗
          </button>{" "}
          — takes about a minute.
        </p>
      )}
      {info && !info.needs_key && !oauth && (
        <p className="text-[12px] text-faint mt-2">
          No API key needed — Ollama runs models on this computer.{" "}
          <button className={LINK} onClick={() => openExternal("https://ollama.com/download")}>
            Install Ollama ↗
          </button>
        </p>
      )}
      {endpoint && <CustomEndpoint ps={ps} tp={tp} field={endpoint} />}

      {/* A fixed height, so a failure never moves the form. */}
      <div className="provider-error-line mt-3 min-h-[19px] text-[13px]">{ps.verify.state === "error" && <span className="text-warnInk">{ps.verify.msg}</span>}</div>
      {oauth ? null : footer}
    </>
  );
}
