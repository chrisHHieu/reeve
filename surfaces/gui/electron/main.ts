// Reeve's desktop shell. A window over the React app that:
//   1. starts the Python server on a free port with a per-launch token, and watches it;
//   2. hands the app the server's address and token before the app loads (preload.ts);
//   3. lives in the tray: closing the window hides it or quits, as the person chose;
//   4. answers the app's native requests (folder picker, save, open at login, keep awake,
//      updates, dictation, window controls) over one IPC channel, by the names the Tauri
//      shell used, so the app's bridge (src/desktop.ts) did not have to learn new ones.
// The Browser tab's pages are `<webview>`s, locked down in browser.ts.
import { app, BrowserWindow, dialog, ipcMain, net, powerSaveBlocker, protocol, shell, type WebContents } from "electron";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { clearBrowserData, watchContents } from "./browser";
import { DictationHost, dictationHostBin } from "./dictation";
import { logsDir, readPrefs, serverBin, sidecarLogTail, stateDir, writePref } from "./paths";
import { closeBehavior, externalAllowed } from "./policy";
import { loginShellEnv } from "./shellEnv";
import { freePort, Sidecar } from "./sidecar";
import { checkForUpdate, clearPendingUpdate, downloadUpdate, installUpdate, rollbackBackupPath } from "./updates";
import { Shell } from "./window";

/** The app's own origin in a packaged build: a scheme of its own, so the server can tell the app's
 *  requests from a web page's (`harness/interface/server/app/access.py`). */
const SCHEME = "reeve";
const APP_ORIGIN = `${SCHEME}://app`;
const DEV_URL = process.env.REEVE_DEV_URL || "http://localhost:1420";

// The shell's own data (Chromium's caches, the Browser tab's profile) stays out of the state folder
// the server owns (%APPDATA%\Reeve), which Electron would otherwise share by the product's name.
app.setPath(
  "userData",
  process.platform === "win32" && process.env.LOCALAPPDATA
    ? path.join(process.env.LOCALAPPDATA, "Reeve Desktop")
    : path.join(app.getPath("appData"), "Reeve Desktop"),
);

protocol.registerSchemesAsPrivileged([
  { scheme: SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true } },
]);

const iconDir = path.join(__dirname, "..", "electron", "icons");
const shellState = new Shell(iconDir);
let sidecar: Sidecar | null = null;
let dictation: DictationHost | null = null;
let keepAwake: number | null = null;

const platformName = () => (process.platform === "darwin" ? "macos" : process.platform === "win32" ? "windows" : "linux");

function startKeepAwake(): void {
  if (keepAwake === null) keepAwake = powerSaveBlocker.start("prevent-app-suspension");
}

function stopKeepAwake(): void {
  if (keepAwake !== null) powerSaveBlocker.stop(keepAwake);
  keepAwake = null;
}

/** Whether a request came from the app's own page (not a `<webview>`, not a popup). */
function fromApp(contents: WebContents): boolean {
  return !!shellState.window && contents === shellState.window.webContents;
}

type Handler = (args: Record<string, unknown>) => unknown;

const commands: Record<string, Handler> = {
  pick_folder: async () => {
    const result = await dialog.showOpenDialog(shellState.window!, { properties: ["openDirectory", "createDirectory"] });
    return result.canceled ? null : (result.filePaths[0] ?? null);
  },
  // The path comes from the dialog, never from the app: the app hands a suggested name and the
  // text, nothing else. A path from the page would make this an arbitrary-write primitive.
  save_text_file: async ({ suggestedName, contents }) => {
    const result = await dialog.showSaveDialog(shellState.window!, { defaultPath: String(suggestedName || "export.txt") });
    if (result.canceled || !result.filePath) return null;
    fs.writeFileSync(result.filePath, String(contents ?? ""));
    return result.filePath;
  },
  get_autostart: () => app.getLoginItemSettings().openAtLogin,
  set_autostart: ({ enabled }) => {
    app.setLoginItemSettings({ openAtLogin: !!enabled });
    return app.getLoginItemSettings().openAtLogin;
  },
  get_keep_awake: () => keepAwake !== null,
  set_keep_awake: ({ enabled }) => {
    if (enabled) startKeepAwake();
    else stopKeepAwake();
    writePref("keep_awake", keepAwake !== null);
    return keepAwake !== null;
  },
  // Moving the window is the drag regions' (`-webkit-app-region`), not a command's.
  start_window_drag: () => false,
  window_minimize: () => shellState.window?.minimize(),
  window_toggle_maximize: () => {
    const win = shellState.window;
    if (!win) return;
    if (win.isMaximized()) win.unmaximize();
    else win.maximize();
  },
  window_close: () => shellState.window?.close(),
  open_external: ({ url }) => {
    const target = String(url || "");
    if (!externalAllowed(target)) throw new Error("not an address the app may open");
    return shell.openExternal(target);
  },
  get_dictation_status: () => dictation!.status(),
  start_dictation: () => dictation!.call("start"),
  stop_dictation: () => dictation!.call("stop"),
  cancel_dictation: () => dictation!.call("cancel"),
  download_dictation_model: () => dictation!.call("download"),
  cancel_dictation_model_download: () => dictation!.call("cancel_download"),
  verify_dictation_model: () => dictation!.call("verify"),
  mark_dictation_test_passed: () => dictation!.call("mark_test_passed"),
  delete_dictation_model: () => dictation!.call("delete"),
  dictation_level: () => (dictation!.available() ? dictation!.call("level") : 0),
  check_for_update: () => checkForUpdate(),
  download_update: () => downloadUpdate(),
  clear_pending_update: () => clearPendingUpdate(),
  install_update: () => installUpdate(() => (shellState.quitting = true)),
  rollback_backup_path: () => rollbackBackupPath(),
  sidecar_status: () => sidecar?.state() ?? null,
  // The boot screen's way out when the server did not start: start it again with a fresh
  // budget, show its log folder, and quote the end of its log for "Copy details".
  sidecar_restart: () => sidecar?.relaunch() ?? null,
  open_logs: () => shell.openPath(logsDir()),
  sidecar_log_tail: () => sidecarLogTail(30),
  set_tray_status: ({ running, needsYou, titles }) =>
    shellState.setStatus(Number(running) || 0, Number(needsYou) || 0, String(titles ?? "")),
  get_close_behavior: () => closeBehavior(readPrefs().on_close),
  set_close_behavior: ({ onClose }) => {
    if (onClose === "tray" || onClose === "quit") writePref("on_close", onClose);
    return closeBehavior(readPrefs().on_close);
  },
  quit_app: () => shellState.quit(),
  set_quit_when_done: ({ on }) => shellState.setQuitWhenDone(!!on),
  close_to_tray: () => shellState.hideToTray(),
  close_ack: () => shellState.ackClose(),
  browser_clear_data: () => clearBrowserData(),
};

