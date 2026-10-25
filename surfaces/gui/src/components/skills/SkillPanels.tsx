import type { SkillInstallPreview, SkillUploadPreview } from "../../api";
import { BADGE, BADGE_WARN, BTN_ACCENT, BTN_BORDERED, CARD, FIELD, FIELD_LABEL } from "./styles";

// The panels Settings ▸ Skills opens over its list, one at a time: the review of a file someone
// shared, the form that fetches skills from a repository and the review of what it fetched, and the
// editor. Nothing is added until the person confirms.

const CATALOG_CAP = 500;

const sizeOf = (bytes: number): string => (bytes < 1024 ? `${bytes} B` : `${Math.round(bytes / 1024)} KB`);

function Actions({ confirm, onConfirm, onCancel, disabled }: { confirm: string; onConfirm: () => void; onCancel: () => void; disabled?: boolean }) {
  return (
    <div className="flex gap-2 mt-3">
      <button className={BTN_ACCENT} disabled={disabled} onClick={onConfirm}>
        {confirm}
      </button>
      <button className={BTN_BORDERED} onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}

/** A shared .zip or SKILL.md, staged: its instructions are read before it is added. */
export function UploadReview({ upload, onConfirm, onCancel }: { upload: SkillUploadPreview; onConfirm: () => void; onCancel: () => void }) {
  return (
    <div className={`${CARD} p-4 mb-4`}>
      <div className="text-[13px] font-medium mb-1">Review before installing</div>
      <p className="text-[13px] text-muted mb-3">Read the instructions: once it is installed, the agent follows them.</p>
      <div className="text-[13px] mb-1">
        <span className="font-medium">{upload.name}</span>
        <span className="text-muted"> — {upload.description || "no description"}</span>
      </div>
      <pre className="text-[12px] bg-paper border border-line rounded-lg p-3 whitespace-pre-wrap max-h-64 overflow-y-auto mb-2">{upload.instructions}</pre>
      {upload.files?.length ? <div className="text-[12px] text-muted mb-2">Bundled files: {upload.files.join(", ")}</div> : null}
      <Actions confirm="Install skill" onConfirm={onConfirm} onCancel={onCancel} />
    </div>
  );
}

export type InstallDraft = { source: string; names: string };

/** Where to fetch skills from: a repository, and optionally which of its skills. */
export function InstallForm({ draft, onChange, onRun, onCancel }: { draft: InstallDraft; onChange: (next: InstallDraft) => void; onRun: () => void; onCancel: () => void }) {
  return (
    <div className={`${CARD} p-4 mb-4`}>
      <div className="text-[13px] font-medium mb-1">Install from repo</div>
      <p className="text-[13px] text-muted mb-3">
        Fetches skills with <code>npx skills</code> into a staging folder. Nothing is installed until you review and confirm.
      </p>
      <label className={FIELD_LABEL} htmlFor="skill-install-source">
        Source
      </label>
      <input id="skill-install-source" className={`${FIELD} mt-1`} value={draft.source} placeholder="owner/repo" onChange={(e) => onChange({ ...draft, source: e.target.value })} />
      <div className="text-[12px] text-muted mt-1 mb-3">owner/repo, or a git/https URL — uses npx skills</div>
      <label className={FIELD_LABEL} htmlFor="skill-install-names">
        Skills (optional)
      </label>
      <input id="skill-install-names" className={`${FIELD} mt-1`} value={draft.names} placeholder="all skills in the repo" onChange={(e) => onChange({ ...draft, names: e.target.value })} />
      <div className="text-[12px] text-muted mt-1 mb-3">Names apart by spaces or commas; empty takes every skill in the repo.</div>
      <div className="flex gap-2">
        <button className={BTN_ACCENT} disabled={!draft.source.trim()} onClick={onRun}>
          Run
        </button>
        <button className={BTN_BORDERED} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/** What the fetch found, before anything is installed: each skill, what it replaces or why it
 *  cannot go in, how long its description is against the menu's cap, its files and size. */
export function InstallReview({ preview, onConfirm, onCancel }: { preview: SkillInstallPreview; onConfirm: () => void; onCancel: () => void }) {
  const skills = preview.skills ?? [];
  return (
    <div className={`${CARD} p-4 mb-4`}>
      <div className="text-[13px] font-medium mb-1">Review before installing</div>
      <p className="text-[13px] text-muted mb-3">
        From <b>{preview.source}</b> — nothing has been installed yet.
      </p>
      <div className="divide-y divide-line border border-line rounded-lg mb-2">
        {skills.map((skill) => (
          <div key={skill.name} className="px-3 py-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[13px] font-medium">{skill.name}</span>
              {skill.replaces && <span className={BADGE_WARN}>replaces your copy</span>}
              {skill.blocked && <span className={BADGE_WARN}>can't install</span>}
              <span
                className={BADGE}
                title={skill.description_capped ? `Longer than the ${CATALOG_CAP}-character catalog cap — the agent's menu trims it` : `Description length (catalog cap: ${CATALOG_CAP} characters)`}
              >
                {skill.description_length}/{CATALOG_CAP}
              </span>
            </div>
            <div className="text-[12px] text-muted leading-relaxed">{skill.description || "no description"}</div>
            <div className="text-[11px] text-faint">
              {skill.files.length} bundled file{skill.files.length === 1 ? "" : "s"}
              {skill.files.length ? ` (${skill.files.join(", ")})` : ""} · {sizeOf(skill.size)}
            </div>
            {skill.blocked && <div className="text-[12px] text-warnInk mt-1">{skill.blocked}</div>}
            {skill.description_capped && (
              <div className="text-[12px] text-warnInk mt-1">The description is longer than the {CATALOG_CAP}-character catalog cap — the agent's menu will trim it.</div>
            )}
          </div>
        ))}
      </div>
      {preview.warnings?.length ? <div className="text-[12px] text-muted mb-2">{preview.warnings.join(" · ")}</div> : null}
      <Actions confirm={skills.some((s) => s.replaces) ? "Replace skills" : "Install skills"} disabled={preview.installable === false} onConfirm={onConfirm} onCancel={onCancel} />
    </div>
  );
}

export type Editor = { mode: "new" | "edit"; name: string; description: string; instructions: string };

export const emptyEditor = (): Editor => ({ mode: "new", name: "", description: "", instructions: "" });

/** A skill written by hand: its name (fixed once it exists), its one line, its instructions. */
export function SkillEditor({ editor, onChange, onSave, onCancel }: { editor: Editor; onChange: (next: Editor) => void; onSave: () => void; onCancel: () => void }) {
  return (
    <div className={`${CARD} p-4 mb-4`}>
      <div className="text-[13px] font-medium mb-3">{editor.mode === "new" ? "New skill" : `Edit ${editor.name}`}</div>
      <label className={FIELD_LABEL} htmlFor="skill-name">
        Name
      </label>
      <input id="skill-name" className={`${FIELD} mt-1 mb-3`} value={editor.name} disabled={editor.mode === "edit"} placeholder="weekly-report" onChange={(e) => onChange({ ...editor, name: e.target.value })} />
      <label className={FIELD_LABEL} htmlFor="skill-desc">
        Description
      </label>
      <input
        id="skill-desc"
        className={`${FIELD} mt-1 mb-3`}
        value={editor.description}
        placeholder="One line the agent reads to decide when this applies"
        onChange={(e) => onChange({ ...editor, description: e.target.value })}
      />
      <label className={FIELD_LABEL} htmlFor="skill-instructions">
        Instructions
      </label>
      <textarea
        id="skill-instructions"
        className={`${FIELD} mt-1 mb-3 min-h-[140px] font-mono`}
        value={editor.instructions}
        placeholder={"1. Gather last week's updates\n2. Write the report, under 300 words"}
        onChange={(e) => onChange({ ...editor, instructions: e.target.value })}
      />
      <Actions confirm="Save skill" disabled={!editor.name.trim() || !editor.instructions.trim()} onConfirm={onSave} onCancel={onCancel} />
    </div>
  );
}
