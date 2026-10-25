// The Python server, started and watched by the shell (the Tauri shell's supervisor
// before it). It runs on a free port with a per-launch token, restarts on the same port and token
// after a crash inside a bounded budget (`policy.ts`), reports every transition, and stops politely
// on quit: `POST /v1/shutdown` runs the same teardown Ctrl+C does, then a kill after a short grace.
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import { HEALTHY_UPTIME_MS, MAX_RESTARTS, nextRestart, type SidecarState } from "./policy";
import { sidecarLog } from "./paths";

export function freePort(): Promise<number> {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(8765));
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 8765;
      server.close(() => resolve(port));
    });
  });
}

export class Sidecar {
  private child: ChildProcess | null = null;
  private startedAt = 0;
  private stopping = false;
  private current: SidecarState = { state: "started", attempt: 0, max_attempts: MAX_RESTARTS, reason: null };

  constructor(
    readonly port: number,
    readonly token: string,
    private readonly bin: string,
    private readonly env: Record<string, string>,
    /** Told every transition, for the app's banner. */
    private readonly report: (state: SidecarState) => void,
  ) {}

  state(): SidecarState {
    return { ...this.current };
  }

  private set(next: SidecarState, tell = true): void {
    this.current = next;
    if (tell) this.report(this.state());
  }

  /** Start it: the first launch and every restart, so the two cannot differ. */
  start(): void {
    if (!fs.existsSync(this.bin)) throw new Error(`the server was not found at ${this.bin}`);
    const log = sidecarLog();
    const out = log ?? "ignore";
    const child = spawn(this.bin, ["--host", "127.0.0.1", "--port", String(this.port)], {
      env: {
        ...process.env,
        // The person's login-shell environment first, so the explicit REEVE_* values win.
        ...this.env,
        // It leaves if the shell dies abruptly; the pid matters because a bootloader may sit
        // between this process and the Python one.
        REEVE_EXIT_WITH_PARENT: "1",
        REEVE_PARENT_PID: String(process.pid),
        REEVE_API_TOKEN: this.token,
      },
      // A GUI app has no console: the server gets real handles (its log) and no console window.
      stdio: ["ignore", out, out],
      windowsHide: true,
      // Its own process group on POSIX, so a stop ends what it started too (`killTree`).
      detached: process.platform !== "win32",
    });
    if (log !== null) fs.closeSync(log);
    this.child = child;
    this.startedAt = Date.now();
    child.once("exit", (code) => this.exited(child, code));
    child.once("error", () => this.exited(child, null));
  }

  private exited(child: ChildProcess, code: number | null): void {
    if (this.child !== child) return;
    this.child = null;
    if (this.stopping) return; // our own stop, not a crash
    if (Date.now() - this.startedAt >= HEALTHY_UPTIME_MS) {
      // A long run, a fresh budget: the count describes a crash loop, not a lifetime.
      this.set({ state: "started", attempt: 0, max_attempts: MAX_RESTARTS, reason: null }, false);
    }
    const attempt = this.current.attempt;
    this.report({ state: "crashed", attempt, max_attempts: MAX_RESTARTS, reason: code === null ? null : `exit code ${code}` });
    const next = nextRestart(attempt);
    if (!next) {
      this.set({ state: "stopped", attempt, max_attempts: MAX_RESTARTS, reason: `crashed ${MAX_RESTARTS} times in a row` });
      return;
    }
    this.set({ state: "restarting", attempt: next.attempt, max_attempts: MAX_RESTARTS, reason: null });
    setTimeout(() => {
      if (this.stopping) return;
      try {
        this.start();
        // Same port and token: the app's endpoints stay valid, so it reconnects instead of reloading.
        this.set({ state: "started", attempt: next.attempt, max_attempts: MAX_RESTARTS, reason: null });
      } catch (error) {
        this.set({ state: "stopped", attempt: next.attempt, max_attempts: MAX_RESTARTS, reason: `could not start it again: ${String(error)}` });
      }
    }, next.delayMs);
  }

  /** Start it again on the person's word once it has stopped for good (a crash loop, a start that
   *  failed): a fresh budget, the same port and token. One still running is left alone. The app's
   *  "Try again" used to reload the window, which never brought a stopped server back. */
  relaunch(): SidecarState {
    if (this.child && this.child.exitCode === null) return this.state();
    this.stopping = false;
    this.set({ state: "restarting", attempt: 0, max_attempts: MAX_RESTARTS, reason: null });
    try {
      this.start();
      this.set({ state: "started", attempt: 0, max_attempts: MAX_RESTARTS, reason: null });
    } catch (error) {
      this.set({ state: "stopped", attempt: 0, max_attempts: MAX_RESTARTS, reason: `could not start it: ${String(error)}` });
    }
    return this.state();
  }

  /** The first start, reported as a stop when it fails, so the app says so instead of waiting. */
  launch(): void {
    try {
      this.start();
    } catch (error) {
      this.set({ state: "stopped", attempt: 0, max_attempts: MAX_RESTARTS, reason: `could not start it: ${String(error)}` }, false);
    }
  }

  /** Ask it to stop, wait up to `graceMs` for it to leave, then end it and what it started. */
  async stop(graceMs = 2000): Promise<void> {
    this.stopping = true;
    const child = this.child;
    if (!child || child.exitCode !== null) return;
    const left = new Promise<void>((resolve) => child.once("exit", () => resolve()));
    await requestShutdown(this.port, this.token).catch(() => undefined);
    const inTime = await Promise.race([left.then(() => true), new Promise<boolean>((r) => setTimeout(() => r(false), graceMs))]);
    if (!inTime && child.pid) killTree(child.pid);
  }
}

function requestShutdown(port: number, token: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = http.request(
      { host: "127.0.0.1", port, path: "/v1/shutdown", method: "POST", headers: { "x-reeve-token": token, "content-length": 0 }, timeout: 1000 },
      (response) => {
        response.resume();
        resolve();
      },
    );
    request.on("error", reject);
    request.on("timeout", () => request.destroy(new Error("timeout")));
    request.end();
  });
}

/** End a process and the ones it started. */
export function killTree(pid: number): void {
  try {
    if (process.platform === "win32") spawnSync("taskkill", ["/T", "/F", "/PID", String(pid)], { windowsHide: true });
    else process.kill(-pid, "SIGKILL");
  } catch {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      /* already gone */
    }
  }
}
