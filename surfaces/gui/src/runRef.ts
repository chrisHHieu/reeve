// A delegated call's child-session reference, so its transcript row can offer
// `· N steps` beside the summary and `↗ Open` under it.
//
// Two sources, in this order:
//
//  1. the tool's PRESENTATION (`run_id` / `run_steps`) — delivered whole on both paths (the
//     live event and the replayed `_presentation` sidecar). This is the reliable one: a
//     result PREVIEW is the first 300 characters of the result JSON, so a `run_id` that sat
//     after an 8k report never survived it (owner report 2026-09-16: a finished researcher
//     row offered no way into its session).
//  2. the regex over the preview, kept for sessions recorded before the field existed —
//     the engine now writes `run_id` first, so a prefix match still finds it. A background
//     spawn's result names the child `task_id` instead (that is the name the model resumes
//     it by, and the child session's id either way), so subagent tools read that spelling
//     too. Scoped by tool name: a shell task's `task_id` is not a subagent child.

export interface RunRef {
  runId: string;
  steps?: number;
}

const RUN_ID = /"run_id"\s*:\s*"([^"]+)"/;
const TASK_ID = /"task_id"\s*:\s*"([^"]+)"/;
const STEPS = /"steps"\s*:\s*(\d+)/;

/** The tools whose `task_id` IS a delegated child session. `shell_task_output`'s is not. */
const SUBAGENT_TOOLS = new Set(["task"]);

/** The child session behind a tool item, or null when the call has none. */
export function runRefOf(item: {
  presentation?: unknown;
  callPresentation?: unknown;
  preview?: unknown;
  args?: any;
  name?: string;
}): RunRef | null {
  for (const presented of [item.presentation, item.callPresentation]) {
    const ref = runRefFromPresentation(presented);
    if (ref) return ref;
  }
  return runRefFrom(item.preview, {
    taskId: typeof item.name === "string" && SUBAGENT_TOOLS.has(item.name),
  });
}

/** A presentation as `ToolPresentation` serializes it (`harness/tools/contract.py`). */
export function runRefFromPresentation(presented: unknown): RunRef | null {
  if (!presented || typeof presented !== "object") return null;
  const raw = (presented as { run_id?: unknown }).run_id;
  if (typeof raw !== "string" || !raw) return null;
  const steps = (presented as { run_steps?: unknown }).run_steps;
  return typeof steps === "number" ? { runId: raw, steps } : { runId: raw };
}

/** The run reference in a result's text (whole on replay, a prefix on the live event). */
export function runRefFrom(
  preview: unknown,
  options?: { taskId?: boolean },
): RunRef | null {
  if (typeof preview !== "string" || !preview) return null;
  const id = RUN_ID.exec(preview) ?? (options?.taskId ? TASK_ID.exec(preview) : null);
  if (!id) return null;
  const steps = STEPS.exec(preview);
  return { runId: id[1], ...(steps ? { steps: Number(steps[1]) } : {}) };
}
