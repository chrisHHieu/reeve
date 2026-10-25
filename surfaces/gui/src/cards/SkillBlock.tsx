// A loaded skill's body, rendered for the person watching the run.
//
// Without this, a `load_skill` row expanded to an IN/OUT JSON pair and a result preview the
// engine caps at ~300 characters with escaped newlines — so the instructions the model
// actually received were effectively invisible. The card shows the name, description, and
// the body as markdown, plus a link back to Settings ▸ Skills where the skill is edited.
//
// The Settings link is a window event rather than a prop: `reeve:open-settings` is the
// same door the desktop tray uses (App.tsx), and it keeps the row free of navigation
// plumbing it has no other reason to carry.

import type { SkillResultCard } from "./types";
import { Markdown } from "../components/Markdown";
import { ScrollFade } from "../components/primitives/ScrollFade";

function openInSettings() {
  window.dispatchEvent(
    new CustomEvent("reeve:open-settings", { detail: { section: "skills" } }),
  );
}

export function SkillBlock({ card }: { card: SkillResultCard }) {
  const body = (card.instructions ?? "").trim();
  return (
    <div className="rv-card" data-skill="">
      <div className="rv-banner">
        <span className="rv-banner-label">{card.name}</span>
        <span className="rv-banner-spacer" />
        <button type="button" className="skill-open" onClick={openInSettings}>
          Open in Settings ↗
        </button>
      </div>
      {card.description && <div className="skill-desc">{card.description}</div>}
      {body ? (
        <ScrollFade className="skill-body rv-scroll" data-testid="skill-instructions">
          <Markdown text={body} />
        </ScrollFade>
      ) : (
        <div className="rv-empty">No instructions</div>
      )}
    </div>
  );
}
