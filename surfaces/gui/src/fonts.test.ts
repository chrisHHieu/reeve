// Typography preferences and the stylesheet contract behind them. One registry
// (src/fonts.ts) drives the Appearance pickers. A palette names a face pair while "Use the
// theme's fonts" is on; whatever is in force, the house pair (Inter / JetBrains Mono) is the
// ABSENCE of the inline property, so :root's stacks stay the source of truth for them. jsdom does
// not resolve custom properties or load fonts, so the second half reads fonts.css / styles.css as
// text: the @font-face family names and the terminal panel's var() ARE the contract.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  getFont,
  getThemeFonts,
  initFonts,
  MONO_FONTS,
  SAME_AS_CODE,
  setCodeSize,
  setFont,
  setLigatures,
  setTermCursor,
  setTermLine,
  setTermSize,
  setTextSize,
  setThemeFonts,
  terminalOptions,
  themeFonts,
  UI_FONTS,
} from "./fonts";
import { setPalette } from "./theme";
import fontsCss from "./fonts.css?raw";
import stylesCss from "./styles.css?raw";

const KEYS = { ui: "reeve:font-ui", code: "reeve:font-code", terminal: "reeve:font-terminal" } as const;
const VARS = ["--sans", "--mono", "--term-font", "--fs-body", "--code-delta", "--code-liga"];
const css = (name: string) => document.documentElement.style.getPropertyValue(name);

function reset() {
  localStorage.clear();
  for (const name of VARS) document.documentElement.style.removeProperty(name);
  delete document.documentElement.dataset.palette;
}

beforeEach(reset);
afterEach(reset);

describe("the theme's fonts", () => {
  it("are on by default and give Reeve its Manrope, the house mono for code", () => {
    expect(getThemeFonts()).toBe(true);
    expect(getFont("ui")).toBe("manrope");
    expect(getFont("code")).toBe("jetbrains");
    expect(getFont("terminal")).toBe(SAME_AS_CODE);
    initFonts();
    expect(css("--sans")).toContain("Manrope Variable");
    expect(css("--mono")).toBe(""); // the house mono is the stylesheet's own
  });

  it("follow the palette: Harbor speaks in the house pair, Geist in its own", () => {
    initFonts();
    setPalette("harbor");
    expect(getFont("ui")).toBe("inter");
    expect(css("--sans")).toBe("");
    setPalette("geist");
    expect(themeFonts("geist")).toEqual({ ui: "geist", code: "geist-mono" });
    expect(css("--sans")).toContain("Geist Variable");
    expect(css("--mono")).toContain("Geist Mono Variable");
  });

  it("end when a face is picked by hand, and the other role keeps what it showed", () => {
    setFont("ui", "ibm-plex-sans");
    expect(getThemeFonts()).toBe(false);
    expect(getFont("ui")).toBe("ibm-plex-sans");
    expect(getFont("code")).toBe("jetbrains");
    // A palette switch no longer moves a face the person chose.
    setPalette("geist");
    expect(getFont("ui")).toBe("ibm-plex-sans");
  });

  it("keep a choice made before the switch existed", () => {
    localStorage.setItem(KEYS.ui, "plus-jakarta");
    expect(getThemeFonts()).toBe(false);
    expect(getFont("ui")).toBe("plus-jakarta");
  });

  it("turned off keeps what is on screen", () => {
    setThemeFonts(false);
    expect(localStorage.getItem(KEYS.ui)).toBe("manrope");
    expect(getFont("ui")).toBe("manrope");
    setThemeFonts(true);
    setPalette("harbor");
    expect(getFont("ui")).toBe("inter");
  });
});

describe("font preference", () => {
  it("ignores garbage in storage instead of trusting it", () => {
    setThemeFonts(false);
    localStorage.setItem(KEYS.ui, "comic-sans");
    expect(getFont("ui")).toBe("inter");
  });

  it("rejects an unknown id without writing storage or CSS", () => {
    setFont("ui", "nope");
    expect(localStorage.getItem(KEYS.ui)).toBeNull();
    expect(getThemeFonts()).toBe(true);
  });

  it("the terminal follows the code face until it has one of its own", () => {
    setFont("code", "fira-code");
    expect(css("--mono")).toContain("Fira Code Variable");
    expect(css("--term-font")).toContain("Fira Code Variable");
    setFont("terminal", "cascadia");
    expect(css("--term-font")).toContain("Cascadia Code Variable");
    expect(css("--mono")).toContain("Fira Code Variable");
    setFont("terminal", SAME_AS_CODE);
    expect(localStorage.getItem(KEYS.terminal)).toBeNull();
    expect(css("--term-font")).toContain("Fira Code Variable");
  });
});

