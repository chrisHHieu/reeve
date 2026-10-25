import { useEffect, useRef, useState, type ReactNode } from "react";
import { getProviders, removeProvider, setProvider, verifyProvider, type ProviderInfo } from "../api";
import { sinceLabel } from "../ago";
import { providerRank } from "./logos";

// What the provider gallery and a provider's form are doing, for both places that show them
// (Onboarding's first step, Settings ▸ Models): which provider is open, what has been typed, and the
// Test that checks a key and saves it. The markup is ProviderSetup.tsx's.

export type Verify = { state: "idle" | "testing" | "ok" | "error"; msg?: string };

export interface ProviderSetupState {
  providers: ProviderInfo[];
  ordered: ProviderInfo[];
  refreshProviders: () => Promise<void>;
  sel: string | null;
  info: ProviderInfo | undefined;
  fields: Record<string, string>;
  setFieldValue: (key: string, value: string) => void;
  dirty: boolean;
  verify: Verify;
  showEndpoint: boolean;
  setShowEndpoint: (v: boolean) => void;
  keylessOk: Set<string>;
  credentialed: boolean;
  savedState: boolean;
  secretFilled: boolean;
  openProvider: (name: string) => void;
  backToGallery: () => void;
  runTestAndSave: () => Promise<boolean>;
  removeKey: () => Promise<void>;
  cancelBackTimer: () => void;
  statusFor: (p: ProviderInfo, opts?: { lastUsed?: boolean }) => ReactNode;
  // Saves a field that is not a secret when it loses focus, on a provider set up already. Test saves
  // the KEY; something like Anthropic's thinking_budget must not need the key tested again (the
  // owner, 2026-07-23: the budget never saved, and nothing said so).
  saveField: (key: string) => Promise<void>;
  fieldSaved: string | null; // the field showing "✓ Saved" for a moment
}

/** When a provider last answered, as "2h ago"; null when it never has. */
export const relTime = (epoch?: number | null): string | null => sinceLabel(epoch);

const BACK_AFTER_TEST_MS = 900;
const SAVED_FLASH_MS = 1400;

const STATUS = "block text-[12px] truncate";

