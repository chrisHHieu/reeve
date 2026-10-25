// The connectors' logos, by the stable `logo` id their API descriptor gives: the brand's real mark in
// one colour, and a name for it. No colour is kept here: it comes from the API (`brand_color`), so the
// descriptor alone decides it. An empty or unknown id gets FALLBACK, a neutral plug.
//
// Most marks are from the `simple-icons` package: official one-path 24x24 marks that paint with
// `currentColor`, so ConnectorIcon and ConnectorBadge colour them. Marks the package no longer
// carries, and the vendors' own, are path data in `marks.ts`. Brands with no published one-colour
// mark (Descript, Clay) and the utilities that are no brand (email, the browser, MCP, the plug) are
// simple glyphs drawn below. The file is `.tsx` because its entries are JSX.

import type { SimpleIcon } from "simple-icons";
import {
  siDatadog,
  siDuckduckgo,
  siFigma,
  siGithub,
  siGmail,
  siGooglecalendar,
  siGoogledocs,
  siGoogledrive,
  siGooglesheets,
  siGoogleslides,
  siJira,
  siNotion,
  siPagerduty,
  siPostman,
  siRedmine,
  siTelegram,
  siTrello,
  siUpwork,
  siWhatsapp,
} from "simple-icons";
import * as MARK from "./marks";

// `JSX` is global with the react-jsx runtime and @types/react.
type LogoComponent = () => JSX.Element;

export interface ConnectorRegistryEntry {
  label: string;
  logo: LogoComponent;
}

