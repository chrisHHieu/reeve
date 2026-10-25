// A connector's logo, from the registry by its `logo` id, in the connector's `brand_color`:
// ConnectorIcon is the bare mark, ConnectorBadge the mark on a rounded square of its colour. The
// colour comes in through props from the API, so the descriptor alone decides it (no stylesheet
// keeps a colour per connector). A `logo` id that is empty or unknown draws a neutral plug, still in
// the colour given. `--brand` and the paler `--brand-soft` behind a badge are set inline.

import type { CSSProperties } from "react";
import { defaultBrandColor, resolveConnector } from "./registry";

export const NEUTRAL = "#6b7280"; // the grey a descriptor has when it gives no colour

/** Whatever carries a connector's visuals; a whole `Connector` fits. */
export interface ConnectorVisual {
  logo?: string;
  brand_color?: string;
  title?: string;
  label?: string;
}

/** `#rgb` or `#rrggbb` as its three channels, or null when it is neither. */
function channels(hex: string): [number, number, number] | null {
  let digits = hex.trim().replace(/^#/, "");
  if (digits.length === 3) digits = [...digits].map((d) => d + d).join("");
  if (!/^[0-9a-fA-F]{6}$/.test(digits)) return null;
  return [0, 2, 4].map((at) => parseInt(digits.slice(at, at + 2), 16)) as [number, number, number];
}

/** `#rgb` / `#rrggbb` as `rgba(r, g, b, alpha)`; "" for anything else. */
export function hexToRgba(hex: string, alpha: number): string {
  const rgb = channels(hex);
  return rgb ? `rgba(${rgb.join(", ")}, ${alpha})` : "";
}

/** Whether a mark is so dark (GitHub's #1f2328, Notion's) it would disappear on a dark panel. The
 *  dark theme then mixes a bare icon toward the ink, and sets a badge on a light plate (styles.css),
 *  where the true colour is right. */
export function isDarkMark(color: string): boolean {
  const rgb = channels(color);
  if (!rgb) return false;
  const [r, g, b] = rgb.map((channel) => channel / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.25;
}

function brandStyle(color: string, size: number): CSSProperties {
  return { ["--brand"]: color, ["--brand-soft"]: hexToRgba(color, 0.12) || "var(--line)", color, width: size, height: size } as CSSProperties;
}

/** The descriptor's colour when it has one; the registry's own for the utilities with no descriptor
 *  row (the browser, computer use); grey otherwise. */
function colorOf(connector: ConnectorVisual): string {
  return (connector.brand_color || "").trim() || defaultBrandColor(connector.logo) || NEUTRAL;
}

function look(connector: ConnectorVisual, title?: string) {
  const { key, entry } = resolveConnector(connector.logo);
  return { key, entry, color: colorOf(connector), label: title ?? connector.label ?? connector.title ?? entry.label };
}

/** The bare mark, in the connector's colour. */
export function ConnectorIcon({ connector, size = 18, title }: { connector: ConnectorVisual; size?: number; title?: string }) {
  const { key, entry, color, label } = look(connector, title);
  const Logo = entry.logo;
  return (
    <span className="connector-icon" data-logo={key} data-brand={key} data-dark-mark={isDarkMark(color) || undefined} role="img" aria-label={label} title={label} style={brandStyle(color, size)}>
      <Logo />
    </span>
  );
}

/** The mark on a rounded square of its paler colour. */
export function ConnectorBadge({ connector, size = 32, title }: { connector: ConnectorVisual; size?: number; title?: string }) {
  const { key, color, label } = look(connector, title);
  return (
    <span className="connector-badge" data-logo={key} data-brand={key} role="img" aria-label={label} title={label} style={brandStyle(color, size)}>
      <ConnectorIcon connector={connector} size={Math.round(size * 0.56)} title={label} />
    </span>
  );
}
