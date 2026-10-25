import type { On } from "../router";
import type { MockState } from "../state";

// Model providers: checking a key (read only), saving one, forgetting one, and the ChatGPT
// subscription's sign-in, which finishes at once here.

export function providerRoutes(on: On, s: MockState) {
  const { providers } = s;
  const named = (name: string) => providers.find((row) => row.name === name);
  const unknown = (name: string) => ({ ok: false, error: `unknown provider: ${name}` });

  // A key with "bad" in it fails the check; any other passes. Checking changes nothing.
  on("POST", "/v1/providers/verify", ({ body }) =>
    /bad/i.test(String(body?.fields?.api_key || "")) ? { ok: false, error: "Invalid API key." } : { ok: true });
  // Saving, as set_provider does: a key makes the provider configured and dates it; the other
  // fields go into `values`, an empty one taking its value out. The reply has to say `ok`, or the
  // save on blur sees nothing.
  on("POST", "/v1/providers", ({ body }) => {
    const row = named(body.name);
    if (!row) return unknown(body.name);
    if (body.fields?.api_key) Object.assign(row, { configured: true, key_set_at: "2026-07-05" });
    for (const [key, value] of Object.entries(body.fields || {})) {
      if (key === "api_key") continue;
      if (value) row.values = { ...row.values, [key]: value };
      else if (row.values) delete row.values[key];
    }
    return { ok: true, provider: body.name };
  });
  // Settings' "Remove key…". A provider that needs no key (ollama) stays configured.
  on("DELETE", /\/v1\/providers\/([^/]+)$/, ({ match }) => {
    const row = named(match[1]);
    if (!row) return unknown(match[1]);
    Object.assign(row, { configured: !row.needs_key, key_set_at: null });
    return { ok: true, provider: match[1] };
  });

  const codex = () => named("openai-codex");
  const signedIn = (yes: boolean) => {
    const row = codex();
    if (row) Object.assign(row, { signed_in: yes, configured: yes, account: yes ? "sam@example.com" : null });
  };
  on("POST", "/v1/providers/openai-codex/signin", () => {
    signedIn(true);
    return { ok: true, started: true };
  });
  on("*", "/v1/providers/openai-codex/status", () => ({
    signed_in: !!codex()?.signed_in, account: codex()?.account || null, authorizing: false, last_error: null, authorize_url: null,
  }));
  on("POST", "/v1/providers/openai-codex/signout", () => {
    signedIn(false);
    return { ok: true, had_tokens: true };
  });
  on("*", "/v1/providers", () => providers);
}
