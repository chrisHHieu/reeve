import { defineConfig, devices } from "@playwright/test";

// The end-to-end specs (e2e/). Each page has the server mocked before it loads (e2e/fixtures.ts),
// so no Python runs and nothing real is changed: safe in CI, and the same on every machine.

/** Vite for the specs, on a port of its own so a running `npm run dev` (:1420) is left alone. */
export const SPEC_PORT = 5199;
export const specServer = (reuse: boolean) => ({
  command: `npm run dev -- --port ${SPEC_PORT} --strictPort`,
  url: `http://localhost:${SPEC_PORT}`,
  reuseExistingServer: reuse,
  timeout: 120_000,
});

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : [["list"]],
  use: { baseURL: `http://localhost:${SPEC_PORT}`, trace: "on-first-retry" },
  // A window with room for the queue beside the side panel, which the mock opens: under 1500px an
  // open panel folds the queue into the rail (app/navLayout.ts). Specs about laptop windows set
  // their own size.
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 900 } } }],
  webServer: specServer(!process.env.CI),
});