describe("sizes, ligatures and the terminal's shape", () => {
  it("text and code sizes are steps from the stylesheet's own; default clears them", () => {
    setTextSize("large");
    expect(css("--fs-body")).toBe("15px");
    setCodeSize("small");
    expect(css("--code-delta")).toBe("-1px");
    setTextSize("default");
    setCodeSize("default");
    expect(css("--fs-body")).toBe("");
    expect(css("--code-delta")).toBe("");
  });

  it("ligatures are off unless asked for", () => {
    expect(css("--code-liga")).toBe("");
    setLigatures(true);
    expect(css("--code-liga")).toBe("normal");
  });

  it("the terminal is built from its own size, line height and cursor", () => {
    expect(terminalOptions()).toMatchObject({ fontSize: 12, lineHeight: 1.2, cursorStyle: "block" });
    setTermSize(14);
    setTermLine("roomy");
    setTermCursor("bar");
    expect(terminalOptions()).toMatchObject({ fontSize: 14, lineHeight: 1.35, cursorStyle: "bar" });
    setTermSize(99);
    expect(terminalOptions().fontSize).toBe(14);
  });
});

describe("typeface registry + stylesheet contract", () => {
  const primary = (stack: string) => stack.split(",")[0].replace(/"/g, "").trim();

  it("has unique ids, real stacks and a licence for every option", () => {
    const all = [...UI_FONTS, ...MONO_FONTS];
    expect(new Set(all.map((f) => f.id)).size).toBe(all.length);
    expect(UI_FONTS).toHaveLength(8);
    expect(MONO_FONTS).toHaveLength(8);
    for (const f of all) {
      expect(f.stack.length, f.id).toBeGreaterThan(0);
      expect(["OFL-1.1", "system"], f.id).toContain(f.license);
      expect(f.pkg.length, f.id).toBeGreaterThan(0);
    }
  });

  it("declares an @font-face for every bundled family", () => {
    const declared = new Set(
      [...fontsCss.matchAll(/@font-face\s*\{[^}]*?font-family:\s*"([^"]+)"/gs)].map((m) => m[1]),
    );
    for (const f of [...UI_FONTS, ...MONO_FONTS].filter((o) => o.pkg.startsWith("@"))) {
      expect(declared.has(primary(f.stack)), `${f.id} @font-face`).toBe(true);
    }
  });

  it("carries Vietnamese marks in the faces that have them, Reeve's Manrope first", () => {
    for (const family of ["Manrope Variable", "IBM Plex Sans Variable", "Source Sans 3 Variable", "Victor Mono Variable"]) {
      const faces = [...fontsCss.matchAll(/@font-face\s*\{[^}]*\}/gs)].map((m) => m[0]).filter((f) => f.includes(`"${family}"`));
      expect(faces.some((f) => f.includes("vietnamese")), family).toBe(true);
    }
  });

  it("keeps the terminal panel on --term-font, defaulting to the shipped mono stack", () => {
    expect(/--term-font\s*:/.test(stylesCss)).toBe(true);
    const block = (selector: string) => {
      const re = new RegExp(selector.replace(/\./g, "\\.") + "\\s*\\{");
      const at = stylesCss.search(re);
      expect(at, selector).toBeGreaterThanOrEqual(0);
      const open = stylesCss.indexOf("{", at);
      return stylesCss.slice(open, stylesCss.indexOf("}", open));
    };
    // The emulator takes its face from terminalLook(); these are the panel's own lines around it.
    for (const selector of [".terminal-shell", ".terminal-state", ".terminal-cwd"]) {
      expect(block(selector), selector).toContain("var(--term-font)");
    }
  });

  it("draws every code surface in the chosen face at the chosen step", () => {
    // These used to name `ui-monospace` and ignore the Code font picker.
    for (const selector of [".rev-code {", ".rev-diff {", ".fm-editor {"]) {
      const at = stylesCss.indexOf(selector);
      const rule = stylesCss.slice(at, stylesCss.indexOf("}", at));
      expect(rule, selector).toContain("var(--mono)");
      expect(rule, selector).toContain("var(--code-delta)");
    }
  });
});
