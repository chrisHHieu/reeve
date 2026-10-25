// The "This session" submenu of the composer's "+": which named project memory this
// conversation uses, naming the one it has, or opening the editor. Project memory only; the global
// memory is never listed here.
//
// Choosing a row is the switch, as with radio buttons. The memory this folder gives by itself comes
// first: for a git repository its name, with a branch mark; for a folder its path with `~` for home,
// cut to the last three parts. Named memories follow, most recently used first, five of them; from
// six on a filter appears and the list becomes its results, in the same place.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { getProjectMenu, nameCurrentProject, setProjectBinding } from "../api";
import type { ProjectMenu } from "../api";
import { Icon } from "./Icon";

const SHOWN_UNFILTERED = 5;
const FILTER_FROM = 6;
const ACTION = "w-full flex items-center gap-2.5 px-3 py-1.5 text-[12.5px] text-left text-muted hover:bg-paper";

/** "~/fleet/ro4d/demo-universe/notes" becomes "…/ro4d/demo-universe/notes"; three parts or fewer stay whole. */
export function trimPath(label: string): string {
  const parts = label.split("/").filter(Boolean);
  return parts.length > 3 ? `…/${parts.slice(-3).join("/")}` : label;
}

function Choice({ chosen, children, tag, onPick }: { chosen: boolean; children: ReactNode; tag?: string; onPick: () => void }) {
  return (
    <button className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-left hover:bg-paper" onClick={onPick}>
      <span className={"inline-block w-3.5 h-3.5 rounded-full border shrink-0 " + (chosen ? "border-accent border-[4.5px]" : "border-faint")} />
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {tag && <span className="text-[10.5px] text-faint shrink-0">{tag}</span>}
    </button>
  );
}

function DerivedLabel({ derived }: { derived: NonNullable<ProjectMenu["derived"]> }) {
  if (derived.kind !== "git") {
    return (
      <span className="font-mono text-[12px] text-muted" title={derived.full}>
        {trimPath(derived.label)}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon name="branch" size={12} className="text-faint shrink-0" />
      <span className="font-mono text-[12px]">{derived.label}</span>
    </span>
  );
}

export function ProjectBindMenu({
  sessionId,
  kind,
  onClose,
  onOpenMemory,
}: {
  sessionId: string;
  kind: "memory";
  onClose: () => void;
  onOpenMemory?: () => void;
}) {
  const [menu, setMenu] = useState<ProjectMenu | null>(null);
  const [filter, setFilter] = useState("");
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const nameField = useRef<HTMLInputElement>(null);

  const load = () => {
    getProjectMenu(sessionId, kind)
      .then(setMenu)
      .catch(() => setMenu(null));
  };
  useEffect(load, [sessionId, kind]);
  useEffect(() => {
    if (naming) nameField.current?.focus();
  }, [naming]);

  const use = async (chosen: string | null) => {
    const done = await setProjectBinding(sessionId, kind, chosen);
    if (done.ok) onClose();
    else setError(done.error || "could not bind");
  };

  const saveName = async () => {
    const wanted = name.trim();
    if (!wanted) return;
    const done = await nameCurrentProject(sessionId, kind, wanted);
    if (!done.ok) {
      setError(done.error || "could not name");
      return;
    }
    setNaming(false);
    setName("");
    load();
  };

  const named = menu?.named ?? [];
  const query = filter.trim().toLowerCase();
  const listed = query ? named.filter((entry) => entry.name.toLowerCase().includes(query)) : named.slice(0, SHOWN_UNFILTERED);
  const derived = menu?.derived;

  return (
    <div className="composer-pop absolute z-50 bottom-full mb-1 left-[190px] w-[248px] rounded-xl border border-line bg-panel shadow-2xl py-1.5" data-testid={`project-menu-${kind}`}>
      <div className="px-3 pt-1 pb-1.5 text-[12px] font-medium text-muted">Project memory</div>
      {named.length >= FILTER_FROM && (
        <div className="mx-2.5 mb-1.5 flex items-center gap-1.5 rounded-lg border border-line px-2 py-1">
          <Icon name="search" size={12} className="text-faint shrink-0" />
          <input className="w-full bg-transparent text-[12.5px] outline-none" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
      )}
      {derived && !query && (
        <Choice chosen={!menu?.bound} tag={derived.kind === "git" ? "this repo" : "this folder"} onPick={() => use(null)}>
          <DerivedLabel derived={derived} />
        </Choice>
      )}
      {listed.map((entry) => {
        const chosen = menu?.bound === entry.name;
        return (
          <Choice key={entry.name} chosen={chosen} tag={chosen ? "bound" : undefined} onPick={() => use(entry.name)}>
            {entry.name}
          </Choice>
        );
      })}
      {query && listed.length === 0 && <div className="px-3 py-1.5 text-[12px] text-faint">no matches</div>}
      <div className="my-1 border-t border-line" />
      {naming ? (
        <div className="mx-2.5 my-1 flex items-center gap-1.5">
          <input
            ref={nameField}
            className="w-full rounded-lg border border-line bg-transparent px-2 py-1 text-[12.5px] outline-none"
            placeholder={`Name this ${kind}…`}
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") saveName();
              if (e.key === "Escape") setNaming(false);
            }}
          />
        </div>
      ) : (
        <button className={ACTION} onClick={() => setNaming(true)}>
          <Icon name="pencil" size={13} className="shrink-0" /> Name current {kind}…
        </button>
      )}
      {onOpenMemory && (
        <button
          className={ACTION}
          onClick={() => {
            onClose();
            onOpenMemory();
          }}
        >
          <Icon name="sliders" size={13} className="shrink-0" /> View &amp; edit…
        </button>
      )}
      {error && <div className="px-3 py-1 text-[11.5px] text-red-500">{error}</div>}
    </div>
  );
}
