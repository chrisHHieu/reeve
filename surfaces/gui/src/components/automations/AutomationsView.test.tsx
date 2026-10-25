import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AutomationsView } from "./AutomationsView";

// A hermetic fetch stub: routes by a substring of the URL and the method, and records every call.
type Route = { match: string; method?: string; status?: number; json: any };
function stubFetch(routes: Route[]) {
  const calls: { url: string; method: string; body: any }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = (init?.method || "GET").toUpperCase();
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const hit = routes.find((r) => url.includes(r.match) && (!r.method || r.method === method));
      const status = hit?.status ?? 200;
      return { ok: status < 400, status, json: async () => hit?.json ?? {} } as Response;
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const now = Math.floor(Date.now() / 1000);
const task = (over: Record<string, unknown> = {}) => ({
  id: "task-1",
  title: "Daily AI News",
  instructions: "Fetch the news.",
  schedule: "Every day at ~5:40 PM",
  schedule_raw: { kind: "cron", cron: "40 17 * * *", fire_at: null, timezone: "local" },
  workspace: "",
  agent: "reeve",
  model: null,
  mode: null,
  enabled: true,
  next_run: now + 600,
  last_run: null,
  last_status: null,
  run_count: 0,
  notify_on_completion: false,
  always_allowed: [],
  unseen_runs: 0,
  latest_run: null,
  ...over,
});
const run = (over: Record<string, unknown> = {}) => ({
  run_id: "r1",
  task_id: "task-1",
  session_id: "__run__r1",
  started_at: now - 120,
  finished_at: now - 60,
  status: "ok",
  result_text: null,
  artifacts: [],
  error: null,
  trigger: "schedule",
  attention: 0,
  attention_kind: null,
  ...over,
});
const SETTINGS = { match: "/v1/settings", json: { models: ["m1"], model_labels: { m1: "Gemini 3.7 Flash · Vertex AI" } } };
const base = { onOpenRun: vi.fn(), onRunNow: vi.fn() };

describe("the Automations list", () => {
  it("shows a skeleton until the first read, then the rows", async () => {
    stubFetch([{ match: "/v1/automations", json: { tasks: [task()] } }]);
    render(<AutomationsView {...base} />);
    expect(screen.getByTestId("automations-loading")).toBeTruthy();
    expect(await screen.findByTestId("automation-row")).toBeTruthy();
    expect(screen.queryByTestId("automations-loading")).toBeNull();
    expect(screen.getByTestId("automation-last-run").textContent).toBe("Not run yet");
  });

  it("says the list could not be loaded, which is not the empty page, and tries again", async () => {
    const routes: Route[] = [{ match: "/v1/automations", status: 500, json: { detail: "boom" } }];
    stubFetch(routes);
    render(<AutomationsView {...base} />);
    expect((await screen.findByTestId("automations-error")).textContent).toContain("could not be loaded");
    expect(screen.queryByTestId("automations-empty")).toBeNull();

    routes[0] = { match: "/v1/automations", json: { tasks: [] } };
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("automations-empty")).toBeTruthy();
  });

  it("says what each automation's last run came to", async () => {
    stubFetch([
      {
        match: "/v1/automations",
        json: {
          tasks: [
            task({ id: "a", title: "Waiting", latest_run: run({ status: "running", finished_at: null, attention: 1, attention_kind: "approval" }) }),
            task({ id: "b", title: "Broken", latest_run: run({ status: "error", error: "no answer" }) }),
            task({ id: "c", title: "Fine", latest_run: run(), unseen_runs: 3 }),
            task({ id: "d", title: "Resting", enabled: false }),
          ],
        },
      },
    ]);
    render(<AutomationsView {...base} />);
    const rows = await screen.findAllByTestId("automation-row");
    const last = (i: number) => within(rows[i]).getByTestId("automation-last-run");
    expect(last(0).textContent).toBe("Needs you · Approval");
    expect(last(0).className).toContain("warm");
    expect(last(1).textContent).toMatch(/^Failed · (today|yesterday) /); // a minute ago
    expect(last(1).className).toContain("bad");
    expect(last(2).textContent).toMatch(/^Done · (today|yesterday) /);
    expect(within(rows[2]).getByLabelText("3 new runs").textContent).toBe("3");
    expect(rows[3].textContent).toContain("paused");
    expect(rows[3].className).toContain("off");
  });

  it("turns one off from its row, and puts the switch back when the server refuses", async () => {
    const calls = stubFetch([
      { match: "/v1/automations/task-1", method: "PATCH", json: { ok: false, error: "not found" } },
      { match: "/v1/automations", json: { tasks: [task()] } },
    ]);
    render(<AutomationsView {...base} />);
    const onOff = await screen.findByRole("switch", { name: "Daily AI News: on or off" });
    fireEvent.click(onOff);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("It could not be turned off."));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ enabled: false });
    expect(onOff.getAttribute("aria-checked")).toBe("true");
  });

  it("asks before deleting, and deletes only on the answer", async () => {
    const calls = stubFetch([
      { match: "/v1/automations/task-1", method: "DELETE", json: { ok: true } },
      { match: "/v1/automations", json: { tasks: [task()] } },
    ]);
    render(<AutomationsView {...base} />);
    fireEvent.click(await screen.findByRole("button", { name: "More for Daily AI News" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete…" }));
    expect(screen.getByTestId("confirm-dialog").textContent).toContain("Delete “Daily AI News”?");
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByTestId("confirm-dialog-ok"));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });

  it("runs one from its row", async () => {
    const onRunNow = vi.fn();
    stubFetch([{ match: "/v1/automations", json: { tasks: [task()] } }]);
    render(<AutomationsView {...base} onRunNow={onRunNow} />);
    fireEvent.click(await screen.findByRole("button", { name: "Run Daily AI News now" }));
    await waitFor(() => expect(onRunNow).toHaveBeenCalledWith("task-1", "Daily AI News"));
  });
});

