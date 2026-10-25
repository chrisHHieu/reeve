// The environment the sidecar runs with (from the Tauri shell's).
//
// An app opened from the Dock or Finder inherits launchd's minimal PATH, so every tool the person
// installed through Homebrew, nvm, pyenv or asdf is invisible to the agent. The login shell is
// asked for its environment once, as VS Code does, and merged in. Guards: `-i` as well as `-l`
// (brew and nvm init usually live in .zshrc); markers, so a chatty profile's own output is not
// read as variables; five seconds, so a hanging profile never blocks the launch; and the usual
// install folders added to PATH whatever happens. Skipped when launched from a shell (SHLVL set):
// the environment is real already. Windows apps inherit the person's environment and skip it.
import { spawn } from "node:child_process";
import fs from "node:fs";

const START = "__REEVE_ENV_START__";
const END = "__REEVE_ENV_END__";
const KNOWN_TOOL_DIRS = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "/usr/local/sbin", "/opt/local/bin"];

/** What `env` printed between the markers, as variables; continuation lines are skipped. */
export function parseEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  let inside = false;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trimEnd();
    if (line === START) {
      inside = true;
      continue;
    }
    if (line === END) break;
    if (!inside) continue;
    const at = line.indexOf("=");
    if (at <= 0) continue;
    const key = line.slice(0, at);
    if (/\s/.test(key)) continue;
    out[key] = line.slice(at + 1);
  }
  for (const key of ["SHLVL", "PWD", "OLDPWD", "_"]) delete out[key];
  return out;
}

export async function loginShellEnv(): Promise<Record<string, string>> {
  if (process.platform === "win32" || process.env.SHLVL) return {};
  const shell = process.env.SHELL || "/bin/zsh";
  const text = await new Promise<string>((resolve) => {
    let out = "";
    let child;
    try {
      child = spawn(shell, ["-ilc", `echo ${START}; env; echo ${END}`], { stdio: ["ignore", "pipe", "ignore"] });
    } catch {
      resolve("");
      return;
    }
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      resolve("");
    }, 5000);
    child.stdout.on("data", (chunk: Buffer) => (out += chunk.toString("utf8")));
    child.on("error", () => {
      clearTimeout(timer);
      resolve("");
    });
    child.on("close", () => {
      clearTimeout(timer);
      resolve(out);
    });
  });
  const env = parseEnv(text);
  const parts = (env.PATH ?? process.env.PATH ?? "").split(":").filter(Boolean);
  for (const dir of KNOWN_TOOL_DIRS) {
    if (!parts.includes(dir) && fs.existsSync(dir)) parts.push(dir);
  }
  env.PATH = parts.join(":");
  return env;
}
