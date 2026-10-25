import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

/** The server's state folder (`harness.safety.secrets.state_dir`). */
function stateFolder(): string {
  if (process.env.REEVE_STATE_DIR) return process.env.REEVE_STATE_DIR;
  const base = process.platform === "win32" ? process.env.APPDATA || os.homedir() : path.join(os.homedir(), ".config");
  return path.join(base, "Reeve");
}

/** The token in `file`, or "" while no server has written one. */
function readToken(file: string): string {
  try {
    return fs.readFileSync(file, "utf8").trim();
  } catch {
    return "";
  }
}

// Built asset URLs are relative (`base: "./"`), so the bundle loads from the desktop shell's own
// `reeve://app` origin as well as from a server. The dev server is always on 1420
// (strictPort): the shell's dev window loads that address (electron/main.ts's DEV_URL), and a port
// that wandered would load some other server.
export default defineConfig(({ command }) => {
  const tokenFile = command === "serve" ? path.join(stateFolder(), "sidecar-8765.token") : "";
  return {
    base: "./",
    plugins: [
      react(),
      {
        // The server writes a new token each time it starts, and `define` keeps the one there was
        // when Vite started. A restarted server then met a dead token on every call, which the
        // boot read as "not up yet", retrying for twenty seconds over a blank page. The page asks
        // here for the current token instead, so a server restart costs a reload. Dev only: a
        // build never runs `configureServer`. With no server yet the answer is an empty token,
        // not an error, and the page keeps the one it was built with and its retry.
        name: "reeve-dev-token",
        configureServer(server) {
          server.middlewares.use("/__dev/token", (_req, res) => {
            res.setHeader("Content-Type", "application/json");
            res.setHeader("Cache-Control", "no-store");
            res.end(JSON.stringify({ token: readToken(tokenFile) }));
          });
        },
      },
    ],
    server: { port: 1420, strictPort: true },
    // Recharts comes with Metrics, on its first open. Found only then, it was bundled during that
    // request and the page reloaded under the reader (and under two specs that opened Metrics
    // first), so it is bundled at the start.
    optimizeDeps: { include: ["recharts"] },
    // A worker that loads a library when it needs it (the spreadsheet reader, `rail/sheet/`) has to
    // be built as a module: in the default format, one file, the build will not split it.
    worker: { format: "es" },
    // The token on disk when Vite started, for a page that cannot reach `/__dev/token`. The
    // desktop shell hands over its own at run time.
    define: { __REEVE_DEV_TOKEN__: JSON.stringify(tokenFile ? readToken(tokenFile) : "") },
    clearScreen: false,
    envPrefix: ["VITE_"],
  };
});
