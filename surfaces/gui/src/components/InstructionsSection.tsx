import { useEffect, useRef, useState } from "react";
import { getMemorySettings, setMemorySettings, type MemorySettings } from "../api";
import { Icon } from "./Icon";

/** How long typing has to pause before the file is written. */
const SAVE_AFTER_MS = 800;

/** Starters a person can add with one click: what belongs here, in their words to finish. */
const STARTERS: { label: string; line: string }[] = [
  { label: "How to address you", line: "Call me …" },
  { label: "Language of answers", line: "Answer in …, unless the code or file is in another language." },
  { label: "Date and number format", line: "Write dates as DD-MM-YYYY." },
  { label: "What to never do", line: "Never … without asking first." },
  { label: "Accessibility needs", line: "I use a screen reader: no tables, describe any image." },
];

// Settings ▸ Instructions (settings redesign, 2026-10-20): the file as an editor the height of the
// page, written as the person pauses (no Save to forget), with what else applies beside it.
export function InstructionsSection() {
  const [settings, setSettings] = useState<MemorySettings | null>(null);
  const [draft, setDraft] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const kept = useRef("");
  const timer = useRef<number | undefined>(undefined);
  const editor = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    getMemorySettings()
      .then((s) => {
        setSettings(s);
        setDraft(s.user_rules ?? "");
        kept.current = s.user_rules ?? "";
      })
      .catch(() => setSettings(null));
    return () => window.clearTimeout(timer.current);
  }, []);

  const save = async (text: string) => {
    window.clearTimeout(timer.current);
    if (text === kept.current) return;
    setState("saving");
    try {
      const next = await setMemorySettings({ user_rules: text });
      kept.current = next.user_rules ?? text;
      setSettings(next);
      setState("saved");
    } catch {
      setState("error");
    }
  };

  const change = (text: string) => {
    setDraft(text);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void save(text), SAVE_AFTER_MS);
  };

  const addStarter = (line: string) => {
    const text = draft.trim() ? `${draft.replace(/\s+$/, "")}\n${line}` : line;
    change(text);
    // The cursor goes to the words left to finish ("…"), so the person types there.
    requestAnimationFrame(() => {
      const box = editor.current;
      if (!box) return;
      box.focus();
      const blank = line.indexOf("…");
      const at = text.length - line.length + Math.max(blank, line.length);
      box.setSelectionRange(at, blank >= 0 ? at + 1 : at);
    });
  };

  if (!settings) return <div className="settings-note">Loading…</div>;

  const lines = draft.trim() ? draft.trim().split("\n").length : 0;
  return (
    <div className="instructions-layout" data-testid="instructions-card">
      <div className="instructions-editor">
        <div className="instructions-editor-head">
          <span className={"instructions-file" + (settings.soul_path ? " is-path" : "")} data-testid="instructions-path" title={settings.soul_path || undefined}>
            {settings.soul_path || "Your instructions"}
          </span>
          <span className="instructions-status" aria-live="polite" data-testid="instructions-status" data-state={state}>
            {state === "saving" ? (
              "Saving…"
            ) : state === "saved" ? (
              <>
                <Icon name="check" size={12} /> Saved · new conversations use it
              </>
            ) : state === "error" ? (
              "Could not save the file — keep typing to try again."
            ) : lines ? (
              `${lines} line${lines === 1 ? "" : "s"}`
            ) : (
              ""
            )}
          </span>
        </div>
        <textarea
          ref={editor}
          aria-label="Your instructions"
          value={draft}
          onChange={(event) => change(event.target.value)}
          onBlur={() => void save(draft)}
          placeholder={"I use a screen reader — no tables, describe any image\nUse DD-MM-YYYY for dates"}
          data-testid="instructions-input"
          spellCheck={false}
          className="instructions-input"
        />
      </div>
      <aside className="instructions-side">
        <section>
          <h2 className="settings-card-heading">Also followed in a project</h2>
          <p className="instructions-side-text">
            A project's own <code>AGENTS.md</code> or <code>CLAUDE.md</code> applies on top of these, only in that project.
            Conversations already open keep the instructions they started with.
          </p>
        </section>
        <section>
          <h2 className="settings-card-heading">Good things to say here</h2>
          <div className="instructions-starters">
            {STARTERS.map((starter) => (
              <button key={starter.label} type="button" className="instructions-starter" onClick={() => addStarter(starter.line)}>
                <Icon name="plus" size={12} />
                {starter.label}
              </button>
            ))}
          </div>
          <p className="instructions-side-text">Facts about one project belong in that project's memory, not here.</p>
        </section>
      </aside>
    </div>
  );
}
