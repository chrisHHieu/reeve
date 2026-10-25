// The bridge to the desktop shell (Electron, Tauri before it). In a browser every
// call here is inert (`isDesktop() === false`), so the app stays one codebase. The shell's preload
// gives the page one object, `window.reeveDesktop`, to ask for things (`invoke`, by the command
// names in electron/main.ts) and to hear from it (`on`); nothing else of Electron reaches the page.

type Bridge = {
  invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
  on: (name: string, listener: (payload: unknown) => void) => () => void;
};

const bridge = (): Bridge | null => ((globalThis as any).reeveDesktop as Bridge | undefined) ?? null;

export const isDesktop = (): boolean => bridge() !== null;

// "macos" | "windows" | "linux": set by the shell before the app loads; the user agent covers a
// browser. The macOS overlay title bar (and its traffic-light room) must never apply on Windows,
// which draws its own title strip (alignment bug, caught on Windows 2026-07-21).
export const platformOS = (): string => {
  const injected = (globalThis as any).__REEVE_PLATFORM__;
  if (typeof injected === "string" && injected) return injected;
  return /mac/i.test(navigator.userAgent) ? "macos" : /win/i.test(navigator.userAgent) ? "windows" : "linux";
};

export type DictationStatus = {
  recording: boolean;
  model_installed: boolean;
  model_verified: boolean;
  test_passed: boolean;
  download_in_progress: boolean;
  model_name: string;
  model_bytes: number;
  supported: boolean;
  device_summary: string;
  compatibility_reason: string | null;
};

export type DictationDownloadProgress = {
  downloaded_bytes: number;
  total_bytes: number;
};

const invoke = async <T>(cmd: string, args?: Record<string, unknown>): Promise<T | null> => {
  const shell = bridge();
  if (!shell) return null;
  try {
    return (await shell.invoke(cmd, args)) as T;
  } catch {
    return null;
  }
};

const invokeStrict = async <T>(cmd: string, args?: Record<string, unknown>): Promise<T> => {
  const shell = bridge();
  if (!shell) throw new Error("This feature is available in the desktop app.");
  return (await shell.invoke(cmd, args)) as T;
};

/** Hear one of the shell's events. Returns the way to stop hearing it. */
export function onShellEvent<T>(name: string, cb: (payload: T) => void): () => void {
  const shell = bridge();
  if (!shell) return () => {};
  return shell.on(name, (payload) => cb(payload as T));
}

/** The native folder picker (desktop only). Returns the chosen path, or null. */
export async function pickFolder(): Promise<string | null> {
  const path = await invoke<string>("pick_folder");
  return typeof path === "string" && path ? path : null;
}

/** The folder picker that works everywhere: the shell's dialog in the desktop app, else the
 * dialog the local server opens (the browser GUI still gets a real picker, owner 2026-07-04). */
export async function chooseFolder(): Promise<string | null> {
  if (isDesktop()) return pickFolder();
  const { pickFolderViaServer } = await import("./api");
  return pickFolderViaServer();
}

/** Save text to a file the person picks, on every surface. The desktop app gets a native save
 * dialog (a download from the window may land anywhere); a browser gets an ordinary download.
 * Returns a path when one is known, "" for a browser download that started, null on cancel. */
