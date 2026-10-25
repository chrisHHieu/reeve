// Updates (the Tauri updater before it). The app asks whether a newer release exists,
// fetches it in the background as soon as one is offered so "Restart to update" is instant, and
// installs it, keeping a copy of the version being replaced. The feed is the release channel
// electron-builder publishes (`latest.yml`, each file named with its sha512): the newest GitHub
// release, whose `latest/download/` serves its files by name. `REEVE_UPDATE_URL` points it elsewhere
// (the local channel `scripts/update_channel.ps1` serves is http://127.0.0.1:8899/).
import { app } from "electron";
import fs from "node:fs";
import path from "node:path";
import { autoUpdater } from "electron-updater";
import { stateDir } from "./paths";

const FEED = process.env.REEVE_UPDATE_URL || "https://github.com/chrisHHieu/reeve/releases/latest/download/";

let configured = false;
let downloaded: string | null = null;

function updater() {
  if (!configured) {
    autoUpdater.autoDownload = false;
    autoUpdater.autoInstallOnAppQuit = false;
    autoUpdater.setFeedURL({ provider: "generic", url: FEED });
    configured = true;
  }
  return autoUpdater;
}

function newer(version: string): boolean {
  const a = version.split(/[.+-]/).map((part) => Number.parseInt(part, 10) || 0);
  const b = app.getVersion().split(/[.+-]/).map((part) => Number.parseInt(part, 10) || 0);
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if ((a[i] ?? 0) !== (b[i] ?? 0)) return (a[i] ?? 0) > (b[i] ?? 0);
  }
  return false;
}

/** A build of the private repository says so in its package.json (`reeveChannel: "private"`, set
 *  by packaging/build_windows.ps1 when the connectors' client ids are in the tree). It never
 *  updates: every release on the feed is a public build, and installing one would take those ids
 *  away. */
function privateBuild(): boolean {
  try {
    const pkg = JSON.parse(fs.readFileSync(path.join(app.getAppPath(), "package.json"), "utf8"));
    return pkg.reeveChannel === "private";
  } catch {
    return false;
  }
}

/** A newer release, or null when up to date, when the channel cannot be reached, in a dev run, or
 *  in a private build. */
export async function checkForUpdate(): Promise<{ version: string; notes: string } | null> {
  if (!app.isPackaged || privateBuild()) return null;
  try {
    const result = await updater().checkForUpdates();
    const info = result?.updateInfo;
    if (!info || !newer(info.version)) return null;
    const notes = typeof info.releaseNotes === "string" ? info.releaseNotes : "";
    return { version: info.version, notes };
  } catch {
    return null;
  }
}

/** Fetch the offered release now; the same version again is a no-op. */
export async function downloadUpdate(): Promise<void> {
  const offered = await checkForUpdate();
  if (!offered) throw new Error("no update available");
  if (downloaded === offered.version) return;
  await updater().downloadUpdate();
  downloaded = offered.version;
}

/** Forget what was fetched ("Later"): changing one's mind fetches again. */
export function clearPendingUpdate(): void {
  downloaded = null;
}

/** Copy the running app aside before an update replaces it, keeping the newest two, so a version
 *  that does not start leaves the previous one on disk. Restoring it is not done here:
 *  overwriting a running app is the one thing the OS forbids. */
function backupBeforeUpdate(): void {
  const from = path.dirname(app.getPath("exe"));
  const dir = path.join(stateDir(), "rollback");
  const target = path.join(dir, `${app.getVersion()}-${Math.floor(Date.now() / 1000)}`);
  try {
    fs.cpSync(from, target, { recursive: true, verbatimSymlinks: true, filter: (src) => !fs.lstatSync(src).isSymbolicLink() });
  } catch {
    return;
  }
  const backups = fs.readdirSync(dir).sort();
  while (backups.length > 2) fs.rmSync(path.join(dir, backups.shift()!), { recursive: true, force: true });
}

export function rollbackBackupPath(): string | null {
  try {
    const dir = path.join(stateDir(), "rollback");
    const backups = fs.readdirSync(dir).sort();
    return backups.length ? path.join(dir, backups[backups.length - 1]) : null;
  } catch {
    return null;
  }
}

/** Install the offered release and restart into it. Resolves only when it could not. */
export async function installUpdate(beforeQuit: () => void): Promise<void> {
  await downloadUpdate();
  backupBeforeUpdate();
  beforeQuit();
  updater().quitAndInstall(true, true);
}
