import { useEffect, useRef, useState } from "react";
import {
  announceAutomationsChanged,
  deleteAutomation,
  getAutomations,
  updateAutomation,
  type Automation,
} from "../../api";
import { Button, ConfirmDialog, PageHeader, PageShell } from "../../ui";
import { Icon } from "../Icon";
import { Toggle } from "../Toggle";
import { AutomationDetail } from "./AutomationDetail";
import { AutomationForm, TemplateCard } from "./AutomationForm";
import { RowMenu } from "./RowMenu";
import { runState } from "./runState";
import { TEMPLATES } from "./templates";
import { clock, whenText } from "./when";
import "./automations.css";

// The Automations page. The list is where an automation is run, paused and found out about:
// each row carries its switch, what its last run came to, how many runs have not been looked
// at, Run now, and a menu. A name opens the automation and its runs; New automation and Edit
// open the one form.

interface Props {
  // `task` gives the opened run its context: the banner over the conversation, and the way back.
  onOpenRun: (sessionId: string, workspace: string, task?: { id: string; title: string }) => void;
  onRunNow: (taskId: string, title?: string) => void | Promise<void>;
  /** Open on this automation (the run banner's "Back to runs"). */
  initialOpenId?: string | null;
}

type Page = { at: "list" } | { at: "detail"; id: string } | { at: "new"; template?: string } | { at: "edit"; task: Automation };

