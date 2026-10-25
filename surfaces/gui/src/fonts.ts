// Typeface choices for Settings → Appearance: the UI (`--sans`), code blocks, diffs and files
// (`--mono`), and the user terminal (the `--term-font` token the panel reads), plus their sizes.
// Preferences are per-device, localStorage-only — like the palette — and must apply before the
// sidecar is reachable, so `initFonts()` runs next to `initTheme()` in main.tsx. Fonts are not
// flash-sensitive, so unlike theme/palette there is no pre-paint script in index.html.
//
// A palette carries a font pair: Reeve speaks in Manrope, its wordmark's face. While
// "Use the theme's fonts" is on, the UI and code faces are the palette's; picking a face turns it
// off. Whatever the effective face, the shipped default (Inter / JetBrains Mono) is the ABSENCE
// of the inline property: the stacks in styles.css `:root` stay the source of truth for them.
import "./fonts.css";
import { useEffect, useState } from "react";
import { getPalette, PALETTE_EVENT, type PaletteId } from "./theme";

export type FontRole = "ui" | "code" | "terminal";

export interface FontOption {
  id: string;
  name: string;
  /** The @font-face family first, then the app's fallback chain. */
  stack: string;
  /** One line under the name in the picker — what visibly changes. */
  note: string;
  /** OFL-1.1 for a bundled face; `system` for one the operating system supplies. */
  license: string;
  /** npm package that supplies the woff2 (bundled by Vite), `bundled` for src/fonts, or `system`. */
  pkg: string;
}

export const UI_FONTS: FontOption[] = [
  {
    id: "manrope",
    name: "Manrope",
    stack: '"Manrope Variable", "Inter", "Segoe UI", system-ui, sans-serif',
    note: "Reeve's face: the wordmark's family, open and a little wide.",
    license: "OFL-1.1",
    pkg: "@fontsource-variable/manrope",
  },
  {
    id: "inter",
    name: "Inter",
    stack: '"Inter", "Segoe UI", system-ui, sans-serif',
    note: "Neutral grotesque, tall x-height, reads cleanly at 11–15px.",
    license: "OFL-1.1",
    pkg: "bundled (src/fonts)",
  },
  {
    id: "geist",
    name: "Geist",
    stack: '"Geist Variable", "Inter", "Segoe UI", system-ui, sans-serif',
    note: "Tighter and more compact; pairs with the Geist theme.",
    license: "OFL-1.1",
    pkg: "@fontsource-variable/geist",
  },
  {
    id: "plus-jakarta",
    name: "Plus Jakarta Sans",
    stack: '"Plus Jakarta Sans Variable", "Inter", "Segoe UI", system-ui, sans-serif',
    note: "Rounder and a touch wider, the friendliest of the set.",
    license: "OFL-1.1",
    pkg: "@fontsource-variable/plus-jakarta-sans",
  },
  {
    id: "ibm-plex-sans",
    name: "IBM Plex Sans",
    stack: '"IBM Plex Sans Variable", "Inter", "Segoe UI", system-ui, sans-serif',
    note: "Engineered and a little technical; long reading without fatigue.",
    license: "OFL-1.1",
    pkg: "@fontsource-variable/ibm-plex-sans",
  },
  {
    id: "source-sans-3",
    name: "Source Sans 3",
    stack: '"Source Sans 3 Variable", "Inter", "Segoe UI", system-ui, sans-serif',
    note: "Adobe's humanist sans: narrow, so dense lists fit more.",
    license: "OFL-1.1",
    pkg: "@fontsource-variable/source-sans-3",
  },
  {
    id: "atkinson",
    name: "Atkinson Hyperlegible Next",
    stack: '"Atkinson Hyperlegible Next Variable", "Inter", "Segoe UI", system-ui, sans-serif',
    note: "Made for low vision: every letter shape distinct (Il1, 0O, rn/m). No Vietnamese marks.",
    license: "OFL-1.1",
    pkg: "@fontsource-variable/atkinson-hyperlegible-next",
  },
  {
    id: "system",
    name: "System",
    stack: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif',
    note: "The operating system's own: SF Pro on macOS, Segoe UI on Windows. Nothing to load.",
    license: "system",
    pkg: "system",
  },
];

