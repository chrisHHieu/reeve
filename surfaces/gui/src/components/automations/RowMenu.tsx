import { useEffect, useRef, useState } from "react";
import { Icon, type IconName } from "../Icon";

// The "more" menu of an automation: the actions that are not worth a button of their own.
// It closes on a pick, on Escape (focus goes back to the button) and on a press outside it.

export interface MenuItem {
  id: string;
  label: string;
  icon: IconName;
  danger?: boolean;
  onPick: () => void;
}

export function RowMenu({ label, items }: { label: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLSpanElement | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      button.current?.focus();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span className="auto-menu-wrap" ref={wrap}>
      <button
        ref={button}
        type="button"
        className="auto-icon"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid="automation-menu"
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name="moreHorizontal" size={15} className="rotate-90" />
      </button>
      {open && (
        <div className="auto-menu" role="menu" aria-label={label}>
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              role="menuitem"
              className={"auto-menu-item" + (item.danger ? " danger" : "")}
              data-testid={`automation-menu-${item.id}`}
              onClick={() => {
                setOpen(false);
                item.onPick();
              }}
            >
              <Icon name={item.icon} size={14} />
              {item.label}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}
