import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures";
import { composer, openPinned, say } from "./steps";

const sendButton = (page: Page) => page.getByRole("button", { name: "Send" });
/** A PDF of `body`; the mock reads its page count from a "%%pages=N" line in it. */
const pdf = (name: string, body: string) => ({ name, mimeType: "application/pdf", buffer: Buffer.from(body) });
const attach = (page: Page, files: Parameters<ReturnType<Page["locator"]>["setInputFiles"]>[0]) =>
  page.locator('input[type="file"]').setInputFiles(files);

// The composer's row of three controls: Send, in the accent only when there is something to
// send; "+", to attach; and Mode, the permission presets with the Plan switch.
test("composer: send-gating, + attach menu, Mode menu", async ({ page }) => {
  await openPinned(page);
  const send = sendButton(page);
  await expect(send).not.toHaveClass(/bg-accent/);
  await composer(page).fill("hello there");
  await expect(send).toHaveClass(/bg-accent/);
  await composer(page).fill("");
  await expect(send).not.toHaveClass(/bg-accent/);

  // "+": one way to files, and the "@" and "/" lists with their keys (owner, 2026-10-13: it
  // was three ways to one file window).
  await page.getByRole("button", { name: "Attach" }).click();
  const plus = page.getByTestId("plus-menu");
  await expect(plus.getByTestId("plus-files")).toContainText("Files from this computer");
  await expect(plus.getByTestId("plus-files")).toContainText("Or paste, or drop them here.");
  await expect(plus.getByRole("button", { name: /Photo or image|^PDF$|Other files/ })).toHaveCount(0);
  // A click beside it closes it: at the layer's corner, since its middle can lie under the menu
  // depending on where the column sits.
  await page.locator(".fixed.inset-0.z-30").click({ position: { x: 8, y: 8 } });
  await expect(plus).toHaveCount(0);

  // Mode: the three presets, the one in force ticked, and Plan, the one switch. Nothing in it
  // sends an approval anywhere but the conversation.
  await page.getByRole("button", { name: "Mode", exact: true }).click();
  const mode = page.getByTestId("mode-menu");
  for (const preset of ["Read only", "Workspace write", "Full access"]) await expect(mode.getByText(preset)).toBeVisible();
  await expect(mode.locator("button").filter({ hasText: "Workspace write" })).toContainText("✓");
  await expect(mode.getByTestId("plan-toggle-row")).toBeVisible();
  await expect(mode.getByRole("switch", { name: "Plan" })).toBeVisible();
  await expect(mode.getByRole("switch")).toHaveCount(1);
  await expect(mode).not.toContainText("Inbox");
  // A pick closes it (and would change the engine's mode).
  await mode.getByText("Full access").click();
  await expect(page.getByTestId("mode-menu")).toHaveCount(0);
});

// A PDF is read as a data URL and shown as a named card. They once did nothing at all: only
// pictures and text were read (found in the walkthrough).
test("composer: picking a PDF shows an attachment chip and arms send", async ({ page }) => {
  await openPinned(page);
  const send = sendButton(page);
  await expect(send).not.toHaveClass(/bg-accent/);
  await attach(page, pdf("report.pdf", "%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"));

  // A card: the name, then the kind and the size. A file alone is enough to send.
  const card = page.getByTestId("attachment-card");
  await expect(card).toContainText("report.pdf");
  await expect(card).toContainText(/PDF · \d+ B/);
  await expect(page.getByTestId("composer-attach-total")).toHaveText(/1 attached · \d+ B/);
  await expect(send).toHaveClass(/bg-accent/);
  await page.getByRole("button", { name: "Remove report.pdf" }).click();
  await expect(card).toHaveCount(0);
  await expect(send).not.toHaveClass(/bg-accent/);
});

// A sent message carries its picture and its file above the bubble, out of it: the
// bubble is the words, the picture opens large, the file is a card that says what it is.
test("a sent message draws its picture and file above the bubble, and the picture opens", async ({ page }) => {
  await openPinned(page);
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");
  await attach(page, [{ name: "shot.png", mimeType: "image/png", buffer: png }, pdf("report.pdf", "%PDF-1.4\n%%pages=1\ntrailer\n<<>>\n%%EOF")]);
  await expect(page.getByTestId("composer-attachments").getByTestId("attachment-tile")).toHaveCount(1);
  await say(page, "what is in these");

  const sent = page.getByTestId("message-attachments");
  await expect(sent.getByTestId("attachment-tile")).toHaveCount(1);
  await expect(sent.getByTestId("attachment-card")).toContainText("report.pdf");
  await expect(page.locator(".bubble-user").filter({ hasText: "what is in these" })).toBeVisible();
  await expect(page.locator(".bubble-user").filter({ hasText: "report.pdf" })).toHaveCount(0);
  await sent.getByRole("button", { name: "Open shot.png" }).click();
  await expect(page.getByTestId("md-image-viewer")).toBeVisible();
});

