// The live jobs strip (`JobsDock.tsx`): it is there while a job runs and gone when none does,
// its closed header says what the job is printing, and its rows are the shared ones — the
// moving mark for running, the two-press Stop, the log behind the row.
//
// A view of the pushed roster: it reads nothing itself.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const getSessionJobs = vi.fn();
const stopSessionJob = vi.fn();
const getJobOutput = vi.fn();
const observeSessionJob = vi.fn();
vi.mock("../../api/jobs", () => ({
  getSessionJobs: (...args: unknown[]) => getSessionJobs(...args),
  stopSessionJob: (...args: unknown[]) => stopSessionJob(...args),
  getJobOutput: (...args: unknown[]) => getJobOutput(...args),
  observeSessionJob: (...args: unknown[]) => observeSessionJob(...args),
}));

import { ActivityRow } from "./ActivityRow";
import type { JobRow as JobRowData } from "../../api/jobs";
import type { JobOutputBuffer } from "../../live/jobOutput";

// The jobs are a segment of the activity row and the list under it; alone in the row, they
// are what these tests read.
const JobsDock = (props: { sessionId: string; jobs?: readonly JobRowData[]; buffers?: Record<string, JobOutputBuffer> }) => (
  <ActivityRow {...props} />
);
import {
  LOG_GAP,
  LOG_RENDER_CHARS,
  jobElapsedMs,
  jobOutcome,
  jobTone,
  lastLine,
  liveJobs,
  logText,
  sameJobs,
  settledJobs,
} from "../../jobView";

afterEach(cleanup);
beforeEach(() => {
  getSessionJobs.mockReset();
  stopSessionJob.mockReset();
  getJobOutput.mockReset();
  observeSessionJob.mockReset();
  observeSessionJob.mockResolvedValue({ ok: true, observing: 1 });
  stopSessionJob.mockResolvedValue({ ok: true });
  getJobOutput.mockResolvedValue({ ok: true, output: "", truncated: false, total_chars: 0 });
});

const RUNNING = {
  id: "bg-1",
  command: 'npx playwright test --workers=3 2>&1 | Select-Object -Last 8',
  cwd: "D:\\Project\\reeve\\surfaces\\gui",
  shell: "pwsh",
  status: "running" as const,
  started_at: 1_700_000_000,
  exit_code: null,
};

const DONE = {
  id: "bg-2",
  command: "npm test",
  shell: "pwsh",
  status: "finished" as const,
  started_at: 1_700_000_000,
  ended_at: 1_700_000_031,
  exit_code: 0,
  duration_ms: 31_000,
};

