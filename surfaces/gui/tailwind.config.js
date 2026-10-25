// Tailwind reads the app's own tokens, the custom properties styles.css defines, so a theme or a
// palette is set in one place and the utility classes follow it.

// The colour tokens. Each becomes a colour of the same name in camel case (`--chrome-hover` is
// `chromeHover`).
const COLORS = [
  "paper", "panel", "canvas", "chrome", "chrome-hover", "ink", "muted", "faint", "line", "line-strong",
  "accent", "accent-soft", "ok", "ok-soft", "ok-line", "warn-ink", "warn-soft", "danger", "danger-soft",
  "teal-ink", "teal-soft", "teal-line", "solid", "on-solid",
];
const camel = (name) => name.replace(/-(\w)/g, (_, c) => c.toUpperCase());

// The tokens are colours Tailwind cannot take apart, so an opacity (`bg-panel/70`) is mixed with
// transparent instead; with none, the token is used as it is.
const token = (name) => ({ opacityValue }) =>
  opacityValue === undefined ? `var(${name})` : `color-mix(in srgb, var(${name}) calc(${opacityValue} * 100%), transparent)`;

// A palette with a shape of its own (Cupertino's rounder corners) sets these; every
// other palette falls back to Tailwind's own sizes and draws what it always drew.
const RADII = { sm: ["sm", "0.125rem"], DEFAULT: ["", "0.25rem"], md: ["md", "0.375rem"], lg: ["lg", "0.5rem"], xl: ["xl", "0.75rem"], xl2: ["xl2", "14px"], "2xl": ["2xl", "1rem"] };

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["selector", '[data-theme="dark"]'],
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
    // Streamdown and its plugins ship built components whose classes are in their bundles, not in
    // this source: without these, code blocks, tables and maths have no style.
    "./node_modules/streamdown/dist/*.js",
    "./node_modules/@streamdown/*/dist/*.js",
  ],
  theme: {
    extend: {
      colors: Object.fromEntries(COLORS.map((name) => [camel(name), token(`--${name}`)])),
      // The fonts the Typography pickers set (`--sans`, `--mono`), as styles.css uses them. A
      // list of its own here once disagreed with the app's on macOS.
      fontFamily: { sans: ["var(--sans)"], mono: ["var(--mono)"] },
      borderRadius: Object.fromEntries(
        Object.entries(RADII).map(([key, [suffix, size]]) => [key, `var(--rd${suffix ? `-${suffix}` : ""}, ${size})`]),
      ),
    },
  },
  plugins: [],
};
