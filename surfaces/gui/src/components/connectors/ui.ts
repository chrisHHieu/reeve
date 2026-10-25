// The look the Connectors pages share, in the manner of macOS System Settings:
// rows grouped on an inset card with hairlines between them, pill buttons, small state tags.

/** The card a group of rows sits on; the rows are divided by hairlines. */
export const GRP = "rounded-xl bg-panel shadow-[0_0_0_1px_var(--line-muted)] divide-y divide-[color:var(--line-muted)] overflow-hidden";

/** One row, at least 52px tall. */
export const ROW = "flex items-center gap-3 px-4 py-2.5 min-h-[52px] transition-colors";

/** The heading over a group, in sentence case. */
export const GRP_H = "text-[13px] font-semibold text-ink px-0.5 mt-7 mb-2";

/** The small note under a group. */
export const FOOT = "text-[12px] text-faint px-4 pt-1.5";

const PILL = "text-[12.5px] font-medium shrink-0";
export const PILL_ACCENT = `${PILL} px-3 py-1.5 rounded-full bg-accent text-[color:var(--canvas)] disabled:opacity-50`;
export const PILL_QUIET = `inline-flex items-center h-[30px] ${PILL} px-3 rounded-lg bg-panel border border-lineStrong text-ink hover:border-ink/30 hover:text-ink transition-colors`;
export const PILL_LINE = `${PILL} px-3 py-1.5 rounded-full border border-lineStrong text-ink hover:border-ink/30 hover:text-ink`;

const TAG = "text-[10.5px] font-semibold px-1.5 py-0.5 rounded shrink-0";
export const TAG_ACCENT = `${TAG} bg-accentSoft text-accent`;
export const TAG_WARN = `${TAG} bg-warnSoft text-warnInk`;
export const TAG_QUIET = `${TAG} bg-paper border border-line text-muted`;

// A connector's state in the Connected list: a dot and a word, the chips bringing their own "●",
// as in Settings ▸ Models.
const CHIP = "text-[12px] font-medium shrink-0 whitespace-nowrap";
export const CHIP_OK = `${CHIP} text-ok`;
export const CHIP_WARN = `${CHIP} text-warnInk`;
export const CHIP_OFF = `${CHIP} text-muted`;
export const CHIP_ERR = `${CHIP} text-danger`;

/** The small ×, red on hover. */
export const XBTN = "text-faint hover:text-danger shrink-0 leading-none";
