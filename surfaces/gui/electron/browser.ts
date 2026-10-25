// The Browser tab's pages (a `<webview>`). A `<webview>` is drawn with
// the app's own page, so the app's menus and dialogs show over it, which the native child webview
// of the Tauri shell could not do: it was hidden whenever one opened and the tab went blank (owner,
// 2026-10-20). The pages are the web's, so they get nothing of the app's: no preload, no Node, a
// sandbox, isolation, their own profile (`PARTITION`), and only web addresses.
import { app, session, shell, type WebContents } from "electron";
import { externalAllowed, webAddress } from "./policy";

/** The profile every Browser tab page shares: its cookies and logins, kept apart from the app's. */
export const PARTITION = "persist:reeve-browser";

/** Lock down every `<webview>` the app attaches, whatever the page asked for. */
export function guardWebviews(appContents: WebContents): void {
  appContents.on("will-attach-webview", (event, prefs, params) => {
    delete (prefs as { preload?: string }).preload;
    prefs.nodeIntegration = false;
    prefs.nodeIntegrationInSubFrames = false;
    prefs.contextIsolation = true;
    prefs.sandbox = true;
    prefs.webSecurity = true;
    const src = params.src || "about:blank";
    if (params.partition !== PARTITION || (src !== "about:blank" && !webAddress(src))) event.preventDefault();
  });
}

/** A page's own popups (a sign-in window) open as windows of the same profile; a link the page
 *  sends to a new tab opens in the person's browser. */
export function guardGuest(contents: WebContents): void {
  contents.setWindowOpenHandler(({ url, disposition }) => {
    if (!webAddress(url)) return { action: "deny" };
    if (disposition === "new-window") {
      return {
        action: "allow",
        overrideBrowserWindowOptions: {
          autoHideMenuBar: true,
          webPreferences: { partition: PARTITION, sandbox: true, contextIsolation: true, nodeIntegration: false },
        },
      };
    }
    if (externalAllowed(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  contents.on("will-navigate", (event, url) => {
    if (!webAddress(url)) event.preventDefault();
  });
}

/** Forget what the Browser tab's pages stored: cookies, logins, caches. */
export async function clearBrowserData(): Promise<void> {
  const profile = session.fromPartition(PARTITION);
  await profile.clearStorageData();
  await profile.clearCache();
}

/** Every web contents the app makes: the app's own window, `<webview>` guests, their popups. */
export function watchContents(isAppContents: (contents: WebContents) => boolean): void {
  app.on("web-contents-created", (_event, contents) => {
    if (isAppContents(contents)) {
      guardWebviews(contents);
      return;
    }
    guardGuest(contents);
  });
}
