// `/open` — the composer's file picker (the `file.open` /
// `dialog-select-file`): a searchable dialog over the session's workspace, scratch and
// extra roots. Typing filters server-side (GET /v1/sessions/{id}/files), results are
// grouped by directory in the server's rank order, ↑/↓ walk them, Enter opens the file
// in the existing RightRail viewer. Our design language: the SearchModal shell (the
// app's command-palette pattern), quiet rows with file icons.
import { useEffect, useMemo, useRef, useState } from "react";
import { searchSessionFiles, type SessionFile } from "../api";
import { FileIcon } from "./FileIcon";
import { Icon } from "./Icon";

// The query is debounced: every keystroke is a server walk, and a typed name arrives
// faster than a walk finishes.
const DEBOUNCE_MS = 180;

const dirOf = (path: string) => {
  const parts = path.split("/");
  parts.pop();
  return parts.join("/");
};

export function OpenFileDialog({
  sessionId,
  onOpen,
  onClose,
}: {
  sessionId: string;
  /** Receives the file's absolute path — what the viewer reads. */
  onOpen: (path: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [files, setFiles] = useState<SessionFile[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  // Stale responses lose: each request carries a sequence number and only the newest
  // lands (a slow "a" must not overwrite a fast "app.py").
  const seq = useRef(0);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const id = ++seq.current;
    setLoading(true);
    const timer = window.setTimeout(() => {
      searchSessionFiles(sessionId, query)
        .then((res) => {
          if (seq.current !== id) return;
          setFiles(res.files);
          setTruncated(res.truncated);
          // `ok: false` is the server's honest refusal (a remote session's files are on
          // the host); render its own words, never an empty list that reads as "none".
          setError(res.ok ? null : (res.error ?? "Could not search this session's files."));
        })
        .catch(() => {
          if (seq.current !== id) return;
          setFiles([]);
          setError("Could not search this session's files.");
        })
        .finally(() => {
          if (seq.current === id) setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [sessionId, query]);

  // Reset the highlight whenever the result set changes.
  useEffect(() => {
    setActive(0);
  }, [query, files.length]);

  // Grouped by directory, first-seen order (the server ranks name matches first, so a
  // group's position is the rank of its best hit); the flat list drives ↑/↓.
  const groups = useMemo(() => {
    const out: { dir: string; files: SessionFile[] }[] = [];
    const at = new Map<string, number>();
    for (const file of files) {
      const dir = dirOf(file.path);
      let index = at.get(dir);
      if (index === undefined) {
        index = out.length;
        at.set(dir, index);
        out.push({ dir, files: [] });
      }
      out[index].files.push(file);
    }
    return out;
  }, [files]);
  const ordered = useMemo(() => groups.flatMap((g) => g.files), [groups]);

  const choose = (file?: SessionFile) => {
    const target = file || ordered[active];
    if (!target) return;
    onOpen(target.abs_path || target.path);
    onClose();
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, ordered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose();
    }
  };

  const empty = !loading && !error && ordered.length === 0;
  let flat = 0;
  return (
    <div className="fixed inset-0 z-50" data-testid="open-file-dialog" onKeyDown={onKey}>
      <div className="absolute inset-0 bg-black/30 backdrop-blur-[1px]" onClick={onClose} />
      <div className="absolute left-1/2 top-[14vh] -translate-x-1/2 w-[640px] max-w-[92vw] rounded-xl2 border border-line bg-panel shadow-2xl overflow-hidden">
        <div className="px-4 pt-3.5 pb-2.5 border-b border-line flex items-center gap-2.5">
          <Icon name="search" size={16} className="text-faint shrink-0" />
          <input
            ref={inputRef}
            data-testid="open-file-input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search files in this session"
            aria-label="Search files"
            className="flex-1 bg-transparent outline-none text-[14px] text-ink placeholder:text-faint"
          />
          <kbd className="text-[11px] text-faint bg-paper border border-line rounded px-1.5 py-0.5 font-sans">
            Esc
          </kbd>
        </div>
        <div className="max-h-[52vh] overflow-y-auto hairline-scroll py-2">
          {error ? (
            <div className="px-4 py-8 text-center text-[13px] text-faint" data-testid="open-file-error">
              {error}
            </div>
          ) : loading && ordered.length === 0 ? (
            <div className="px-4 py-8 text-center text-[13px] text-faint">Searching…</div>
          ) : empty ? (
            <div className="px-4 py-8 text-center text-[13px] text-faint" data-testid="open-file-empty">
              {query ? `No files match “${query}”.` : "This session has no files yet."}
            </div>
          ) : (
            groups.map((group) => (
              <div className="px-2" key={group.dir || "."}>
                <div className="px-2 py-1 text-[11px] uppercase tracking-[0.05em] text-faint font-semibold truncate">
                  {group.dir || "."}
                </div>
                {group.files.map((file) => {
                  const index = flat++;
                  return (
                    <button
                      key={file.path}
                      data-testid="open-file-row"
                      className={
                        "w-full flex items-center gap-2.5 px-2 py-1.5 rounded-lg text-left " +
                        (index === active ? "bg-accentSoft" : "hover:bg-paper")
                      }
                      title={file.path}
                      onMouseEnter={() => setActive(index)}
                      onClick={() => choose(file)}
                    >
                      <FileIcon path={file.path} size={14} className="shrink-0" />
                      <span className="min-w-0 flex-1 truncate text-[13px] text-ink">
                        {file.name}
                      </span>
                      <span className="text-[11px] text-faint truncate max-w-[40%] shrink-0">
                        {file.path}
                      </span>
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
        {truncated && !empty && (
          <div className="px-4 py-1.5 border-t border-line text-[11px] text-faint">
            Showing the first {ordered.length} matches — keep typing to narrow.
          </div>
        )}
      </div>
    </div>
  );
}
