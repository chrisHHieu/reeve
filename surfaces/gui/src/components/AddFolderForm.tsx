import { useState } from "react";
import { chooseFolder } from "../desktop";
import { Icon } from "./Icon";

// "Give access to a folder", for the composer's chip and the start panel. Closed it is one button;
// open it is a path (Browse in the desktop app, or paste one) and "Allow writes", unticked, so a
// folder is read-only unless the person says otherwise.
export function AddFolderForm({
  onAdd,
  busy,
  compact,
  startOpen,
  onDismiss,
}: {
  onAdd: (path: string, writable: boolean) => Promise<boolean> | boolean | void;
  busy?: boolean;
  compact?: boolean;
  // Open from the start, for a caller with its own button; Cancel and a successful add then call
  // onDismiss so the caller can close it.
  startOpen?: boolean;
  onDismiss?: () => void;
}) {
  const [open, setOpen] = useState(!!startOpen);
  const [path, setPath] = useState("");
  const [writable, setWritable] = useState(false);
  const chosen = path.trim();

  const close = () => {
    setOpen(false);
    setPath("");
    setWritable(false);
    onDismiss?.();
  };

  const browse = async () => {
    const picked = await chooseFolder();
    if (picked) setPath(picked);
  };

  const add = async () => {
    if (!chosen) return;
    // `false` means the caller refused it; anything else closes the form.
    if ((await onAdd(chosen, writable)) !== false) close();
  };

  if (!open) {
    return (
      <button className={compact ? "addfolder-trigger compact" : "addfolder-trigger"} onClick={() => setOpen(true)}>
        <Icon name="folderPlus" size={15} /> Give access to a folder
      </button>
    );
  }

  return (
    <div className="addfolder-form">
      <div className="addfolder-row">
        <input
          className="addfolder-path"
          autoFocus
          placeholder="Choose or paste a folder path…"
          value={path}
          spellCheck={false}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") add();
            if (e.key === "Escape") close();
          }}
        />
        <button className="btn icon-only" onClick={browse} title="Choose location" aria-label="Choose location">
          <Icon name="folder" size={15} />
        </button>
      </div>
      <div className="addfolder-actions">
        <label className="addfolder-write" title="Unticked, the folder is read-only. Tick it to let the agent write there.">
          <input type="checkbox" checked={writable} onChange={(e) => setWritable(e.target.checked)} />
          Allow writes
        </label>
        <span className="spacer" />
        <button className="btn" onClick={close}>
          Cancel
        </button>
        <button className="btn primary" disabled={busy || !chosen} onClick={add}>
          Add
        </button>
      </div>
    </div>
  );
}