// A slash command is drawn as the command: `/init` highlighted in the box, and in the
// message the command and what was typed after it, with the template it expanded to folded away.
test("/init is highlighted in the box and drawn as the command once sent", async ({ page }) => {
  await openPinned(page);
  const box = composer(page);
  await box.click();
  await box.pressSequentially("/ini");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("command-mark")).toContainText("/init");
  await box.pressSequentially("keep it under 40 lines");
  await page.keyboard.press("Enter");

  const bubble = page.getByTestId("command-bubble");
  await expect(bubble.getByTestId("command-name")).toHaveText("/init");
  await expect(bubble).toContainText("keep it under 40 lines");
  await expect(bubble).not.toContainText("Write or update");
  await bubble.getByTestId("command-sent-toggle").click();
  await expect(bubble.getByTestId("command-sent")).toContainText("Write or update `AGENTS.md`");
});

// A PDF over the person's page limit (owner, 2026-07-17; two pages in the mock) is refused, and
// says so on its own card until it is taken off. Send stays off meanwhile.
test("composer: PDF over the page threshold is rejected with a notice", async ({ page }) => {
  await openPinned(page);
  await attach(page, pdf("big-report.pdf", "%PDF-1.4\n%%pages=34\ntrailer\n<<>>\n%%EOF"));
  const refused = page.locator('[data-testid="attachment-card"][data-state="refused"]');
  await expect(refused).toContainText("big-report.pdf");
  await expect(refused).toContainText("34 pages is over your 2-page limit");
  await expect(page.getByTestId("composer-attach-refused")).toHaveText("1 file will not be sent");
  await expect(sendButton(page)).not.toHaveClass(/bg-accent/);
  await page.getByRole("button", { name: "Remove big-report.pdf" }).click();
  await expect(refused).toHaveCount(0);

  // A refusal leaves the next file alone: one of a page attaches.
  await attach(page, pdf("small.pdf", "%PDF-1.4\n%%pages=1\ntrailer\n<<>>\n%%EOF"));
  await expect(page.getByTestId("attachment-card")).toContainText("small.pdf");
});

// The composer's shape (redesign of 2026-10-12). The row of controls: attach and permissions
// on the left; ONE model control, the ring, the mic and Send on the right (seven controls in
// a row were cut to "Vertex AI …", "H…" and "feat/tool-outp" in a rail-open column). Where
// a new session works is picked in a strip tucked under the card; with the first message the
// strip goes and a crumb in the top bar says it (owner, 2026-10-13).
test("the composer's controls and its strip fit at every width, before and after the first message", async ({ page }) => {
  // Nothing in the row or the strip may reach past its own right edge.
  const fits = () =>
    page.locator(".composer-wrap").evaluate((wrap) => {
      const report = (box: Element | null) => {
        if (!box) return null;
        const edge = box.getBoundingClientRect().right;
        const past = [...box.querySelectorAll("button, [data-testid]")]
          .filter((b) => b.getBoundingClientRect().width > 0 && b.getBoundingClientRect().right > edge + 0.5)
          .map((b) => b.getAttribute("data-testid") || b.getAttribute("aria-label") || b.textContent);
        return { overflow: box.scrollWidth - box.clientWidth, past };
      };
      return { row: report(wrap.querySelector(".composer-footer")), strip: report(wrap.querySelector(".composer-tray")) };
    });
  const FITS = { row: { overflow: 0, past: [] }, strip: { overflow: 0, past: [] } };
  // Where the strip is: the card's next sibling, tucked under the card's lower edge.
  const place = () =>
    page.getByTestId("composer-session").evaluate((strip) => {
      const card = strip.previousElementSibling!;
      const c = card.getBoundingClientRect();
      const s = strip.getBoundingClientRect();
      return {
        underCard: card.classList.contains("composer"),
        tucked: s.top < c.bottom && s.bottom > c.bottom,
        inset: Math.round(s.left - c.left),
      };
    });

  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).waitFor();
  await expect(page.getByTestId("model-picker-trigger")).toContainText("Claude Opus");
  // One model control; no chips above the card; the pickers are in the strip, not the row.
  await expect(page.getByTestId("provider-picker-trigger")).toHaveCount(0);
  await expect(page.getByTestId("composer-chips")).toHaveCount(0);
  await expect(page.getByTestId("composer-session").getByTestId("folder-chip")).toBeVisible();
  await expect(page.locator(".composer-footer").getByTestId("folder-chip")).toHaveCount(0);
  // Send is the last control of the row, and the text has its zone to itself.
  await expect(page.locator(".composer-footer > :last-child").getByRole("button", { name: "Send" })).toBeVisible();
  await expect(page.locator(".composer-input button")).toHaveCount(0);
  const before = await place();
  expect(before).toEqual({ underCard: true, tucked: true, inset: 14 });

  for (const width of [1500, 1280, 1100, 1000]) {
    await page.setViewportSize({ width, height: 860 });
    await expect.poll(fits, { message: `fresh session at ${width}px` }).toEqual(FITS);
  }

  await page.setViewportSize({ width: 1500, height: 860 });
  await page.getByPlaceholder(/Ask anything/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Echo: hello", { exact: false }).first()).toBeVisible();
  // Nothing under the card any more: the top bar says where it works, before the name.
  await expect(page.getByTestId("composer-session")).toHaveCount(0);
  await expect(page.getByTestId("where-crumb")).toContainText("launch-note");
  for (const width of [1500, 1280, 1100, 1000]) {
    await page.setViewportSize({ width, height: 860 });
    await expect.poll(fits, { message: `in a session at ${width}px` }).toEqual({ ...FITS, strip: null });
  }
});

