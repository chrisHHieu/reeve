import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Icon, type IconName } from "../Icon";
import { Disclosure, Tooltip } from "../../ui";

/** Where a tab's one primary action goes: beside the page title (SettingsView renders the slot). */
export const SETTINGS_PAGE_ACTION_ID = "settings-page-action";

/** A tab's primary action, placed beside the page title instead of in the tab's body, so every
 * page puts it in the same place. Rendered in place when there is no slot (a tab shown alone). */
export function SettingsPageAction({ children }: { children: ReactNode }) {
  // undefined until looked up (the slot is committed with this tab, so it is found after mount).
  const [slot, setSlot] = useState<HTMLElement | null | undefined>(undefined);
  useEffect(() => {
    setSlot(document.getElementById(SETTINGS_PAGE_ACTION_ID));
  }, []);
  if (slot === undefined) return null;
  return slot ? createPortal(children, slot) : <div className="settings-page-action">{children}</div>;
}

/** FLIP for a list whose rows come and go (a filter, a search): each `[data-flip]` row that
 * moved glides from where it was to where it is, and a new one fades in. One measurement per
 * render, transform and opacity only, nothing when the reader asks for reduced motion. */
export function useFlip(container: React.RefObject<HTMLElement | null>, deps: unknown[]) {
  const last = useRef<Map<string, number>>(new Map());
  useLayoutEffect(() => {
    const root = container.current;
    if (!root) return;
    const reduce = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const now = new Map<string, number>();
    const rows = [...root.querySelectorAll<HTMLElement>("[data-flip]")];
    for (const row of rows) now.set(row.dataset.flip || "", row.getBoundingClientRect().top);
    const before = last.current;
    last.current = now;
    if (reduce || before.size === 0) return;
    for (const row of rows) {
      const key = row.dataset.flip || "";
      const was = before.get(key);
      if (was === undefined) {
        row.classList.remove("settings-arrive");
        void row.offsetWidth;
        row.classList.add("settings-arrive");
        continue;
      }
      const dy = was - (now.get(key) ?? was);
      if (Math.abs(dy) < 0.5) continue;
      row.style.transition = "none";
      row.style.transform = `translateY(${dy}px)`;
      requestAnimationFrame(() => {
        row.style.transition = "transform var(--dur-3) var(--ease-out)";
        row.style.transform = "";
        row.addEventListener("transitionend", () => (row.style.transition = ""), { once: true });
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

const SAVED_EVENT = "reeve:settings-saved";

/** Say a change was kept: the quiet "Saved" pill (SettingsSavedToast) shows for a moment. */
export function announceSaved(text = "Saved") {
  window.dispatchEvent(new CustomEvent(SAVED_EVENT, { detail: text }));
}

export function SettingsSavedToast() {
  const [text, setText] = useState("Saved");
  const [show, setShow] = useState(false);
  useEffect(() => {
    let timer: number | undefined;
    const on = (event: Event) => {
      setText(String((event as CustomEvent<string>).detail || "Saved"));
      setShow(true);
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setShow(false), 1400);
    };
    window.addEventListener(SAVED_EVENT, on);
    return () => {
      window.removeEventListener(SAVED_EVENT, on);
      window.clearTimeout(timer);
    };
  }, []);
  return (
    <div className={"settings-saved" + (show ? " show" : "")} aria-live="polite" data-testid="settings-saved">
      <Icon name="check" size={13} />
      <span>{show ? text : ""}</span>
    </div>
  );
}

/** A state as a dot and a word, the same wherever it shows (Models, Connectors, Subagents). */
export function StatusDot({ tone, children }: { tone?: "ok" | "warn" | "danger"; children: ReactNode }) {
  return <span className={"settings-status" + (tone ? " " + tone : "")}>{children}</span>;
}

// One text input for Settings: on the card's own surface with a hairline, like the provider key
// form (a filled grey field read as disabled beside it).
export const SETTINGS_INPUT =
  "w-full min-w-[140px] h-[30px] px-3 rounded-lg border border-line bg-panel text-[13px] text-ink outline-none transition-colors placeholder:text-faint hover:border-lineStrong focus:border-accent";

export function HelpIcon({ label }: { label: string }) {
  return (
    <Tooltip label={label} side="bottom">
      <button type="button" className="settings-help" aria-label={label}>
        ?
      </button>
    </Tooltip>
  );
}

export function SettingsRow({
  title,
  description,
  detail,
  children,
  wide,
  flat,
  testId,
}: {
  title: string;
  description?: string;
  detail?: ReactNode;
  children?: ReactNode;
  wide?: boolean;
  flat?: boolean;
  testId?: string;
}) {
  // A row's one explanation is said under its name, not hidden behind a "?" (settings redesign,
  // 2026-10-20). The "?" stays only for the longer word a row keeps besides its short line.
  const said = detail ?? description;
  const help = detail != null && description ? description : null;
  return (
    <div
      className={"settings-row" + (wide ? " settings-row-wide" : "") + (flat ? " settings-row-flat" : "")}
      data-testid={testId}
    >
      <div className="settings-row-copy">
        <div className="settings-row-title" data-settings-title={title}>
          <span className="settings-row-title-text">{title}</span>
          {help ? <HelpIcon label={help} /> : null}
        </div>
        {said ? <div className="settings-row-detail">{said}</div> : null}
      </div>
      <div className="settings-row-control">{children}</div>
    </div>
  );
}

export function SettingsCard({
  title,
  description,
  children,
  tile,
  testId,
  action,
}: {
  title?: string;
  description?: string;
  children: ReactNode;
  tile?: boolean;
  testId?: string;
  action?: ReactNode;
}) {
  return (
    <section className="settings-card-block" data-testid={testId}>
      {title ? (
        <div className="settings-card-heading-row">
          <div className="settings-card-heading-help">
            <h2 className="settings-card-heading" data-settings-title={title}>
              {title}
            </h2>
            {description ? <HelpIcon label={description} /> : null}
          </div>
          {action ? <div className="settings-card-heading-action">{action}</div> : null}
        </div>
      ) : null}
      <div className={"settings-panel" + (tile ? "" : " settings-rows")}>{children}</div>
    </section>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
  testId,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
  testId?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      data-testid={testId}
      className={"settings-toggle" + (checked ? " on" : "")}
      onClick={() => onChange(!checked)}
    >
      <span className="settings-toggle-thumb" />
    </button>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  radio,
  testId,
  optionTestId,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (next: T) => void;
  label: string;
  radio?: boolean;
  testId?: string;
  optionTestId?: (value: T) => string;
}) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const group = useRef<HTMLDivElement>(null);
  const thumb = useRef<HTMLSpanElement>(null);
  const [placed, setPlaced] = useState(false);
  const activeIndex = options.findIndex((option) => option.value === value);

  // The thumb follows the picked option. Measured once per change (and on resize), then moved by
  // transform; the first placement has no transition, so a page opens with it already in place.
  useLayoutEffect(() => {
    const place = () => {
      const picked = refs.current[activeIndex];
      const el = thumb.current;
      if (!picked || !el || !picked.offsetWidth) return false;
      el.style.width = `${picked.offsetWidth}px`;
      el.style.height = `${picked.offsetHeight}px`;
      el.style.transform = `translate(${picked.offsetLeft}px, ${picked.offsetTop}px)`;
      return true;
    };
    setPlaced(place());
    const node = group.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setPlaced(place()));
    observer.observe(node);
    return () => observer.disconnect();
  }, [activeIndex, options.length]);

  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!placed || ready) return;
    const frame = requestAnimationFrame(() => setReady(true));
    return () => cancelAnimationFrame(frame);
  }, [placed, ready]);

  const move = (event: React.KeyboardEvent, index: number) => {
    const delta =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? -1
          : 0;
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      const next = event.key === "Home" ? 0 : options.length - 1;
      onChange(options[next].value);
      refs.current[next]?.focus();
      return;
    }
    if (!delta) return;
    event.preventDefault();
    const next = (index + delta + options.length) % options.length;
    onChange(options[next].value);
    refs.current[next]?.focus();
  };

  return (
    <div
      ref={group}
      className={"settings-segment" + (placed ? " has-thumb" : "") + (ready ? " ready" : "")}
      role={radio ? "radiogroup" : "group"}
      aria-label={label}
      data-testid={testId}
    >
      <span ref={thumb} className="settings-segment-thumb" aria-hidden="true" hidden={!placed} />
      {options.map((option, index) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            ref={(el) => {
              refs.current[index] = el;
            }}
            type="button"
            role={radio ? "radio" : undefined}
            aria-label={option.label}
            aria-checked={radio ? active : undefined}
            aria-pressed={radio ? undefined : active}
            tabIndex={radio ? (active ? 0 : -1) : undefined}
            data-testid={optionTestId ? optionTestId(option.value) : undefined}
            className={"settings-segment-item" + (active ? " active" : "")}
            title={option.label}
            onClick={() => onChange(option.value)}
            onKeyDown={radio ? (event) => move(event, index) : undefined}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/** A choice that matters, as cards: each answer with the line that says what it lets happen
 *  (General ▸ Permission, settings redesign 2026-10-20). A radio group, moved with the arrows
 *  like `Segmented`. `caution` inks the answer that removes a guard. */
export function ChoiceCards<T extends string>({
  value,
  options,
  onChange,
  label,
  optionTestId,
}: {
  value: T;
  options: { value: T; label: string; detail: string; caution?: boolean }[];
  onChange: (next: T) => void;
  label: string;
  optionTestId?: (value: T) => string;
}) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const move = (event: React.KeyboardEvent, index: number) => {
    const delta =
      event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!delta) return;
    event.preventDefault();
    const next = (index + delta + options.length) % options.length;
    onChange(options[next].value);
    refs.current[next]?.focus();
  };
  return (
    <div className="settings-choices" role="radiogroup" aria-label={label}>
      {options.map((option, index) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            ref={(el) => {
              refs.current[index] = el;
            }}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            data-testid={optionTestId ? optionTestId(option.value) : undefined}
            data-caution={option.caution || undefined}
            className={"settings-choice" + (active ? " active" : "")}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => move(event, index)}
          >
            <span className="settings-choice-title">{option.label}</span>
            <span className="settings-choice-detail">{option.detail}</span>
          </button>
        );
      })}
    </div>
  );
}

