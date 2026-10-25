// The line a transcript gets when the permission mode changes, so that afterwards it still says
// which mode each exchange ran under. It names the three presets (Read only, Workspace write, Full
// access); `modeLabel` maps the older wire values onto them, so an old session reads the same way.
import { modeLabel } from "./components/composer/modes";
import type { NoticeItem } from "./types";

/** The mode the transcript last showed, and for which session. */
export type ModeMark = { session: string; mode: string } | null;

export type ModeNoticeState = { mark: ModeMark };

/** The line for moving from `previous` to `mode`, or null. Only a change inside one session is
 *  said: opening another session is not a change of mode, and with no mark yet there is nothing
 *  it changed from. `inline` (a turn is running) puts the line inside the turn, which a line of its
 *  own split in two: "Worked for 34s / Full access is on. / Worked for 52s". */
export function modeNotice(mode: string, sessionId: string, previous: ModeMark, inline = false): NoticeItem | null {
  if (!previous || previous.session !== sessionId || previous.mode === mode) return null;
  return { kind: "notice", tone: "info", text: `${modeLabel(mode)} is on.`, ...(inline ? { inline: true } : {}) };
}

/** One (mode, session) the app saw: the line to add, if any, and the state to keep.
 *
 *  `confirmedFor` is the session the mode is known to belong to. Right after a switch the app still
 *  holds the last session's mode until the server's `ready` brings this one's; acting then put the
 *  old session's line in the new transcript, and recording it would make the real mode look like a
 *  change when it lands (seen 2026-08-22). So until the two agree, nothing is said or kept. */
export function modeNoticeStep(
  state: ModeNoticeState,
  mode: string,
  sessionId: string,
  confirmedFor: string,
  inline = false,
): { item: NoticeItem | null; state: ModeNoticeState } {
  if (confirmedFor !== sessionId) return { item: null, state };
  return { item: modeNotice(mode, sessionId, state.mark, inline), state: { mark: { session: sessionId, mode } } };
}

/** `items` with the line added, when there is a conversation to add it to. On a conversation with
 *  nothing in it yet, the line made the transcript non-empty, and a permission picked on the start
 *  page turned the page into an empty conversation reading "Read only is on." (owner-hit
 *  2026-10-14). The picker already shows the mode a conversation starts in. */
export function withModeNotice<T>(items: T[], item: T | null): T[] {
  return item && items.length > 0 ? [...items, item] : items;
}
