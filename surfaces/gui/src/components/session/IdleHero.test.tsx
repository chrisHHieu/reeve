import { describe, expect, it } from "vitest";
import { pickUpSessions } from "./IdleHero";
import type { SessionInfo } from "../../types";

const row = (over: Partial<SessionInfo>): SessionInfo =>
  ({ session_id: "x", title: "x", workspace: "/w/app", agent: "reeve", model: "m", mode: "interactive", updated_at: "2026-10-10", messages: 2, ...over }) as SessionInfo;

const SESSIONS = [
  row({ session_id: "old", title: "old", updated_at: "2026-10-01" }),
  row({ session_id: "new", title: "new", updated_at: "2026-10-11" }),
  row({ session_id: "busy", title: "busy", liveness: "working", updated_at: "2026-10-02" }),
  row({ session_id: "asks", title: "asks", attention: 1, attention_kind: "approval", updated_at: "2026-09-01" }),
  row({ session_id: "elsewhere", title: "elsewhere", workspace: "/w/other", updated_at: "2026-10-12" }),
  row({ session_id: "chat", title: "chat", workspace: "/scratch/c1", temp_workspace: true, updated_at: "2026-10-12" }),
  row({ session_id: "gone", title: "gone", archived: true, updated_at: "2026-10-12" }),
  row({ session_id: "__run__1", title: "run", updated_at: "2026-10-12" }),
  row({ session_id: "here", title: "this draft", updated_at: "2026-10-12" }),
];

describe("what the start page offers to pick up", () => {
  it("in this folder: what waits on the person, then what is working, then the newest", () => {
    const { inFolder, rows } = pickUpSessions(SESSIONS, "here", "/w/app");
    expect(inFolder).toBe(true);
    expect(rows.map((s) => s.session_id)).toEqual(["asks", "busy", "new"]);
  });

  it("from everywhere when the folder has nothing to pick up, or there is no folder", () => {
    const none = pickUpSessions(SESSIONS, "here", "/w/empty");
    expect(none.inFolder).toBe(false);
    expect(none.rows.map((s) => s.session_id)).toEqual(["asks", "busy", "elsewhere"]);
    expect(pickUpSessions(SESSIONS, "here", null).inFolder).toBe(false);
  });

  it("never offers the session on screen, an archived one, or an automation's run", () => {
    const ids = pickUpSessions(SESSIONS, "here", null).rows.map((s) => s.session_id);
    expect(ids).not.toContain("here");
    expect(ids).not.toContain("gone");
    expect(ids).not.toContain("__run__1");
  });
});
