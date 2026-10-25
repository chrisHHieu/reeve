// The app's line icons: Lucide's geometry (lucide-static 0.469.0, ISC licence), drawn on its
// 24px grid at a 1.75 stroke, currentColor, rounded caps and joins. One set for the whole app
// (owner, 2026-10-12, from the "Icon set + hover motion" mockup): it replaced a hand-drawn set
// that mixed three stroke weights with filled 16px glyphs.
//
// Every glyph sits inside one `<g class="ico-g">`, and the parts that move carry a class
// (`ico-lid`, `ico-hand`, …). The motion itself is CSS (`ui/icon-motion.css`): a small one when
// the control holding the icon is hovered, a loop while `running` is set. The motion moves the
// group and its parts, never the <svg>, so a caller that rotates or flips the icon through its
// own class keeps doing so.

import type { ReactNode } from "react";

const FOLDER = "M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z";
const FOLDER_OPEN =
  "m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2";
const SPARKLE =
  "M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z";
const BRAIN_L = "M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z";
const BRAIN_R = "M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z";
const DOC = "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z";
const DOC_FOLD = "M14 2v4a2 2 0 0 0 2 2h4";
const PANEL = <rect width="18" height="18" x="3" y="3" rx="2" />;
const GEAR =
  "M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z";
const WRENCH =
  "M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z";
const UNDO = (
  <>
    <path d="M9 14 4 9l5-5" />
    <path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11" />
  </>
);
const SHIELD =
  "M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z";
const CLOCK_FACE = <circle cx="12" cy="12" r="10" />;

