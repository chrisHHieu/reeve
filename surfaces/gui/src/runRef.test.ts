import { describe, expect, it } from "vitest";
import { runRefFrom, runRefFromPresentation, runRefOf } from "./runRef";

describe("runRefFrom (result text)", () => {
  it("reads the id and the step count out of a whole result", () => {
    expect(
      runRefFrom('{"run_id":"explore-2","status":"completed","tokens":41200,"steps":14,"report":"…"}'),
    ).toEqual({ runId: "explore-2", steps: 14 });
  });

  it("still finds the id in a TRUNCATED preview (the report is cut, not the reference)", () => {
    // The live event carries a prefix of the JSON; the engine puts the reference fields
    // before the report so this stays readable no matter how long the report is.
    expect(runRefFrom('{"run_id":"worker-1","status":"running","tokens":1,"steps":3,"rep')).toEqual(
      { runId: "worker-1", steps: 3 },
    );
  });

  it("is null when there is no run behind the call", () => {
    expect(runRefFrom('{"matches":12}')).toBeNull();
    expect(runRefFrom("")).toBeNull();
    expect(runRefFrom(undefined)).toBeNull();
    expect(runRefFrom({ run_id: "x" })).toBeNull();
  });
});

describe("runRefOf (the item's sources, presentation first)", () => {
  it("prefers the presentation, which survives even when the preview is truncated", () => {
    // The failure this fixes: a finished researcher's 8k report pushed `run_id` past the
    // 300-char preview, so the row offered no way into its session (owner report).
    const ref = runRefOf({
      presentation: { summary: "Researcher finished", run_id: "researcher-1", run_steps: 7 },
      preview: '{"report": "a very long report that pushed everything else out of the pre',
    });
    expect(ref).toEqual({ runId: "researcher-1", steps: 7 });
  });

  it("falls back to the result text for sessions recorded before the field existed", () => {
    expect(
      runRefOf({ preview: '{"run_id":"explore-9","steps":2,"report":"…"}' }),
    ).toEqual({ runId: "explore-9", steps: 2 });
  });

  it("ignores a presentation with no run behind it", () => {
    expect(runRefFromPresentation({ summary: "Read x.py" })).toBeNull();
    expect(runRefFromPresentation(null)).toBeNull();
  });
});

describe("runRefOf (a background task names its run)", () => {
  it("reads task_id from a background task's result — that IS the run's id", () => {
    // A background `task` returns `{"task_id": "explore-1", "status": "running", …}` and
    // renders the `state="running"` envelope as its report; the row opens its own run.
    const ref = runRefOf({
      name: "task",
      preview: '{"task_id": "explore-1", "status": "running"}',
    });
    expect(ref).toEqual({ runId: "explore-1" });
  });

  it("reads a running background call's run from the presentation beside the envelope", () => {
    // The envelope names the child by `id`, not `task_id`; the run id the view keys on
    // rides the presentation (`run_id`/`run_steps`, harness/tools/task.py).
    const ref = runRefOf({
      name: "task",
      presentation: { summary: "Explore running in the background as explore-1", run_id: "explore-1" },
      preview:
        '<task id="explore-1" state="running">\n<task_result>\nstill working\n</task_result>\n</task>',
    });
    expect(ref).toEqual({ runId: "explore-1" });
  });

  it("does not read a shell task's task_id as a run", () => {
    expect(
      runRefOf({ name: "shell_task_output", preview: '{"task_id": "shell-3", "output": "…"}' }),
    ).toBeNull();
  });
});
