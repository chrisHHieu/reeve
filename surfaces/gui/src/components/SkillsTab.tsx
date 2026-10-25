import { useEffect, useRef, useState } from "react";
import {
  confirmSkillInstall,
  confirmSkillUpload,
  createSkill,
  deleteSkill,
  listSkills,
  revealSkill,
  stageSkillInstall,
  stageSkillUpload,
  updateSkill,
  type SkillInstallInfo,
  type SkillInstallPreview,
  type SkillRow,
  type SkillUploadPreview,
} from "../api";
import { Icon } from "./Icon";
import { announceSaved, Segmented, SettingsPageAction, useFlip } from "./settings/primitives";
import { SkillDetails } from "./skills/SkillDetails";
import { emptyEditor, InstallForm, InstallReview, SkillEditor, UploadReview, type Editor, type InstallDraft } from "./skills/SkillPanels";
import { SkillTags } from "./skills/SkillTags";
import { fileToB64, usageLine } from "./skills/usage";
import { Toggle } from "./Toggle";

// Settings ▸ Skills, where skills are managed. The list is the page; every way
// of adding one opens only from the one "Add skill" menu (write it, import a file, install from a
// repository, or ask Reeve). A skill made here is global: what the agent knows, it knows in every
// conversation. Asking Reeve to make one is a conversation (the menu starts it, and the agent
// proposes the skill with save_skill), so there is no drafting here and no box to describe it in:
// the composer is where it is described.

export { usageLine } from "./skills/usage";

type Notice = { name: string; text: string; tone: "ok" | "warn" };
type Show = "all" | "on" | "off" | "used";

// What a change says: the skill's name first, then what happened and what to do,
// in words people already have (now, everywhere, off, start a new one). Never how it works ("the
// model will be told…") or when the engine applies it ("from the next message"): the owner's review
// rounds, 2026-07-27. The engine quietly countermands a skill switched off after it was loaded; the
// words promise only the part that is certain.
const ADDED = "— the agent can now use it in every conversation.";
const SWITCHED_OFF = "turned off everywhere. If a conversation already used it, start a new one for a completely clean slate.";
const REMOVED = "removed. If a conversation already used it, start a new one for a completely clean slate.";

function AddMenu({ onWrite, onImport, onInstall, onAsk }: { onWrite: () => void; onImport: () => void; onInstall: () => void; onAsk?: () => void }) {
  const [open, setOpen] = useState(false);
  const item = (title: string, line: string, act: (() => void) | undefined) => (
    <button
      role="menuitem"
      className="w-full text-left px-3 py-2 rounded-lg hover:bg-paper disabled:opacity-40"
      disabled={!act}
      onClick={() => {
        setOpen(false);
        act?.();
      }}
    >
      <div className="text-[13px] font-medium">{title}</div>
      <div className="text-[12px] text-muted">{line}</div>
    </button>
  );
  return (
    <div className="relative shrink-0">
      <button className="settings-text-btn accent" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((was) => !was)}>
        <Icon name="plus" size={13} /> Add skill
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div role="menu" className="absolute right-0 top-full mt-1.5 w-80 rounded-xl2 border border-line bg-panel shadow-xl z-20 p-1.5" onKeyDown={(e) => e.key === "Escape" && setOpen(false)}>
            {item("Write it myself", "A name, a description, and the instructions", onWrite)}
            {item("Import a file", "A .zip or SKILL.md someone shared — you review before it installs", onImport)}
            {item("Install from repo…", "A GitHub repo or URL — fetched with npx skills; nothing installs until you confirm", onInstall)}
            {item("Create with Reeve", "Starts a conversation — the agent builds it and asks before adding it to your skills", onAsk)}
          </div>
        </>
      )}
    </div>
  );
}

const SHOW_EMPTY: Record<Exclude<Show, "all">, string> = { on: "Every skill is off.", off: "Every skill is on.", used: "No skill has been used yet." };

