// Local dictation. The engine is the Rust `reeve-stt` crate, which Electron cannot link,
// so it runs as its own small program, `reeve-stt-host`, started on first use and spoken to in JSON
// lines: a request `{id, cmd}` is answered `{id, ok, value | error}`, and a model download reports
// `{event: "progress", downloaded_bytes, total_bytes}` as it goes.
import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";

type Pending = { resolve: (value: unknown) => void; reject: (error: Error) => void };

export class DictationHost {
  private child: ChildProcess | null = null;
  private next = 1;
  private pending = new Map<number, Pending>();

  constructor(
    private readonly bin: string,
    private readonly modelDir: string,
    private readonly onProgress: (progress: { downloaded_bytes: number; total_bytes: number }) => void,
  ) {}

  available(): boolean {
    return fs.existsSync(this.bin);
  }

  private ensure(): ChildProcess {
    if (this.child && this.child.exitCode === null) return this.child;
    const child = spawn(this.bin, ["--models", this.modelDir], { stdio: ["pipe", "pipe", "ignore"], windowsHide: true });
    const lines = readline.createInterface({ input: child.stdout! });
    lines.on("line", (line) => {
      let message: { id?: number; ok?: boolean; value?: unknown; error?: string; event?: string; downloaded_bytes?: number; total_bytes?: number };
      try {
        message = JSON.parse(line);
      } catch {
        return;
      }
      if (message.event === "progress") {
        this.onProgress({ downloaded_bytes: message.downloaded_bytes ?? 0, total_bytes: message.total_bytes ?? 0 });
        return;
      }
      const waiting = message.id === undefined ? undefined : this.pending.get(message.id);
      if (!waiting) return;
      this.pending.delete(message.id!);
      if (message.ok) waiting.resolve(message.value);
      else waiting.reject(new Error(message.error || "Voice Input failed"));
    });
    child.once("exit", () => {
      for (const waiting of this.pending.values()) waiting.reject(new Error("Voice Input stopped unexpectedly"));
      this.pending.clear();
      if (this.child === child) this.child = null;
    });
    this.child = child;
    return child;
  }

  call<T>(cmd: string): Promise<T> {
    if (!this.available()) return Promise.reject(new Error("Voice Input is not part of this build."));
    const child = this.ensure();
    const id = this.next++;
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (value: unknown) => void, reject });
      child.stdin!.write(`${JSON.stringify({ id, cmd })}\n`);
    });
  }

  /** The status the app shows, or an honest "not available" without a host. */
  async status(): Promise<unknown> {
    if (!this.available()) {
      return {
        recording: false, model_installed: false, model_verified: false, test_passed: false, download_in_progress: false,
        model_name: "", model_bytes: 0, supported: false, device_summary: `${process.platform} · ${process.arch}`,
        compatibility_reason: "Voice Input is not part of this build.",
      };
    }
    return this.call("status");
  }

  stop(): void {
    this.child?.kill();
    this.child = null;
  }
}

/** Where the host is: beside the app when packaged, the crate's build output in a dev run. */
export function dictationHostBin(resourcesPath: string, packaged: boolean, appPath: string): string {
  const exe = process.platform === "win32" ? "reeve-stt-host.exe" : "reeve-stt-host";
  if (packaged) return path.join(resourcesPath, exe);
  return path.resolve(appPath, "..", "..", "stt", "target", "release", exe);
}