describe("JobsDock", () => {
  it("renders nothing while the session has no jobs", () => {
    const { container } = render(<JobsDock sessionId="s1" jobs={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("appears with the roster that names a running job, and reads nothing itself", () => {
    const { rerender } = render(<JobsDock sessionId="s1" jobs={[]} />);
    rerender(<JobsDock sessionId="s1" jobs={[RUNNING]} />);
    expect(screen.getByTestId("jobs-dock").getAttribute("data-state")).toBe("live");
    expect(getSessionJobs).not.toHaveBeenCalled();
  });

  it("names the running job in the header and lists it with its command", () => {
    render(<JobsDock sessionId="s1" jobs={[RUNNING]} />);
    expect(screen.getByTestId("jobs-dock-progress").textContent).toContain("1 running");
    expect(screen.getByTestId("jobs-dock-toggle").textContent).toContain("npx playwright test");

    fireEvent.click(screen.getByTestId("jobs-dock-toggle"));
    const row = screen.getAllByTestId("job-row")[0];
    expect(row.getAttribute("data-status")).toBe("running");
    expect(row.textContent).toContain("npx playwright test");
    // Running is the moving mark, never a solid dot (a green dot would read as "finished").
    expect(screen.getByTestId("job-running-dot")).toBeTruthy();
    expect(screen.queryByTestId("job-dot")).toBeNull();
    // It has printed nothing: the row says so rather than leaving a blank line.
    expect(row.textContent).toContain("No output yet");
  });

  it("says in its closed header what the job is printing, and moves with the roster", () => {
    const { rerender } = render(
      <JobsDock sessionId="s1" jobs={[{ ...RUNNING, last_line: "ok 12 › login works" }]} />,
    );
    expect(screen.getByTestId("jobs-dock-said").textContent).toBe("ok 12 › login works");
    // The next pushed roster carries a new line: the closed strip shows it.
    rerender(<JobsDock sessionId="s1" jobs={[{ ...RUNNING, last_line: "ok 13 › logout works" }]} />);
    expect(screen.getByTestId("jobs-dock-said").textContent).toBe("ok 13 › logout works");
    // None of that needed the job to be observed.
    expect(observeSessionJob).not.toHaveBeenCalled();
  });

  it("speaks for the job that is printing when another one is silent", () => {
    render(
      <JobsDock
        sessionId="s1"
        jobs={[
          { ...RUNNING, id: "bg-7", command: "npm run dev", started_at: 1_700_000_500 },
          { ...RUNNING, last_line: "ok 12 › login works" },
        ]}
      />,
    );
    const header = screen.getByTestId("jobs-dock-toggle").textContent ?? "";
    expect(header).toContain("npx playwright test");
    expect(header).toContain("ok 12 › login works");
    expect(screen.getByTestId("jobs-dock-progress").textContent).toContain("2 running");
  });

  it("stays as a quiet count once every job has finished, and Clear forgets them", () => {
    const { container, rerender } = render(<JobsDock sessionId="s1" jobs={[RUNNING]} />);
    expect(screen.getByTestId("jobs-dock").getAttribute("data-state")).toBe("live");
    rerender(<JobsDock sessionId="s1" jobs={[DONE]} />);
    // The finished list was the header chip's. Its home is this segment now.
    expect(screen.getByTestId("jobs-dock").getAttribute("data-state")).toBe("done");
    expect(screen.getByTestId("jobs-dock-progress").textContent).toBe("1 finished");
    fireEvent.click(screen.getByTestId("jobs-dock-toggle"));
    expect(screen.getByTestId("jobs-finished-toggle").textContent).toBe("Finished 1");
    expect(screen.getAllByTestId("job-item")).toHaveLength(1);
    fireEvent.click(screen.getByTestId("jobs-clear"));
    expect(container.innerHTML).toBe("");
  });

  it("arms Stop on the first press and fires on the second", async () => {
    render(<JobsDock sessionId="s1" jobs={[RUNNING]} />);
    fireEvent.click(screen.getByTestId("jobs-dock-toggle"));
    const stop = screen.getByTestId("job-stop");

    fireEvent.click(stop);
    expect(stop.getAttribute("data-kill-state")).toBe("armed");
    expect(stop.textContent).toContain("Confirm stop");
    expect(stopSessionJob).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("job-stop"));
    await waitFor(() => expect(stopSessionJob).toHaveBeenCalledWith("s1", "bg-1"));
  });

  it("opens a job's log from its row, and gives the observation back when it closes", async () => {
    getJobOutput.mockResolvedValue({
      ok: true,
      output: "line 28\nline 29\nline 30\n",
      truncated: true,
      total_chars: 51_234,
    });
    render(<JobsDock sessionId="s1" jobs={[RUNNING]} />);
    fireEvent.click(screen.getByTestId("jobs-dock-toggle"));
    // The strip opens its list, not a log: a row is opened by the reader.
    expect(screen.queryByTestId("job-output")).toBeNull();
    expect(observeSessionJob).not.toHaveBeenCalled();

    fireEvent.click(screen.getAllByTestId("job-expand")[0]);
    await waitFor(() => expect(getJobOutput).toHaveBeenCalledWith("s1", "bg-1", expect.any(Number)));
    const output = await screen.findByTestId("job-output");
    await waitFor(() => expect(output.textContent).toContain("line 30"));
    // The panel never passes a cut log off as the whole one.
    expect(screen.getByTestId("job-output-cut")).toBeTruthy();

    expect(observeSessionJob).toHaveBeenCalledWith("s1", "bg-1", true);
    fireEvent.click(screen.getAllByTestId("job-expand")[0]);
    expect(screen.queryByTestId("job-output")).toBeNull();
    // Closing the row takes the observation back: an unwatched job streams nothing.
    expect(observeSessionJob).toHaveBeenLastCalledWith("s1", "bg-1", false);
  });

  it("stops watching a log when the strip itself is closed over it", async () => {
    render(<JobsDock sessionId="s1" jobs={[RUNNING]} />);
    fireEvent.click(screen.getByTestId("jobs-dock-toggle"));
    fireEvent.click(screen.getAllByTestId("job-expand")[0]);
    await waitFor(() => expect(observeSessionJob).toHaveBeenCalledWith("s1", "bg-1", true));
    fireEvent.click(screen.getByTestId("jobs-dock-toggle"));
    expect(observeSessionJob).toHaveBeenLastCalledWith("s1", "bg-1", false);
  });

  it("says a job produced nothing yet rather than showing an empty box", async () => {
    render(<JobsDock sessionId="s1" jobs={[RUNNING]} />);
    fireEvent.click(screen.getByTestId("jobs-dock-toggle"));
    fireEvent.click(screen.getAllByTestId("job-expand")[0]);
    await waitFor(() =>
      expect(screen.getByTestId("job-output").textContent).toContain("No output yet."),
    );
  });
});

describe("job derivations", () => {
  it("reads an outcome from the exit code, and elapsed from the measured duration", () => {
    expect(jobOutcome(RUNNING)).toBe("running");
    expect(jobOutcome(DONE)).toBe("exit 0");
    expect(jobOutcome({ ...DONE, exit_code: 3 })).toBe("exit 3");
    expect(jobOutcome({ ...DONE, exit_code: null })).toBe("finished");
    // A stopped job says so, whatever code the kill left behind.
    expect(jobOutcome({ ...DONE, exit_code: 1, killed: true })).toBe("stopped");
    expect(jobElapsedMs(DONE, 0)).toBe(31_000);
    // Still running: now − its start (epoch seconds on the wire).
    expect(jobElapsedMs(RUNNING, 1_700_002_000_000)).toBe(2_000_000);
  });

  it("tells a failure from a stop from a success", () => {
    expect(jobTone(RUNNING)).toBe("running");
    expect(jobTone(DONE)).toBe("ok");
    expect(jobTone({ ...DONE, exit_code: null })).toBe("ok");
    expect(jobTone({ ...DONE, exit_code: 2 })).toBe("failed");
    // The kill's own code is not the command's verdict.
    expect(jobTone({ ...DONE, exit_code: 1, killed: true })).toBe("stopped");
  });

  it("reads a row's last line as plain text", () => {
    expect(lastLine(RUNNING)).toBe("");
    expect(lastLine({ ...RUNNING, last_line: "\u001b[1;32mPASS\u001b[0m  src/a.test.ts" })).toBe(
      "PASS src/a.test.ts",
    );
    // A hyperlink escape (OSC 8) is furniture too.
    expect(
      lastLine({ ...RUNNING, last_line: "\u001b]8;;https://x.test\u0007report\u001b]8;;\u0007 ready" }),
    ).toBe("report ready");
  });

  it("orders running jobs by start and finished ones by finish, newest first", () => {
    const later = { ...RUNNING, id: "bg-9", started_at: 1_700_000_900 };
    const earlierDone = { ...DONE, id: "bg-3", ended_at: 1_700_000_010 };
    const jobs = [later, DONE, RUNNING, earlierDone];
    expect(liveJobs(jobs).map((job) => job.id)).toEqual(["bg-1", "bg-9"]);
    expect(settledJobs(jobs).map((job) => job.id)).toEqual(["bg-2", "bg-3"]);
  });

  it("knows two rosters a row would draw the same way", () => {
    expect(sameJobs([RUNNING, DONE], [{ ...RUNNING }, { ...DONE }])).toBe(true);
    expect(sameJobs([RUNNING], [{ ...RUNNING, last_line: "one more line" }])).toBe(false);
    expect(sameJobs([RUNNING], [{ ...RUNNING, status: "finished", exit_code: 0 }])).toBe(false);
    expect(sameJobs([RUNNING], [RUNNING, DONE])).toBe(false);
  });
});

describe("a job's log", () => {
  const buffer = (text: string, total: number) => ({ text, total, gapBefore: false, ended: false });

  it("is being read until the anchor answers", () => {
    expect(logText(null).state).toBe("reading");
  });

  it("is the anchor followed by what the stream delivered beyond it", () => {
    const anchor = { ok: true, output: "one\ntwo\n", truncated: false, total_chars: 8 };
    // The buffer overlaps the anchor ("two\n") and then goes on: nothing is shown twice.
    expect(logText(anchor, buffer("two\nthree\n", 14))).toEqual({
      state: "ready",
      text: "one\ntwo\nthree\n",
      cut: false,
    });
  });

  it("prints a hole between the two as a line of its own", () => {
    const anchor = { ok: true, output: "one", truncated: false, total_chars: 3 };
    const log = logText(anchor, buffer("nine\n", 400));
    expect(log.text).toBe(`one\n${LOG_GAP}\nnine\n`);
    expect(log.cut).toBe(false);
  });

  it("says the head is missing when the tail was cut, or the stream started late", () => {
    expect(logText({ ok: true, output: "end\n", truncated: true, total_chars: 900 }).cut).toBe(true);
    // No backlog at all and a buffer that begins later: a missing head, not a hole in the middle.
    const late = logText({ ok: false, error: "gone" }, buffer("tail\n", 500));
    expect(late).toEqual({ state: "ready", text: "tail\n", cut: true });
  });

  it("fails only when there is nothing to show at all", () => {
    expect(logText({ ok: false, error: "No job bg-9 in this session." })).toEqual({
      state: "failed",
      text: "",
      cut: false,
      error: "No job bg-9 in this session.",
    });
    expect(logText({ ok: false }).error).toBe("This job's output could not be read.");
  });

  it("draws the end of a very long log, from a whole line, and says it is cut", () => {
    const line = "x".repeat(99) + "\n";
    const long = line.repeat(1_000); // 100,000 characters
    const log = logText({ ok: true, output: long, truncated: false, total_chars: long.length });
    expect(log.cut).toBe(true);
    expect(log.text.length).toBeLessThanOrEqual(LOG_RENDER_CHARS);
    expect(log.text.length).toBeGreaterThan(LOG_RENDER_CHARS - 200);
    expect(log.text.startsWith("x".repeat(99) + "\n")).toBe(true);
    expect(log.text.endsWith(line)).toBe(true);
  });
});
