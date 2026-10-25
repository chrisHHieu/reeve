// A form the model asked the person to fill. Its submit sends the answers as the
// person's next message, written as a reader would ("Format: Excel"), so they reach the model
// through the ordinary turn and stay in the transcript as what the person said.

import { useState, type FormEvent } from "react";
import type { FormField, FormSpec } from "./visualModel";

/** Asks the session to send `text` as the person's message (App listens; it queues the
 *  message when a turn is still running). */
export const SEND_PROMPT_EVENT = "reeve-send-prompt";

type Answers = Record<string, string | string[] | boolean>;

function initialOf(field: FormField): string | string[] | boolean {
  if (field.kind === "checkbox") {
    if (field.options?.length) return Array.isArray(field.value) ? field.value.map(String) : [];
    return field.value === true;
  }
  if (field.value !== undefined && field.value !== null) return String(field.value);
  return field.kind === "select" && field.options?.length ? field.options[0] : "";
}

/** The message a submitted form sends: its title, then one line per answered field. */
export function formMessage(spec: FormSpec, answers: Answers): string {
  const lines = spec.fields
    .map((field) => {
      const value = answers[field.name];
      const said = Array.isArray(value) ? value.join(", ") : typeof value === "boolean" ? (value ? "yes" : "no") : value;
      return said ? `${field.label}: ${said}` : null;
    })
    .filter(Boolean);
  return [`[${spec.title ?? "Form"}]`, ...lines].join("\n");
}

export function FormBlock({ spec }: { spec: FormSpec }) {
  const [answers, setAnswers] = useState<Answers>(() => Object.fromEntries(spec.fields.map((f) => [f.name, initialOf(f)])));
  const [sent, setSent] = useState(false);
  const set = (name: string, value: string | string[] | boolean) => setAnswers((a) => ({ ...a, [name]: value }));
  const missing = spec.fields.some((f) => f.required && (answers[f.name] === "" || (Array.isArray(answers[f.name]) && !(answers[f.name] as string[]).length)));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (sent || missing) return;
    window.dispatchEvent(new CustomEvent(SEND_PROMPT_EVENT, { detail: { text: formMessage(spec, answers) } }));
    setSent(true);
  };

  return (
    <form className="vz-form" data-sent={sent || undefined} onSubmit={submit}>
      <fieldset disabled={sent}>
        {spec.fields.map((field) => {
          const id = `vz-${field.name}-${spec.title ?? ""}`.replace(/\W+/g, "-");
          const value = answers[field.name];
          return (
            <div key={field.name} className="vz-field" data-wide={field.kind === "textarea" || field.kind === "checkbox" || field.kind === "radio" || undefined}>
              {field.kind === "checkbox" && !field.options?.length ? (
                <label className="vz-check">
                  <input type="checkbox" checked={value === true} onChange={(e) => set(field.name, e.target.checked)} />
                  {field.label}
                </label>
              ) : (
                <>
                  <label htmlFor={id} className="vz-field-label">
                    {field.label}
                    {field.required && <span aria-hidden> *</span>}
                  </label>
                  {field.kind === "select" ? (
                    <select id={id} value={String(value)} onChange={(e) => set(field.name, e.target.value)}>
                      {(field.options ?? []).map((o) => (
                        <option key={o}>{o}</option>
                      ))}
                    </select>
                  ) : field.kind === "radio" || field.kind === "checkbox" ? (
                    <div className="vz-choices" id={id} role="group">
                      {(field.options ?? []).map((o) => {
                        const checked = field.kind === "radio" ? value === o : Array.isArray(value) && value.includes(o);
                        return (
                          <label key={o} className="vz-check">
                            <input
                              type={field.kind}
                              name={id}
                              checked={checked}
                              onChange={(e) =>
                                field.kind === "radio"
                                  ? set(field.name, o)
                                  : set(field.name, e.target.checked ? [...(value as string[]), o] : (value as string[]).filter((x) => x !== o))
                              }
                            />
                            {o}
                          </label>
                        );
                      })}
                    </div>
                  ) : field.kind === "textarea" ? (
                    <textarea id={id} rows={3} placeholder={field.placeholder} value={String(value)} onChange={(e) => set(field.name, e.target.value)} />
                  ) : (
                    <input
                      id={id}
                      type={field.kind === "number" ? "number" : "text"}
                      placeholder={field.placeholder}
                      value={String(value)}
                      onChange={(e) => set(field.name, e.target.value)}
                    />
                  )}
                </>
              )}
            </div>
          );
        })}
      </fieldset>
      <div className="vz-form-foot">
        {sent ? (
          <span className="vz-form-sent" data-testid="visual-form-sent">
            Sent as your next message
          </span>
        ) : (
          <button type="submit" className="vz-submit" disabled={missing} data-testid="visual-form-submit">
            {spec.submit}
          </button>
        )}
      </div>
    </form>
  );
}
