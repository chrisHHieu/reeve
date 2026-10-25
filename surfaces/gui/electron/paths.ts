// Where the shell keeps its things: the state folder the server also uses, the shell's
// own settings in it, the sidecar's log, and the sidecar itself.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { lastLines } from "./policy";

/** Mirror of `harness.safety.secrets.state_dir()`, so the shell and the server agree on the folder.
 *  `REEVE_STATE_DIR` overrides. */
export function stateDir(): string {
  if (process.env.REEVE_STATE_DIR) return process.env.REEVE_STATE_DIR;
  const base = process.platform === "win32" && process.env.APPDATA ? process.env.APPDATA : path.join(os.homedir(), ".config");
  return path.join(base, "Reeve");
}

const prefsPath = () => path.join(stateDir(), "desktop.json");

/** The shell's settings, one JSON object in `desktop.json`. */
export function readPrefs(): Record<string, unknown> {
  try {
    const value = JSON.parse(fs.readFileSync(prefsPath(), "utf8"));
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

/** Write one setting, merged into what is there: the file was once rewritten whole on every
 *  change, so a second setting would have been erased by the next toggle of the first. */
export function writePref(key: string, value: unknown): void {
  const file = prefsPath();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const prefs = readPrefs();
  prefs[key] = value;
  fs.writeFileSync(file, JSON.stringify(prefs));
}

/** The sidecar's log for this launch: `<state>/logs/reeve-server.log`, the previous run kept as
 *  `.old`. The old one is removed first because a rename onto an existing file fails on Windows,
 *  and the second rotation would then truncate the crashed run's log. Null when the folder cannot
 *  be made: logging never blocks startup. */
export function sidecarLog(): number | null {
  try {
    const dir = logsDir();
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, "reeve-server.log");
    if (fs.existsSync(file)) {
      const old = `${file}.old`;
      fs.rmSync(old, { force: true });
      fs.renameSync(file, old);
    }
    return fs.openSync(file, "w");
  } catch {
    return null;
  }
}

/** Where the server's logs are: the folder "Open the log folder" opens. */
export function logsDir(): string {
  return path.join(stateDir(), "logs");
}

/** The end of this launch's server log (`sidecarLog`), or "" when there is none to read. Read
 *  from the file's last 16 KB: a log that ran for days is not loaded whole to quote its tail. */
export function sidecarLogTail(count = 30): string {
  try {
    const file = path.join(logsDir(), "reeve-server.log");
    const size = fs.statSync(file).size;
    const length = Math.min(size, 16 * 1024);
    const fd = fs.openSync(file, "r");
    try {
      const buffer = Buffer.alloc(length);
      fs.readSync(fd, buffer, 0, length, size - length);
      return lastLines(buffer.toString("utf8"), count);
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return "";
  }
}

/** The server to start: `REEVE_SERVER_BIN`, else the bundled one beside the app (packaged), else
 *  the repository's own venv (a dev run from `surfaces/gui`). */
export function serverBin(resourcesPath: string, packaged: boolean, appPath: string): string {
  if (process.env.REEVE_SERVER_BIN) return process.env.REEVE_SERVER_BIN;
  const exe = process.platform === "win32" ? "reeve-server.exe" : "reeve-server";
  if (packaged) return path.join(resourcesPath, "sidecar", exe);
  const repo = path.resolve(appPath, "..", "..");
  return process.platform === "win32"
    ? path.join(repo, ".venv", "Scripts", exe)
    : path.join(repo, ".venv", "bin", exe);
}
