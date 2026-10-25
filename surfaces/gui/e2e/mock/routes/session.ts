import type { Page } from "@playwright/test";
import { fixtureJobs, JOB_ANCHOR_TOTAL, sessionSockets } from "../channels";
import { CHILD_SESSION_MESSAGES, PROBE_A_MESSAGES, PROBE_B_MESSAGES } from "../data";
import { inSession, type On } from "../router";
import type { MockState } from "../state";

// What hangs off one session: its connections, sharing, terminal, children's transcripts,
// background jobs, compaction and forks; then its goal, the permission broker and its projects.
// Any session id gets the same answers.

const ROOT = "/Users/test/Reeve/launch-note";

export function sessionRoutes(on: On, s: MockState, page: Page) {
  const { connections, shareState } = s;
  // Muting one of the session's connectors in Access, seen again on the section's reload.
  on("POST", inSession("connections"), ({ body }) => {
    const row = connections.connected.find((c) => c.connector === body.connector);
    if (row) row.enabled = !!body.enabled;
    return { ok: true };
  });
  on("*", inSession("connections"), () => connections);
  // Sharing: sharing gives a URL and unsharing clears it. "No endpoint" is played by
  // a spec answering the GET itself.
  on("POST", inSession("share"), () => {
    shareState.share = { url: "https://share.example.test/s/sess-1", share_id: "sess-1", shared_at: "2026-09-18T00:00:00Z" };
    return { ok: true, ...shareState.share };
  });
  on("*", inSession("share"), () => ({
    configured: shareState.configured, can_revoke: shareState.configured && !!shareState.share, share: shareState.share,
  }));
  on("POST", inSession("unshare"), () => {
    shareState.share = null;
    return { ok: true };
  });

  // The project's worktrees, for `/workspace`: a second one, so "open a session here" has a
  // place to go that is not the current folder.
  const worktree = (path: string, head: string, branch: string, here: boolean) =>
    ({ path, name: path.split("/").pop(), head, branch, primary: here, current: here });
  on("*", "/v1/workspaces/worktrees", () => ({
    is_git: true, root: ROOT,
    worktrees: [worktree(ROOT, "abc1234def", "main", true), worktree(`${ROOT}-feature`, "def5678abc", "feature/statements", false)],
  }));

  // The terminal's REST side. The panel runs on its socket; these answer whatever calls them.
  on("GET", inSession("terminal/shells"), () => ({
    ok: true,
    shells: [
      { id: "cmd", name: "cmd.exe", path: "C:\\Windows\\System32\\cmd.exe" },
      { id: "bash", name: "bash", path: "/bin/bash" },
      { id: "powershell", name: "powershell.exe", path: "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" },
    ],
  }));
  on("DELETE", inSession("terminal"), () => ({ ok: true, closed: true }));
  on("*", inSession("terminal"), () => ({ ok: true, id: "user-sess", cwd: ROOT, shell: "pwsh", running: true, output: "", produced: 0 }));

  // The children's own transcripts, read by a child's view. Every other session's is
  // the empty one further on.
  for (const [id, messages] of [["explore-2", CHILD_SESSION_MESSAGES], ["probe-a", PROBE_A_MESSAGES], ["probe-b", PROBE_B_MESSAGES]] as const) {
    on("GET", `/v1/sessions/${id}/messages`, () => ({ messages }));
  }

  // Background jobs. The list is read once, as the socket opens; after that the roster is
  // pushed. A job's tail is `truncated`, so a spec can see the panel say the log was shortened.
  const jobs = () => fixtureJobs.get(page) ?? [];
  const job = (id: string) => jobs().find((row) => row.id === id);
  on("GET", inSession("jobs"), () => ({ jobs: jobs() }));
  on("GET", inSession("jobs/output"), ({ query }) => {
    const id = query.get("id") ?? "";
    const row = job(id);
    if (!row) return { ok: false, error: "no such job" };
    return {
      ok: true, id, status: row.status, exit_code: row.exit_code ?? null,
      output: "[build] vite v5.4.2 building for production…\n✓ 145 modules transformed.\n", truncated: true, total_chars: JOB_ANCHOR_TOTAL,
    };
  });
  // An open row says it is watching. The mock streams either way; this answers as
  // the engine would.
  on("POST", inSession("jobs/observe"), ({ body }) => {
    const id = String(body.id || "");
    const row = job(id);
    if (!row) return { ok: false, error: `No job ${id} in this session.` };
    if (row.status !== "running") return { ok: false, error: `Job ${id} has finished; there is nothing left to stream.` };
    return { ok: true, id, observing: body.on === false ? 0 : 1 };
  });
  // Stopping a job ends its process, and the roster is pushed as the engine's watcher pushes it:
  // the reply alone does not move the row.
  on("POST", inSession("jobs/kill"), ({ body }) => {
    const row = job(String(body.id || ""));
    if (!row) return { ok: false, error: "no such job" };
    const now = Math.floor(Date.now() / 1000);
    Object.assign(row, { status: "finished", ended_at: now, exit_code: 1, killed: true, duration_ms: Math.max(1000, (now - Number(row.started_at ?? now)) * 1000) });
    sessionSockets.get(page)?.send("jobs", { jobs: jobs().map((r) => ({ ...r })) });
    return { ok: true };
  });

  // "Compact now": the marker goes out on the socket first, as the real route sends it (the
  // meter moves on that), then the reply says what the compaction saved.
  on("POST", inSession("compact"), () => {
    const result = { ok: true, reason: "summarized", saved_tokens: 12_500, now_tokens: 30_000, summary: "Objective: keep the launch note moving.\nNext: draft the follow-up." };
    sessionSockets.get(page)?.send("compacted", { text: "Context compacted — earlier turns were summarized", ...result });
    return result;
  });
  // A fork is a new session, which the app opens at once.
  on("POST", inSession("fork"), () => ({ ok: true, session_id: "forked-1", title: "Draft the launch note · fork", parent_session: "pinned-session-1", messages: 2 }));
}

