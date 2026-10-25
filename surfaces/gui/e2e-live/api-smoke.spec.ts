// The real server's answers, read for their shape: what the GUI reads off /v1/health and
// /v1/providers, so a mock that has drifted from the server shows here. No model is called and no
// key is needed; with no server on :8765 the specs skip, so this can run any time.
import { expect, test } from "@playwright/test";
import { backendFetch, backendUp } from "./helpers";

test.beforeEach(async () => {
  test.skip(!(await backendUp()), "no server on :8765");
});

test("health reports ok with the fields the GUI reads", async () => {
  const health = await (await backendFetch("/v1/health")).json();
  expect(health.status).toBe("ok");
  // The boot screen reads these beside the status.
  for (const field of ["model", "default_workspace"]) expect(health).toHaveProperty(field);
});

test("providers list has the shape the Settings pane expects", async () => {
  const providers = await (await backendFetch("/v1/providers")).json();
  expect(Array.isArray(providers)).toBe(true);
  expect(providers.length).toBeGreaterThan(0);
  // What Settings ▸ Models draws a provider from.
  for (const provider of providers) {
    expect(provider).toMatchObject({ name: expect.any(String), title: expect.any(String), needs_key: expect.any(Boolean), configured: expect.any(Boolean) });
    expect(Array.isArray(provider.fields)).toBe(true);
  }
  expect(providers.map((p: { name: string }) => p.name)).toEqual(expect.arrayContaining(["openai", "anthropic"]));
});