/** One registry for code AND terminal: the terminal picker lists the same families. */
export const MONO_FONTS: FontOption[] = [
  {
    id: "jetbrains",
    name: "JetBrains Mono",
    stack: '"JetBrains Mono", "SF Mono", Menlo, Consolas, monospace',
    note: "The house mono: 0/O and 1/l/I told apart, box-drawing.",
    license: "OFL-1.1",
    pkg: "bundled (src/fonts)",
  },
  {
    id: "geist-mono",
    name: "Geist Mono",
    stack: '"Geist Mono Variable", "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace',
    note: "Geist's geometric mono — crisp at 11px, neutral.",
    license: "OFL-1.1",
    pkg: "@fontsource-variable/geist-mono",
  },
  {
    id: "fira-code",
    name: "Fira Code",
    stack: '"Fira Code Variable", "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace',
    note: "The ligature classic — -> => != render as single glyphs with ligatures on.",
    license: "OFL-1.1",
    pkg: "@fontsource-variable/fira-code",
  },
  {
    id: "cascadia",
    name: "Cascadia Code",
    stack: '"Cascadia Code Variable", "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace',
    note: "Microsoft's terminal-first face — box-drawing, legacy computing, ligatures.",
    license: "OFL-1.1",
    pkg: "@fontsource-variable/cascadia-code",
  },
  {
    id: "ibm-plex-mono",
    name: "IBM Plex Mono",
    stack: '"IBM Plex Mono", "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace',
    note: "Warmer, slab-tinged; very readable in long diffs.",
    license: "OFL-1.1",
    pkg: "@fontsource/ibm-plex-mono",
  },
  {
    id: "source-code-pro",
    name: "Source Code Pro",
    stack: '"Source Code Pro Variable", "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace',
    note: "Adobe's light, airy mono with generous spacing.",
    license: "OFL-1.1",
    pkg: "@fontsource-variable/source-code-pro",
  },
  {
    id: "victor-mono",
    name: "Victor Mono",
    stack: '"Victor Mono Variable", "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace',
    note: "Narrow, with handwritten italics for comments.",
    license: "OFL-1.1",
    pkg: "@fontsource-variable/victor-mono",
  },
  {
    id: "system-mono",
    name: "System",
    stack: 'ui-monospace, "SF Mono", "Cascadia Mono", Consolas, monospace',
    note: "SF Mono on macOS, Consolas on Windows. Nothing to load.",
    license: "system",
    pkg: "system",
  },
];

/** The terminal's own choice: follow the code font (the default), or a face of its own. */
export const SAME_AS_CODE = "same";

/** The face pair a palette speaks in while "Use the theme's fonts" is on. A palette not named
 *  here speaks in the house pair, Inter and JetBrains Mono. */
const THEME_FONTS: Partial<Record<PaletteId, { ui: string; code: string }>> = {
  reeve: { ui: "manrope", code: "jetbrains" },
  cupertino: { ui: "system", code: "system-mono" },
  geist: { ui: "geist", code: "geist-mono" },
};
const HOUSE = { ui: "inter", code: "jetbrains" };

export function themeFonts(palette: PaletteId): { ui: string; code: string } {
  return THEME_FONTS[palette] ?? HOUSE;
}

const KEYS = {
  ui: "reeve:font-ui",
  code: "reeve:font-code",
  terminal: "reeve:font-terminal",
  themeFonts: "reeve:theme-fonts",
  textSize: "reeve:text-size",
  codeSize: "reeve:code-size",
  ligatures: "reeve:code-ligatures",
  termSize: "reeve:term-size",
  termLine: "reeve:term-line",
  termCursor: "reeve:term-cursor",
} as const;

