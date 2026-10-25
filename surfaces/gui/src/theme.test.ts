// The palette preference and the stylesheet contract behind it. `data-palette` on <html>
// selects one of sixteen complete colour tables; absent/garbage storage = reeve (the default;
// see the palettes), Harbor is the stylesheet's own `:root` and no attribute, and every palette must
// define every token Harbor defines — that last part is asserted by reading the shipped
// stylesheets as text, because jsdom does not resolve custom properties and the palette
// contract IS the files.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { DEFAULT_PALETTE, getPalette, initTheme, PALETTE_GROUPS, PALETTES, setPalette } from "./theme";
import styles from "./styles.css?raw";
import generated from "./palettes.css?raw";
import indexHtml from "../index.html?raw";

// Line endings made `\n`: a checkout with core.autocrlf has CRLF files, and some blocks below are
// found by text that spans a line break.
const css = `${styles}\n${generated}`.replace(/\r\n/g, "\n");

const PALETTE_KEY = "reeve:palette";

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.palette;
  delete document.documentElement.dataset.theme;
});

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.palette;
  delete document.documentElement.dataset.theme;
});

describe("palette preference", () => {
  it("defaults to reeve when nothing is stored", () => {
    expect(DEFAULT_PALETTE).toBe("reeve");
    expect(getPalette()).toBe("reeve");
  });

  it("defaults to reeve for garbage storage instead of trusting it", () => {
    localStorage.setItem(PALETTE_KEY, "not-a-palette");
    expect(getPalette()).toBe("reeve");
  });

  it("round-trips every palette through storage and the html attribute", () => {
    for (const p of PALETTES) {
      setPalette(p.id);
      expect(getPalette()).toBe(p.id);
      // The default is the absence of the key; Harbor is the absence of the attribute.
      expect(localStorage.getItem(PALETTE_KEY)).toBe(p.id === "reeve" ? null : p.id);
      expect(document.documentElement.dataset.palette).toBe(p.id === "harbor" ? undefined : p.id);
    }
  });

  it("files every palette under one of the three groups, Reeve's first", () => {
    expect(PALETTES).toHaveLength(16);
    expect(new Set(PALETTES.map((p) => p.id)).size).toBe(16);
    for (const p of PALETTES) expect(PALETTE_GROUPS.map((g) => g.id)).toContain(p.group);
    expect(PALETTES.filter((p) => p.group === "reeve").map((p) => p.id)).toEqual(["reeve", "harbor"]);
  });

  it("applies a stored palette at init, before any React render", () => {
    localStorage.setItem(PALETTE_KEY, "midnight");
    initTheme();
    expect(document.documentElement.dataset.palette).toBe("midnight");
  });
});

// == styles.css contract ========================================================
// Every token Harbor defines must exist in each candidate's light AND dark block, with the
// same name — that is what lets components switch palettes without changing a line of CSS.
const SHARED = new Set([
  "--serif",
  "--sans",
  "--mono",
  "--term-font",
  "--code-delta",
  "--code-liga",
  "--fs-title",
  "--fs-heading",
  "--fs-body",
  "--fs-ui",
  "--fs-mono",
  "--fs-label",
  "--artifact-rail-w",
  "--review-rail-w",
  "--thread-w",
  "--topbar-h",
  "--thread-gutter",
  "--sb-w",
  "--thread-pad",
  "--shiki-foreground",
  "--shiki-background",
  "--syntax-unknown",
  "--md-code-lh",
]);

/** The text between `selector`'s `{` and its matching `}` (braces counted, so nested
 *  at-rule blocks would still parse). */
function blockFor(selector: string): string {
  const at = css.indexOf(selector);
  expect(at, `selector ${selector}`).toBeGreaterThanOrEqual(0);
  const open = css.indexOf("{", at);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") depth++;
    else if (css[i] === "}") {
      depth--;
      if (depth === 0) return css.slice(open + 1, i);
    }
  }
  throw new Error(`unclosed block for ${selector}`);
}

describe("styles.css palette contract", () => {
  it("defines every Harbor token in every candidate's light and dark block", () => {
    // Collect names from Harbor's two light blocks: the UI `:root` and the syntax `:root`.
    const ui = blockFor(":root {");
    const syntaxAt = css.indexOf("--syntax-comment");
    const syntaxOpen = css.lastIndexOf("{", syntaxAt);
    const syntaxClose = css.indexOf("}", syntaxAt);
    const syntax = css.slice(syntaxOpen + 1, syntaxClose);
    const names = new Set(
      [...`${ui}\n${syntax}`.matchAll(/--([a-z0-9-]+)\s*:/g)]
        .map((m) => `--${m[1]}`)
        .filter((name) => !SHARED.has(name)),
    );
    names.add("--overlay-hover");
    names.add("--overlay-pressed");
    expect(names.size).toBeGreaterThan(60);

    for (const p of PALETTES) {
      if (p.id === "harbor") continue;
      for (const [appearance, selector] of [
        ["light", `html[data-palette="${p.id}"]`],
        ["dark", `html[data-theme="dark"][data-palette="${p.id}"]`],
      ] as const) {
        const block = blockFor(selector);
        for (const name of names) {
          expect(new RegExp(`${name}\\s*:`).test(block), `${p.id} ${appearance} ${name}`).toBe(true);
        }
      }
    }
  });

  it("gives the terminal 16 colours: its own table, or the hand-tuned palettes' roles", () => {
    const ansi = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"].flatMap((c) => [
      `--ansi-${c}`,
      `--ansi-bright-${c}`,
    ]);
    const fallback = blockFor(":root {\n  --ansi-black");
    for (const name of ansi) expect(new RegExp(`${name}\\s*:`).test(fallback), name).toBe(true);
    // A palette from a table carries its theme's own terminal colours, in both faces.
    for (const id of ["reeve", "catppuccin", "nord", "gruvbox"]) {
      for (const selector of [`html[data-palette="${id}"]`, `html[data-theme="dark"][data-palette="${id}"]`]) {
        const block = blockFor(selector);
        for (const name of ansi) expect(new RegExp(`${name}\\s*:\\s*#`).test(block), `${selector} ${name}`).toBe(true);
      }
    }
  });
});

// index.html sets the palette before the app has loaded, from a list of its own: one the app has
// and the page does not would flash Reeve's colours first on every start.
describe("the palette set before first paint", () => {
  it("knows every palette the app has, and no other", () => {
    const listed = /var palettes = \[([^\]]*)\]/.exec(indexHtml.replace(/\r\n/g, "\n"))?.[1] ?? "";
    const early = [...listed.matchAll(/"([^"]+)"/g)].map((m) => m[1]).sort();
    // Reeve is the fallback and Harbor is no attribute at all: neither needs naming.
    const app = PALETTES.map((p) => p.id).filter((id) => id !== "reeve" && id !== "harbor").sort();
    expect(early).toEqual(app);
  });
});