export function useProviderSetup(opts?: {
  onSaved?: () => void;
  /** Stay on the provider after Test passes, instead of going back to the gallery: for a form that
   *  is one step inside a pane still in use, where going back would empty it the moment it worked. */
  keepOpen?: boolean;
}): ProviderSetupState {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [sel, setSel] = useState<string | null>(null); // null is the gallery
  const [fields, setFields] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  const [showEndpoint, setShowEndpoint] = useState(false);
  const [verify, setVerify] = useState<Verify>({ state: "idle" });
  // A provider with no key (Ollama) says configured without anything having run: it counts as
  // running once Detect has passed in this session.
  const [keylessOk, setKeylessOk] = useState<Set<string>>(new Set());
  // What was typed and not saved, per provider, kept while another card is open (the owner,
  // 2026-07-16).
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({});
  const [fieldSaved, setFieldSaved] = useState<string | null>(null);
  const backTimer = useRef<number | null>(null);
  const savedTimer = useRef<number | null>(null);

  const refreshProviders = () =>
    getProviders()
      .then(setProviders)
      .catch(() => {});
  useEffect(() => {
    refreshProviders();
    return () => {
      if (backTimer.current) window.clearTimeout(backTimer.current);
    };
  }, []);

  const info = providers.find((p) => p.name === sel);
  const credentialed = !!info?.configured && !!info?.needs_key;
  const keepDraft = (name: string, draft: Record<string, string>) => setDrafts((all) => ({ ...all, [name]: draft }));
  const toGallery = () => {
    setSel(null);
    setVerify({ state: "idle" });
  };

  const openProvider = (name: string) => {
    const provider = providers.find((p) => p.name === name);
    if (sel) keepDraft(sel, fields);
    const draft = drafts[name];
    setFields(Object.fromEntries((provider?.fields || []).map((f) => [f.key, draft?.[f.key] || provider?.values?.[f.key] || f.default || ""])));
    setSel(name);
    setDirty(!!draft && Object.values(draft).some(Boolean));
    setVerify({ state: "idle" });
    setShowEndpoint(false);
  };

  const backToGallery = () => {
    // Only what is NOT saved is kept. Keeping everything kept the key just saved, on the way back
    // after Test, so opening a connected provider again put the key back in the field as plain text
    // where the masked placeholder and the saved pill belong (the owner, 2026-07-19). A clean form
    // clears its old draft.
    if (sel) keepDraft(sel, dirty ? fields : {});
    toGallery();
  };

  // Test checks, saves and goes back: a Test that passes saves the key and returns to the
  // gallery, where the card now has its ✓, with no more clicks.
  const runTestAndSave = async (): Promise<boolean> => {
    if (!sel) return false;
    const name = sel;
    setVerify({ state: "testing" });
    const checked = await verifyProvider(name, fields).catch(() => ({ ok: false, error: "unreachable" }));
    if (!checked.ok) {
      setVerify({ state: "error", msg: checked.error || "couldn't verify" });
      return false;
    }
    if (dirty || !info?.configured) await setProvider(name, fields).catch(() => {});
    if (!info?.needs_key) setKeylessOk((names) => new Set(names).add(name));
    setVerify({ state: "ok" });
    setDirty(false);
    keepDraft(name, {});
    await refreshProviders();
    opts?.onSaved?.();
    if (opts?.keepOpen) return true;
    // Long enough to see "✓ Tested & saved" in the field, then back. Not backToGallery, whose
    // closure is from before the save and would keep the saved key as a draft (the 2026-07-19
    // bug); this way back clears the draft whatever.
    backTimer.current = window.setTimeout(() => {
      keepDraft(name, {});
      toGallery();
    }, BACK_AFTER_TEST_MS);
    return true;
  };

  // Secrets keep their rule, saved by Test; a provider not set up yet saves everything on its first
  // Test. Everything else, on one that is, saves when it loses focus.
  const saveField = async (key: string) => {
    if (!sel || !info?.configured) return;
    const spec = info.fields.find((f) => f.key === key);
    if (!spec || spec.secret) return;
    const value = (fields[key] || "").trim();
    if (value === (info.values?.[key] || "").trim()) return;
    const saved = await setProvider(sel, { [key]: value }).catch(() => ({ ok: false }));
    if (!saved.ok) return;
    await refreshProviders();
    opts?.onSaved?.();
    setFieldSaved(key);
    if (savedTimer.current) window.clearTimeout(savedTimer.current);
    savedTimer.current = window.setTimeout(() => setFieldSaved(null), SAVED_FLASH_MS);
  };

  // Settings only: forget the key, and the card goes back to "Not set up".
  const removeKey = async () => {
    if (!sel) return;
    const name = sel;
    await removeProvider(name).catch(() => {});
    keepDraft(name, {});
    setKeylessOk((names) => new Set([...names].filter((n) => n !== name)));
    await refreshProviders();
    opts?.onSaved?.();
    toGallery();
  };

  const statusFor = (p: ProviderInfo, o?: { lastUsed?: boolean }) => {
    if (p.auth === "oauth") {
      // Only the state on the card: an email cut short at card width read badly (the owner,
      // 2026-08-21); the provider's own pane says who is signed in.
      return p.signed_in ? <span className={STATUS + " text-ok font-medium"}>✓ Signed in</span> : <span className={STATUS + " text-faint"}>Sign in with your plan</span>;
    }
    if (p.configured && p.needs_key) {
      const used = o?.lastUsed ? relTime(p.last_used_at) : null;
      return (
        <span className={STATUS + " text-ok font-medium"}>
          ✓ Connected{used ? <span className="text-muted font-normal"> · used {used}</span> : ""}
        </span>
      );
    }
    if (!p.needs_key) {
      return <span className={STATUS + " text-faint"}>{keylessOk.has(p.name) ? <span className="text-ok font-medium">✓ Running</span> : "No key needed"}</span>;
    }
    return <span className={STATUS + " text-faint"}>Not set up</span>;
  };

  return {
    providers,
    // By name, alphabetically (the owner, 2026-08-21: an order chosen by hand reads as favouring a
    // vendor); the old ranking only breaks ties between equal names.
    ordered: [...providers].sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" }) || providerRank(a.name) - providerRank(b.name)),
    refreshProviders,
    sel,
    info,
    fields,
    setFieldValue: (key, value) => {
      setFields((was) => ({ ...was, [key]: value }));
      setDirty(true);
      setVerify({ state: "idle" });
    },
    dirty,
    verify,
    showEndpoint,
    setShowEndpoint,
    keylessOk,
    credentialed,
    // Saved shows inside the key field, green with a pill, for a stored key and a Test just
    // passed alike; typing takes it away.
    savedState: (credentialed && !dirty) || verify.state === "ok",
    // Only a REQUIRED secret holds Test back: the cloud providers (Bedrock, Vertex) have key
    // fields that are optional, their credentials perhaps in ~/.aws or ADC.
    secretFilled: (info?.fields || []).every((f) => !f.secret || !f.required || (fields[f.key] || "").trim()),
    openProvider,
    backToGallery,
    runTestAndSave,
    removeKey,
    saveField,
    fieldSaved,
    cancelBackTimer: () => {
      if (backTimer.current) window.clearTimeout(backTimer.current);
    },
    statusFor,
  };
}
