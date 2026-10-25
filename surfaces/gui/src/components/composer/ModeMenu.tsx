import { useState } from "react";
import { Icon } from "../Icon";
import { Toggle } from "../Toggle";
import { modeLabel, modeValue, PERMISSION_OPTIONS } from "./modes";
import { ReachedProjects } from "./ReachedProjects";

// The composer's permission menu: a chip naming the session's preset, opening the three
// presets with the current one checked, then Plan, which works around any of them. The divider
// keeps a preset from reading as one more switch.

type ModeMenuProps = {
  mode: string;
  onModeChange: (mode: string) => void;
  plan?: boolean;
  onPlanChange?: (on: boolean) => void;
  sessionId?: string;
};

export function ModeMenu({ mode, onModeChange, plan, onPlanChange, sessionId }: ModeMenuProps) {
  const [open, setOpen] = useState(false);
  // `modeLabel` maps an older session's wire value (plan, discuss, interactive, auto…) onto a
  // preset, so the chip always says a preset's name.
  const label = modeLabel(mode);
  // What runs unasked should never take opening a menu to learn. Full access wears the warning
  // tint, since in it the agent changes things without asking; Read only a shield; the middle
  // preset stays quiet.
  const preset = modeValue(mode);
  const full = preset === "danger-full-access";
  const readOnly = preset === "read-only";
  const title = `Mode: ${label}${plan ? " · Plan on" : ""}` + (full ? " · edits and commands run without asking" : "") + (readOnly ? " · edits and commands are refused" : "");
  return (
    <div className="relative">
      {/* No border, and the chosen preset's name rather than "Mode" (owner, 2026-07-11); the
          accessible name stays "Mode" whatever is chosen. */}
      <button
        className={"inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[12px] shrink-0 whitespace-nowrap " + (full ? "text-warnInk bg-warnSoft font-medium hover:brightness-95" : "text-muted hover:text-ink")}
        data-permission={full ? "full" : readOnly ? "read-only" : "ask"}
        onClick={() => setOpen(!open)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Mode"
        title={title}
      >
        {full && <Icon name="warning" size={12} className="shrink-0" />}
        {readOnly && <Icon name="shield" size={12} className="shrink-0 text-faint" />}
        {label}
        {plan && <span className="text-accent font-medium">· Plan</span>}
        <Icon name="chevronDown" size={11} className="text-faint" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div className="composer-pop absolute z-40 bottom-full mb-1 left-0 w-[260px] rounded-xl border border-line bg-panel shadow-2xl p-1.5" role="menu" data-testid="mode-menu">
            {PERMISSION_OPTIONS.map((option) => {
              const chosen = option.value === mode;
              return (
                <button
                  key={option.value}
                  className="w-full flex flex-col items-start px-2.5 py-1.5 rounded-lg text-left hover:bg-paper"
                  onClick={() => {
                    onModeChange(option.value);
                    setOpen(false);
                  }}
                >
                  <span className={"flex items-center text-[13px] " + (chosen ? "font-medium text-accent" : "text-ink")}>
                    {option.caution && <Icon name="warning" size={13} className="mr-1.5 shrink-0 text-warnInk" />}
                    {option.label}
                    {chosen && <span className="ml-1.5">✓</span>}
                  </span>
                  <span className="text-[11px] text-faint leading-snug">{option.description}</span>
                </button>
              );
            })}
            {onPlanChange && (
              <>
                <div className="my-1 border-t border-line" />
                <div className="flex items-center gap-2 px-2.5 py-1.5" data-testid="plan-toggle-row">
                  <span className="flex-1 min-w-0">
                    <span className="block text-[13px] text-ink">Plan</span>
                    <span className="block text-[11px] text-faint leading-snug">Explore read-only and propose a plan — nothing changes until you approve it</span>
                  </span>
                  <Toggle checked={!!plan} onChange={onPlanChange} title="Plan" />
                </div>
              </>
            )}
            {sessionId && <ReachedProjects sessionId={sessionId} />}
          </div>
        </>
      )}
    </div>
  );
}
