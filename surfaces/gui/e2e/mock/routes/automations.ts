import { AUTOMATION } from "../data";
import type { On } from "../router";
import type { MockState } from "../state";

// The inbox of prompts waiting on the person, the channels heard from lately, and the
// automations: a scheduled task with a run going (its detail page, the run's banner, Back to
// runs), which can be run now, switched, revoked from, deleted and created.

const nowSeconds = () => Math.floor(Date.now() / 1000);

export function inboxRoutes(on: On, s: MockState) {
  const { inbox } = s;
  on("*", "/v1/channels/recent", () => ({
    channels: [
      { channel: "slack:C0AAA111", name: "reeve-test", last_from: "amy", last_text: "standup at 10" },
      { channel: "slack:C0BBB222", last_from: "bob", last_text: "deploy failed" },
    ],
  }));
  on("POST", /\/v1\/inbox\/([^/]+)\/resolve$/, ({ match, body }) => {
    const item = inbox.find((row) => row.id === decodeURIComponent(match[1]));
    if (item) Object.assign(item, { state: "resolved", resolution: body.resolution });
    return { ok: true };
  });
  on("*", "/v1/inbox", ({ query }) => {
    const sid = query.get("session_id");
    const state = query.get("state");
    return { items: inbox.filter((row) => (!sid || row.session_id === sid) && (!state || row.state === state)) };
  });
}

export function automationRoutes(on: On, s: MockState) {
  const { automations, automationRuns } = s;
  const task = (id: string) => automations.find((t) => t.id === id);

  on("POST", /\/v1\/automations\/([^/]+)\/seen$/, ({ match }) => {
    const t = task(match[1]);
    if (t) Object.assign(t, { unseen_runs: 0, unseen_failed: false, seen_runs_at: nowSeconds() });
    return { ok: !!t };
  });
  // Run now: a new run at the head of the list, whose live session the app then opens.
  on("POST", /\/v1\/automations\/([^/]+)\/run$/, ({ match }) => {
    const t = task(match[1]);
    if (!t) return { ok: false, error: "unknown task" };
    const run_id = `r${automationRuns.length + 1}`;
    const session_id = `__run__${run_id}`;
    automationRuns.unshift({
      run_id, task_id: t.id, session_id, started_at: nowSeconds(), finished_at: null, status: "running",
      result_text: null, artifacts: [], error: null, trigger: "manual",
    });
    return { ok: true, run_id, session_id, workspace: t.workspace, agent: t.agent, prompt: t.instructions };
  });
  on("GET", /\/v1\/automations\/([^/]+)$/, ({ match }) => {
    const t = task(match[1]) ?? automations[0];
    return { task: t, runs: automationRuns.filter((run) => run.task_id === t?.id) };
  });
  // A change of fields, or `revoke`: a command that takes one standing approval away, not
  // a field to set on the task.
  on("PATCH", /\/v1\/automations\/([^/]+)$/, ({ match, body }) => {
    const t = task(match[1]);
    if (t && body.revoke) t.always_allowed = (t.always_allowed || []).filter((rule: any) => rule.entry !== body.revoke);
    else if (t) Object.assign(t, body);
    return { ok: true, task: t };
  });
  on("DELETE", /\/v1\/automations\/([^/]+)$/, ({ match }) => {
    const i = automations.findIndex((t) => t.id === match[1]);
    if (i >= 0) automations.splice(i, 1);
    return { ok: true };
  });
  // Created from the app or an onboarding recipe, as the server checks it: a title,
  // instructions and a time are needed, and of the permissions only the writes become
  // standing approvals.
  on("POST", "/v1/automations", ({ body }) => {
    if (!body.title || !body.instructions || !(body.cron || body.fire_at)) return { ok: false, error: "missing fields" };
    const grants = (body.permissions || [])
      .filter((g: any) => g && g.access === "write" && g.tool && g.target)
      .map((g: any) => ({ entry: `${g.tool} ${g.target}`, tool: g.tool, target: g.target }));
    const created = {
      ...AUTOMATION,
      id: `task-ob-${automations.length}`, title: body.title, instructions: body.instructions,
      schedule: body.cron || body.fire_at, schedule_raw: { kind: "cron", cron: body.cron || null, fire_at: body.fire_at || null, timezone: "local" },
      always_allowed: grants, run_count: 0, unseen_runs: 0, unseen_failed: false, latest_run: null,
    };
    automations.push(created);
    return { ok: true, task: created };
  });
  // Each task with its newest run, as the server lists them.
  on("*", "/v1/automations", () => ({
    tasks: automations.map((t) => ({ ...t, latest_run: automationRuns.find((run) => run.task_id === t.id) ?? t.latest_run ?? null })),
  }));
}