function registerIpc(config: { http: string; ws: string; token: string; platform: string }): void {
  ipcMain.on("reeve:config", (event) => {
    event.returnValue = fromApp(event.sender) ? config : null;
  });
  ipcMain.handle("reeve:invoke", async (event, cmd: string, args?: Record<string, unknown>) => {
    if (!fromApp(event.sender)) throw new Error("not the app");
    const handler = Object.prototype.hasOwnProperty.call(commands, cmd) ? commands[cmd] : undefined;
    if (!handler) throw new Error(`unknown command: ${cmd}`);
    return handler(args ?? {});
  });
}

function serveApp(): void {
  const root = path.join(__dirname, "..", "dist");
  protocol.handle(SCHEME, (request) => {
    const url = new URL(request.url);
    const wanted = path.normalize(path.join(root, decodeURIComponent(url.pathname)));
    const file = wanted.startsWith(root) && fs.existsSync(wanted) && fs.statSync(wanted).isFile() ? wanted : path.join(root, "index.html");
    return net.fetch(pathToFileURL(file).toString());
  });
}

function createWindow(): BrowserWindow {
  const mac = process.platform === "darwin";
  const win = new BrowserWindow({
    title: "Reeve",
    width: 1360,
    height: 900,
    minWidth: 980,
    minHeight: 640,
    show: false,
    icon: path.join(iconDir, process.platform === "win32" ? "icon.ico" : "icon.png"),
    // Windows runs frameless: the app's own top strip carries the drag region and – □ ×.
    // macOS keeps its traffic lights floating over the edge-to-edge app.
    ...(mac ? { titleBarStyle: "hiddenInset" as const, trafficLightPosition: { x: 19, y: 24 } } : { frame: false }),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webviewTag: true,
      spellcheck: false,
    },
  });
  shellState.window = win;
  shellState.watchClose(win);
  win.once("ready-to-show", () => win.show());
  // Links leave the app for the person's browser; the app's own page never navigates away.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (externalAllowed(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    const own = app.isPackaged ? url.startsWith(`${APP_ORIGIN}/`) : url.startsWith(DEV_URL);
    if (!own) event.preventDefault();
  });
  if (app.isPackaged) void win.loadURL(`${APP_ORIGIN}/index.html`);
  else void win.loadURL(DEV_URL);
  return win;
}

async function start(): Promise<void> {
  const port = await freePort();
  const token = crypto.randomBytes(32).toString("hex");
  const config = {
    http: `http://127.0.0.1:${port}`,
    ws: `ws://127.0.0.1:${port}`,
    token,
    platform: platformName(),
  };
  registerIpc(config);
  const env = await loginShellEnv();
  sidecar = new Sidecar(port, token, serverBin(process.resourcesPath, app.isPackaged, app.getAppPath()), env, (state) =>
    shellState.send("sidecar-status", state),
  );
  sidecar.launch();
  dictation = new DictationHost(
    dictationHostBin(process.resourcesPath, app.isPackaged, app.getAppPath()),
    path.join(stateDir(), "models"),
    (progress) => shellState.send("dictation-download-progress", progress),
  );
  if (readPrefs().keep_awake === true) startKeepAwake();
  if (app.isPackaged) serveApp();
  watchContents(fromApp);
  createWindow();
  shellState.createTray();
}

if (!app.requestSingleInstanceLock()) {
  // A second launch brings the running app forward and leaves before it can start a second server.
  app.quit();
} else {
  app.on("second-instance", () => shellState.show());
  app.on("activate", () => shellState.show());
  // The tray keeps the app alive with no window on screen.
  app.on("window-all-closed", () => undefined);
  let stopped = false;
  app.on("before-quit", (event) => {
    shellState.quitting = true;
    if (stopped) return;
    event.preventDefault();
    stopKeepAwake();
    dictation?.stop();
    void (sidecar?.stop(2000) ?? Promise.resolve()).finally(() => {
      stopped = true;
      app.quit();
    });
  });
  void app.whenReady().then(start);
}
