// A button that opens a short menu under it: the design tab's Present, Export, Share and editor, and
// the Browser tab's page menu (laid out after OpenDesign's, the owner's pick 2026-10-19).
// The menu opens down and toward the panel's inside, closes on a pick, a click outside or Escape,
// and fades in on the app's motion tokens.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Icon, type IconName } from "../Icon";

export type MenuEntry =
  | { id: string; label: string; icon?: IconName; onSelect: () => void; disabled?: boolean; hint?: string }
  | "sep";

export function ActionMenu({
  label,
  icon,
  items,
  className,
  align = "right",
  showLabel = true,
  caret = false,
  busy = false,
  testid,
  title,
  head,
}: {
  label: string;
  icon?: IconName;
  items: MenuEntry[];
  /** Classes for the trigger (`am-dark`, `am-outline`, or none for an icon button). */
  className?: string;
  align?: "left" | "right";
  showLabel?: boolean;
  caret?: boolean;
  busy?: boolean;
  testid?: string;
  title?: string;
  /** Something above the items (the Browser menu's page width). */
  head?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!wrap.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <div className="am" ref={wrap}>
      <button
        type="button"
        className={"am-btn" + (className ? " " + className : "") + (open ? " on" : "")}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={showLabel ? undefined : label}
        title={title ?? label}
        data-testid={testid}
        disabled={busy}
        onClick={() => setOpen((value) => !value)}
      >
        {icon && <Icon name={busy ? "loader" : icon} size={15} running={busy} />}
        {showLabel && <span className="am-label">{label}</span>}
        {caret && <Icon name="chevronDown" size={13} className="am-caret" />}
      </button>
      {open && (
        <div className={"am-menu " + align} role="menu" aria-label={label} data-testid={testid ? `${testid}-menu` : undefined}>
          {head}
          {items.map((item, index) =>
            item === "sep" ? (
              <div key={`sep-${index}`} className="am-sep" role="separator" />
            ) : (
              <button
                key={item.id}
                type="button"
                role="menuitem"
                className="am-item"
                data-testid={testid ? `${testid}-${item.id}` : undefined}
                disabled={item.disabled}
                title={item.hint}
                onClick={() => {
                  setOpen(false);
                  item.onSelect();
                }}
              >
                {item.icon && <Icon name={item.icon} size={15} />}
                <span>{item.label}</span>
              </button>
            ),
          )}
        </div>
      )}
    </div>
  );
}
