declare const __REEVE_DEV_TOKEN__: string;

// Endpoint resolution order: runtime-injected globals (Tauri sets `window.__REEVE_HTTP__`
// for its dynamically-chosen sidecar port) → Vite env → the 127.0.0.1:8765 dev default. This
// keeps a single codebase: browser `npm run dev` hits 8765; the desktop shell hits its sidecar.
export const httpBase = (): string =>
  (globalThis as any).__REEVE_HTTP__ ||
  (import.meta as any).env?.VITE_REEVE_HTTP ||
  "http://127.0.0.1:8765";
export const wsBase = (): string =>
  (globalThis as any).__REEVE_WS__ ||
  (import.meta as any).env?.VITE_REEVE_WS ||
  "ws://127.0.0.1:8765";
export const apiToken = (): string =>
  (globalThis as any).__REEVE_API_TOKEN__ ||
  (import.meta as any).env?.VITE_REEVE_API_TOKEN ||
  (typeof __REEVE_DEV_TOKEN__ === "string" ? __REEVE_DEV_TOKEN__ : "");

/** Pick up the token the dev server holds RIGHT NOW, before anything is fetched.
 *
 * The build-time `__REEVE_DEV_TOKEN__` is a snapshot of the file as it was when Vite
 * started; the sidecar rewrites it on every launch. Adopting the live one turns "restart
 * the server, then restart Vite, then reload" into just "reload".
 *
 * It writes the SAME global the desktop shell sets, so there is one precedence rule rather
 * than two -- and it never overwrites a value already there: in the Tauri dev shell that
 * value is the real in-memory token of a sidecar on a different port, and the file this
 * reads belongs to someone else's server.
 */
export async function adoptDevToken(): Promise<void> {
  if (!(import.meta as any).env?.DEV) return;
  if ((globalThis as any).__REEVE_API_TOKEN__) return;
  try {
    const res = await globalThis.fetch("/__dev/token", { cache: "no-store" });
    const token = (await res.json())?.token;
    if (typeof token === "string" && token) {
      (globalThis as any).__REEVE_API_TOKEN__ = token;
    }
  } catch {
    // Not served by our dev middleware (a preview build, a different host): the baked
    // token stands and behaviour is exactly what it was before.
  }
}

// All local REST calls pass through this module, so a module-local wrapper applies launch
// authentication without asking every endpoint helper to remember the security header.
export const fetch = (
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> => {
  const headers = new Headers(init.headers);
  const token = apiToken();
  if (token) headers.set("X-Reeve-Token", token);
  return globalThis.fetch(input, { ...init, headers });
};

export const openWebSocket = (url: string): WebSocket => {
  const token = apiToken();
  return token
    ? new WebSocket(url, ["reeve", token])
    : new WebSocket(url);
};
