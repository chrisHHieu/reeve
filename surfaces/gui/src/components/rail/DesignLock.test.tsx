import { describe, expect, it } from "vitest";
import { handoffAttachment, lockLabel } from "./DesignLock";

describe("DesignLock", () => {
  it("names the page's state on its button", () => {
    expect(lockLabel(null).text).toBe("Lock");
    expect(lockLabel({ locked: false }).text).toBe("Lock");
    expect(lockLabel({ locked: true, locked_at: 1_790_000_000_000, changed: false }).text).toBe("Locked");
    const changed = lockLabel({ locked: true, locked_at: 1_790_000_000_000, changed: true });
    expect(changed.text).toBe("Changed");
    expect(changed.title).toMatch(/has changed since/);
  });

  it("asks the agent to build from the measured file, read the app first and wait for a go", () => {
    const ask = handoffAttachment("design/pricing.b.html", "design/pricing.b.handoff.md", { name: "northwind-web", path: "C:/work/northwind-web" });
    expect(ask.kind).toBe("text");
    expect(ask.name).toBe("pricing.b.html · handoff");
    const text = ask.kind === "text" ? ask.text : "";
    expect(text).toContain("design/pricing.b.handoff.md");
    expect(text).toContain("into northwind-web");
    expect(text).toMatch(/Wait for their go before writing code/);
  });
});

import { appLine } from "./DesignLock";

describe("the Lock panel's App row", () => {
  it("says what the app is and what of the design is new to it", () => {
    expect(appLine({ found: false })).toBe("No app found in this project's files");
    expect(
      appLine({ found: true, stack: "React + Vite + Tailwind", theme: ["tailwind.config.ts"], colors_new: 1, fonts_new: ["Inter", "Newsreader"], components: 12 }),
    ).toBe("React + Vite + Tailwind · theme in tailwind.config.ts · 1 color and 2 fonts new to it · 12 components");
    expect(appLine({ found: true, stack: "Vue", colors_new: 0, fonts_new: [] })).toBe("Vue · every color and font already in it");
  });
});
