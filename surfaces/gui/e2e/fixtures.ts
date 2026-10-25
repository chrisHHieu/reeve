import { test as playwrightTest, expect, type Page } from "@playwright/test";
import { routeDoor } from "./mock/door";
import { routeRest } from "./mock/rest";
import { scriptSockets } from "./mock/sockets";
import { createMockState } from "./mock/state";

export { dropSessionSocket, dropSessionSocketOf, sendAppEvent, sendSessionEvent, sendSessionEventTo, sessionFramesSent, sessionSocketsOpened } from "./mock/channels";
export { expect };

// What every hermetic spec runs against: no Python server, no real state. Each /v1 request is
// answered from the page's own state (mock/rest.ts), the sockets are played by a script
// (mock/sockets.ts) and the preview door is served (mock/door.ts). What a test changes, it sees
// again only through the app reading it back.

/** Put the mock on a page, before it navigates. */
export async function mockApi(page: Page) {
  const s = createMockState(page);
  // The side panel starts hidden in the app; the specs were written with it open, so they run as
  // if the person had opened it. rail-default.spec.ts, which tests the default itself, sets the
  // flag that skips this.
  await page.addInitScript(() => {
    try {
      if (!localStorage.getItem("reeve-e2e-rail-default")) localStorage.setItem("reeve:rail-hidden:v1", "0");
    } catch {
      // Storage refused: the app's default stands.
    }
  });
  await scriptSockets(page, s);
  await routeRest(page, s, await routeDoor(page));
}

/** Give one session a saved transcript. The mock's transcripts are all empty, so a reopened
 *  session starts blank; a route added later wins, and this one serves `messages` for this
 *  session alone, so a spec can test the reopened view (itemsFromMessages) without playing every
 *  turn through the fake agent. Call it any time before the session opens.
 *
 *  `state` holds the session's revert marker, read at each request, so a spec that
 *  answers `/revert` and `/unrevert` itself can set and clear it and the reload sees it. */
export async function seedSessionMessages(page: Page, sessionId: string, messages: Record<string, unknown>[], state?: { revert?: unknown }): Promise<void> {
  await page.route(new RegExp(`/v1/sessions/${sessionId}/messages$`), (route) =>
    route.fulfill({ json: { messages, revert: state?.revert ?? null } }),
  );
}

/** Open Settings ▸ Connectors, where the catalog lives. Settings takes the whole window, so from
 *  inside it (a spec back from a connector's page) the settings rail is the way; from the app it
 *  is the rail's foot, folded or open. Right after a load neither is up yet, so this waits for
 *  whichever comes. */
export async function openConnectors(page: Page): Promise<void> {
  await page.locator('[data-testid="rail-settings"], [data-testid="settings-shell"]').first().waitFor();
  if (!(await page.getByTestId("settings-shell").isVisible())) await page.locator('[data-testid="rail-settings"]').first().click();
  await page.locator(".settings-rail-item", { hasText: "Connectors" }).click();
}

/** `test`, with the page mocked before it navigates. */
export const test = playwrightTest.extend({
  page: async ({ page }, use) => {
    await mockApi(page);
    await use(page);
  },
});
