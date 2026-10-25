import { defineConfig, devices } from "@playwright/test";
import { SPEC_PORT, specServer } from "./playwright.config";

// The live specs (e2e-live/): the real server on :8765 and a real model, which is what the dev
// server talks to when nothing is mocked. A folder and config of their own, so `npm run e2e` and
// CI never run them. They spend tokens and their answers vary: they are run by hand
// (`npm run e2e:live`), one at a time, to see the whole way through work.
export default defineConfig({
  testDir: "./e2e-live",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  // A model and its tools take real time.
  timeout: 180_000,
  use: { baseURL: `http://localhost:${SPEC_PORT}`, trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: specServer(true),
});