// "@" is the session's files and folders, from one read of the folder, filtered as it is
// typed (owner, 2026-10-13). A pick points at the file: `@path` stays in the sentence and a
// chip says so; nothing is copied into the message.
test("@ offers the session's files and folders; a pick points at the file", async ({ page }) => {
  await page.goto("/");
  const box = page.getByPlaceholder("Ask anything, / for commands, @ for files");
  await box.fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Echo: hello", { exact: false }).first()).toBeVisible();

  await box.fill("sum up @");
  const popup = page.getByTestId("mention-popup");
  // A bare "@" is the top of the folder, folders first.
  await expect(popup.getByTestId("mention-dir").first()).toHaveAttribute("data-path", "reports/");
  await box.fill("sum up @notes");
  const file = popup.getByTestId("mention-file").filter({ hasText: "notes.md" });
  await expect(file).toBeVisible();
  await file.click();

  await expect(box).toHaveValue("sum up @notes.md ");
  await expect(popup).toHaveCount(0);
  await expect(page.getByTestId("pointer-chip-notes.md")).toBeVisible();
  await expect(page.locator(".composer").getByTestId("composer-attachments")).toHaveCount(0);
});

// The "+" menu opens the lists "@" and "/" open, so they are found without the keys.
test("+ opens the @ list and the / list", async ({ page }) => {
  await page.goto("/");
  const box = page.getByPlaceholder("Ask anything, / for commands, @ for files");
  await box.fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Echo: hello", { exact: false }).first()).toBeVisible();

  await box.fill("sum up");
  await page.getByRole("button", { name: "Attach" }).click();
  // "/" starts a message: with words in the box it says so and waits.
  await expect(page.getByTestId("plus-slash")).toBeDisabled();
  await page.getByTestId("plus-at").click();
  await expect(box).toHaveValue("sum up @");
  await expect(page.getByTestId("mention-popup")).toBeVisible();

  await box.fill("");
  await page.getByRole("button", { name: "Attach" }).click();
  await page.getByTestId("plus-slash").click();
  await expect(box).toHaveValue("/");
  await expect(page.getByTestId("skill-popup")).toBeVisible();
});

// The window sizes a layout is looked at: a desktop, and laptops at 125% and 150% scaling
// (their height is what breaks things: 600 to 730px once the browser's own bars are taken).
// The session top bar's height (`--topbar-h`).
const TOPBAR = 48;
const WINDOWS: Array<[number, number]> = [
  [1920, 948],
  [1536, 730],
  [1366, 650],
  [1280, 600],
  [1093, 520],
];

