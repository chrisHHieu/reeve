import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures";
import { composer, openSettings } from "./steps";

// Skills, the four ways a person meets them: kept in Settings ▸ Skills (made,
// edited, turned off, deleted); imported through a review that installs nothing until confirmed;
// offered by the composer's "/" as they stand right now; and run by picking one there, which
// sends the skill as a field of its own beside what was typed.

/** Settings ▸ Skills. */
const skillsPage = (page: Page) => openSettings(page, "Skills");

/** Write a skill by hand from Add skill, and save it. */
async function writeSkill(page: Page, name: string, description: string, instructions: string) {
  await page.getByRole("button", { name: /Add skill/ }).click();
  await fillSkill(page, name, description, instructions);
}

/** From the open Add skill menu, write a skill by hand and save it. */
async function fillSkill(page: Page, name: string, description: string, instructions: string) {
  await page.getByText("Write it myself").click();
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("Description").fill(description);
  await page.getByLabel("Instructions").fill(instructions);
  await page.getByRole("button", { name: "Save skill" }).click();
}

/** The pinned session, open. */
async function pinnedSession(page: Page) {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
}

/** Type "/" in the composer and wait for the skills it offers. */
async function slash(page: Page) {
  await composer(page).fill("/");
  await expect(page.getByTestId("skill-popup")).toBeVisible();
}

test.describe("Settings ▸ Skills", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await skillsPage(page);
  });

  test("skills-settings: create via the menu → name-first banner; edit persists", async ({ page }) => {
    // The list is the page: the skills, the uploaded one with its folder of two files, and no
    // standing forms to add one.
    await expect(page.getByText("weekly-report")).toBeVisible();
    await expect(page.getByText("uploaded")).toBeVisible();
    await expect(page.getByText("2 files", { exact: true })).toBeVisible();
    await expect(page.getByText("Start a conversation")).toHaveCount(0);

    // Add skill offers three ways: import, make with Reeve, write by hand.
    await page.getByRole("button", { name: /Add skill/ }).click();
    await expect(page.getByText("Import a file")).toBeVisible();
    await expect(page.getByText("Create with Reeve")).toBeVisible();
    await fillSkill(page, "greet-warmly", "Greets people warmly", "Always greet warmly.");
    // The confirmation leads with its name.
    const status = page.getByRole("status");
    await expect(status).toContainText("greet-warmly");
    await expect(status).toContainText("can now use it in every conversation");
    await expect(page.getByText("Greets people warmly")).toBeVisible();

    // Editing from a skill's details: filled in, its name fixed, saved through to the list.
    await page.getByLabel("Open weekly-report").click();
    await page.getByTestId("skill-details-edit").click();
    await expect(page.getByLabel("Name")).toBeDisabled();
    await page.getByLabel("Description").fill("Monday status report, sharper");
    await page.getByRole("button", { name: "Save skill" }).click();
    await expect(page.getByText("Monday status report, sharper")).toBeVisible();
  });

  test("skills-settings: disable → amber everywhere/clean-slate banner; delete is two-step", async ({ page }) => {
    // Off everywhere at once, and a conversation already open is told how to start clean.
    await page.getByLabel("weekly-report enabled").click();
    const status = page.getByRole("status");
    await expect(status).toContainText("weekly-report");
    await expect(status).toContainText("turned off everywhere");
    await expect(status).toContainText("start a new one for a completely clean slate");

    // Deleting asks twice: the first press only arms it.
    await page.getByLabel("Open html-to-markdown").click();
    await expect(page.getByTestId("skill-details")).toBeVisible();
    await page.getByLabel("Delete html-to-markdown").click();
    await expect(page.getByLabel("Open html-to-markdown")).toBeVisible();
    await page.getByText("Confirm delete").click();
    await expect(page.getByTestId("skill-details")).toHaveCount(0);
    // Its name is left only in the line saying it was removed.
    await expect(page.getByText("html-to-markdown")).toHaveCount(1);
    await expect(page.getByRole("status")).toContainText("removed");
  });

  test("skills-upload: preview installs nothing → confirm → uploaded badge", async ({ page }) => {
    // Import a file goes straight to the file picker.
    await page.getByRole("button", { name: /Add skill/ }).click();
    await page.getByText("Import a file").click();
    await page.getByLabel("Upload a skill archive").setInputFiles({ name: "greet.zip", mimeType: "application/zip", buffer: Buffer.from("PKfake") });

    // The review: all of it read, none of it installed. Its name shows once, in the preview.
    await expect(page.getByText("Review before installing")).toBeVisible();
    for (const shown of ["says hello", "Say hello warmly."]) await expect(page.getByText(shown)).toBeVisible();
    await expect(page.getByText(/notes\.txt/)).toBeVisible();
    await expect(page.getByText("greet", { exact: true })).toHaveCount(1);

    await page.getByRole("button", { name: "Install skill" }).click();
    const status = page.getByRole("status");
    await expect(status).toContainText("greet");
    await expect(status).toContainText("can now use it in every conversation");
    // Now in the confirmation and as a row, uploaded like html-to-markdown.
    await expect(page.getByText("greet", { exact: true })).toHaveCount(2);
    await expect(page.getByText("uploaded")).toHaveCount(2);
  });
});

// The "/" list is what the session can use right now: a skill made in Settings is offered, and
// one turned off is not.
test("skills-session: new skill offered in '/', disabled one absent", async ({ page }) => {
  await pinnedSession(page);
  await slash(page);
  for (const offered of ["/weekly-report", "/html-to-markdown"]) await expect(page.getByText(offered)).toBeVisible();
  await composer(page).fill("");

  await skillsPage(page);
  await writeSkill(page, "fresh-skill", "Freshly made skill", "Do the fresh thing.");
  await expect(page.getByRole("status")).toContainText("fresh-skill");
  await page.getByLabel("weekly-report enabled").click();
  await expect(page.getByRole("status")).toContainText("turned off everywhere");

  // Back to the app (Settings takes the whole window) and into the session again.
  await page.getByTestId("settings-back").click();
  await page.getByText("Draft the launch note").first().click();
  await slash(page);
  await expect(page.getByText("/fresh-skill")).toBeVisible();
  await expect(page.getByText("/weekly-report")).toHaveCount(0);
  await expect(page.getByText("/html-to-markdown")).toBeVisible();
});

// Picking a skill puts "/name " at the start of the line, with no chip. The message goes with the
// skill as its own field and without the prefix; the transcript has one bubble, the skill drawn
// as its command over what was typed, and never the framing the model reads.
test("skills-forcerun: popup pick → inline /name → skill rides the frame → one bubble", async ({ page }) => {
  await pinnedSession(page);
  await slash(page);
  await page.getByText("/weekly-report").click();
  await expect(composer(page)).toHaveValue("/weekly-report ");
  await composer(page).type("cover last week");
  await composer(page).press("Enter");

  const bubble = page.getByTestId("command-bubble");
  await expect(bubble).toHaveCount(1);
  await expect(bubble.getByTestId("command-name")).toHaveText("/weekly-report");
  await expect(bubble).toContainText("cover last week");
  await expect(page.getByText(/Use the skill/)).toHaveCount(0);
  // The echo says what went over the wire.
  await expect(page.getByText(/\[skill=weekly-report\]/)).toBeVisible();
  await expect(page.getByText(/Echo: cover last week/)).toBeVisible();
});
