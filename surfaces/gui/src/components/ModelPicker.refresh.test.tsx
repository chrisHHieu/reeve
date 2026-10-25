// @vitest-environment jsdom
// The composer's picker stays mounted behind Settings (the session surface is hidden, not
// unmounted), and it caches its catalogue for the mount. A provider connected in Settings
// therefore reached the picker only after a restart (owner, 2026-10-18). The real API
// functions run here and only the network is faked: saving a provider is what must make the
// picker ask again.
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const net = vi.hoisted(() => ({ catalog: [] as unknown[] }));

vi.mock("../api/base", () => ({
  httpBase: () => "http://reeve.test",
  fetch: vi.fn(async (url: string) => {
    const body = url.endsWith("/v1/models/catalog") ? { models: net.catalog, default: "" } : { ok: true };
    return { ok: true, status: 200, json: async () => body } as Response;
  }),
}));

import { ModelPicker } from "./ModelPicker";
import { removeProvider, setProvider } from "../api";

const OPENAI = { id: "gpt-5.6-sol", name: "GPT-5.6 Sol", provider: "openai", provider_title: "OpenAI" };
const CLAUDE = { id: "anthropic:claude-test", name: "Claude Test", provider: "anthropic", provider_title: "Anthropic" };

beforeEach(() => {
  net.catalog = [OPENAI];
});

afterEach(() => cleanup());

it("shows a provider connected elsewhere without a restart, and drops one removed", async () => {
  // Both turned on in Settings: the picker shows what the catalogue says is reachable.
  render(<ModelPicker value={OPENAI.id} models={[OPENAI.id, CLAUDE.id]} onChange={() => {}} />);
  fireEvent.click(screen.getByTestId("model-picker-trigger"));
  fireEvent.click(await screen.findByTestId("provider-row-all"));
  await screen.findByTestId(`model-row-${OPENAI.id}`);
  expect(screen.queryByTestId(`model-row-${CLAUDE.id}`)).toBeNull();

  // Settings saves a key while the picker stays mounted.
  net.catalog = [OPENAI, CLAUDE];
  await act(async () => {
    await setProvider("anthropic", { api_key: "invented-test-key" });
  });
  expect(await screen.findByTestId(`model-row-${CLAUDE.id}`)).toBeTruthy();

  net.catalog = [OPENAI];
  await act(async () => {
    await removeProvider("anthropic");
  });
  await waitFor(() => expect(screen.queryByTestId(`model-row-${CLAUDE.id}`)).toBeNull());
});
