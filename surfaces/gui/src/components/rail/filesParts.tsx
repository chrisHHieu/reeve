// Small parts of the Files tab (workspace panel redesign, 2026-10-11): a new file or folder is
// named in place, in the tree where it will live, instead of in a dialog over it.
import { useState } from "react";

export type TreeAction = "rename" | "move" | "delete" | "new-file" | "new-folder";

/** What the system calls the place a deleted file goes, so the question says it plainly. */
export const trashName =
  typeof navigator !== "undefined" && /Win/.test(navigator.platform) ? "Recycle Bin" : "Trash";

export function NewEntryRow({
  kind,
  initial = "",
  depth,
  onCreate,
  onCancel,
}: {
  kind: "file" | "folder";
  /** A name to start from (a search that found nothing offers to make it). */
  initial?: string;
  depth: number;
  /** Make it; the error to show under the name, or null when it is made. */
  onCreate: (name: string) => Promise<string | null>;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const value = name.trim();
    if (!value || busy) return;
    setBusy(true);
    const refused = await onCreate(value);
    setBusy(false);
    setError(refused);
  };
  return (
    <div className="tree-new" style={{ paddingLeft: 24 + depth * 13 }} data-testid="fm-new-row">
      <input
        autoFocus
        className="tree-new-input"
        data-testid="fm-new-name"
        aria-label={kind === "folder" ? "New folder name" : "New file name"}
        aria-invalid={!!error}
        aria-describedby={error ? "fm-new-error" : undefined}
        placeholder={kind === "folder" ? "Folder name" : "File name"}
        value={name}
        spellCheck={false}
        autoComplete="off"
        disabled={busy}
        onChange={(event) => {
          setName(event.target.value);
          setError(null);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") void submit();
          if (event.key === "Escape") onCancel();
        }}
        onBlur={() => {
          if (!name.trim() && !busy) onCancel();
        }}
      />
      {error ? (
        <div className="tree-new-error" id="fm-new-error" role="alert" data-testid="fm-new-error">
          {error}
        </div>
      ) : (
        <div className="tree-new-hint">Enter to create · Esc</div>
      )}
    </div>
  );
}