/** A small number with − and + beside it (settings redesign, 2026-10-20): a step is one press,
 *  and the field still takes a typed value. The value stays within `min`–`max`. */
export function Stepper({
  value,
  onChange,
  min,
  max,
  label,
  unit,
  testId,
}: {
  value: number;
  onChange: (next: number) => void;
  min: number;
  max: number;
  label: string;
  unit?: string;
  testId?: string;
}) {
  const clamp = (n: number) => Math.max(min, Math.min(max, n));
  return (
    <span className="settings-stepper">
      <button type="button" aria-label={`Less ${label.toLowerCase()}`} disabled={value <= min} onClick={() => onChange(clamp(value - 1))}>
        <Icon name="minus" size={13} />
      </button>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        aria-label={label}
        data-testid={testId}
        onChange={(event) => {
          const n = Number(event.target.value);
          if (Number.isFinite(n) && event.target.value !== "") onChange(clamp(Math.round(n)));
        }}
      />
      <button type="button" aria-label={`More ${label.toLowerCase()}`} disabled={value >= max} onClick={() => onChange(clamp(value + 1))}>
        <Icon name="plus" size={13} />
      </button>
      {unit ? <span className="settings-stepper-unit">{unit}</span> : null}
    </span>
  );
}

/** One choice in a Select. `style` sets how its label is drawn (a font picker draws each face in
 *  itself), and `hint` is a quiet word after it. */