const FONT_EVENT = "reeve:font-changed";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* private mode etc. — still applies for this session */
  }
}

const optionsFor = (role: FontRole) => (role === "ui" ? UI_FONTS : MONO_FONTS);
const known = (role: FontRole, id: string | null) => !!id && optionsFor(role).some((o) => o.id === id);

/** Whether the palette's font pair is in force. Unset, it is on, unless the person had picked a
 *  UI or code face before the switch existed: a choice made is kept. */
export function getThemeFonts(): boolean {
  const stored = read(KEYS.themeFonts);
  if (stored === "on") return true;
  if (stored === "off") return false;
  return !known("ui", read(KEYS.ui)) && !known("code", read(KEYS.code));
}

/** The face in force for a role: the palette's while the switch is on, else the person's pick.
 *  The terminal answers `SAME_AS_CODE` until a face of its own is picked. */
export function getFont(role: FontRole): string {
  if (role === "terminal") {
    const stored = read(KEYS.terminal);
    return known("terminal", stored) ? (stored as string) : SAME_AS_CODE;
  }
  if (getThemeFonts()) return themeFonts(getPalette())[role];
  const stored = read(KEYS[role]);
  return known(role, stored) ? (stored as string) : HOUSE[role];
}

export function fontOption(role: FontRole, id: string): FontOption {
  const options = optionsFor(role);
  return options.find((o) => o.id === id) ?? options.find((o) => o.id === HOUSE[role === "ui" ? "ui" : "code"])!;
}

const VARS = { ui: "--sans", code: "--mono", terminal: "--term-font" } as const;

function setVar(name: string, value: string | null) {
  if (value === null) document.documentElement.style.removeProperty(name);
  else document.documentElement.style.setProperty(name, value);
}

/** Every face and size, from storage to the inline custom properties. */
export function applyFonts() {
  const ui = getFont("ui");
  const code = getFont("code");
  const terminal = getFont("terminal");
  setVar(VARS.ui, ui === HOUSE.ui ? null : fontOption("ui", ui).stack);
  setVar(VARS.code, code === HOUSE.code ? null : fontOption("code", code).stack);
  const termFace = terminal === SAME_AS_CODE ? code : terminal;
  setVar(VARS.terminal, termFace === HOUSE.code ? null : fontOption("terminal", termFace).stack);
  setVar("--fs-body", TEXT_SIZES[getTextSize()]);
  setVar("--code-delta", CODE_DELTAS[getCodeSize()]);
  setVar("--code-liga", getLigatures() ? "normal" : null);
}

function changed() {
  applyFonts();
  window.dispatchEvent(new CustomEvent(FONT_EVENT));
}

export function setFont(role: FontRole, id: string) {
  if (role === "terminal") {
    if (id !== SAME_AS_CODE && !known("terminal", id)) return;
    write(KEYS.terminal, id === SAME_AS_CODE ? null : id);
    changed();
    return;
  }
  if (!known(role, id)) return;
  if (getThemeFonts()) {
    // A face picked by hand ends the theme's pair; the other role keeps the face it showed.
    const other = role === "ui" ? "code" : "ui";
    write(KEYS[other], getFont(other));
    write(KEYS.themeFonts, "off");
  }
  write(KEYS[role], id);
  changed();
}

export function setThemeFonts(on: boolean) {
  if (!on) {
    // Off keeps what is on screen: the pair in force becomes the person's own picks.
    write(KEYS.ui, getFont("ui"));
    write(KEYS.code, getFont("code"));
  }
  write(KEYS.themeFonts, on ? "on" : "off");
  changed();
}

// -- sizes -----------------------------------------------------------------------------------

export type Size = "small" | "default" | "large";
const SIZES: Size[] = ["small", "default", "large"];