describe("the one form", () => {
  it("creates with what the form holds: the schedule, the folder, the permissions and the model", async () => {
    const calls = stubFetch([
      { match: "/v1/automations", method: "POST", json: { ok: true, task: task({ id: "new", title: "PR watch" }) } },
      { match: "/v1/automations/new", json: { task: task({ id: "new", title: "PR watch" }), runs: [] } },
      { match: "/v1/automations", method: "GET", json: { tasks: [] } },
      SETTINGS,
    ]);
    render(<AutomationsView {...base} />);
    fireEvent.click(await screen.findByRole("button", { name: "New automation" }));
    const create = await screen.findByTestId("ob-create");
    expect((create as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("ob-create-hint").textContent).toBe("Give it a name.");

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "PR watch" } });
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "review merged PRs" } });
    fireEvent.click(screen.getByRole("button", { name: "Repeats" }));
    fireEvent.click(screen.getByRole("option", { name: /Weekdays/ }));
    fireEvent.change(screen.getByLabelText("At"), { target: { value: "07:15" } });
    fireEvent.click(screen.getByRole("button", { name: "Permissions" }));
    fireEvent.click(screen.getByRole("option", { name: /Full access/ }));
    fireEvent.click(screen.getByRole("button", { name: "Model" }));
    fireEvent.click(await screen.findByRole("option", { name: /Gemini 3.7 Flash/ }));

    expect(screen.getByTestId("ob-create-hint").textContent).toBe("");
    fireEvent.click(create);
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({
      title: "PR watch",
      instructions: "review merged PRs",
      cron: "15 7 * * 1-5",
      workspace: "",
      model: "m1",
      mode: "danger-full-access",
      permissions: [],
    });
    // It lands on the automation's own page.
    expect(await screen.findByRole("heading", { name: "PR watch" })).toBeTruthy();
  });

  it("says the server's refusal beside the button and keeps the form", async () => {
    stubFetch([
      { match: "/v1/automations", method: "POST", json: { ok: false, error: "invalid cron expression: x" } },
      { match: "/v1/automations", method: "GET", json: { tasks: [] } },
      SETTINGS,
    ]);
    render(<AutomationsView {...base} />);
    fireEvent.click(await screen.findByRole("button", { name: "New automation" }));
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "PR watch" } });
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "review" } });
    fireEvent.click(screen.getByTestId("ob-create"));
    await waitFor(() => expect(screen.getByTestId("ob-create-hint").textContent).toBe("invalid cron expression: x"));
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("PR watch");
  });

  it("keeps a schedule it cannot say, and sends only what was changed", async () => {
    // The agent wrote "every 15 minutes". The old editor showed it as 09:00 every day and wrote
    // that back on Save, whatever the person had come to change.
    const every15 = task({ schedule: "*/15 * * * *", schedule_raw: { kind: "cron", cron: "*/15 * * * *" }, mode: "read-only" });
    const calls = stubFetch([
      { match: "/v1/automations/task-1", method: "PATCH", json: { ok: true, task: every15 } },
      { match: "/v1/automations/task-1", json: { task: every15, runs: [] } },
      { match: "/v1/automations", json: { tasks: [every15] } },
      SETTINGS,
    ]);
    render(<AutomationsView {...base} />);
    fireEvent.click(await screen.findByRole("button", { name: "More for Daily AI News" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit" }));

    expect((await screen.findByTestId("schedule-kept")).textContent).toContain("*/15 * * * *");
    expect(screen.queryByRole("button", { name: "Repeats" })).toBeNull();
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "Fetch the news, briefly." } });
    fireEvent.click(save);
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ instructions: "Fetch the news, briefly." });
  });

  it("writes a new schedule over one it could not say only when the person sets one", async () => {
    const every15 = task({ schedule: "*/15 * * * *", schedule_raw: { kind: "cron", cron: "*/15 * * * *" } });
    const calls = stubFetch([
      { match: "/v1/automations/task-1", method: "PATCH", json: { ok: true, task: every15 } },
      { match: "/v1/automations/task-1", json: { task: every15, runs: [] } },
      { match: "/v1/automations", json: { tasks: [every15] } },
      SETTINGS,
    ]);
    render(<AutomationsView {...base} />);
    fireEvent.click(await screen.findByRole("button", { name: "More for Daily AI News" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    fireEvent.click(await screen.findByRole("button", { name: "Set a new schedule" }));
    fireEvent.change(screen.getByLabelText("At"), { target: { value: "06:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ cron: "0 6 * * *" });
  });
});

describe("one automation", () => {
  it("lists its runs with the reason a run failed and what waits on the person", async () => {
    const t = task({ seen_runs_at: now - 90 });
    stubFetch([
      {
        match: "/v1/automations/task-1",
        json: {
          task: t,
          runs: [
            run({ run_id: "r3", status: "running", started_at: now - 30, finished_at: null, attention: 1, attention_kind: "question" }),
            run({ run_id: "r2", status: "error", error: "The news site did not answer in time.", started_at: now - 600, finished_at: now - 559 }),
            run({ run_id: "r1", started_at: now - 7200, finished_at: now - 7057, trigger: "manual" }),
          ],
        },
      },
      { match: "/v1/automations", json: { tasks: [t] } },
    ]);
    const onOpenRun = vi.fn();
    render(<AutomationsView {...base} onOpenRun={onOpenRun} initialOpenId="task-1" />);
    const table = await screen.findByTestId("automation-runs");
    const rows = within(table).getAllByRole("row").slice(1); // without the head
    expect(rows[0].textContent).toContain("Needs you · Question");
    expect(rows[0].textContent).toContain("so far");
    expect(within(rows[0]).getByTestId("run-new")).toBeTruthy(); // started after the seen mark
    expect(rows[1].textContent).toContain("Failed");
    expect(rows[1].textContent).toContain("41s");
    expect(within(rows[1]).queryByTestId("run-new")).toBeNull();
    expect(rows[2].textContent).toBe("The news site did not answer in time.");
    expect(rows[3].textContent).toContain("2m 23s");
    expect(rows[3].textContent).toContain("You");

    fireEvent.click(within(rows[0]).getByRole("button", { name: "Open" }));
    expect(onOpenRun).toHaveBeenCalledWith("__run__r1", "", { id: "task-1", title: "Daily AI News" });
  });

  it("says it could not be loaded, and does not wait forever", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/v1/automations/task-1") && !url.includes("/seen")) throw new Error("offline");
        return { ok: true, status: 200, json: async () => ({ tasks: [] }) } as Response;
      }),
    );
    render(<AutomationsView {...base} initialOpenId="task-1" />);
    expect((await screen.findByRole("alert")).textContent).toContain("could not be loaded");
  });
});
