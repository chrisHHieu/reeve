import { useEffect, useRef, useState } from "react";
import {
  announceAutomationsChanged,
  getAutomation,
  markAutomationSeen,
  updateAutomation,
  type Automation,
  type AutomationRun,
} from "../../api";
import { displayModelFull } from "../../modelName";
import { Button, PageShell } from "../../ui";
import { modeLabel } from "../composer/modes";
import { Icon } from "../Icon";
import { Toggle } from "../Toggle";
import { RowMenu } from "./RowMenu";
import { runState, startedBy } from "./runState";
import { took, whenText } from "./when";

// One automation: when it runs, what it does, what it may do without asking, and every run
// it has made. A run is a conversation, so each row opens one.

const folderName = (path: string) => path.split(/[\\/]/).filter(Boolean).pop() || path;

interface Props {
  id: string;
  onBack: () => void;
  onEdit: (task: Automation) => void;
  onDelete: (task: Automation) => void;
  onOpenRun: (sessionId: string, workspace: string, task?: { id: string; title: string }) => void;
  onRunNow: (taskId: string, title?: string) => void | Promise<void>;
}

export function AutomationDetail({ id, onBack, onEdit, onDelete, onOpenRun, onRunNow }: Props) {
  const [task, setTask] = useState<Automation | null>(null);
  const [runs, setRuns] = useState<AutomationRun[]>([]);
  const [failed, setFailed] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The seen mark as it was when the page opened. Opening moves the stored mark forward (the
  // sidebar's count clears), and the rows that were new then stay marked while the page is up.
  const [seenMark, setSeenMark] = useState<number | null>(null);
  const back = useRef(onBack);
  back.current = onBack;

  const load = () =>
    getAutomation(id)
      .then((d) => {
        if (!d.task) {
          // Deleted, or a stale reopen target: go to the list. A page that waits for it forever is a trap.
          back.current();
          return;
        }
        setFailed(false);
        setTask(d.task);
        setRuns(d.runs || []);
        setSeenMark((mark) => (mark === null ? d.task.seen_runs_at ?? 0 : mark));
      })
      .catch(() => setFailed(true));

  useEffect(() => {
    setTask(null);
    setSeenMark(null);
    load();
    markAutomationSeen(id)
      .then(() => announceAutomationsChanged())
      .catch(() => {});
    // A run that is going ends while the page is open: the table is read again as the list is.
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [id]);

  if (!task) {
    return (
      <PageShell testId="automation-detail">
        <div className="auto-view">
          <button type="button" className="auto-back" onClick={onBack}>
            <Icon name="arrowLeft" size={13} />
            Automations
          </button>
          {failed ? (
            <div className="auto-alert" role="alert">
              This automation could not be loaded.
              <div>
                <Button size="sm" icon="refresh" onClick={load}>
                  Try again
                </Button>
              </div>
            </div>
          ) : (
            <div aria-busy="true" aria-label="Loading the automation" className="auto-skeleton">
              <div className="auto-sk" style={{ width: "38%", height: 18 }} />
              <div className="auto-sk" style={{ width: "56%", height: 9 }} />
              <div className="auto-sk" style={{ width: "100%", height: 64, marginTop: 14 }} />
            </div>
          )}
        </div>
      </PageShell>
    );
  }

  const flip = async (on: boolean) => {
    setError(null);
    setTask({ ...task, enabled: on });
    try {
      const res = await updateAutomation(id, { enabled: on });
      if (!res?.ok) throw new Error();
      await load();
    } catch {
      setTask({ ...task, enabled: !on });
      setError(on ? "It could not be turned on." : "It could not be turned off.");
    }
  };
  const run = async () => {
    setStarting(true);
    try {
      await onRunNow(id, task.title);
    } finally {
      setStarting(false);
    }
  };
  const grants = task.always_allowed || [];
  const now = new Date();

  return (
    <PageShell testId="automation-detail">
      <div className="auto-view">
        <button type="button" className="auto-back" onClick={onBack}>
          <Icon name="arrowLeft" size={13} />
          Automations
        </button>
        <div className="auto-head">
          <div className="auto-fill">
            <h1 className="page-title">{task.title}</h1>
            <div className="auto-headline">
              <Toggle checked={task.enabled} onChange={flip} ariaLabel={`${task.title}: on or off`} />
              <span className="auto-num">
                {task.enabled ? (task.next_run ? `On · next run ${whenText(task.next_run, now)}` : "On") : "Paused"} ·{" "}
                {task.schedule}
              </span>
            </div>
            {error && (
              <p className="auto-error" role="alert">
                {error}
              </p>
            )}
          </div>
          <Button variant="primary" icon="play" loading={starting} onClick={run}>
            Run now
          </Button>
          <Button onClick={() => onEdit(task)}>Edit</Button>
          <RowMenu
            label={`More for ${task.title}`}
            items={[{ id: "delete", label: "Delete…", icon: "trash", danger: true, onPick: () => onDelete(task) }]}
          />
        </div>

        <div className="auto-facts">
          <span>
            Works in <b title={task.workspace || undefined}>{task.workspace ? folderName(task.workspace) : "Scratch folder"}</b>
          </span>
          <span>
            Permissions <b>{modeLabel(task.mode || "workspace-write")}</b>
          </span>
          <span>
            Model <b>{task.model ? displayModelFull(task.model) : "Server default"}</b>
          </span>
        </div>

        <h2 className="auto-h2">Instructions</h2>
        <div className="auto-card auto-box">{task.instructions}</div>

        {grants.length > 0 && (
          <>
            <h2 className="auto-h2">Allowed without asking</h2>
            <p className="auto-help">What this automation may do without stopping to ask. Everything else still asks first.</p>
            <div className="auto-card" data-testid="task-grants">
              {grants.map((rule) => (
                <div className="auto-grant" key={rule.entry}>
                  <span className="auto-fill auto-mono" title={rule.entry}>
                    {rule.tool}
                    {rule.target ? ` → ${rule.target}` : ""}
                  </span>
                  <Button
                    size="sm"
                    title="This automation will ask for approval again"
                    onClick={async () => {
                      await updateAutomation(id, { revoke: rule.entry });
                      load();
                    }}
                  >
                    Revoke
                  </Button>
                </div>
              ))}
            </div>
          </>
        )}

        <h2 className="auto-h2">Runs</h2>
        <p className="auto-help">Each run is a conversation. Open one to see what was done, or to ask a follow-up.</p>
        {runs.length === 0 ? (
          <div className="auto-card auto-box auto-quiet">It has not run yet.</div>
        ) : (
          <div className="auto-card auto-runs-wrap">
            <table className="auto-runs" data-testid="automation-runs">
              <thead>
                <tr>
                  <th scope="col">Result</th>
                  <th scope="col">Started</th>
                  <th scope="col" className="auto-wide-only">
                    Took
                  </th>
                  <th scope="col" className="auto-wide-only">
                    Started by
                  </th>
                  <th scope="col">
                    <span className="auto-sr">Open</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => {
                  const state = runState(r);
                  const fresh = seenMark !== null && r.started_at > seenMark;
                  const note = r.error || r.result_text;
                  const seconds = (r.finished_at ?? now.getTime() / 1000) - r.started_at;
                  return (
                    <RunRows
                      key={r.run_id}
                      note={note}
                      noteIsError={!!r.error}
                      cells={
                        <>
                          <td>
                            <span className={"auto-status " + state.tone}>
                              {r.status === "running" ? (
                                <Icon name="loader" size={12} />
                              ) : (
                                state.tone && <span className="auto-dot" aria-hidden />
                              )}
                              {state.word}
                              {state.waitsFor ? ` · ${state.waitsFor}` : ""}
                            </span>
                            {fresh && (
                              <span className="auto-new" data-testid="run-new">
                                New
                              </span>
                            )}
                          </td>
                          <td className="auto-num">{whenText(r.started_at, now)}</td>
                          <td className="auto-num auto-wide-only">
                            {took(seconds)}
                            {r.finished_at ? "" : " so far"}
                          </td>
                          <td className="auto-wide-only">{startedBy(r.trigger)}</td>
                          <td className="auto-end">
                            <button
                              type="button"
                              className="auto-link"
                              title="Open this run's conversation"
                              disabled={!r.session_id}
                              onClick={() => onOpenRun(r.session_id, task.workspace, { id: task.id, title: task.title })}
                            >
                              Open
                            </button>
                          </td>
                        </>
                      }
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </PageShell>
  );
}

/** A run's row, and under it the one line that says why it failed or what it produced. */
function RunRows({ cells, note, noteIsError }: { cells: React.ReactNode; note: string | null; noteIsError: boolean }) {
  return (
    <>
      <tr>{cells}</tr>
      {note && (
        <tr className="auto-note">
          <td colSpan={5} className={noteIsError ? "bad" : undefined} title={note}>
            {note}
          </td>
        </tr>
      )}
    </>
  );
}