// One column (2026-10-12). The transcript scrolls and the composer under it does not, and
// they had different gutters (8% and 24px): in a laptop-sized column the composer was up to
// 48px wider than the transcript on each side, and at full width it sat 5px to its right,
// half a scrollbar. Everything in the column has the same two edges now, at every size.
test("the transcript and the composer share their two edges at every window size; the head spans the column", async ({ page }) => {
  const edges = (selectors: string[]) =>
    page.evaluate(
      (sels) =>
        sels.map((sel) => {
          const r = document.querySelector(sel)!.getBoundingClientRect();
          return [Math.round(r.left), Math.round(r.right)];
        }),
      selectors,
    );
  const same = async (selectors: string[], message: string) => {
    await expect
      .poll(async () => new Set((await edges(selectors)).map((e) => e.join("-"))).size, { message })
      .toBe(1);
  };

  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).waitFor();
  for (const [width, height] of WINDOWS) {
    await page.setViewportSize({ width, height });
    await same([".start-head", ".composer", ".start-cols"], `the start page at ${width}x${height}`);
  }

  await page.getByPlaceholder(/Ask anything/).fill("make a checklist");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Both steps done.")).toBeVisible();
  await expect(page.getByTestId("todo-dock")).toBeVisible();
  for (const [width, height] of WINDOWS) {
    await page.setViewportSize({ width, height });
    await same([".transcript", ".composer"], `a conversation at ${width}x${height}`);
    // The plan is the task's head (2026-10-13): across the column, under the top bar.
    const [head, column] = await edges([".act-head", ".main-chat"]);
    expect(head).toEqual(column);
  }
});

// The start page is placed by the window's height: its group (heading, composer, tasks) sits
// a little above the middle, not in the top third with the rest of the window empty.
test("the start page sits a little above the middle of the window, tall or short", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).waitFor();
  for (const [width, height] of WINDOWS) {
    await page.setViewportSize({ width, height });
    await expect
      .poll(
        async () => {
          const top = (await page.locator(".start-title").boundingBox())!.y;
          const tasks = (await page.locator(".start-cols").boundingBox())!;
          const above = top - TOPBAR; // under the top bar
          const below = height - (tasks.y + tasks.height);
          // More room under the group than above it, never more than about three times as
          // much, and all of the group inside the window.
          return above > 0 && below >= above && below <= above * 3.2 + 40;
        },
        { message: `the start page at ${width}x${height}` },
      )
      .toBe(true);
  }
});

