import { useEffect, useState } from "react";
import { addRoot, getRoots, removeRoot, type RootInfo } from "../../api";
import { Icon } from "../Icon";

// The projects this conversation opened on its own, listed where its permissions are
// chosen and not in Files: Files is the folders the person chose to work in, and a project the
// agent read for reference is a permission it holds, not a place to work. Listed in the tree, they
// piled up there, one per project the agent looked at (owner, 2026-10-18). Each can be taken back,
// or added to Files, which makes it the person's folder.

/** Read when the menu opens, so the list is the session's as of now (a reach adds one mid-turn). */
export function ReachedProjects({ sessionId }: { sessionId: string }) {
  const [reached, setReached] = useState<RootInfo[]>([]);
  const [busy, setBusy] = useState("");

  const load = () =>
    getRoots(sessionId)
      .then((roots) => setReached(roots.filter((r) => r.reached)))
      .catch(() => setReached([]));

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const act = async (path: string, change: () => Promise<{ ok: boolean }>) => {
    setBusy(path);
    try {
      if ((await change()).ok) window.dispatchEvent(new CustomEvent("reeve:roots-changed", { detail: sessionId }));
    } finally {
      setBusy("");
      void load();
    }
  };

  if (!reached.length) return null;
  return (
    <>
      <div className="my-1 border-t border-line" />
      <div className="px-2.5 pt-1 pb-0.5 text-[11px] text-faint" data-testid="reached-projects">
        Opened by the agent
      </div>
      {reached.map((r) => {
        const name = r.label || r.path.split(/[\\/]/).filter(Boolean).pop() || r.path;
        return (
          <div
            key={r.path}
            className="flex items-center gap-2 px-2.5 py-1 text-[12.5px]"
            data-testid="reached-project"
            title={r.path}
          >
            <Icon name="folder" size={13} className="shrink-0 text-faint" />
            <span className="min-w-0 flex-1 truncate text-ink">{name}</span>
            <span className="shrink-0 text-[11px] text-faint">{r.writable ? "read & write" : "read"}</span>
            <button
              type="button"
              className="shrink-0 grid place-items-center w-5 h-5 rounded text-faint hover:text-ink disabled:opacity-40"
              aria-label={`Add ${name} to Files`}
              title="Add to Files"
              disabled={busy === r.path}
              onClick={() => act(r.path, () => addRoot(sessionId, r.path, r.writable))}
            >
              <Icon name="folderPlus" size={13} />
            </button>
            <button
              type="button"
              className="shrink-0 grid place-items-center w-5 h-5 rounded text-faint hover:text-ink disabled:opacity-40"
              aria-label={`Take back ${name}`}
              title="Take back: the agent asks again before it reads here"
              disabled={busy === r.path}
              onClick={() => act(r.path, () => removeRoot(sessionId, r.path))}
            >
              <Icon name="x" size={13} />
            </button>
          </div>
        );
      })}
    </>
  );
}
