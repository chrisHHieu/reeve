// What a question card sends back: one answer, or a map keyed by each question's header.
//
// From FORM_FROM questions the card is a form the person fills in any order: a question with a
// recommended option starts on it, a question left alone is the agent's to settle (LEFT_TO_AGENT),
// and the agent says what it chose. Pure, so the rules are tested without a DOM.

/** The answer a question gets when the person leaves it to the agent (the owner's ask for a design
 *  brief, 2026-10-20; any question may use it). */
export const LEFT_TO_AGENT = "Up to you: choose what fits best, and say in your answer what you chose.";

/** From this many grouped questions the card is a form, not a stepper: ten steps of one question
 *  each is ten clicks before anything happens. */
export const FORM_FROM = 5;

export interface FormOption {
  label: string;
  description: string;
  recommended: boolean;
  preview: string;
}

export interface FormQuestion {
  question: string;
  header: string;
  options: FormOption[];
  allowText: boolean;
  multi: boolean;
}

/** One question's state in the form: the options picked and what was typed. `null` picks means the
 *  person has not touched it, so the recommended option stands. */
export interface FormPick {
  picked: string[] | null;
  text: string;
}

/** The key an answer goes under: the header, else the question (what the server's answer map uses). */
export const answerKey = (question: FormQuestion) => question.header || question.question;

/** The options a question stands on now: what was picked, else the recommended one(s). */
export function standing(question: FormQuestion, pick: FormPick | undefined): string[] {
  if (pick?.picked) return pick.picked;
  const recommended = question.options.filter((option) => option.recommended).map((option) => option.label);
  return question.multi ? recommended : recommended.slice(0, 1);
}

/** One question's answer: its picks and words, else left to the agent. */
export function answerOf(question: FormQuestion, pick: FormPick | undefined): string {
  const typed = (pick?.text ?? "").trim();
  const chosen = standing(question, pick);
  if (!question.multi && typed) return typed;
  const parts = [...chosen, ...(typed ? [typed] : [])];
  return parts.length ? parts.join(", ") : LEFT_TO_AGENT;
}

/** The whole form's answer: a JSON map, every question in it. */
export function formAnswers(questions: FormQuestion[], picks: Record<number, FormPick>): string {
  const out: Record<string, string> = {};
  questions.forEach((question, index) => {
    out[answerKey(question)] = answerOf(question, picks[index]);
  });
  return JSON.stringify(out);
}

/** Every question left to the agent. */
export function allToAgent(questions: FormQuestion[]): string {
  const out: Record<string, string> = {};
  for (const question of questions) out[answerKey(question)] = LEFT_TO_AGENT;
  return JSON.stringify(out);
}

/** A pick moved by a click on `label`: single choice toggles to it or off, multi toggles it in or out. */
export function toggle(question: FormQuestion, pick: FormPick | undefined, label: string): FormPick {
  const now = standing(question, pick);
  const text = question.multi ? (pick?.text ?? "") : "";
  if (question.multi) return { picked: now.includes(label) ? now.filter((item) => item !== label) : [...now, label], text };
  return { picked: now.includes(label) ? [] : [label], text };
}