export function planRoutes(on: On, s: MockState, page: Page) {
  const { goalState, projectBindings, projectNames } = s;
  // The goal: what the bar reloads, and the person's controls (pause, resume, edit and clear on
  // the bar, set from `/goal`). Each change is also sent on the socket, the view and then its
  // activation, as `_broadcast_goal` does, so the bar moves without reading again.
  on("POST", inSession("goal"), ({ body }) => {
    const current = goalState.goal;
    const next = (fields: Record<string, unknown>) => ({ ...current, ...fields, revision: current.revision + 1 });
    if (body.action === "clear") goalState.goal = null;
    else if (body.action === "set") {
      goalState.goal = {
        goal_id: "goal-1", objective: String(body.objective ?? ""), phase: "active", activation: "armed",
        rounds_started: 0, max_rounds: Number(body.max_rounds || 256), revision: 1, note: "", blocker: null,
      };
    } else if (current && body.action === "pause") goalState.goal = next({ phase: "paused", activation: "disarmed" });
    else if (current && body.action === "resume") goalState.goal = next({ phase: "active", activation: "armed" });
    else if (current && body.action === "edit") {
      goalState.goal = next({
        ...(body.objective ? { objective: String(body.objective) } : {}),
        ...(body.max_rounds ? { max_rounds: Number(body.max_rounds) } : {}),
        ...(body.token_budget !== undefined ? { token_budget: Number(body.token_budget) } : {}),
      });
    }
    const channel = sessionSockets.get(page);
    const g = goalState.goal;
    channel?.send("goal", { ...(g ?? {}), action: body.action, text: "" });
    channel?.send("goal_activation", { goal_id: g?.goal_id ?? "", revision: g?.revision ?? 0, phase: g?.phase ?? "", activation: g?.activation ?? "disarmed" });
    return { ok: true, goal: goalState.goal };
  });
  on("*", inSession("goal"), () => ({ goal: goalState.goal }));

  // The permission broker's reply. Its list is served as the real GET is, but the app follows
  // the frames a spec sends (`permission_asked`, `permission_replied`).
  on("GET", "/v1/permissions", () => ({ permissions: [] }));
  on("POST", /^\/v1\/permissions\/[^/]+\/reply$/, ({ body }) =>
    ["once", "always", "reject"].includes(body.reply) ? { ok: true } : { ok: false, error: `unknown reply: ${body.reply}` });

  // The project menu: bind, unbind and name, kept for the test.
  const NOTES = "/Users/u/fleet/ro4d/demo-universe/notes";
  on("*", inSession("project-menu"), ({ query }) => {
    const kind = query.get("kind") || "memory";
    return {
      kind, bound: projectBindings[kind] ?? null,
      derived: { kind: "folder", label: "~/fleet/ro4d/demo-universe/notes", full: NOTES, key: NOTES },
      named: projectNames[kind] || [],
    };
  });
  on("PUT", inSession("bindings"), ({ body }) => {
    if (body.name) projectBindings[body.kind] = body.name;
    else delete projectBindings[body.kind];
    return { ok: true, bindings: projectBindings };
  });
  on("POST", inSession("project-name"), ({ body }) => {
    (projectNames[body.kind] ||= []).unshift({ name: body.name, key: NOTES });
    return { ok: true, kind: body.kind, name: body.name };
  });
}
