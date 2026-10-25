import { useEffect, useState } from "react";
import { Icon } from "./Icon";

// A select drawn like the rest of the forms. The native <select> cannot hold a status dot or a
// second line, and it looks like a bare OS control among the app's own. A row has its label, may
// have a quiet line under it ("Last used 2h ago"), and may end in a green dot (a key is set).
export interface SelectMenuOption {
  value: string;
  label: string;
  sub?: string; // the quiet line under the label
  dot?: boolean; // the green dot at the end of the row
  group?: string; // a heading; options in a row with the same group share one
}

const ROW = "w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-left";
const GROUP = "px-2.5 pb-1 text-[11px] uppercase tracking-[0.06em] text-faint font-semibold";

function Dot({ on, title }: { on?: boolean; title?: string }) {
  return <span className={"w-1.5 h-1.5 rounded-full shrink-0 " + (on ? "bg-ok" : "bg-transparent")} title={title} />;
}

export function SelectMenu({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: string;
  options: SelectMenuOption[];
  onChange: (value: string) => void;
  ariaLabel: string;
}) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open]);
  const chosen = options.find((option) => option.value === value);
  const pick = (next: string) => {
    onChange(next);
    setOpen(false);
  };

  return (
    <div className="relative">
      <button
        type="button"
        className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink hover:border-lineStrong"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={() => setOpen((was) => !was)}
      >
        <span className="flex-1 min-w-0 text-left truncate">{chosen?.label || value}</span>
        {chosen?.dot && <span className="w-1.5 h-1.5 rounded-full bg-ok shrink-0" />}
        <Icon name="chevronDown" size={13} className="text-faint shrink-0" />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div role="listbox" aria-label={ariaLabel} className="absolute z-40 left-0 right-0 mt-1 max-h-72 overflow-y-auto rounded-xl border border-line bg-panel shadow-xl p-1">
            {options.map((option, index) => {
              const selected = option.value === value;
              const startsGroup = !!option.group && option.group !== options[index - 1]?.group;
              return (
                <div key={option.value}>
                  {startsGroup && <div className={GROUP + (index === 0 ? " pt-1" : " pt-2.5 mt-1.5 border-t border-line")}>{option.group}</div>}
                  <button type="button" role="option" aria-selected={selected} className={ROW + (selected ? " bg-paper" : " hover:bg-paper")} onClick={() => pick(option.value)}>
                    <span className="min-w-0 flex-1">
                      <span className={"block text-[13px] truncate text-ink" + (selected ? " font-semibold" : "")}>{option.label}</span>
                      {option.sub && <span className="block text-[12px] text-faint truncate">{option.sub}</span>}
                    </span>
                    {selected && <span className="text-accent text-[12px] shrink-0">✓</span>}
                    <Dot on={option.dot} title={option.dot ? "Key set" : undefined} />
                  </button>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
