// A queue row's last line: where the work lives and what it amounts to. The branch
// (and "worktree" when the folder is a linked one), the project when the list is not filtered to
// one, then the lines the session changed and the last check it ran. Each part is there only when
// the server knows it; a row with none of them has no last line.

import type { SessionInfo } from "../../types";
import { baseName } from "../../paths";
import { Icon } from "../Icon";

export function QueueFacts({ s, showProject }: { s: SessionInfo; showProject: boolean }) {
  const project = showProject && !s.temp_workspace && s.workspace ? baseName(s.workspace) : "";
  const work = s.work;
  const check = work?.check && !work.check.stale ? work.check : undefined;
  if (!s.branch && !s.worktree && !project && !work) return null;
  return (
    <span className="q-facts" data-testid="queue-facts">
      {s.branch && (
        <span className="q-branch" title={`Branch ${s.branch}`}>
          <Icon name="branch" size={11} />
          <span className="q-branch-name">{s.branch}</span>
        </span>
      )}
      {s.worktree && (
        <span className="q-tag" title="This folder is a git worktree of its own">
          worktree
        </span>
      )}
      {project && <span className="q-project">{project}</span>}
      <span className="q-fill" />
      {work && (work.added > 0 || work.removed > 0) && (
        <span
          className="q-diff"
          title={`${work.files} file${work.files === 1 ? "" : "s"} changed` + (work.open ? `, ${work.open} not committed` : "")}
        >
          <span className="q-add">+{work.added}</span>
          <span className="q-del">−{work.removed}</span>
        </span>
      )}
      {check && (
        <span className={"q-check" + (check.ok ? " ok" : " bad")} title={check.summary || (check.ok ? "The last check passed" : "The last check failed")}>
          {/* A mark: its words (the check's own summary) are its title and its name. */}
          <Icon name={check.ok ? "check" : "x"} size={12} />
          <span className="sr-only">{check.ok ? "passed" : "failed"}</span>
        </span>
      )}
    </span>
  );
}
