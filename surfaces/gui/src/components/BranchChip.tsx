// The branch readout under the composer, now a control: a click opens the branch
// picker above it. Before the first message the picker can also make a new worktree, so the
// session starts in its own checkout.

import { useEffect, useRef, useState } from "react";
import { BranchPicker } from "./BranchPicker";
import { Icon } from "./Icon";

export function BranchChip({
  branch,
  path,
  className,
  iconSize = 12,
  allowWorktree = false,
  onSwitched,
  onWorktree,
}: {
  branch: string;
  path: string;
  className: string;
  iconSize?: number;
  allowWorktree?: boolean;
  onSwitched: (branch: string) => void;
  onWorktree?: (path: string, branch: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <span className="branch-pop" ref={ref}>
      <button
        type="button"
        className={className}
        data-testid="branch-chip"
        aria-haspopup="dialog"
        aria-expanded={open}
        title={`On ${branch}: switch or create a branch`}
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name="branch" size={iconSize} />
        <span className="composer-chip-label">{branch}</span>
        <Icon name={open ? "chevronUp" : "chevronDown"} size={iconSize - 1} />
      </button>
      {open && (
        <div className="branch-pop-panel">
          <BranchPicker
            path={path}
            allowWorktree={allowWorktree}
            onSwitched={onSwitched}
            onWorktree={onWorktree}
            onClose={() => setOpen(false)}
          />
        </div>
      )}
    </span>
  );
}
