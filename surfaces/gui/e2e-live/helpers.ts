import { readFileSync, readdirSync, statSync } from "fs";
import { homedir } from "os";
import { join } from "path";
import type { Page } from "@playwright/test";

// What the live specs share: reaching the real server on :8765 with its token, finding out
// whether a model is ready, finding the file a session wrote, and the few steps in the app every
// live spec takes. Kept apart from the hermetic specs (its own folder and config; e2e/README.md).

export const BACKEND = "http://127.0.0.1:8765";

/** The token the server wrote for this launch, from its state folder. */
function launchToken(): string {
  const base = process.platform === "win32" ? process.env.APPDATA || homedir() : join(homedir(), ".config");
  const folder = process.env.REEVE_STATE_DIR || join(base, "Reeve");
  try {
    return readFileSync(join(folder, "sidecar-8765.token"), "utf8").trim();
  } catch {
    return "";
  }
}

/** A request to the real server, with its token. */
export function backendFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = launchToken();
  if (token) headers.set("X-Reeve-Token", token);
  return fetch(`${BACKEND}${path}`, { ...init, headers });
}

/** Whether the server answers at all. */
export async function backendUp(): Promise<boolean> {
  try {
    return (await backendFetch("/v1/health")).ok;
  } catch {
    return false;
  }
}

/** The scratch folder, `~` expanded, when the server is up with a model ready; null when it is
 *  not, and the spec skips. */
export async function scratchBaseIfReady(): Promise<string | null> {
  try {
    const res = await backendFetch("/v1/settings");
    const settings = await res.json();
    if (res.ok && settings.model_ready) return String(settings.scratch_base || "~/Reeve").replace(/^~(?=\/|$)/, homedir());
  } catch {
    // The server cannot be reached.
  }
  return null;
}

/** The newest `name` among the session folders under the scratch folder (each session has one). */
export function newestFile(scratchBase: string, name: string): string | null {
  let sessions: string[];
  try {
    sessions = readdirSync(scratchBase);
  } catch {
    return null;
  }
  let newest: { path: string; at: number } | null = null;
  for (const session of sessions) {
    const path = join(scratchBase, session, name);
    try {
      const at = statSync(path).mtimeMs;
      if (!newest || at > newest.at) newest = { path, at };
    } catch {
      // Not written in this session.
    }
  }
  return newest?.path ?? null;
}

/** Where a session wrote `name`: its newest copy under the scratch folder, else the server's
 *  default folder. A session opens in the folder the app used last, which for a dev server started
 *  with `--cwd` is that folder, not a scratch one. */
export async function writtenFile(scratchBase: string, name: string): Promise<string | null> {
  const inScratch = newestFile(scratchBase, name);
  if (inScratch) return inScratch;
  try {
    const { default_workspace } = await (await backendFetch("/v1/health")).json();
    if (!default_workspace) return null;
    const path = join(String(default_workspace), name);
    statSync(path);
    return path;
  } catch {
    return null;
  }
}

/** A new session, ready to type in. There is one agent: nothing to pick first. */
export async function startSession(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "New session" }).first().click();
  await page.getByPlaceholder(/Ask anything/).waitFor();
}

/** Set the composer's permission preset from its Mode menu, or turn Plan on, which is a switch
 *  in the same menu and not a preset. An option is found by the start of its name: the chosen one
 *  carries a ✓ in its label, so its exact text is not the preset's name. */
export async function selectMode(page: Page, choice: "Full access" | "Workspace write" | "Read only" | "Plan") {
  await page.getByRole("button", { name: "Mode", exact: true }).click();
  const menu = page.getByTestId("mode-menu");
  if (choice === "Plan") await menu.getByRole("switch", { name: "Plan" }).click();
  else await menu.getByRole("button", { name: new RegExp(`^${choice}\\b`) }).click();
}

/** Type a task and send it. */
export async function sendTask(page: Page, text: string) {
  await page.getByPlaceholder(/Ask anything/).fill(text);
  await page.getByRole("button", { name: "Send", exact: true }).click();
}
