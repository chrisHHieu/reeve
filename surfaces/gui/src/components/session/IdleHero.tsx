// The empty conversation: the page a session with no history shows in place of a transcript.
//
// The composer's page (redesigned 2026-10-12, mockup "New session page", direction 2): a
// heading, the composer right under it, and under that two short lists side by side. On the
// left, the sessions to pick up: the ones that wait on the person first, then the ones still
// working, then the newest, from the folder this session works in (else from everywhere). On
// the right, a few things to start. Two components because the composer sits between them in
// the column: `IdleHero` is the heading (in the scroll region, above the composer), `IdleStart`
// is what goes under the composer.

import type { Attachment, SessionInfo } from "../../types";
import { baseName } from "../../paths";
import { NEEDS } from "../../needs";
import { sessionAge } from "../../sessionAge";
import { SessionIntroTasks } from "../SessionIntro";

/** The page's one heading (owner, 2026-10-12: "What should we work on?" read as a ChatGPT
 *  clone; this says what Reeve is for: it takes the work). */
export const START_HEADING = "What can I take off your plate?";

export function IdleHero() {
  return (
    <div className="start-head" data-testid="start-head">
      <h1 className="start-title" data-testid="start-title">
        {START_HEADING}
      </h1>
    </div>
  );
}

const PICK_UP = 3;

/** The sessions the start page offers to pick up: in this folder when it has any, else
 *  anywhere; waiting on the person first, then working, then the newest. */
export function pickUpSessions(
  sessions: readonly SessionInfo[],
  current: string,
  folder: string | null,
): { inFolder: boolean; rows: SessionInfo[] } {
  const open = sessions.filter(
    (s) => s.session_id !== current && !s.archived && !s.session_id.startsWith("__") && (s.messages ?? 1) > 0,
  );
  const rank = (s: SessionInfo) => ((s.attention || 0) > 0 ? 0 : s.liveness === "working" ? 1 : 2);
  const order = (list: SessionInfo[]) =>
    list.slice().sort((a, b) => rank(a) - rank(b) || (b.updated_at || "").localeCompare(a.updated_at || ""));
  const here = folder ? open.filter((s) => s.workspace === folder && !s.temp_workspace) : [];
  if (here.length > 0) return { inFolder: true, rows: order(here).slice(0, PICK_UP) };
  return { inFolder: false, rows: order(open).slice(0, PICK_UP) };
}

function pickUpState(s: SessionInfo): { tone: string; text: string } {
  if ((s.attention || 0) > 0) return { tone: "warm", text: NEEDS[s.attention_kind || ""] || "Needs you" };
  if (s.liveness === "working") return { tone: "live", text: "Working" };
  return { tone: "", text: sessionAge(s.updated_at) };
}

export function IdleStart({
  sessionId,
  onPrefill,
  sessions,
  folder,
  onOpenSession,
}: {
  sessionId: string;
  onPrefill: (text: string, attachments?: Attachment[]) => void;
  sessions: readonly SessionInfo[];
  /** The folder this session works in; null for a chat's temporary folder. */
  folder: string | null;
  onOpenSession: (id: string, workspace: string) => void;
}) {
  const { inFolder, rows } = pickUpSessions(sessions, sessionId, folder);
  return (
    <div className={"start-cols" + (rows.length ? "" : " one")}>
      {rows.length > 0 && (
        <section className="start-sec" data-testid="pick-up" aria-labelledby="pick-up-title">
          <h2 className="start-sec-title" id="pick-up-title">
            {inFolder && folder ? `Pick up in ${baseName(folder)}` : "Pick up where you left off"}
          </h2>
          {rows.map((s) => {
            const state = pickUpState(s);
            return (
              <button
                key={s.session_id}
                type="button"
                className="start-row"
                data-testid="pick-up-row"
                onClick={() => onOpenSession(s.session_id, s.workspace)}
              >
                <span className={"start-row-dot " + state.tone} aria-hidden />
                <span className="start-row-text">{s.title || s.session_id}</span>
                <span className={"start-row-state " + state.tone}>{state.text}</span>
              </button>
            );
          })}
        </section>
      )}
      <section className="start-sec" aria-labelledby="start-try-title">
        <h2 className="start-sec-title" id="start-try-title">
          {rows.length ? "Or start something" : "Start something"}
        </h2>
        <SessionIntroTasks sessionId={sessionId} onPrefill={onPrefill} />
      </section>
    </div>
  );
}
