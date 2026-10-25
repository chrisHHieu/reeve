import { useState } from "react";
import { Icon, type IconName } from "./Icon";

// A pill that opens a menu of choices, with the chosen one ticked: the composer's pickers and the
// design tab's device switch.
export interface Option {
  value: string;
  label: string;
  description?: string;
  // A glyph before the label (the design tab's Desktop / Tablet / Mobile).
  icon?: IconName;
}

interface Props {
  prefix?: string;
  value: string;
  options: Option[];
  onChange: (value: string) => void;
  align?: "left" | "right";
  // Classes added to the pill (e.g. "chip", the bordered look in the composer's head).
  className?: string;
  // A glyph before the pill's label (the composer's effort picker has the brain).
  icon?: IconName;
}

function OptionLabel({ option, chosen }: { option: Option; chosen: boolean }) {
  return (
    <div className="dd-label">
      {option.icon ? (
        <span className="dd-label-main">
          <Icon name={option.icon} size={14} className="dd-ico" />
          {option.label}
        </span>
      ) : (
        option.label
      )}
      {chosen && <Icon name="check" size={13} className="chk" />}
    </div>
  );
}

export function Dropdown({ prefix, value, options, onChange, align = "left", className, icon }: Props) {
  const [open, setOpen] = useState(false);
  const shown = options.find((option) => option.value === value)?.label || value;
  const label = prefix ? `${prefix}: ${shown}` : shown;
  const choose = (next: string) => {
    onChange(next);
    setOpen(false);
  };
  return (
    <div className="dd">
      <button className={className ? `pill ${className}` : "pill"} onClick={() => setOpen((was) => !was)} title={label} aria-haspopup="menu" aria-expanded={open}>
        {icon && <Icon name={icon} size={12} className="dd-ico" />}
        <span className="pill-label">{label}</span>
        <Icon name="chevronDown" size={13} className="caret" />
      </button>
      {open && (
        <>
          <div className="dd-backdrop" onClick={() => setOpen(false)} />
          <div className={`dd-menu ${align}`} role="menu">
            {options.map((option) => {
              const chosen = option.value === value;
              return (
                <button key={option.value} type="button" role="menuitemradio" aria-checked={chosen} className={chosen ? "dd-item sel" : "dd-item"} onClick={() => choose(option.value)}>
                  <OptionLabel option={option} chosen={chosen} />
                  {option.description && <div className="dd-desc">{option.description}</div>}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