export function SkillsTab({
  onCreateSkill,
}: {
  // Asking Reeve: a new conversation opens with the description in the composer,
  // and the agent builds the skill and proposes it with save_skill.
  onCreateSkill?: (description: string) => void;
}) {
  const [rows, setRows] = useState<SkillRow[]>([]);
  const [installs, setInstalls] = useState<Record<string, SkillInstallInfo>>({});
  const [editor, setEditor] = useState<Editor | null>(null);
  const [upload, setUpload] = useState<SkillUploadPreview | null>(null);
  // From a repository: the form, then what it fetched, then the confirmation.
  const [installDraft, setInstallDraft] = useState<InstallDraft | null>(null);
  const [installPreview, setInstallPreview] = useState<SkillInstallPreview | null>(null);
  const [armedDelete, setArmedDelete] = useState<string | null>(null);
  const [error, setError] = useState("");
  // What changed, by name, so it is clear WHICH skill, and drawn so it cannot be skimmed past
  // (a tester, 2026-07-27).
  const [notice, setNotice] = useState<Notice | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  // The list's search and filter (the 2026-10-10 redesign): a long list is read by scanning it.
  const [query, setQuery] = useState("");
  const [show, setShow] = useState<Show>("all");
  // The skill whose details are open beside the list (the settings redesign, 2026-10-20): what a row
  // used to carry, Open and a bin, is there, so a row is its name, its line and its switch.
  const [opened, setOpened] = useState<string | null>(null);
  const list = useRef<HTMLDivElement>(null);

  const wanted = query.trim().toLowerCase();
  const passes = (row: SkillRow) => show === "all" || (show === "on" ? row.enabled : show === "off" ? !row.enabled : !!row.uses);
  const visible = rows.filter((row) => passes(row) && (!wanted || row.name.toLowerCase().includes(wanted) || (row.description ?? "").toLowerCase().includes(wanted)));
  useFlip(list, [visible.map((row) => row.name).join("|")]);
  const allShared = rows.length > 1 && rows.every((row) => row.compat);
  const count = (keep: (row: SkillRow) => boolean) => rows.filter(keep).length;

  const refresh = () =>
    listSkills().then(({ skills, installs: installed }) => {
      setRows(skills);
      setInstalls(installed);
    });
  useEffect(() => {
    refresh();
  }, []);

  /** Shows the server's refusal and says whether there was one. */
  const refused = (answer: { ok?: boolean; error?: string }) => {
    setNotice(null);
    setError(answer.ok === false ? answer.error || "Something went wrong." : "");
    return answer.ok === false;
  };
  const done = (name: string, text = ADDED, tone: Notice["tone"] = "ok") => {
    setNotice({ name, text, tone });
    refresh();
  };

  const save = async () => {
    if (!editor) return;
    // The description is the agent's menu entry: without one the skill could never be found, so it
    // is asked for kindly rather than saved.
    if (!editor.description.trim()) {
      setNotice(null);
      setError("Add a one-line description — it's how the agent decides when to use this skill.");
      return;
    }
    const fields = { description: editor.description.trim(), instructions: editor.instructions };
    const answer = editor.mode === "new" ? await createSkill({ name: editor.name.trim(), ...fields }) : await updateSkill(editor.name, fields);
    if (refused(answer)) return;
    setEditor(null);
    if (editor.mode === "new") done(editor.name.trim());
    else refresh();
  };

  const stageFile = async (file: File | undefined) => {
    if (!file) return;
    const staged = await stageSkillUpload(await fileToB64(file), file.name);
    if (!refused(staged)) setUpload(staged);
  };

  const confirmUpload = async () => {
    if (!upload?.token) return;
    if (refused(await confirmSkillUpload(upload.token))) return;
    setUpload(null);
    done(upload.name || "Skill");
  };

  const fetchFromRepo = async () => {
    if (!installDraft) return;
    const names = installDraft.names.split(/[\s,]+/).map((n) => n.trim()).filter(Boolean);
    const staged = await stageSkillInstall({ source: installDraft.source.trim(), skills: names });
    if (!refused(staged)) setInstallPreview(staged);
  };

  const confirmInstall = async () => {
    if (!installPreview?.token) return;
    const skills = installPreview.skills ?? [];
    if (refused(await confirmSkillInstall(installPreview.token, skills.some((s) => s.replaces)))) return;
    setInstallPreview(null);
    setInstallDraft(null);
    done(skills.map((s) => s.name).join(", ") || "Skill");
  };

  // The first press arms the button; the second removes.
  const remove = async (row: SkillRow) => {
    if (armedDelete !== row.name) return setArmedDelete(row.name);
    setArmedDelete(null);
    if (!refused(await deleteSkill(row.name))) done(row.name, REMOVED, "warn");
  };

  const switchSkill = (row: SkillRow, on: boolean) =>
    updateSkill(row.name, { enabled: on }).then((answer) => {
      if (!refused(answer)) {
        setNotice({ name: row.name, text: on ? ADDED : SWITCHED_OFF, tone: on ? "ok" : "warn" });
        announceSaved(on ? "Turned on" : "Turned off");
      }
      refresh();
    });

  const openedRow = opened ? rows.find((row) => row.name === opened) : undefined;
  const closeDetails = () => {
    setOpened(null);
    setArmedDelete(null);
  };

  return (
    <section>
      {/* One way to add, four doors behind it, next to the page's title, where every
          Settings page keeps its main action. */}
      <SettingsPageAction>
        <AddMenu
          onWrite={() => setEditor(emptyEditor())}
          onImport={() => fileInput.current?.click()}
          onInstall={() => {
            setError("");
            setUpload(null);
            setInstallPreview(null);
            setInstallDraft({ source: "", names: "" });
          }}
          onAsk={onCreateSkill ? () => onCreateSkill("") : undefined}
        />
      </SettingsPageAction>
      {rows.length > 0 && (
        <div className="settings-toolbar mb-3">
          <input
            className="settings-search-input"
            value={query}
            placeholder={`Search ${rows.length} skill${rows.length === 1 ? "" : "s"}`}
            aria-label="Search skills"
            spellCheck={false}
            data-testid="skills-search"
            onChange={(e) => setQuery(e.target.value)}
          />
          <Segmented
            label="Show"
            value={show}
            options={[
              { value: "all", label: `All ${rows.length}` },
              { value: "on", label: `On ${count((row) => row.enabled)}` },
              { value: "off", label: `Off ${count((row) => !row.enabled)}` },
              { value: "used", label: `Used ${count((row) => !!row.uses)}` },
            ]}
            onChange={setShow}
            optionTestId={(value) => `skills-show-${value}`}
          />
        </div>
      )}
      <input
        ref={fileInput}
        type="file"
        accept=".zip,.md"
        className="hidden"
        aria-label="Upload a skill archive"
        onChange={(e) => {
          stageFile(e.target.files?.[0]);
          e.target.value = "";
        }}
      />

      {error && (
        <div className="text-[13px] text-red-500 mb-3" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div role="status" className={"mb-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-[13px] " + (notice.tone === "ok" ? "bg-tealSoft/70 text-tealInk border-tealInk/20" : "bg-warnSoft/70 text-warnInk border-warnInk/20")}>
          <span className="min-w-0">
            <b>{notice.name}</b> {notice.text}
          </span>
          <button className="ml-auto shrink-0 opacity-60 hover:opacity-100" aria-label="Dismiss" onClick={() => setNotice(null)}>
            ✕
          </button>
        </div>
      )}

      {upload && <UploadReview upload={upload} onConfirm={confirmUpload} onCancel={() => setUpload(null)} />}
      {installDraft && !installPreview && <InstallForm draft={installDraft} onChange={setInstallDraft} onRun={fetchFromRepo} onCancel={() => setInstallDraft(null)} />}
      {installPreview && (
        <InstallReview
          preview={installPreview}
          onConfirm={confirmInstall}
          onCancel={() => {
            setInstallPreview(null);
            setInstallDraft(null);
          }}
        />
      )}
      {editor && <SkillEditor editor={editor} onChange={setEditor} onSave={save} onCancel={() => setEditor(null)} />}

      {allShared && (
        <div className="settings-note mb-2 flex items-center gap-1.5" data-testid="skills-shared-note">
          <Icon name="folder" size={12} /> These skills come from a shared skills folder and are managed there.
        </div>
      )}
      <div ref={list} className="settings-list" data-testid="skills-list">
        {rows.length === 0 && !editor && (
          <div className="settings-empty">
            No skills yet — <b>Add skill</b> teaches the agent its first one, like “prepare my Monday status report”.
          </div>
        )}
        {rows.length > 0 && visible.length === 0 && (
          <div className="settings-empty" data-testid="skills-no-match">
            {wanted ? `No skill matches “${query.trim()}”.` : SHOW_EMPTY[show as Exclude<Show, "all">]}
          </div>
        )}
        {visible.map((row) => {
          const meta = row.metadata ?? {};
          const byline = [row.license ? `License ${row.license}` : "", meta.author ? `by ${meta.author}` : "", meta.version ? `v${meta.version}` : ""].filter(Boolean).join(" · ");
          const isOpen = opened === row.name;
          return (
            <div key={row.name} className={isOpen ? "settings-item is-open" : "settings-item"} data-flip={row.name}>
              <button type="button" className="settings-item-text settings-item-open" aria-label={`Open ${row.name}`} aria-expanded={isOpen} onClick={() => setOpened(isOpen ? null : row.name)}>
                <span className="settings-item-name">
                  <span className={row.enabled ? "" : "text-muted"} title={row.name}>
                    {row.name}
                  </span>
                  <SkillTags row={row} install={installs[row.name]} allShared={allShared} />
                </span>
                {/* Two lines at most, all of it on hover: a skill's line is its menu entry, and one
                    line hid what the skill does (a live run), where the whole text let a page hold
                    five skills (the 2026-10-10 redesign). */}
                <span className="settings-item-line clamp-2" title={row.description}>
                  {row.description}
                </span>
                {/* How often it was used, then its license, author and version when it has them. */}
                <span className="settings-item-sub" data-testid={`skill-meta-${row.name}`}>
                  {[usageLine(row), byline].filter(Boolean).join(" · ")}
                </span>
              </button>
              {/* A skill in a shared folder is not this app's to write (edit, delete, switch), so it
                  has no switch either. */}
              {!row.compat && <Toggle checked={row.enabled} ariaLabel={`${row.name} enabled`} title={row.enabled ? "On — offered to the agent" : "Off everywhere"} onChange={(on) => switchSkill(row, on)} />}
            </div>
          );
        })}
      </div>

      {openedRow && (
        <SkillDetails
          row={openedRow}
          installedFrom={installs[openedRow.name]?.source}
          armed={armedDelete === openedRow.name}
          onClose={closeDetails}
          onEdit={
            openedRow.compat
              ? undefined
              : () => {
                  setOpened(null);
                  setEditor({ mode: "edit", name: openedRow.name, description: openedRow.description, instructions: openedRow.instructions });
                  document.querySelector(".settings-content")?.scrollTo({ top: 0, behavior: "smooth" });
                }
          }
          onReveal={openedRow.files ? () => revealSkill(openedRow.name) : undefined}
          onDelete={openedRow.compat ? undefined : () => void remove(openedRow)}
          onDisarm={() => setArmedDelete(null)}
        />
      )}
    </section>
  );
}