// Everything that opens from the composer fits the window (2026-10-12). On a laptop-height
// window the "/" list, the permissions menu, the folder list and the branch list each ran
// past the foot of the window; a panel is now never taller than the room it opens into.
test("nothing that opens from the composer runs past the window at laptop sizes", async ({ page }) => {
  test.setTimeout(90_000);
  // The right panel closed, as the app starts: on the narrowest window it lies over the chat.
  await page.addInitScript(() => {
    localStorage.setItem("reeve-e2e-rail-default", "1");
    localStorage.removeItem("reeve:rail-hidden:v1");
  });
  const box = page.getByPlaceholder(/Ask anything/);
  const inside = async (panel: import("@playwright/test").Locator, what: string) => {
    await expect(panel, what).toBeVisible();
    await expect
      .poll(
        async () => {
          const r = (await panel.boundingBox())!;
          const v = page.viewportSize()!;
          return r.x >= 0 && r.y >= TOPBAR && r.x + r.width <= v.width && r.y + r.height <= v.height;
        },
        { message: what },
      )
      .toBe(true);
  };
  const dismiss = async (panel: import("@playwright/test").Locator) => {
    await page.keyboard.press("Escape");
    // A click outside, in the chat's own column: once a turn has changed files the side panel
    // stands at the window's right (layout step 3), and a click there is a click in the panel.
    const chat = (await page.locator(".main-chat").boundingBox())!;
    if (await panel.count()) await page.mouse.click(chat.x + chat.width - 30, chat.y + chat.height - 6);
    await expect(panel).toHaveCount(0);
  };
  const LAPTOPS = WINDOWS.slice(2);

  await page.goto("/");
  await box.waitFor();
  for (const [width, height] of LAPTOPS) {
    const at = `a new session at ${width}x${height}`;
    await page.setViewportSize({ width, height });

    await page.getByTestId("folder-chip").click();
    await inside(page.getByTestId("folder-panel"), `the folder list, ${at}`);
    await dismiss(page.getByTestId("folder-panel"));

    await page.getByTestId("environment-chip").click();
    await inside(page.locator(".setup-env .setup-menu"), `the machine list, ${at}`);
    await dismiss(page.locator(".setup-env .setup-menu"));

    await page.getByTestId("model-picker-trigger").click();
    await inside(page.locator(".mp-panel"), `the model panel, ${at}`);
    await dismiss(page.locator(".mp-panel"));

    await page.getByRole("button", { name: "Mode", exact: true }).click();
    await inside(page.getByTestId("mode-menu"), `the permissions menu, ${at}`);
    await dismiss(page.getByTestId("mode-menu"));

    await page.getByRole("button", { name: "Attach" }).click();
    const attach = page.getByTestId("plus-menu");
    await inside(attach, `the attach menu, ${at}`);
    await dismiss(attach);

    await box.fill("/");
    await inside(page.getByTestId("skill-popup"), `the "/" list, ${at}`);
    await box.fill("");
    await expect(page.getByTestId("skill-popup")).toHaveCount(0);
  }

  // In a conversation the composer is at the foot and everything opens upward, under the
  // top bar. This one works in a repository, so the branch has its panel.
  await page.setViewportSize({ width: 1366, height: 650 });
  await box.fill("edit the upload client");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Added retry and a test.")).toBeVisible();
  for (const [width, height] of LAPTOPS) {
    const at = `a conversation at ${width}x${height}`;
    await page.setViewportSize({ width, height });

    // The branch is switched from the top bar's crumb, in the panel it opens.
    await page.getByTestId("where-crumb").click();
    await page.getByTestId("where-switch-branch").click();
    await expect(page.getByTestId("branch-picker")).toBeVisible();
    const branches = page.getByTestId("where-pop");
    await inside(branches, `the branch list, ${at}`);
    await dismiss(branches);

    await page.getByTestId("model-picker-trigger").click();
    await inside(page.locator(".mp-panel"), `the model panel, ${at}`);
    await dismiss(page.locator(".mp-panel"));

    await page.getByRole("button", { name: "Mode", exact: true }).click();
    await inside(page.getByTestId("mode-menu"), `the permissions menu, ${at}`);
    await dismiss(page.getByTestId("mode-menu"));

    await box.fill("/");
    await inside(page.getByTestId("skill-popup"), `the "/" list, ${at}`);
    await box.fill("");
    await expect(page.getByTestId("skill-popup")).toHaveCount(0);
  }
});

// The right panel and the composer (2026-10-12). Under 1100px the navigation is its 56px rail,
// so the panel and the chat both fit down to 990px and the panel stands BESIDE the chat. It
// used to lie over the chat from 1100px down, and on a 1093px window (a 1366px laptop at
// 125%) it covered the right half of the composer, Send included. Below 990px they do not
// both fit and the panel overlays, as designed.
test("with the right panel open on a small laptop, the composer and Send are beside it, not under it", async ({ page }) => {
  await page.goto("/");
  const box = page.getByPlaceholder(/Ask anything/);
  await box.waitFor();
  await expect(page.locator(".right-rail")).toBeVisible();
  const gap = async () => {
    const rail = (await page.locator(".right-rail").boundingBox())!;
    const card = (await page.locator(".composer").boundingBox())!;
    return Math.round(rail.x - (card.x + card.width));
  };
  for (const [width, height] of [
    [1093, 520],
    [1000, 600],
  ]) {
    await page.setViewportSize({ width, height });
    await expect.poll(gap, { message: `beside the panel at ${width}px` }).toBeGreaterThanOrEqual(0);
    // The chat keeps its floor beside the panel. (Polled: the panel's width and the chat's
    // margin move on a 240ms curve after a resize. The panel's own border and a rounded
    // width leave a pixel or two between them, never the panel's width.)
    await expect
      .poll(
        async () => {
          const chat = (await page.locator(".main-chat").boundingBox())!;
          const rail = (await page.locator(".right-rail").boundingBox())!;
          return chat.width >= 450 && Math.round(chat.x + chat.width) <= Math.round(rail.x) + 3;
        },
        { message: `the chat's floor beside the panel at ${width}px` },
      )
      .toBe(true);
  }
  // And it can be used: a message goes out with the panel open, on the smallest of them.
  await page.setViewportSize({ width: 1093, height: 520 });
  await box.fill("hello");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Echo: hello", { exact: false }).first()).toBeVisible();

  // Under 990px there is no room for both: the panel lies over the chat.
  await page.setViewportSize({ width: 940, height: 600 });
  await expect.poll(gap, { message: "over the chat at 940px" }).toBeLessThan(0);
});
