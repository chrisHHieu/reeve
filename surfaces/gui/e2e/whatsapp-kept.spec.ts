// Settings ▸ Connectors ▸ WhatsApp (personal), connected: the page says what the linked
// device keeps on this computer, saves how long it keeps it, and clears it without unlinking. The
// connector and its store are served here; invented numbers throughout.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

const WHATSAPP = {
  name: "whatsapp_personal", title: "WhatsApp (personal)", icon: "◌", blurb: "Read your own WhatsApp chats.",
  auth: "pairing", two_way: false, channels: false, available: true, brand_color: "#25d366", logo: "whatsapp",
  fields: [], instructions: [], connected: true, account: "+84909999999", enabled: true, allowed_users: [], tools: [],
};

test("the WhatsApp page says what is kept, keeps it for the chosen time and clears it", async ({ page }) => {
  let kept = { messages: 1240, chats: 38, oldest: 1772496000, bytes: 4404019, keep_days: 90 as number | null };
  const calls: string[] = [];
  await page.route("**/v1/connectors", async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    const list = Array.isArray(body) ? body : body.connectors;
    // The fixtures list it unlinked; here it is linked.
    const at = list.findIndex((c: { name: string }) => c.name === WHATSAPP.name);
    if (at >= 0) list[at] = { ...list[at], ...WHATSAPP };
    else list.push(WHATSAPP);
    await route.fulfill({ json: body });
  });
  await page.route("**/v1/connectors/whatsapp_personal/kept", async (route) => {
    const method = route.request().method();
    calls.push(method);
    if (method === "PUT") {
      const days = route.request().postDataJSON().keep_days;
      kept = { ...kept, keep_days: days, messages: days === 30 ? 310 : kept.messages };
    }
    if (method === "DELETE") kept = { ...kept, messages: 0, chats: 0, oldest: null as unknown as number, bytes: 8192 };
    await route.fulfill({ json: method === "GET" ? kept : { ok: true, ...kept } });
  });

  await page.goto("/");
  await page.getByTestId("rail-settings").click();
  await page.getByRole("button", { name: "Connectors", exact: true }).click();
  await page.getByText("WhatsApp (personal)").first().click();

  await expect(page.getByTestId("whatsapp-kept-count")).toContainText("1,240 messages in 38 chats");
  await expect(page.getByTestId("whatsapp-kept-count")).toContainText("4.2 MB");
  await expect(page.getByTestId("whatsapp-keep-90")).toHaveAttribute("aria-checked", "true");

  await page.getByTestId("whatsapp-keep-30").click();
  await expect(page.getByTestId("whatsapp-kept-count")).toContainText("310 messages");
  await expect(page.getByTestId("whatsapp-keep-30")).toHaveAttribute("aria-checked", "true");

  await page.getByTestId("whatsapp-clear").click();
  await page.getByRole("button", { name: "Clear messages" }).click();
  await expect(page.getByTestId("whatsapp-kept-count")).toContainText("Nothing yet");
  expect(calls).toContain("PUT");
  expect(calls).toContain("DELETE");
});
