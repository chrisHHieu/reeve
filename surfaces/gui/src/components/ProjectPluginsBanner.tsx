// A project that declares plugins or suggests a marketplace (design §7.3, §11.1)
// says so once in its conversations: what is not in place on this machine yet, and a way to
// the Plugins page to deal with it. Nothing here installs or adds anything. Dismissed per
// folder until what the project asks for changes.
import { useEffect, useState } from "react";
import { projectPlugins, type ProjectPlugins } from "../api/plugins";
import { Icon } from "./Icon";

const DISMISSED = "reeve.projectPlugins.dismissed:";

/** "1 plugin not installed · 1 waiting for your yes · 1 marketplace to add" */
export function projectPluginsLine(view: ProjectPlugins): string {
  const missing = view.plugins.filter((p) => p.state === "missing").length;
  const waiting = view.plugins.filter((p) => p.state === "needs_consent").length;
  const markets = view.marketplaces.filter((m) => !m.known).length;
  const parts: string[] = [];
  if (missing) parts.push(`${missing} plugin${missing === 1 ? "" : "s"} not installed`);
  if (waiting) parts.push(`${waiting} waiting for your yes`);
  if (markets) parts.push(`${markets} marketplace${markets === 1 ? "" : "s"} to add`);
  return parts.join(" · ");
}

function signature(view: ProjectPlugins): string {
  return [
    ...view.plugins.filter((p) => p.state !== "ready").map((p) => `${p.id}:${p.state}`),
    ...view.marketplaces.filter((m) => !m.known).map((m) => `m:${m.name}`),
  ].join("|");
}

function readDismissed(workspace: string): string | null {
  try {
    return localStorage.getItem(DISMISSED + workspace);
  } catch {
    return null;
  }
}

export function ProjectPluginsBanner({
  sessionId,
  workspace,
  onReview,
}: {
  sessionId: string;
  workspace: string | null;
  onReview: () => void;
}) {
  const [view, setView] = useState<ProjectPlugins | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);

  useEffect(() => {
    setView(null);
    if (!workspace) return;
    let live = true;
    setDismissed(readDismissed(workspace));
    projectPlugins({ session: sessionId, workspace })
      .then((v) => live && setView(v))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [sessionId, workspace]);

  if (!view || !workspace || !view.trusted || view.pending === 0) return null;
  const sig = signature(view);
  if (dismissed === sig) return null;
  return (
    <div
      className="flex items-center gap-2 px-4 py-2 mb-1 rounded-lg text-[13px] border border-line bg-accentSoft/40"
      data-testid="project-plugins-banner"
    >
      <Icon name="plug" size={14} className="text-accent shrink-0" />
      <span className="truncate text-muted">
        This project uses plugins: <span className="text-ink">{projectPluginsLine(view)}</span>
      </span>
      <button className="ml-auto shrink-0 text-accent font-medium hover:underline" onClick={onReview}>
        Review
      </button>
      <button
        className="shrink-0 text-muted hover:text-ink"
        aria-label="Dismiss"
        data-testid="project-plugins-dismiss"
        onClick={() => {
          try {
            localStorage.setItem(DISMISSED + workspace, sig);
          } catch {
            /* per-viewer convenience only */
          }
          setDismissed(sig);
        }}
      >
        <Icon name="x" size={14} />
      </button>
    </div>
  );
}