export type SelectOption<T extends string> = { value: T; label: string; style?: CSSProperties; hint?: string };

export function Select<T extends string>({
  value,
  options,
  onChange,
  label,
  searchable,
  disabled,
  testId,
  optionTestId,
}: {
  value: T;
  options: SelectOption<T>[];
  onChange: (next: T) => void;
  label: string;
  searchable?: boolean;
  disabled?: boolean;
  testId?: string;
  optionTestId?: (value: T) => string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const current = options.find((option) => option.value === value);
  const needle = query.trim().toLowerCase();
  const visible = needle
    ? options.filter((option) => option.label.toLowerCase().includes(needle))
    : options;

  const close = () => {
    setOpen(false);
    setQuery("");
  };

  return (
    <div
      className="settings-select"
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.stopPropagation();
          close();
        }
      }}
    >
      <button
        type="button"
        className="settings-select-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
        disabled={disabled}
        data-testid={testId}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <span className="settings-select-label" style={current?.style}>
          {current?.label ?? value}
        </span>
        {current?.hint ? <span className="settings-select-hint">{current.hint}</span> : null}
        <Icon name="chevronDown" size={13} />
      </button>
      {open && (
        <>
          <div className="settings-select-backdrop" onClick={close} />
          <div className="settings-select-menu" role="listbox" aria-label={label}>
            {(searchable || options.length > 8) && (
              <input
                className="settings-select-search"
                autoFocus
                value={query}
                spellCheck={false}
                placeholder="Search…"
                aria-label={`Filter ${label}`}
                onChange={(event) => setQuery(event.target.value)}
              />
            )}
            <div className="settings-select-list">
              {visible.length === 0 ? (
                <div className="settings-select-empty">No matches</div>
              ) : (
                visible.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    role="option"
                    aria-selected={option.value === value}
                    data-testid={optionTestId ? optionTestId(option.value) : undefined}
                    className={
                      "settings-select-option" + (option.value === value ? " is-current" : "")
                    }
                    onClick={() => {
                      onChange(option.value);
                      close();
                    }}
                  >
                    <span className="settings-select-option-label" style={option.style}>
                      {option.label}
                    </span>
                    {option.hint ? <span className="settings-select-hint">{option.hint}</span> : null}
                    {option.value === value && (
                      <Icon name="check" size={13} className="settings-select-check" />
                    )}
                  </button>
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export function RowIconButton({
  icon,
  label,
  onClick,
  disabled,
  testId,
  danger,
}: {
  icon: IconName;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  testId?: string;
  danger?: boolean;
}) {
  return (
    <Tooltip label={label}>
      <button
        type="button"
        className={"settings-icon-btn" + (danger ? " danger" : "")}
        aria-label={label}
        disabled={disabled}
        data-testid={testId}
        onClick={onClick}
      >
        <Icon name={icon} size={14} />
      </button>
    </Tooltip>
  );
}

/** The header of a region that shows and hides (a connector's tools, "show all", a plugin's
 * skipped files): a chevron that turns, `aria-expanded`, and an optional count on the right. Every
 * show/hide in Settings uses this with `FoldBody`, so each one opens and closes the same way: the
 * region grows and fades in, never pops in (owner, 2026-10-10: "click it and nothing animates"). */
export function FoldHead({
  open,
  onToggle,
  children,
  aside,
  testId,
}: {
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
  aside?: ReactNode;
  testId?: string;
}) {
  return (
    <button type="button" className="settings-fold-head" aria-expanded={open} data-testid={testId} onClick={onToggle}>
      <Icon name="chevronRight" size={14} />
      <span className="settings-fold-label">{children}</span>
      {aside != null ? <span className="settings-fold-aside">{aside}</span> : null}
    </button>
  );
}

/** The region a `FoldHead` opens: grows from nothing and fades in, and shrinks and fades out
 * before it unmounts (`ui/Disclosure`). Still under prefers-reduced-motion. */
export function FoldBody({ open, children, testId }: { open: boolean; children: ReactNode; testId?: string }) {
  return (
    <Disclosure open={open} className="fold-fade" testId={testId}>
      {children}
    </Disclosure>
  );
}

/** The way back from a Settings sub-page (a provider, a connector) to the list it came from. */
export function SettingsBack({ label, onClick, testId }: { label: string; onClick: () => void; testId?: string }) {
  return (
    <button type="button" className="settings-back" onClick={onClick} data-testid={testId}>
      <Icon name="arrowLeft" size={13} /> {label}
    </button>
  );
}

export function TextButton({
  children,
  onClick,
  disabled,
  testId,
  tone,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  testId?: string;
  tone?: "danger" | "accent";
}) {
  return (
    <button
      type="button"
      className={"settings-text-btn" + (tone ? " " + tone : "")}
      disabled={disabled}
      data-testid={testId}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
