// The queue's groups, as a pure function of the rows and the clock.
import { describe, expect, it } from "vitest";
import type { SessionInfo } from "../../types";
import { buildQueue, groupOf, queueByProject } from "./queueGroups";

const NOW = new Date(2026, 9, 14, 9, 0);
const row = (over: Partial<SessionInfo>): SessionInfo => ({
  session_id: "s",
  title: "t",
  workspace: "/code/shop",
  agent: "reeve",
  model: "m",
  mode: "interactive",
  updated_at: new Date(2026, 9, 14, 8, 0).toISOString(),
  messages: 1,
  ...over,
});

describe("groupOf", () => {
  it("takes the most pressing state", () => {
    const open = { files: 1, added: 1, removed: 0, open: 1 };
    expect(groupOf(row({ attention: 1, liveness: "working", work: open }), NOW)).toBe("needs");
    expect(groupOf(row({ liveness: "working", work: open }), NOW)).toBe("running");
    expect(groupOf(row({ liveness: "sleeping" }), NOW)).toBe("running");
    expect(groupOf(row({ work: open }), NOW)).toBe("review");
    expect(groupOf(row({ work: { ...open, open: 0 } }), NOW)).toBe("today");
    // Without the server's `open` (not a repository, or too old to read), never "review".
    expect(groupOf(row({ work: { files: 3, added: 9, removed: 0 } }), NOW)).toBe("today");
  });

  it("is today by the local calendar day, not the last 24 hours", () => {
    expect(groupOf(row({ updated_at: new Date(2026, 9, 14, 0, 1).toISOString() }), NOW)).toBe("today");
    expect(groupOf(row({ updated_at: new Date(2026, 9, 13, 23, 59).toISOString() }), NOW)).toBe("earlier");
    expect(groupOf(row({ updated_at: null }), NOW)).toBe("earlier");
  });
});

describe("buildQueue and the projects", () => {
  const rows = [
    row({ session_id: "a", workspace: "/code/shop", updated_at: new Date(2026, 9, 14, 7).toISOString() }),
    row({ session_id: "b", workspace: "/code/api", updated_at: new Date(2026, 9, 14, 8).toISOString(), pinned: false }),
    row({ session_id: "c", workspace: "/code/shop", updated_at: new Date(2026, 9, 14, 6).toISOString(), pinned: true }),
    row({ session_id: "d", workspace: "/code/old", updated_at: new Date(2026, 8, 1).toISOString() }),
    row({ session_id: "e", workspace: "/tmp/chat", temp_workspace: true }),
    row({ session_id: "__task__x", attention: 1 }),
    row({ session_id: "z", archived: true }),
  ];

  it("orders each group pinned first, then newest; leaves internal threads out; keeps archived apart", () => {
    const q = buildQueue(rows, NOW);
    // b and e share a time: the order they came in decides.
    expect(q.groups.today.map((s) => s.session_id)).toEqual(["c", "b", "e", "a"]);
    expect(q.groups.earlier.map((s) => s.session_id)).toEqual(["d"]);
    expect(q.groups.needs).toEqual([]);
    expect(q.archived.map((s) => s.session_id)).toEqual(["z"]);
  });

  it("lists every project, newest first, a quiet one too; chats with no folder apart", () => {
    const live = { files: 1, added: 1, removed: 0, open: 1 };
    const { projects, loose } = queueByProject(
      [...rows, row({ session_id: "r", workspace: "/code/shop", updated_at: new Date(2026, 9, 14, 5).toISOString(), work: live })],
      NOW,
    );
    expect(projects.map((p) => p.key)).toEqual(["/code/api", "/code/shop", "/code/old"]);
    const shop = projects[1];
    // Its count holds the live session; what opening it lists does not (that one is in the queue).
    expect(shop.sessions.map((s) => s.session_id)).toEqual(["a", "c", "r"]);
    expect(shop.past.map((s) => s.session_id)).toEqual(["c", "a"]); // pinned first
    expect(shop.live).toEqual(["review"]);
    expect(projects[2].live).toEqual([]);
    expect(loose?.sessions.map((s) => s.session_id)).toEqual(["e"]);
    // Internal threads and archived sessions belong to no project.
    expect(projects.flatMap((p) => p.sessions).some((s) => s.session_id === "z" || s.session_id.startsWith("__"))).toBe(false);
  });
});
