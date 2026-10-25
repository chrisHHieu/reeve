// The Browser tab's page as a browser of its own (a `<webview>`). In the
// desktop app the page is a `<webview>` in the tab, so a site that refuses frames opens as it does
// in OpenDesign's browser, and, being drawn with the app's own page, the app's menus and dialogs
// show over it. The native child webview of the Tauri shell sat above everything the app drew, so it
// was hidden whenever a menu opened and the tab went white (owner, 2026-10-20). Each page runs in
// the Browser tab's own profile with nothing of the app's (electron/browser.ts).
import { useCallback, useEffect, useRef } from "react";
import { browserClearData, canEmbedBrowser } from "../../desktop";

/** The parts of Electron's `<webview>` element the tab uses. */
export interface WebviewElement extends HTMLElement {
  getURL(): string;
  loadURL(url: string): Promise<void>;
  reload(): void;
  capturePage(): Promise<{ toDataURL(): string }>;
}

/** The same address, however it was spelled (`https://a.com` and `https://a.com/`). */
export function sameAddress(one: string, other: string): boolean {
  try {
    return new URL(one).href === new URL(other).href;
  } catch {
    return one === other;
  }
}

export function useBrowserPane({
  id,
  url,
  onVisited,
  onLoading,
  onTitle,
  onError,
}: {
  id?: string;
  url: string;
  /** The page went somewhere by itself (a link clicked in it). */
  onVisited?: (url: string) => void;
  /** Whether the page is loading, and the address it is at. */
  onLoading?: (loading: boolean, url: string) => void;
  onTitle?: (title: string) => void;
  onError?: (message: string) => void;
}) {
  const native = canEmbedBrowser() && !!id;
  const view = useRef<WebviewElement | null>(null);
  const ready = useRef(false);
  const calls = useRef({ onVisited, onLoading, onTitle, onError, url });
  calls.current = { onVisited, onLoading, onTitle, onError, url };
  const detach = useRef<(() => void) | null>(null);
  // The address the `<webview>` was first given. Its `src` stays that, so the page going somewhere
  // by itself (and the tab following it) does not load it again; later addresses go by loadURL.
  const start = useRef<string | null>(null);

  /** The ref for the `<webview>`: what the page says of itself goes to the tab. */
  const attach = useCallback((node: HTMLElement | null) => {
    detach.current?.();
    detach.current = null;
    ready.current = false;
    view.current = node as WebviewElement | null;
    if (!node) {
      start.current = null;
      return;
    }
    const here = () => {
      try {
        return (node as WebviewElement).getURL();
      } catch {
        return calls.current.url;
      }
    };
    const listeners: [string, (event: any) => void][] = [
      [
        "dom-ready",
        () => {
          if (ready.current) return;
          ready.current = true;
          // An address the tab moved to before the page was ready.
          const wanted = calls.current.url;
          if (wanted && !sameAddress(here(), wanted)) void (node as WebviewElement).loadURL(wanted).catch(() => undefined);
        },
      ],
      ["did-start-loading", () => calls.current.onLoading?.(true, here())],
      [
        "did-stop-loading",
        () => {
          const at = here();
          calls.current.onLoading?.(false, at);
          if (at && at !== "about:blank" && !sameAddress(at, calls.current.url)) calls.current.onVisited?.(at);
        },
      ],
      ["page-title-updated", (event) => calls.current.onTitle?.(String(event.title ?? ""))],
      [
        "did-fail-load",
        (event) => {
          // -3 is a navigation the page replaced with another one; not a failure to show.
          if (event.isMainFrame && event.errorCode !== -3) calls.current.onError?.(String(event.errorDescription || event.errorCode));
        },
      ],
    ];
    for (const [name, listener] of listeners) node.addEventListener(name, listener);
    detach.current = () => {
      for (const [name, listener] of listeners) node.removeEventListener(name, listener);
    };
  }, []);

  useEffect(() => () => detach.current?.(), []);

  // A new address from the tab (typed, Back, Forward) loads; the address the page is already at
  // (it went there itself, and the tab followed) does not load again.
  useEffect(() => {
    const node = view.current;
    if (!native || !node || !ready.current || !url) return;
    let now = "";
    try {
      now = node.getURL();
    } catch {
      now = "";
    }
    if (now && sameAddress(now, url)) return;
    node.loadURL(url).catch((error) => calls.current.onError?.(error instanceof Error ? error.message : String(error)));
  }, [native, url]);

  return {
    native,
    attach,
    startAt: (url: string) => (start.current ??= url),
    reload: () => view.current?.reload(),
    clearData: () => browserClearData(),
    /** A picture of the page as the person sees it, logins and all. */
    capture: async (): Promise<string | null> => {
      try {
        return (await view.current?.capturePage())?.toDataURL() ?? null;
      } catch {
        return null;
      }
    },
  };
}
