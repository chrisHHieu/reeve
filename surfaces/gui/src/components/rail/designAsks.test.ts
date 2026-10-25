// What touching a previewed design becomes (phase 3).
import { describe, expect, it } from "vitest";
import { asLevers, elementLabel, isColor, keepVariantText, pinsAttachment, placeRetype, pointedAttachment, retypeAttachment, tweaksAttachment, variantsOf, type DesignPin } from "./designAsks";

const button = { selector: "main > div.plan:nth-of-type(2) > button.cta", tag: "button", cls: "cta", text: "Choose plan", html: '<button class="cta">Choose plan</button>', w: 214, h: 40 };

describe("a pointed element", () => {
  it("is named by what it says, and carries where it is", () => {
    expect(elementLabel(button)).toBe("“Choose plan” button");
    expect(elementLabel({ selector: "div.plans", tag: "div", cls: "plans" })).toBe("div.plans");
    const attachment = pointedAttachment("design/pricing.html", button, 390);
    expect(attachment).toMatchObject({ kind: "text", name: "design/pricing.html · “Choose plan” button" });
    expect(attachment.text).toContain("selector: main > div.plan:nth-of-type(2) > button.cta");
    expect(attachment.text).toContain('html: <button class="cta">Choose plan</button>');
    expect(attachment.text).toContain("size: 214 × 40 px in a 390 px window");
  });

  it("clips what the page says, since the page is not trusted", () => {
    const long = pointedAttachment("p.html", { ...button, text: "x".repeat(5000), html: "<b>".repeat(500) }, 390);
    expect(long.text!.length).toBeLessThan(1200);
  });
});

describe("pinned comments", () => {
  const pins: DesignPin[] = [
    { n: 1, el: button, fx: 0.5, fy: 0.5, comment: "Full width on a phone" },
    { n: 2, el: { selector: "h1", tag: "h1", text: "Plans" }, fx: 0, fy: 0, comment: "  " },
    { n: 3, el: { selector: "p.note", tag: "p", cls: "note", text: "fine print" }, fx: 0, fy: 0, comment: "Darker" },
  ];

  it("go as one attachment, numbered, answered by number; an empty pin stays behind", () => {
    const attachment = pinsAttachment("design/pricing.html", pins)!;
    expect(attachment.name).toBe("design/pricing.html · 2 comments");
    expect(attachment.text).toContain("Answer each by its number");
    expect(attachment.text).toContain("1. Full width on a phone");
    expect(attachment.text).toContain("3. Darker");
    expect(attachment.text).not.toContain("2. ");
  });

  it("send nothing when none has words", () => {
    expect(pinsAttachment("p.html", [{ ...pins[1] }])).toBeNull();
  });
});

describe("retyped text", () => {
  it("goes where the old text is, once, escaped as HTML", () => {
    const placed = placeRetype("<h1>Plans</h1><p>Plans for teams</p>", "Plans for teams", "Plans & prices");
    expect(placed).toEqual({ ok: true, content: "<h1>Plans</h1><p>Plans &amp; prices</p>" });
  });

  it("finds text written with its entities", () => {
    expect(placeRetype("<p>Terms &amp; privacy</p>", "Terms & privacy", "Terms")).toEqual({ ok: true, content: "<p>Terms</p>" });
  });

  it("finds text the page showed without the source's indentation", () => {
    expect(placeRetype("<h1>\n  Plans\n</h1>", "\n  Plans\n", "Pricing")).toEqual({ ok: true, content: "<h1>\n  Pricing\n</h1>" });
  });

  it("is never guessed: missing, more than once, or unchanged leaves the file alone", () => {
    expect(placeRetype("<p>Plans</p><p>Plans</p>", "Plans", "Pricing")).toEqual({ ok: false, reason: "ambiguous" });
    expect(placeRetype("<p>Plans</p>", "Pricing", "Plans")).toEqual({ ok: false, reason: "missing" });
    expect(placeRetype("<p>Plans</p>", "Plans", "Plans")).toEqual({ ok: false, reason: "unchanged" });
  });

  it("tells the agent what changed, or asks it to change what could not be placed", () => {
    expect(retypeAttachment("design/a.html", button, "Choose plan", "Start free", true).text).toContain(
      "“Choose plan” → “Start free”. The file is saved with it; read it again before editing.",
    );
    expect(retypeAttachment("design/a.html", button, "Choose plan", "Start free", false).text).toContain(
      "could not be placed in the file: change “Choose plan” to “Start free”",
    );
  });
});

describe("tweaks", () => {
  const levers = asLevers([
    { name: "accent", label: "Accent", css: "--accent", attr: null, options: ["#74358c", "#0d6a8a"], current: "#74358c" },
    { name: "density", label: "Density", css: null, attr: "data-density", options: ["roomy", "compact"], current: "roomy" },
    { name: "bad", css: "color", options: ["a", "b"] },
    { name: "one", css: "--x", options: ["only"] },
    "nonsense",
  ]);

  it("keeps only levers with a custom property or an attribute and two options", () => {
    expect(levers.map((lever) => lever.name)).toEqual(["accent", "density"]);
    expect(isColor("#0d6a8a") && isColor("rgb(1, 2, 3)") && !isColor("compact")).toBe(true);
  });

  it("asks the agent to keep only what was moved", () => {
    expect(tweaksAttachment("design/a.html", levers, { accent: "#74358c" })).toBeNull();
    const kept = tweaksAttachment("design/a.html", levers, { accent: "#0d6a8a", density: "roomy" })!;
    expect(kept.name).toBe("design/a.html · tweaks");
    expect(kept.text).toContain("- Accent: #0d6a8a (was #74358c; --accent on :root)");
    expect(kept.text).not.toContain("Density");
  });
});

describe("variants", () => {
  const names = ["pricing.html", "pricing.a.html", "pricing.b.html", "pricing.c.html", "pricing.loading.html", "other.a.html"];

  it("are the lettered pages beside a design, from the design or from one of them", () => {
    expect(variantsOf("pricing.html", names).map((v) => v.name)).toEqual(["pricing.a.html", "pricing.b.html", "pricing.c.html"]);
    expect(variantsOf("pricing.b.html", names).map((v) => v.letter)).toEqual(["a", "b", "c"]);
    expect(variantsOf("pricing.b.html", names)[0].base).toBe("pricing.html");
    expect(variantsOf("other.a.html", names)).toEqual([]);
    expect(variantsOf("notes.md", names)).toEqual([]);
  });

  it("keeping one asks the agent to make it the page and drop the others", () => {
    expect(keepVariantText("pricing.html", "pricing.b.html", ["pricing.a.html", "pricing.c.html"])).toBe(
      "Keep pricing.b.html as pricing.html: make pricing.html what pricing.b.html is, and delete the other variants (pricing.a.html, pricing.c.html).",
    );
  });
});
