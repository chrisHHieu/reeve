// Appearance: light, dark or the system's, and one of sixteen palettes. Both are kept in
// localStorage, on this device only, as macOS keeps its own appearance, and both apply before the
// sidecar answers: index.html sets data-theme and data-palette before the first paint, from the
// same keys, and this module keeps them current after that.
import { useEffect, useState } from "react";
import { GENERATED_PREVIEWS } from "./palettes.generated";

/** One preference kept in localStorage: read with a fallback, written (the fallback by removing the
 *  key), applied to the page, and announced so every control showing it follows. Storage can be
 *  unavailable (a private window); the choice then still applies until the page closes. */
function storedChoice<T extends string>(key: string, event: string, fallback: T, valid: (raw: string | null) => raw is T, apply: (value: T) => void) {
  const get = (): T => {
    try {
      const raw = localStorage.getItem(key);
      return valid(raw) ? raw : fallback;
    } catch {
      return fallback;
    }
  };
  const set = (value: T) => {
    try {
      if (value === fallback) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch {
      /* not kept, still applied */
    }
    apply(value);
    window.dispatchEvent(new CustomEvent(event));
  };
  const use = (): [T, (value: T) => void] => {
    const [value, setValue] = useState<T>(get);
    useEffect(() => {
      const follow = () => setValue(get());
      window.addEventListener(event, follow);
      return () => window.removeEventListener(event, follow);
    }, []);
    return [value, set];
  };
  return { get, set, use };
}

export type ThemePref = "light" | "dark" | "auto";

const systemDark = window.matchMedia?.("(prefers-color-scheme: dark)");

function applyTheme(pref: ThemePref) {
  const dark = pref === "dark" || (pref === "auto" && !!systemDark?.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

// The key predates the product's name and stays, so a pick made then is still read.
const theme = storedChoice<ThemePref>("reeve:theme", "reeve:theme-changed", "auto", (raw): raw is ThemePref => raw === "light" || raw === "dark", applyTheme);

export const getThemePref = theme.get;
export const setThemePref = theme.set;
/** The appearance control's value, following a change made anywhere else. */
export const useThemePref = theme.use;

// == Palette ====================================================================
// `data-palette` on <html> names the colour table: the hand-tuned ones in styles.css, the ones
// taken from a table in palettes.css (scripts/make_palettes.py). No attribute is
// Harbor, the stylesheet's own `:root`. Reeve is the default a person gets until they pick,
// so it is the absence of the stored key and Harbor, once picked, is stored. The
// values below are the Settings preview chips only — the live colours are the stylesheet's.
export type PaletteId =
  | "reeve"
  | "harbor"
  | "one"
  | "catppuccin"
  | "rose-pine"
  | "nord"
  | "gruvbox"
  | "solarized"
  | "tokyo-night"
  | "github"
  | "cupertino"
  | "graphite"
  | "paper"
  | "midnight"
  | "geist"
  | "linear";

export const DEFAULT_PALETTE: PaletteId = "reeve";

/** Where a palette sits in the picker: ours, the ones people know from their editor, and the
 *  ones drawn from a product's look. */
export type PaletteGroup = "reeve" | "editor" | "product";

export const PALETTE_GROUPS: { id: PaletteGroup; name: string }[] = [
  { id: "reeve", name: "Reeve" },
  { id: "editor", name: "Editor classics" },
  { id: "product", name: "Product-inspired" },
];

const PALETTE_KEY = "reeve:palette";
export const PALETTE_EVENT = "reeve:palette-changed";

/** The three surfaces a preview chip paints, per appearance. */
export interface PalettePreview {
  canvas: string;
  chrome: string;
  accent: string;
  line: string;
}

export interface PaletteInfo {
  id: PaletteId;
  name: string;
  group: PaletteGroup;
  intent: string;
  light: PalettePreview;
  dark: PalettePreview;
}

const generated = (id: PaletteId) => GENERATED_PREVIEWS[id];

export const PALETTES: PaletteInfo[] = [
  {
    id: "reeve",
    name: "Reeve",
    group: "reeve",
    intent: "Reeve's own: cool parchment neutrals with a plum cast, one plum accent for every action, and Manrope with JetBrains Mono.",
    ...generated("reeve"),
  },
  {
    id: "harbor",
    name: "Harbor",
    group: "reeve",
    intent:
      "The first default: warm papered neutrals for office reading, deep blue-slate for late code, and one harbor teal-blue that owns every action.",
    light: { canvas: "#faf8f4", chrome: "#f4f1eb", accent: "#0d6a8a", line: "rgba(32, 29, 24, 0.22)" },
    dark: { canvas: "#151a20", chrome: "#0f141a", accent: "#6cc7dc", line: "rgba(233, 237, 241, 0.22)" },
  },
  {
    id: "cupertino",
    name: "Cupertino",
    group: "product",
    intent: "The Apple system palette verbatim — systemBlue on grouped backgrounds, Xcode's syntax on top.",
    light: { canvas: "#ffffff", chrome: "#f2f2f7", accent: "#007aff", line: "rgba(60, 60, 67, 0.49)" },
    dark: { canvas: "#000000", chrome: "#1c1c1e", accent: "#0a84ff", line: "rgba(235, 235, 245, 0.32)" },
  },
  {
    id: "graphite",
    name: "Graphite",
    group: "product",
    intent: "Cool, desaturated coding neutrals with a steel-teal accent that stays out of your diffs.",
    light: { canvas: "#fafbfc", chrome: "#f3f5f8", accent: "#37718c", line: "rgba(23, 26, 30, 0.22)" },
    dark: { canvas: "#14161a", chrome: "#101215", accent: "#7fb6ce", line: "rgba(232, 236, 241, 0.22)" },
  },
  {
    id: "paper",
    name: "Paper",
    group: "product",
    intent: "Warm, glare-free paper surfaces and an ember accent for reading-heavy office days.",
    light: { canvas: "#f7f3ec", chrome: "#f2ece3", accent: "#a4571f", line: "rgba(43, 38, 32, 0.24)" },
    dark: { canvas: "#1a1713", chrome: "#14110e", accent: "#d99a5b", line: "rgba(242, 237, 230, 0.22)" },
  },
  {
    id: "midnight",
    name: "Midnight",
    group: "product",
    intent: "True-black OLED depth with Apple's elevated greys and an indigo night accent.",
    light: { canvas: "#ffffff", chrome: "#f7f7f9", accent: "#5856d6", line: "rgba(17, 17, 20, 0.22)" },
    dark: { canvas: "#000000", chrome: "#0a0a0c", accent: "#7d7aff", line: "rgba(245, 245, 247, 0.22)" },
  },
  {
    id: "one",
    name: "One",
    group: "editor",
    intent: "The editor canon: Atom One's blue-grey canvas and warm syntax, carried into a full app shell.",
    light: { canvas: "#fafafa", chrome: "#eaeaeb", accent: "#3f6ed6", line: "rgba(56, 58, 66, 0.22)" },
    dark: { canvas: "#282c34", chrome: "#21252b", accent: "#61afef", line: "rgba(171, 178, 191, 0.22)" },
  },
  {
    id: "catppuccin",
    name: "Catppuccin",
    group: "editor",
    intent: "Latte by day, Mocha by night: soft pastels on lavender-grey, mauve for action.",
    ...generated("catppuccin"),
  },
  {
    id: "rose-pine",
    name: "Rosé Pine",
    group: "editor",
    intent: "Dawn and Main: muted, low-glare surfaces with pine and iris.",
    ...generated("rose-pine"),
  },
  {
    id: "nord",
    name: "Nord",
    group: "editor",
    intent: "Arctic blue-greys and frost accents; the calmest of the set.",
    ...generated("nord"),
  },
  {
    id: "gruvbox",
    name: "Gruvbox",
    group: "editor",
    intent: "Retro warm contrast: cream by day, charcoal and orange at night.",
    ...generated("gruvbox"),
  },
  {
    id: "solarized",
    name: "Solarized",
    group: "editor",
    intent: "The same accents on both faces, built for long reading.",
    ...generated("solarized"),
  },
  {
    id: "tokyo-night",
    name: "Tokyo Night",
    group: "editor",
    intent: "Day and Night: neon-city blues on deep navy.",
    ...generated("tokyo-night"),
  },
  {
    id: "github",
    name: "GitHub",
    group: "editor",
    intent: "Primer's light and dark: what a pull request looks like.",
    ...generated("github"),
  },
  {
    id: "geist",
    name: "Geist",
    group: "product",
    intent: "Vercel's Geist: white/black canvases, hairline borders and one functional blue carrying every action.",
    light: { canvas: "#fafafa", chrome: "#f2f2f2", accent: "#006eee", line: "rgba(0, 0, 0, 0.22)" },
    dark: { canvas: "#000000", chrome: "#0a0a0a", accent: "#3291ff", line: "rgba(237, 237, 237, 0.22)" },
  },
  {
    id: "linear",
    name: "Linear",
    group: "product",
    intent: "Linear's near-black surface ladder and hairlines, with lavender indigo reserved for action.",
    light: { canvas: "#fbfbfc", chrome: "#f1f2f4", accent: "#5e6ad2", line: "rgba(31, 32, 35, 0.22)" },
    dark: { canvas: "#08090a", chrome: "#010102", accent: "#828fff", line: "rgba(247, 248, 248, 0.22)" },
  },
];

// No attribute is Harbor, the stylesheet's `:root`, as the pre-paint script has it.
function applyPalette(id: PaletteId) {
  if (id === "harbor") delete document.documentElement.dataset.palette;
  else document.documentElement.dataset.palette = id;
}

const palette = storedChoice<PaletteId>(PALETTE_KEY, PALETTE_EVENT, DEFAULT_PALETTE, (raw): raw is PaletteId => PALETTES.some((p) => p.id === raw), applyPalette);

export const getPalette = palette.get;
export const setPalette = palette.set;
export const usePalette = palette.use;

/** Once, at startup: applies what is stored, and follows the system's appearance while on auto. */
export function initTheme() {
  applyTheme(getThemePref());
  applyPalette(getPalette());
  systemDark?.addEventListener("change", () => {
    if (getThemePref() === "auto") applyTheme("auto");
  });
}