/** What each name draws. Parts that move carry an `ico-*` class (see ui/icon-motion.css). */
const GLYPHS = {
  sparkle: (
    <>
      <path className="ico-star" d={SPARKLE} />
      <g className="ico-tw">
        <path d="M20 3v4" />
        <path d="M22 5h-4" />
        <path d="M4 17v2" />
        <path d="M5 18H3" />
      </g>
    </>
  ),
  sidebar: (
    <>
      {PANEL}
      <path className="ico-div" d="M9 3v18" />
    </>
  ),
  sidebarRight: (
    <>
      {PANEL}
      <path className="ico-div" d="M15 3v18" />
    </>
  ),
  status: (
    <>
      <path d="m12 14 4-4" />
      <path d="M3.34 19a10 10 0 1 1 17.32 0" />
    </>
  ),
  ops: (
    <>
      <line className="ico-bar" x1="18" x2="18" y1="20" y2="10" />
      <line className="ico-bar" x1="12" x2="12" y1="20" y2="4" />
      <line className="ico-bar" x1="6" x2="6" y1="20" y2="14" />
    </>
  ),
  chart: (
    <>
      <line className="ico-bar" x1="18" x2="18" y1="20" y2="10" />
      <line className="ico-bar" x1="12" x2="12" y1="20" y2="4" />
      <line className="ico-bar" x1="6" x2="6" y1="20" y2="14" />
    </>
  ),
  signOut: (
    <>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline className="ico-arrow" points="16 17 21 12 16 7" />
      <line className="ico-arrow" x1="21" x2="9" y1="12" y2="12" />
    </>
  ),
  chat: <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
  messageQuestion: (
    <>
      <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" />
      <g className="ico-q">
        <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
        <path d="M12 17h.01" />
      </g>
    </>
  ),
  // A skill: the open book with lines in it — written instructions.
  book: (
    <>
      <path d="M12 7v14" />
      <path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z" />
      <path className="ico-ln" d="M6 8h2" />
      <path className="ico-ln" d="M16 8h2" />
      <path className="ico-ln" d="M6 12h2" />
      <path className="ico-ln" d="M16 12h2" />
    </>
  ),
  bookPlus: (
    <>
      <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H19a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H6.5a1 1 0 0 1 0-5H20" />
      <g className="ico-plus">
        <path d="M12 7v6" />
        <path d="M9 10h6" />
      </g>
    </>
  ),
  search: (
    <g className="ico-lens">
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.3-4.3" />
    </g>
  ),
  // The closed folder opens while its control is hovered.
  folder: (
    <>
      <path className="ico-closed" d={FOLDER} />
      <path className="ico-open" d={FOLDER_OPEN} />
    </>
  ),
  folderOpen: <path d={FOLDER_OPEN} />,
  folderPlus: (
    <>
      <g className="ico-plus">
        <path d="M12 10v6" />
        <path d="M9 13h6" />
      </g>
      <path d={FOLDER} />
    </>
  ),
  folderTree: (
    <>
      <path className="ico-kid" d="M20 10a1 1 0 0 0 1-1V6a1 1 0 0 0-1-1h-2.5a1 1 0 0 1-.8-.4l-.9-1.2A1 1 0 0 0 15 3h-2a1 1 0 0 0-1 1v5a1 1 0 0 0 1 1Z" />
      <path className="ico-kid" d="M20 21a1 1 0 0 0 1-1v-3a1 1 0 0 0-1-1h-2.9a1 1 0 0 1-.88-.55l-.42-.85a1 1 0 0 0-.92-.6H13a1 1 0 0 0-1 1v5a1 1 0 0 0 1 1Z" />
      <path d="M3 5a2 2 0 0 0 2 2h3" />
      <path d="M3 3v13a2 2 0 0 0 2 2h3" />
    </>
  ),
  plus: (
    <>
      <path d="M5 12h14" />
      <path d="M12 5v14" />
    </>
  ),
  minus: <path d="M5 12h14" />,
  clock: (
    <>
      {CLOCK_FACE}
      <polyline className="ico-hand" points="12 6 12 12 16 14" />
    </>
  ),
  history: (
    <>
      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path d="M3 3v5h5" />
      <path className="ico-hand" d="M12 7v5l4 2" />
    </>
  ),
  calendarClock: (
    <>
      <path d="M21 7.5V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3.5" />
      <path d="M16 2v4" />
      <path d="M8 2v4" />
      <path d="M3 10h5" />
      <path className="ico-hand2" d="M17.5 17.5 16 16.3V14" />
      <circle cx="16" cy="16" r="6" />
    </>
  ),
  alarmClock: (
    <>
      <circle cx="12" cy="13" r="8" />
      <path d="M12 9v4l2 2" />
      <path d="M5 3 2 6" />
      <path d="m22 6-3-3" />
      <path d="M6.38 18.7 4 21" />
      <path d="M17.64 18.67 20 21" />
    </>
  ),
  // Lucide `palette`: Settings → Appearance.
  palette: (
    <>
      <path d="M12 22a1 1 0 0 1 0-20 10 9 0 0 1 10 9 5 5 0 0 1-5 5h-2.25a1.75 1.75 0 0 0-1.4 2.8l.3.4a1.75 1.75 0 0 1-1.4 2.8z" />
      <circle cx="13.5" cy="6.5" r=".5" fill="currentColor" />
      <circle cx="17.5" cy="10.5" r=".5" fill="currentColor" />
      <circle cx="6.5" cy="12.5" r=".5" fill="currentColor" />
      <circle cx="8.5" cy="7.5" r=".5" fill="currentColor" />
    </>
  ),
  sliders: (
    <>
      <line x1="21" x2="14" y1="4" y2="4" />
      <line x1="10" x2="3" y1="4" y2="4" />
      <line x1="21" x2="12" y1="12" y2="12" />
      <line x1="8" x2="3" y1="12" y2="12" />
      <line x1="21" x2="16" y1="20" y2="20" />
      <line x1="12" x2="3" y1="20" y2="20" />
      <line className="ico-k1" x1="14" x2="14" y1="2" y2="6" />
      <line className="ico-k2" x1="8" x2="8" y1="10" y2="14" />
      <line className="ico-k3" x1="16" x2="16" y1="18" y2="22" />
    </>
  ),
  gear: (
    <>
      <path d={GEAR} />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  inbox: (
    <>
      <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
      <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
    </>
  ),
  code: (
    <>
      <polyline className="ico-r" points="16 18 22 12 16 6" />
      <polyline className="ico-l" points="8 6 2 12 8 18" />
    </>
  ),
  codeXml: (
    <>
      <path className="ico-r" d="m18 16 4-4-4-4" />
      <path className="ico-l" d="m6 8-4 4 4 4" />
      <path d="m14.5 4-5 16" />
    </>
  ),
  braces: (
    <>
      <path className="ico-l" d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5c0 1.1.9 2 2 2h1" />
      <path className="ico-r" d="M16 21h1a2 2 0 0 0 2-2v-5c0-1.1.9-2 2-2a2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1" />
    </>
  ),
  wrench: <path d={WRENCH} />,
  pencil: (
    <g className="ico-pen">
      <path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z" />
      <path d="m15 5 4 4" />
    </g>
  ),
  // An edit: the pen over the line it writes.
  pencilLine: (
    <>
      <path className="ico-base" d="M12 20h9" />
      <g className="ico-pen">
        <path d="M16.376 3.622a1 1 0 0 1 3.002 3.002L7.368 18.635a2 2 0 0 1-.855.506l-2.872.838a.5.5 0 0 1-.62-.62l.838-2.872a2 2 0 0 1 .506-.854z" />
        <path d="m15 5 3 3" />
      </g>
    </>
  ),
  edit: (
    <>
      <path className="ico-base" d="M12 20h9" />
      <g className="ico-pen">
        <path d="M16.376 3.622a1 1 0 0 1 3.002 3.002L7.368 18.635a2 2 0 0 1-.855.506l-2.872.838a.5.5 0 0 1-.62-.62l.838-2.872a2 2 0 0 1 .506-.854z" />
        <path d="m15 5 3 3" />
      </g>
    </>
  ),
  branch: (
    <>
      <line x1="6" x2="6" y1="3" y2="15" />
      <circle cx="18" cy="6" r="3" />
      <circle cx="6" cy="18" r="3" />
      <path className="ico-arc" d="M18 9a9 9 0 0 1-9 9" />
    </>
  ),
  gitFork: (
    <>
      <circle cx="12" cy="18" r="3" />
      <circle className="ico-l" cx="6" cy="6" r="3" />
      <circle className="ico-r" cx="18" cy="6" r="3" />
      <path d="M18 9v2c0 .6-.4 1-1 1H7c-.6 0-1-.4-1-1V9" />
      <path d="M12 12v3" />
    </>
  ),
  gitCompare: (
    <>
      <circle cx="18" cy="18" r="3" />
      <circle cx="6" cy="6" r="3" />
      <path className="ico-a" d="M13 6h3a2 2 0 0 1 2 2v7" />
      <path className="ico-b" d="M11 18H8a2 2 0 0 1-2-2V9" />
    </>
  ),
  gitPullRequest: (
    <>
      <circle cx="6" cy="6" r="3" />
      <line x1="6" x2="6" y1="9" y2="21" />
      <g className="ico-up">
        <circle cx="18" cy="18" r="3" />
        <path d="M13 6h3a2 2 0 0 1 2 2v7" />
      </g>
    </>
  ),
  arrowLeft: (
    <>
      <path d="m12 19-7-7 7-7" />
      <path d="M19 12H5" />
    </>
  ),
  arrowRight: (
    <>
      <path d="M5 12h14" />
      <path d="m12 5 7 7-7 7" />
    </>
  ),
  arrowUp: (
    <>
      <path d="m5 12 7-7 7 7" />
      <path d="M12 19V5" />
    </>
  ),
  arrowDownToLine: (
    <>
      <g className="ico-arrow">
        <path d="M12 17V3" />
        <path d="m6 11 6 6 6-6" />
      </g>
      <path d="M19 21H5" />
    </>
  ),
  cornerDownLeft: (
    <>
      <polyline points="9 10 4 15 9 20" />
      <path d="M20 4v7a4 4 0 0 1-4 4H4" />
    </>
  ),
  copy: (
    <>
      <rect className="ico-front" width="14" height="14" x="8" y="8" rx="2" ry="2" />
      <path className="ico-back" d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
    </>
  ),
  refresh: (
    <>
      <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
      <path d="M8 16H3v5" />
    </>
  ),
  rotateCw: (
    <>
      <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
    </>
  ),
  loader: <path d="M21 12a9 9 0 1 1-6.219-8.56" />,
  panelClose: (
    <>
      {PANEL}
      <path d="M15 3v18" />
      <path className="ico-arr" d="m8 9 3 3-3 3" />
    </>
  ),
  panelOpen: (
    <>
      {PANEL}
      <path d="M15 3v18" />
      <path className="ico-arr" d="m10 15-3-3 3-3" />
    </>
  ),
  plug: (
    <>
      <path className="ico-cord" d="M12 22v-5" />
      <g className="ico-head">
        <path d="M9 8V2" />
        <path d="M15 8V2" />
        <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" />
      </g>
    </>
  ),
  audit: (
    <>
      <path className="ico-ln" d="M15 12h-5" />
      <path className="ico-ln" d="M15 8h-5" />
      <path d="M19 17V5a2 2 0 0 0-2-2H4" />
      <path d="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3" />
    </>
  ),
  // Instructions: a scroll with writing on it.
  scrollText: (
    <>
      <path className="ico-ln" d="M15 8h-5" />
      <path className="ico-ln" d="M15 12h-5" />
      <path d="M19 17V5a2 2 0 0 0-2-2H4" />
      <path d="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3" />
    </>
  ),
  chevronRight: <path d="m9 18 6-6-6-6" />,
  chevronDown: <path d="m6 9 6 6 6-6" />,
  chevronUp: <path d="m18 15-6-6-6 6" />,
  undo: UNDO,
  // The plan, the to-dos: a list with ticks in it.
  checklist: (
    <>
      <path className="ico-tick" d="m3 7 2 2 4-4" />
      <path className="ico-tick" d="m3 17 2 2 4-4" />
      <path d="M13 6h8" />
      <path d="M13 12h8" />
      <path d="M13 18h8" />
    </>
  ),
  list: (
    <>
      <path d="M3 6h.01" />
      <path d="M3 12h.01" />
      <path d="M3 18h.01" />
      <path className="ico-ln" d="M8 6h13" />
      <path className="ico-ln" d="M8 12h13" />
      <path className="ico-ln" d="M8 18h13" />
    </>
  ),
  // The model folding its own context: what is above and below closes onto one line.
  fold: (
    <>
      <path d="M12 22v-6" />
      <path d="M12 8V2" />
      <path d="M4 12H2" />
      <path d="M10 12H8" />
      <path d="M16 12h-2" />
      <path d="M22 12h-2" />
      <path className="ico-dn" d="m15 19-3-3-3 3" />
      <path className="ico-up" d="m15 5-3 3-3-3" />
    </>
  ),
  moreHorizontal: (
    <>
      <circle className="ico-dot" cx="5" cy="12" r="1" />
      <circle className="ico-dot" cx="12" cy="12" r="1" />
      <circle className="ico-dot" cx="19" cy="12" r="1" />
    </>
  ),
  moreVertical: (
    <>
      <circle className="ico-dot" cx="12" cy="5" r="1" />
      <circle className="ico-dot" cx="12" cy="12" r="1" />
      <circle className="ico-dot" cx="12" cy="19" r="1" />
    </>
  ),
  pin: (
    <>
      <path d="M12 17v5" />
      <path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z" />
    </>
  ),
  archive: (
    <>
      <rect className="ico-lid" width="20" height="5" x="2" y="3" rx="1" />
      <path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8" />
      <path d="M10 12h4" />
    </>
  ),
  trash: (
    <>
      <g className="ico-lid">
        <path d="M3 6h18" />
        <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
      </g>
      <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" />
      <line x1="10" x2="10" y1="11" y2="17" />
      <line x1="14" x2="14" y1="11" y2="17" />
    </>
  ),
  shield: (
    <>
      <path d={SHIELD} />
      <path className="ico-chk" d="m9 12 2 2 4-4" />
    </>
  ),
  // A padlock: a folder the agent may read and not write (Lucide `lock`).
  lock: (
    <>
      <rect x="3" y="11" width="18" height="11" rx="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </>
  ),
  // A paper clip: something from this computer added to a message (Lucide `paperclip`).
  paperclip: (
    <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />
  ),
  // A document with lines: a file, and what a read looks at.
  file: (
    <>
      <path d={DOC} />
      <path d={DOC_FOLD} />
      <path className="ico-ln" d="M10 9H8" />
      <path className="ico-ln" d="M16 13H8" />
      <path className="ico-ln" d="M16 17H8" />
    </>
  ),
  fileCode: (
    <>
      <path d="M10 12.5 8 15l2 2.5" />
      <path d="m14 12.5 2 2.5-2 2.5" />
      <path d={DOC_FOLD} />
      <path d={DOC} />
    </>
  ),
  // A diff: a page with a plus over a minus.
  fileDiff: (
    <>
      <path d={DOC} />
      <g className="ico-cross">
        <path d="M9 10h6" />
        <path d="M12 13V7" />
      </g>
      <path d="M9 17h6" />
    </>
  ),
  image: (
    <>
      <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
      <circle cx="9" cy="9" r="2" />
      <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" />
    </>
  ),
  table: (
    <>
      <path d="M12 3v18" />
      <rect width="18" height="18" x="3" y="3" rx="2" />
      <path d="M3 9h18" />
      <path d="M3 15h18" />
    </>
  ),
  mic: (
    <>
      <path className="ico-cap" d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" x2="12" y1="19" y2="22" />
    </>
  ),
  // Listening: the bars of a voice.
  audioLines: (
    <>
      <path className="ico-bar" d="M2 10v3" />
      <path className="ico-bar" d="M6 6v11" />
      <path className="ico-bar" d="M10 3v18" />
      <path className="ico-bar" d="M14 8v7" />
      <path className="ico-bar" d="M18 5v13" />
      <path className="ico-bar" d="M22 10v3" />
    </>
  ),
  warning: (
    <>
      <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" />
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
    </>
  ),
  // The Thought rows' own glyph (owner ask 2026-09-04, reaffirmed 2026-09-11).
  brain: (
    <>
      <path d={BRAIN_L} />
      <path d={BRAIN_R} />
      <path className="ico-ln" d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4" />
      <path className="ico-ln" d="M17.599 6.5a3 3 0 0 0 .399-1.375" />
      <path className="ico-ln" d="M6.003 5.125A3 3 0 0 0 6.401 6.5" />
      <path d="M3.477 10.896a4 4 0 0 1 .585-.396" />
      <path d="M19.938 10.5a4 4 0 0 1 .585.396" />
      <path d="M6 18a4 4 0 0 1-1.967-.516" />
      <path d="M19.967 17.484A4 4 0 0 1 18 18" />
    </>
  ),
  // Memory: a notebook with its pen. The brain stays the Thought rows'.
  notebookPen: (
    <>
      <path d="M13.4 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7.4" />
      <path d="M2 6h4" />
      <path d="M2 10h4" />
      <path d="M2 14h4" />
      <path d="M2 18h4" />
      <path
        className="ico-pen"
        d="M21.378 5.626a1 1 0 1 0-3.004-3.004l-5.01 5.012a2 2 0 0 0-.506.854l-.837 2.87a.5.5 0 0 0 .62.62l2.87-.837a2 2 0 0 0 .854-.506z"
      />
    </>
  ),
  // A subagent: a helper Reeve hands a task to.
  bot: (
    <>
      <path className="ico-ant" d="M12 8V4H8" />
      <rect width="16" height="12" x="4" y="8" rx="2" />
      <path d="M2 14h2" />
      <path d="M20 14h2" />
      <path className="ico-eye" d="M15 13v2" />
      <path className="ico-eye" d="M9 13v2" />
    </>
  ),
  puzzle: (
    <path d="M15.39 4.39a1 1 0 0 0 1.68-.474 2.5 2.5 0 1 1 3.014 3.015 1 1 0 0 0-.474 1.68l1.683 1.682a2.414 2.414 0 0 1 0 3.414L19.61 15.39a1 1 0 0 1-1.68-.474 2.5 2.5 0 1 0-3.014 3.015 1 1 0 0 1 .474 1.68l-1.683 1.682a2.414 2.414 0 0 1-3.414 0L8.61 19.61a1 1 0 0 0-1.68.474 2.5 2.5 0 1 1-3.014-3.015 1 1 0 0 0 .474-1.68l-1.683-1.682a2.414 2.414 0 0 1 0-3.414L4.39 8.61a1 1 0 0 1 1.68.474 2.5 2.5 0 1 0 3.014-3.015 1 1 0 0 1-.474-1.68l1.683-1.682a2.414 2.414 0 0 1 3.414 0z" />
  ),
  server: (
    <>
      <rect width="20" height="8" x="2" y="2" rx="2" ry="2" />
      <rect width="20" height="8" x="2" y="14" rx="2" ry="2" />
      <line className="ico-led" x1="6" x2="6.01" y1="6" y2="6" />
      <line className="ico-led" x1="6" x2="6.01" y1="18" y2="18" />
    </>
  ),
  packageSearch: (
    <>
      <path d="M21 10V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l2-1.14" />
      <path d="m7.5 4.27 9 5.15" />
      <polyline points="3.29 7 12 12 20.71 7" />
      <line x1="12" x2="12" y1="22" y2="12" />
      <g className="ico-lens">
        <circle cx="18.5" cy="15.5" r="2.5" />
        <path d="M20.27 17.27 22 19" />
      </g>
    </>
  ),
  terminal: (
    <>
      <path className="ico-prompt" d="m7 11 2-2-2-2" />
      <path className="ico-caret" d="M11 13h4" />
      <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
    </>
  ),
  globe: (
    <>
      <circle cx="12" cy="12" r="10" />
      <path className="ico-mer" d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" />
      <path d="M2 12h20" />
    </>
  ),
  link: (
    <>
      <path className="ico-a" d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path className="ico-b" d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </>
  ),
  send: (
    <>
      <path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z" />
      <path d="m21.854 2.147-10.94 10.939" />
    </>
  ),
  hash: (
    <>
      <line x1="4" x2="20" y1="9" y2="9" />
      <line x1="4" x2="20" y1="15" y2="15" />
      <line x1="10" x2="8" y1="3" y2="21" />
      <line x1="16" x2="14" y1="3" y2="21" />
    </>
  ),
  externalLink: (
    <>
      <g className="ico-arrow">
        <path d="M15 3h6v6" />
        <path d="M10 14 21 3" />
      </g>
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
    </>
  ),
  // Two corners pulled apart: open larger.
  expand: (
    <>
      <g className="ico-a">
        <polyline points="15 3 21 3 21 9" />
        <line x1="21" x2="14" y1="3" y2="10" />
      </g>
      <g className="ico-b">
        <polyline points="9 21 3 21 3 15" />
        <line x1="3" x2="10" y1="21" y2="14" />
      </g>
    </>
  ),
  minimize: (
    <>
      <g className="ico-a">
        <polyline points="20 10 14 10 14 4" />
        <line x1="14" x2="21" y1="10" y2="3" />
      </g>
      <g className="ico-b">
        <polyline points="4 14 10 14 10 20" />
        <line x1="3" x2="10" y1="21" y2="14" />
      </g>
    </>
  ),
  inspect: (
    <>
      <path d="M3 7V5a2 2 0 0 1 2-2h2" />
      <path d="M17 3h2a2 2 0 0 1 2 2v2" />
      <path d="M21 17v2a2 2 0 0 1-2 2h-2" />
      <path d="M7 21H5a2 2 0 0 1-2-2v-2" />
      <g className="ico-lens">
        <circle cx="12" cy="12" r="3" />
        <path d="m16 16-1.9-1.9" />
      </g>
    </>
  ),
  monitor: (
    <>
      <rect width="20" height="14" x="2" y="3" rx="2" />
      <line x1="8" x2="16" y1="21" y2="21" />
      <line x1="12" x2="12" y1="17" y2="21" />
    </>
  ),
  laptop: (
    <path d="M20 16V7a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v9m16 0H4m16 0 1.28 2.55a1 1 0 0 1-.9 1.45H3.62a1 1 0 0 1-.9-1.45L4 16" />
  ),
  square: <rect width="18" height="18" x="3" y="3" rx="2" />,
  layoutGrid: (
    <>
      <rect width="7" height="7" x="3" y="3" rx="1" />
      <rect width="7" height="7" x="14" y="3" rx="1" />
      <rect width="7" height="7" x="14" y="14" rx="1" />
      <rect width="7" height="7" x="3" y="14" rx="1" />
    </>
  ),
  appWindow: (
    <>
      <rect x="2" y="4" width="20" height="16" rx="2" />
      <path d="M10 4v4" />
      <path d="M2 8h20" />
      <path d="M6 4v4" />
    </>
  ),
  tablet: (
    <>
      <rect width="16" height="20" x="4" y="2" rx="2" ry="2" />
      <line x1="12" x2="12.01" y1="18" y2="18" />
    </>
  ),
  smartphone: (
    <>
      <rect width="14" height="20" x="5" y="2" rx="2" ry="2" />
      <path d="M12 18h.01" />
    </>
  ),
  download: (
    <>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" x2="12" y1="15" y2="3" />
    </>
  ),
  monitorPlay: (
    <>
      <path d="M10 7.75a.75.75 0 0 1 1.142-.638l3.664 2.249a.75.75 0 0 1 0 1.278l-3.664 2.25a.75.75 0 0 1-1.142-.64z" />
      <path d="M12 17v4" />
      <path d="M8 21h8" />
      <rect x="2" y="3" width="20" height="14" rx="2" />
    </>
  ),
  forward: (
    <>
      <polyline points="15 17 20 12 15 7" />
      <path d="M4 18v-2a4 4 0 0 1 4-4h12" />
    </>
  ),
  camera: (
    <>
      <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z" />
      <circle cx="12" cy="13" r="3" />
    </>
  ),
  messageSquare: <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
  messageSquarePlus: (
    <>
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      <path d="M12 7v6" />
      <path d="M9 10h6" />
    </>
  ),
  pointer: (
    <path d="M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z" />
  ),
  mapPin: (
    <>
      <path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0" />
      <circle cx="12" cy="10" r="3" />
    </>
  ),
  type: (
    <>
      <polyline points="4 7 4 4 20 4 20 7" />
      <line x1="9" x2="15" y1="20" y2="20" />
      <line x1="12" x2="12" y1="4" y2="20" />
    </>
  ),
  check: <path className="ico-chk" d="M20 6 9 17l-5-5" />,
  // A goal: the target an objective is worked toward, distinct from the plan's list.
  goal: (
    <>
      <circle className="ico-r1" cx="12" cy="12" r="10" />
      <circle className="ico-r2" cx="12" cy="12" r="6" />
      <circle className="ico-r3" cx="12" cy="12" r="2" />
    </>
  ),
  pause: (
    <>
      <rect x="14" y="4" width="4" height="16" rx="1" />
      <rect x="6" y="4" width="4" height="16" rx="1" />
    </>
  ),
  play: <polygon points="6 3 20 12 6 21 6 3" />,
  eye: (
    <>
      <path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  eyeOff: (
    <>
      <path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49" />
      <path d="M14.084 14.158a3 3 0 0 1-4.242-4.242" />
      <path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143" />
      <path d="m2 2 20 20" />
    </>
  ),
  save: (
    <>
      <path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" />
      <path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7" />
      <path d="M7 3v4a1 1 0 0 0 1 1h7" />
    </>
  ),
  // The callouts of an answer (`> [!TIP]`, `[!IMPORTANT]`, `[!CAUTION]`).
  lightbulb: (
    <>
      <path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5" />
      <path d="M9 18h6" />
      <path d="M10 22h4" />
    </>
  ),
  messageWarning: (
    <>
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      <path d="M12 7v2" />
      <path d="M12 13h.01" />
    </>
  ),
  octagonAlert: (
    <>
      <path d="M12 16h.01" />
      <path d="M12 8v4" />
      <path d="M15.312 2a2 2 0 0 1 1.414.586l4.688 4.688A2 2 0 0 1 22 8.688v6.624a2 2 0 0 1-.586 1.414l-4.688 4.688a2 2 0 0 1-1.414.586H8.688a2 2 0 0 1-1.414-.586l-4.688-4.688A2 2 0 0 1 2 15.312V8.688a2 2 0 0 1 .586-1.414l4.688-4.688A2 2 0 0 1 8.688 2z" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 16v-4" />
      <path d="M12 8h.01" />
    </>
  ),
  x: (
    <>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </>
  ),
} satisfies Record<string, ReactNode>;

/** Every icon there is: a glyph above, or one of the two filled shapes Icon draws itself. */
export type IconName = keyof typeof GLYPHS | "logo" | "stop";

type IconProps = {
  name: IconName;
  size?: number;
  className?: string;
  /** The work this icon names is happening now: it loops (a terminal's caret blinks, a search's
   *  lens circles) until the flag drops. */
  running?: boolean;
};

const box = (size: number) => ({ width: size, height: size, viewBox: "0 0 24 24", "aria-hidden": true as const });

export function Icon({ name, size = 16, className, running }: IconProps) {
  const cls = ["ico", `ico-${name}`, running ? "ico-run" : "", className ?? ""].filter(Boolean).join(" ");
  if (name === "logo")
    // The mark: two rounded squares on a diagonal with their overlap hollowed out. The person's
    // square takes the text colour around it and the agent's the accent; the hollow is the work
    // they share. This is the small-size cut, with thick arms, so it holds at 16-24px. The
    // geometry, and every raster of it, comes from scripts/make_brand_icons.py (`--glyph`).
    return (
      <svg {...box(size)} fill="currentColor" className={cls}>
        <path d="M5.18 2.00L12.91 2.00A3.18 3.18 0 0 1 16.09 5.18L16.09 7.91L11.09 7.91A3.18 3.18 0 0 0 7.91 11.09L7.91 16.09L5.18 16.09A3.18 3.18 0 0 1 2.00 12.91L2.00 5.18A3.18 3.18 0 0 1 5.18 2.00Z" />
        <path d="M18.82 22.00L11.09 22.00A3.18 3.18 0 0 1 7.91 18.82L7.91 16.09L12.91 16.09A3.18 3.18 0 0 0 16.09 12.91L16.09 7.91L18.82 7.91A3.18 3.18 0 0 1 22.00 11.09L22.00 18.82A3.18 3.18 0 0 1 18.82 22.00Z" fill="var(--accent, currentColor)" className="logo-agent" />
      </svg>
    );
  if (name === "stop")
    return (
      <svg {...box(size)} fill="currentColor" className={cls}>
        <rect x="5" y="5" width="14" height="14" rx="2.5" />
      </svg>
    );
  return (
    <svg {...box(size)} fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className={cls}>
      <g className="ico-g">{GLYPHS[name]}</g>
    </svg>
  );
}
