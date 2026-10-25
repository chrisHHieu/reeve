import type { Option } from "../Dropdown";

// The permission presets: what the agent may do without asking. Every mode picker in the app (the
// automation editor's too) offers this list in these words. Plan is not one of them: it is a switch
// beside them (the mode menu's Plan row, `/plan`), since exploring and proposing has nothing to do
// with who approves what. `caution` puts a warning mark before the label.

export type ModeOption = Option & { caution?: boolean; gated?: boolean };

export const PERMISSION_OPTIONS: ModeOption[] = [
  // One plain line each, saying who is asked (amended 2026-10-23). Lists of what the
  // engine asks about read as its internals; the approval card says the rest when it matters.
  { value: "read-only", label: "Read only", description: "Looks around, changes nothing" },
  { value: "workspace-write", label: "Workspace write", description: "Asks you before it changes anything" },
  // Full access asks nobody. "Files that run later still ask" outlived that decision
  // until 2026-10-19; this line promises no ask it cannot keep.
  { value: "danger-full-access", label: "Full access", description: "Doesn't ask: changes happen right away", caution: true },
];

// Values the picker no longer offers and the server still takes (old sessions, automations, the
// TUI, the CLI, config files), each read as the preset it means.
const OLDER_VALUES: Record<string, string> = {
  plan: "read-only",
  discuss: "read-only",
  interactive: "workspace-write",
  custom: "workspace-write",
  "auto-approve": "workspace-write",
  auto: "danger-full-access",
  "bypass-approvals": "danger-full-access",
};

/** The preset a wire value means; a value nobody knows passes through as it is. */
export function modeValue(value: string): string {
  return PERMISSION_OPTIONS.some((o) => o.value === value) ? value : (OLDER_VALUES[value] ?? value);
}

/** A mode's name as the picker says it ("danger-full-access" → "Full access"), for the
 *  transcript's mode lines too, so they use the words the person chose from. */
export function modeLabel(value: string): string {
  const preset = modeValue(value);
  return PERMISSION_OPTIONS.find((o) => o.value === preset)?.label ?? value;
}