/** A mark on the 24x24 grid, as one path painted in `currentColor`. */
function onGrid(d: string): LogoComponent {
  return () => (
    <svg viewBox="0 0 24 24" width="100%" height="100%" fill="currentColor" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

const simpleIcon = (icon: SimpleIcon): ConnectorRegistryEntry => ({ label: icon.title, logo: onGrid(icon.path) });
const pathMark = (label: string, d: string): ConnectorRegistryEntry => ({ label, logo: onGrid(d) });

/** A vendor's mark drawn off simple-icons' grid: it keeps its own viewBox and the SVG scales it.
 *  Scaling the path data by hand would be redrawing someone's trademark with arithmetic, and a
 *  rounding slip shows. The fill rule comes from the vendor's file too: Tavily's is `evenodd`, which
 *  looks the same either way today, but the next mark may have a real hole in it. */
function ownViewBox(label: string, viewBox: string, paths: string[], fillRule: "nonzero" | "evenodd" = "nonzero"): ConnectorRegistryEntry {
  return {
    label,
    logo: () => (
      <svg viewBox={viewBox} width="100%" height="100%" fill="currentColor" aria-hidden="true">
        {paths.map((d, i) => (
          <path key={i} d={d} fillRule={fillRule} clipRule={fillRule} />
        ))}
      </svg>
    ),
  };
}

/** A line glyph of this app's own, for a utility or a brand with no one-colour mark. */
function glyph(label: string, shapes: JSX.Element): ConnectorRegistryEntry {
  return {
    label,
    logo: () => (
      <svg viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {shapes}
      </svg>
    ),
  };
}

const windowFrame = (
  <>
    <rect x="3" y="4.5" width="18" height="15" rx="2.5" />
    <path d="M3 9h18" />
  </>
);

/** The plug, for a logo id nobody knows. */
export const FALLBACK: ConnectorRegistryEntry = glyph("Connector", <path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0V7zM12 16v5" />);

// The utilities have no descriptor, so no API colour; the registry gives theirs. Without one a
// transcript row would draw them grey while the Connectors list, which has its own colours, did
// not: the mismatch the running-effects spec points out.
const UTILITY_COLORS: Record<string, string> = {
  browser: "#2f6fd0",
  "computer-use": "#0f766e",
  "chrome-devtools": "#b45309",
};

export function defaultBrandColor(logo?: string): string | undefined {
  const id = (logo ?? "").trim();
  return id ? UTILITY_COLORS[id] : undefined;
}

export const CONNECTORS: Record<string, ConnectorRegistryEntry> = {
  datadog: simpleIcon(siDatadog),
  // Search engines (Settings ▸ Web search): not connectors, drawn the same way.
  duckduckgo: simpleIcon(siDuckduckgo),
  exa: ownViewBox("Exa", "0 0 106.417 129", [MARK.EXA], "evenodd"),
  tavily: ownViewBox("Tavily", "0 0 55 55", [MARK.TAVILY], "evenodd"),
  figma: simpleIcon(siFigma),
  firecrawl: ownViewBox("Firecrawl", "0 0 200 284", [MARK.FIRECRAWL]),
  github: simpleIcon(siGithub),
  gmail: simpleIcon(siGmail),
  google_calendar: simpleIcon(siGooglecalendar),
  google_docs: simpleIcon(siGoogledocs),
  google_drive: simpleIcon(siGoogledrive),
  google_sheets: simpleIcon(siGooglesheets),
  google_slides: simpleIcon(siGoogleslides),
  jira: simpleIcon(siJira),
  notion: simpleIcon(siNotion),
  pagerduty: simpleIcon(siPagerduty),
  postman: simpleIcon(siPostman),
  redmine: simpleIcon(siRedmine),
  trello: simpleIcon(siTrello),
  tcbs: ownViewBox("Techcom Securities", "182 0 36 24", MARK.TCBS),
  telegram: simpleIcon(siTelegram),
  upwork: simpleIcon(siUpwork),
  whatsapp: simpleIcon(siWhatsapp),
  slack: pathMark("Slack", MARK.SLACK),
  salesforce: pathMark("Salesforce", MARK.SALESFORCE),
  outlook: pathMark("Outlook", MARK.OUTLOOK),
  onedrive: pathMark("OneDrive", MARK.CLOUD),
  teams: pathMark("Microsoft Teams", MARK.TEAMS),
  canva: pathMark("Canva", MARK.CANVA),
  descript: glyph("Descript", <path d="M4 5.5h16M4 10h11M4 14.5h14M4 19h8" />),
  clay: glyph("Clay", <path d="M3 19a9 9 0 0 1 18 0zM1.5 19h21" />),
  email: glyph(
    "Email",
    <>
      <rect x="3" y="5" width="18" height="14" rx="2.5" />
      <path d="M3.5 7.5 12 13.5l8.5-6" />
    </>,
  ),
  // A window, not a globe: it is a local browser Reeve drives, and two dots on a title bar say "an
  // app's window" at 14px, where a globe read as web search.
  browser: glyph(
    "Browser",
    <>
      {windowFrame}
      <circle cx="6" cy="6.8" r="0.85" fill="currentColor" stroke="none" />
      <circle cx="8.4" cy="6.8" r="0.85" fill="currentColor" stroke="none" />
    </>,
  ),
  // Reeve's own desktop-control server: a screen with a pointer.
  "computer-use": glyph(
    "Computer use",
    <>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M9 20h6M12 16v4" />
      <path d="M9.5 8l5 2-2 .8-.8 2z" fill="currentColor" stroke="none" />
    </>,
  ),
  // Chrome DevTools: a window with brackets, the inspector rather than the browser.
  "chrome-devtools": glyph(
    "Chrome DevTools",
    <>
      {windowFrame}
      <path d="m9.5 12.2-2 2 2 2M14.5 12.2l2 2-2 2" />
    </>,
  ),
  mcp: glyph(
    "MCP",
    <>
      <circle cx="12" cy="12" r="2.2" />
      <circle cx="5" cy="6" r="1.8" />
      <circle cx="19" cy="6" r="1.8" />
      <circle cx="12" cy="20" r="1.8" />
      <path d="M10.3 10.6 6.4 7.3M13.7 10.6l3.9-3.3M12 14.2V18.2" />
    </>,
  ),
};

/** The entry for a logo id and the key it was found under; an empty or unknown id gives FALLBACK
 *  under "fallback", so a caller (or a test) can tell a miss from a hit. */
export function resolveConnector(logo?: string): { key: string; entry: ConnectorRegistryEntry } {
  const id = (logo ?? "").trim();
  const known = id && Object.prototype.hasOwnProperty.call(CONNECTORS, id); // not `in`: "constructor" is no logo
  return known ? { key: id, entry: CONNECTORS[id] } : { key: "fallback", entry: FALLBACK };
}