export function AutomationsView({ onOpenRun, onRunNow, initialOpenId }: Props) {
  const [tasks, setTasks] = useState<Automation[]>([]);
  // The first read decides between the skeleton, the list and "could not be loaded". A later
  // read that fails keeps what is on the page: an empty list is a claim, and a failed poll
  // does not make it.
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [page, setPage] = useState<Page>(initialOpenId ? { at: "detail", id: initialOpenId } : { at: "list" });
  const [toDelete, setToDelete] = useState<Automation | null>(null);
  const [starting, setStarting] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ id: string; text: string } | null>(null);
  const loaded = useRef(false);

  // The banner over a run can ask for another automation while this page is already up.
  useEffect(() => {
    if (initialOpenId) setPage({ at: "detail", id: initialOpenId });
  }, [initialOpenId]);

  const refresh = () =>
    getAutomations()
      .then((list) => {
        loaded.current = true;
        setTasks(list);
        setState("ready");
      })
      .catch(() => {
        if (!loaded.current) setState("error");
      });
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, []);

  const flip = async (task: Automation, on: boolean) => {
    setRowError(null);
    setTasks((list) => list.map((t) => (t.id === task.id ? { ...t, enabled: on } : t)));
    try {
      const res = await updateAutomation(task.id, { enabled: on });
      if (!res?.ok) throw new Error();
      await refresh();
    } catch {
      setTasks((list) => list.map((t) => (t.id === task.id ? { ...t, enabled: !on } : t)));
      setRowError({ id: task.id, text: on ? "It could not be turned on." : "It could not be turned off." });
    }
  };
  const run = async (task: Automation) => {
    setStarting(task.id);
    try {
      await onRunNow(task.id, task.title);
    } finally {
      setStarting(null);
    }
  };
  const remove = async (task: Automation) => {
    const res = await deleteAutomation(task.id);
    if (!res?.ok) throw new Error("It could not be deleted. Try again.");
    announceAutomationsChanged();
    setToDelete(null);
    setPage({ at: "list" });
    await refresh();
  };

  const confirm = toDelete && (
    <ConfirmDialog
      title={`Delete “${toDelete.title}”?`}
      body="It stops running, and the list of its past runs is deleted with it."
      confirmLabel="Delete"
      busyLabel="Deleting…"
      onConfirm={() => remove(toDelete)}
      onCancel={() => setToDelete(null)}
    />
  );

  if (page.at === "new" || page.at === "edit") {
    return (
      <AutomationForm
        task={page.at === "edit" ? page.task : undefined}
        startTemplate={page.at === "new" ? page.template : undefined}
        onBack={() => setPage(page.at === "edit" ? { at: "detail", id: page.task.id } : { at: "list" })}
        onDone={(task) => {
          refresh();
          setPage({ at: "detail", id: task.id });
        }}
      />
    );
  }

  if (page.at === "detail") {
    return (
      <>
        <AutomationDetail
          id={page.id}
          onBack={() => {
            setPage({ at: "list" });
            refresh();
          }}
          onEdit={(task) => setPage({ at: "edit", task })}
          onDelete={setToDelete}
          onOpenRun={onOpenRun}
          onRunNow={onRunNow}
        />
        {confirm}
      </>
    );
  }

  const now = new Date();
  return (
    <PageShell testId="automations-page">
      <div className="auto-view">
        <PageHeader
          title="Automations"
          sub="Tasks Reeve runs for you on a schedule."
          actions={
            <Button variant="primary" icon="plus" onClick={() => setPage({ at: "new" })}>
              New automation
            </Button>
          }
        />
        <p className="auto-note-line">
          <Icon name="info" size={14} />
          <span>Automations run while Reeve is open. One that was missed runs once, the next time Reeve starts.</span>
        </p>

        {state === "loading" && (
          <div className="auto-card" aria-busy="true" aria-label="Loading automations" data-testid="automations-loading">
            {[
              [34, 56],
              [42, 48],
              [28, 60],
            ].map(([a, b]) => (
              <div className="auto-row" key={a}>
                <div className="auto-sk" style={{ width: 34, height: 20, borderRadius: 999 }} />
                <div className="auto-fill auto-sk-lines">
                  <div className="auto-sk" style={{ width: `${a}%`, height: 9 }} />
                  <div className="auto-sk" style={{ width: `${b}%`, height: 7 }} />
                </div>
                <div className="auto-sk" style={{ width: 70, height: 8, marginRight: 8 }} />
              </div>
            ))}
          </div>
        )}

        {state === "error" && (
          <div className="auto-alert" role="alert" data-testid="automations-error">
            The automations could not be loaded.
            <div>
              <Button
                size="sm"
                icon="refresh"
                onClick={() => {
                  setState("loading");
                  refresh();
                }}
              >
                Try again
              </Button>
            </div>
          </div>
        )}

        {state === "ready" && tasks.length === 0 && (
          <div data-testid="automations-empty">
            <div className="auto-empty-title">Nothing runs on a schedule yet</div>
            <p className="auto-help">Start from a template, write your own, or ask Reeve for one in a conversation.</p>
            <div className="auto-templates two">
              {TEMPLATES.filter((t) => t.conns.length === 0).map((t) => (
                <TemplateCard key={t.key} template={t} onPick={() => setPage({ at: "new", template: t.key })} />
              ))}
            </div>
          </div>
        )}

        {state === "ready" && tasks.length > 0 && (
          <div className="auto-card auto-list" role="list" aria-label="Automations">
            {tasks.map((t) => {
              const last = t.latest_run || null;
              const result = last ? runState(last) : null;
              const resultWhen = !last
                ? ""
                : result!.waitsFor
                  ? result!.waitsFor
                  : last.status === "running"
                    ? `since ${clock(last.started_at)}`
                    : whenText(last.finished_at ?? last.started_at, now);
              return (
                <div className={"auto-row" + (t.enabled ? "" : " off")} role="listitem" key={t.id} data-testid="automation-row">
                  <Toggle checked={t.enabled} onChange={(on) => flip(t, on)} ariaLabel={`${t.title}: on or off`} />
                  <button type="button" className="auto-open" title={t.title} onClick={() => setPage({ at: "detail", id: t.id })}>
                    <span className="auto-title">{t.title}</span>
                    <span className="auto-when">
                      <Icon name="clock" size={12} />
                      {t.schedule} · {t.enabled ? (t.next_run ? `next ${whenText(t.next_run, now)}` : "no next run") : "paused"}
                    </span>
                    {rowError?.id === t.id && (
                      <span className="auto-error" role="alert">
                        {rowError.text}
                      </span>
                    )}
                  </button>
                  <span className={"auto-status " + (result?.tone || "")} data-testid="automation-last-run">
                    {/* A run going on now turns; one that ended is its dot. */}
                    {last?.status === "running" ? (
                      <Icon name="loader" size={12} className="auto-run-spin" />
                    ) : result?.tone ? (
                      <span className="auto-dot" aria-hidden />
                    ) : null}
                    {result ? `${result.word}${resultWhen ? ` · ${resultWhen}` : ""}` : "Not run yet"}
                  </span>
                  {(t.unseen_runs || 0) > 0 && (
                    <span
                      className="auto-count"
                      title={`${t.unseen_runs} new run${t.unseen_runs === 1 ? "" : "s"}`}
                      aria-label={`${t.unseen_runs} new run${t.unseen_runs === 1 ? "" : "s"}`}
                    >
                      {t.unseen_runs}
                    </span>
                  )}
                  <Button size="sm" icon="play" loading={starting === t.id} onClick={() => run(t)} aria-label={`Run ${t.title} now`}>
                    <span className="auto-run-word">Run now</span>
                  </Button>
                  <RowMenu
                    label={`More for ${t.title}`}
                    items={[
                      { id: "edit", label: "Edit", icon: "pencil", onPick: () => setPage({ at: "edit", task: t }) },
                      { id: "delete", label: "Delete…", icon: "trash", danger: true, onPick: () => setToDelete(t) },
                    ]}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>
      {confirm}
    </PageShell>
  );
}