/** Reading text (answers, messages, tables): `--fs-body`. Default is the stylesheet's 14px. */
const TEXT_SIZES: Record<Size, string | null> = { small: "13px", default: null, large: "15px" };
/** Code everywhere it is shown, as a step from each surface's own size (`--code-delta`). */
const CODE_DELTAS: Record<Size, string | null> = { small: "-1px", default: null, large: "1px" };

const readSize = (key: string): Size => {
  const v = read(key);
  return SIZES.includes(v as Size) ? (v as Size) : "default";
};

export const getTextSize = () => readSize(KEYS.textSize);
export const getCodeSize = () => readSize(KEYS.codeSize);
export const getLigatures = () => read(KEYS.ligatures) === "on";

export function setTextSize(size: Size) {
  write(KEYS.textSize, size === "default" ? null : size);
  changed();
}

export function setCodeSize(size: Size) {
  write(KEYS.codeSize, size === "default" ? null : size);
  changed();
}

export function setLigatures(on: boolean) {
  write(KEYS.ligatures, on ? "on" : null);
  changed();
}

// -- the terminal ----------------------------------------------------------------------------

export type TermLine = "tight" | "normal" | "roomy";
export type TermCursor = "block" | "bar" | "underline";
export const TERM_SIZES = [11, 12, 13, 14] as const;
const TERM_LINES: Record<TermLine, number> = { tight: 1.1, normal: 1.2, roomy: 1.35 };

export interface TerminalOptions {
  fontFamily: string;
  fontSize: number;
  lineHeight: number;
  cursorStyle: TermCursor;
}

export const getTermSize = (): number => {
  const n = Number(read(KEYS.termSize));
  return (TERM_SIZES as readonly number[]).includes(n) ? n : 12;
};
export const getTermLine = (): TermLine => {
  const v = read(KEYS.termLine);
  return v === "tight" || v === "roomy" ? v : "normal";
};
export const getTermCursor = (): TermCursor => {
  const v = read(KEYS.termCursor);
  return v === "bar" || v === "underline" ? v : "block";
};

export function setTermSize(size: number) {
  if (!(TERM_SIZES as readonly number[]).includes(size)) return;
  write(KEYS.termSize, size === 12 ? null : String(size));
  changed();
}

export function setTermLine(line: TermLine) {
  write(KEYS.termLine, line === "normal" ? null : line);
  changed();
}

export function setTermCursor(cursor: TermCursor) {
  write(KEYS.termCursor, cursor === "block" ? null : cursor);
  changed();
}

/** What the terminal panel builds its emulator with, and re-applies when a choice changes. */
export function terminalOptions(): TerminalOptions {
  const face = getFont("terminal");
  return {
    fontFamily: fontOption("terminal", face === SAME_AS_CODE ? getFont("code") : face).stack,
    fontSize: getTermSize(),
    lineHeight: TERM_LINES[getTermLine()],
    cursorStyle: getTermCursor(),
  };
}

/** Call once at startup, next to initTheme(): applies the stored choices, and again whenever the
 *  palette changes, since the palette names the pair while the theme's fonts are on. */
export function initFonts() {
  applyFonts();
  window.addEventListener(PALETTE_EVENT, changed);
}

/** Re-render on any font or size change made anywhere. */
export function useFontPrefs<T>(get: () => T): T {
  const [value, setValue] = useState(get);
  useEffect(() => {
    const sync = () => setValue(get());
    window.addEventListener(FONT_EVENT, sync);
    window.addEventListener(PALETTE_EVENT, sync);
    return () => {
      window.removeEventListener(FONT_EVENT, sync);
      window.removeEventListener(PALETTE_EVENT, sync);
    };
    // `get` is one of this module's getters, stable for the component's life.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return value;
}

/** The picker's hook — the face in force, and the setter. */
export function useFont(role: FontRole): [string, (id: string) => void] {
  const id = useFontPrefs(() => getFont(role));
  return [id, (next: string) => setFont(role, next)];
}

export { FONT_EVENT };
