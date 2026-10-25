import type { Page } from "@playwright/test";

// Steps several specs take, so each spec says what it checks rather than how to get there.

/** Open Settings from the app, at `section` (a rail entry's name) when one is given. */
export async function openSettings(page: Page, section?: string): Promise<void> {
  await page.getByTestId("rail-settings").click();
  if (section) await page.getByRole("button", { name: section, exact: true }).click();
}

/** The JSON body of the `method` request to a path ending in `path` that `act` makes. */
export async function bodySent(page: Page, method: string, path: string, act: () => Promise<unknown>): Promise<any> {
  const [request] = await Promise.all([
    page.waitForRequest((r) => new URL(r.url()).pathname.endsWith(path) && r.method() === method),
    act(),
  ]);
  return request.postDataJSON();
}

/** The composer's box. */
export const composer = (page: Page) => page.getByPlaceholder(/Ask anything/);

/** Type `text` into the composer and send it, with the Send button or with Enter. */
export async function say(page: Page, text: string, by: "button" | "enter" = "button"): Promise<void> {
  await composer(page).fill(text);
  if (by === "enter") await composer(page).press("Enter");
  else await page.getByRole("button", { name: "Send", exact: true }).click();
}

/** Open the app on the pinned session, "Draft the launch note". */
export async function openPinned(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
}
