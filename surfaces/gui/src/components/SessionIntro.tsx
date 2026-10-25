import { useEffect, useState } from "react";
import { getConnectors, getSessionConnections } from "../api";
import { ConnectorIcon } from "../connectors/ConnectorIcon";
import { indexConnectors, visualFor, type ConnectorMap } from "../connectors/visuals";
import type { Attachment } from "../types";
import { useRoots } from "../useRoots";
import { AddFolderForm } from "./AddFolderForm";
import { Icon, type IconName } from "./Icon";

// Things to try in a new conversation, as rows under the composer (its heading is above it, in
// session/IdleHero.tsx). A row says the task in a few words, its tooltip what it comes to, and a
// press puts the task's words in the composer. A task that needs apps is offered only once they
// are connected here: offering it to everyone pitched apps a person may never use.

type Task = { id: string; icon: IconName; label: string; outcome: string; prompt: string; apps: string[] };

const FOLDER: Task = {
  id: "folder",
  icon: "folder",
  label: "Summarize a folder",
  outcome: "I'll read them and summarize what matters",
  prompt: "Analyze the files in this folder and summarize what matters.",
  apps: [],
};

const APP_TASKS: Task[] = [
  {
    id: "github-slack",
    icon: "clock",
    label: "GitHub report to Slack",
    outcome: "Repo activity, summarized and posted every Friday",
    prompt: "Set up a weekly progress report: summarize activity in my GitHub repos and post it to Slack every Friday morning.",
    apps: ["github", "slack"],
  },
];

export function SessionIntroTasks({ sessionId, onPrefill }: { sessionId: string; onPrefill: (text: string, attachments?: Attachment[]) => void }) {
  const { roots, busy, error, addRoot } = useRoots(sessionId);
  // The apps this conversation can use now: connected, and not turned off here — what the
  // connections glance above shows, so the two cannot disagree.
  const [live, setLive] = useState<Set<string>>(new Set());
  const [visuals, setVisuals] = useState<ConnectorMap>({});
  const [addingFolder, setAddingFolder] = useState(false);

  useEffect(() => {
    getSessionConnections(sessionId)
      .then((c) => setLive(new Set(c.connected.filter((x) => x.enabled).map((x) => x.connector))))
      .catch(() => {});
    getConnectors()
      .then((list) => setVisuals(indexConnectors(list)))
      .catch(() => {});
  }, [sessionId]);

  // With a folder shared already, the task starts; without one, the row opens the form to share one.
  const hasFolder = roots.some((r) => !r.primary);
  const ready = APP_TASKS.filter((task) => task.apps.every((app) => live.has(app)));

  const row = (task: Task, onClick: () => void) => (
    <button key={task.id} className="start-row task-row" data-testid={`intro-task-${task.id}`} title={task.outcome} onClick={onClick}>
      <Icon name={task.icon} size={14} className="start-row-ico" />
      <span className="start-row-text">{task.label}</span>
      {task.apps.length > 0 && (
        <span className="task-row-dots">
          {task.apps.map((app) => (
            <span className="task-dot" key={app}>
              <ConnectorIcon connector={visualFor(app, "connector", visuals)} size={12} />
            </span>
          ))}
        </span>
      )}
      <span className="task-row-act">→</span>
    </button>
  );

  return (
    <div className="start-tasks" data-testid="intro-tasks" aria-label="Things to try">
      {row(FOLDER, () => (hasFolder ? onPrefill(FOLDER.prompt) : setAddingFolder((open) => !open)))}
      {ready.map((task) => row(task, () => onPrefill(task.prompt)))}
      {addingFolder && (
        <div className="intro-addfolder">
          <AddFolderForm
            startOpen
            busy={busy}
            onAdd={async (path, writable) => {
              const added = await addRoot(path, writable);
              if (added !== false) onPrefill(FOLDER.prompt);
              return added;
            }}
            onDismiss={() => setAddingFolder(false)}
          />
          {error && <div className="roots-err">{error}</div>}
        </div>
      )}
    </div>
  );
}