export async function saveTextAnywhere(suggestedName: string, contents: string, mime = "application/json"): Promise<string | null> {
  if (isDesktop()) {
    const path = await invoke<string>("save_text_file", { suggestedName, contents });
    return typeof path === "string" && path ? path : null;
  }
  const url = URL.createObjectURL(new Blob([contents], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = suggestedName;
  a.click();
  URL.revokeObjectURL(url);
  return "";
}

/** Open at login. */
export const getAutostart = () => invoke<boolean>("get_autostart");
export const setAutostart = (enabled: boolean) => invoke<boolean>("set_autostart", { enabled });

/** Keep this machine awake so scheduled tasks fire while idle. Persists across restarts. */
export const getKeepAwake = () => invoke<boolean>("get_keep_awake");
export const setKeepAwake = (enabled: boolean) => invoke<boolean>("set_keep_awake", { enabled });

// Local dictation is desktop-only. A browser keeps it unavailable rather than quietly sending
// microphone audio to a server.
export const getDictationStatus = () => invoke<DictationStatus>("get_dictation_status");
/** Instantaneous mic loudness 0..1 while recording (0 otherwise), for the composer's waveform. */
export const getDictationLevel = () => invoke<number>("dictation_level");
export const startDictation = () => invokeStrict<DictationStatus>("start_dictation");
export const stopDictation = () => invokeStrict<string>("stop_dictation");
export const cancelDictation = () => invokeStrict<void>("cancel_dictation");
export const downloadDictationModel = () => invokeStrict<DictationStatus>("download_dictation_model");
export const cancelDictationModelDownload = () => invokeStrict<void>("cancel_dictation_model_download");
export const verifyDictationModel = () => invokeStrict<DictationStatus>("verify_dictation_model");
export const markDictationTestPassed = () => invokeStrict<DictationStatus>("mark_dictation_test_passed");
export const deleteDictationModel = () => invokeStrict<DictationStatus>("delete_dictation_model");

export async function listenDictationDownloadProgress(handler: (progress: DictationDownloadProgress) => void): Promise<() => void> {
  return onShellEvent<DictationDownloadProgress>("dictation-download-progress", handler);
}

// --- Updates (desktop only; a browser sees null / throws) ---------------------------------------

export type UpdateInfo = { version: string; notes: string };

/** Whether a newer release exists. null = up to date, channel unreachable, or not the desktop app. */
export const checkForUpdate = () => invoke<UpdateInfo | null>("check_for_update");
/** Fetch the offered release in the background so the install is instant. */
export const downloadUpdate = () => invokeStrict<void>("download_update");
/** Forget the fetched release ("Later"). */
export const clearPendingUpdate = () => invokeStrict<void>("clear_pending_update");
/** Install the offered release and restart into it. Resolves only when it could not. */
export const installUpdate = () => invokeStrict<void>("install_update");

// The schemes whose default application the OS may be asked for; any other URL is not ours to give.
const EXTERNAL_HREF_RE = /^(?:https?|mailto|tel):/i;

/** Open a URL in the person's browser: the shell in the desktop app, a tab in a browser. */
export function openExternal(url: string): void {
  const tab = () => window.open(url, "_blank", "noopener,noreferrer");
  const shell = bridge();
  if (!shell) {
    tab();
    return;
  }
  shell.invoke("open_external", { url }).catch(tab);
}

/** Send every external link out of the window. Installed once at boot, desktop only: in a browser
 * `target="_blank"` already opens a tab. An anchor counts when its raw href carries one of those
 * schemes; a relative, `artifact:` or `file:` link keeps its own handler, and so does anything that
 * already called preventDefault (this listens on `document` in the bubble phase, above React's
 * root, so React's handler has run first). Middle click is taken too. */
export function installExternalLinkRouter(): void {
  if (!isDesktop()) return;
  const route = (e: MouseEvent) => {
    if (e.defaultPrevented) return;
    if (e.button !== 0 && e.button !== 1) return;
    const anchor = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
    if (!anchor) return;
    const raw = anchor.getAttribute("href") ?? "";
    if (!EXTERNAL_HREF_RE.test(raw)) return;
    e.preventDefault();
    openExternal(anchor.href);
  };
  document.addEventListener("click", route);
  document.addEventListener("auxclick", route);
}

// -- frameless window controls (the TitleBar's – □ ×) --------------------------------------------
// Windows runs frameless; these drive the real window from the app's own title strip. Close goes
// through the shell's close handling, so × hides to the tray or asks, as the person chose.
export function minimizeWindow(): void {
  void invoke("window_minimize");
}
export function toggleMaximizeWindow(): void {
  void invoke("window_toggle_maximize");
}
export function closeWindow(): void {
  void invoke("window_close");
}

// -- closing and quitting -------------------------------------------------------------
// The shell asks before quitting would stop work, and remembers what closing the window does. The
// app knows what runs (its session list), so it reports that and answers the shell's requests.

/** "ask" until the person has chosen once, then "tray" or "quit". */
export type CloseBehavior = "ask" | "tray" | "quit";

export const getCloseBehavior = () => invoke<CloseBehavior>("get_close_behavior");
export const setCloseBehavior = (onClose: "tray" | "quit") => invoke<CloseBehavior>("set_close_behavior", { onClose });

/** What is running across the conversations, for the tray's line, its tooltip, and whether Quit
 *  has to ask. `titles` names the first of them. */
export const setTrayStatus = (running: number, needsYou: number, titles: string) =>
  invoke<void>("set_tray_status", { running, needsYou, titles });

export const quitApp = () => invoke<void>("quit_app");
export const setQuitWhenDone = (on: boolean) => invoke<boolean>("set_quit_when_done", { on });
export const closeToTray = () => invoke<void>("close_to_tray");
export const ackClose = () => invoke<void>("close_ack");

// -- the local server's health (the shell's supervisor) ------------------------------------------
// The shell watches the Python server and restarts it on the same port after a crash, inside a
// budget. A browser has no shell, so both are inert there and the app shows nothing.

export type SidecarState = {
  /** "started" | "crashed" | "restarting" | "stopped" */
  state: string;
  attempt: number;
  max_attempts: number;
  reason: string | null;
};

export const sidecarStatus = (): Promise<SidecarState | null> => invoke<SidecarState>("sidecar_status");

/** Start a stopped server again with a fresh budget (null in a browser, which has no shell). */
export const restartSidecar = (): Promise<SidecarState | null> => invoke<SidecarState>("sidecar_restart");
/** Show the server's log folder in the file manager (inert in a browser). */
export const openLogs = (): Promise<void | null> => invoke<void>("open_logs");
/** The last lines the server wrote to its log ("" in a browser, or when there is none). */
export const sidecarLogTail = async (): Promise<string> => (await invoke<string>("sidecar_log_tail")) ?? "";

/** Hear the supervisor's transitions. Returns the way to stop hearing them. */
export function onSidecarStatus(cb: (state: SidecarState) => void): () => void {
  return onShellEvent<SidecarState>("sidecar-status", cb);
}

// --- The Browser tab's pages (a `<webview>`) ---------------------------

/** Whether the Browser tab can show its page in a browser of its own (the desktop app). */
export const canEmbedBrowser = (): boolean => isDesktop();
/** Forget what the Browser tab's pages stored: cookies, logins, caches. */
export const browserClearData = () => invokeStrict<void>("browser_clear_data");
/** The profile the Browser tab's pages share (electron/browser.ts). */
export const BROWSER_PARTITION = "persist:reeve-browser";
