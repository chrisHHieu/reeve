// A question card with many questions, as one form: every question at once in a box that
// scrolls, each a row of choices, so the person answers what matters to them in any order and sends
// once. A question with a recommended option starts on it; a question left with nothing chosen goes
// to the agent, which says what it chose. "Let the agent choose all" sends every question that way.
//
// Fewer than FORM_FROM questions keep the stepper (`InboxItemCard`): one question a step reads well
// for two or three, and ten steps is ten clicks before anything happens.
import { useState } from "react";
import { Decision, DecisionButton, DecisionSpacer } from "./decision/Decision";
import { allToAgent, formAnswers, standing, toggle, type FormPick, type FormQuestion } from "./questionAnswers";

export function QuestionForm({ questions, onAnswer }: { questions: FormQuestion[]; onAnswer: (answer: string) => void }) {
  const [picks, setPicks] = useState<Record<number, FormPick>>({});
  // The questions whose own-words box is open: "Other…" opens it, so a row of choices stays a row.
  const [typing, setTyping] = useState<Record<number, boolean>>({});
  const set = (index: number, pick: FormPick) => setPicks((now) => ({ ...now, [index]: pick }));
  const open = questions.filter((question, index) => standing(question, picks[index]).length === 0 && !(picks[index]?.text ?? "").trim()).length;

  return (
    <Decision
      icon="chat"
      title={`${questions.length} questions before I start`}
      context={open ? `${open} left to the agent` : "All answered"}
      className="question-card question-form"
      testId="question-card"
      label="Questions"
      fluid
    >
      <p className="dec-note qf-note">Answer what matters to you. A question you leave is the agent&apos;s to choose, and it says what it chose.</p>
      <ol className="qf-list" data-testid="question-form">
        {questions.map((question, index) => {
          const chosen = standing(question, picks[index]);
          const text = picks[index]?.text ?? "";
          const shown = question.options.find((option) => chosen.includes(option.label) && (option.description || option.preview));
          const left = chosen.length === 0 && !text.trim();
          return (
            <li key={index} className="qf-q" data-left={left || undefined} data-testid="question-form-item">
              <div className="qf-head">
                <span className="qf-n">{index + 1}</span>
                <span className="qf-text">{question.question}</span>
                {left && <span className="qf-left">Agent chooses</span>}
              </div>
              {question.options.length > 0 && (
                <div className="qf-opts" role="group" aria-label={question.question}>
                  {question.options.map((option) => {
                    const on = chosen.includes(option.label);
                    return (
                      <button
                        key={option.label}
                        type="button"
                        className="qf-opt"
                        aria-pressed={on}
                        data-on={on || undefined}
                        title={option.description || undefined}
                        onClick={() => set(index, toggle(question, picks[index], option.label))}
                      >
                        {option.label}
                        {option.recommended && <span className="qf-rec">Recommended</span>}
                      </button>
                    );
                  })}
                  {question.allowText && !typing[index] && !text && (
                    <button type="button" className="qf-opt qf-other" onClick={() => setTyping((now) => ({ ...now, [index]: true }))}>
                      Other…
                    </button>
                  )}
                </div>
              )}
              {shown?.description && <p className="qf-desc">{shown.description}</p>}
              {shown?.preview && <pre className="dec-b code qf-preview">{shown.preview}</pre>}
              {(question.options.length === 0 || (question.allowText && (typing[index] || text))) && (
                <input
                  autoFocus={question.options.length > 0}
                  className="dec-field qf-input"
                  placeholder={question.options.length ? "In your own words…" : "Your answer…"}
                  aria-label={`Your own answer to: ${question.question}`}
                  value={text}
                  onChange={(event) =>
                    set(index, { picked: question.multi ? standing(question, picks[index]) : event.target.value.trim() ? [] : picks[index]?.picked ?? null, text: event.target.value })
                  }
                />
              )}
            </li>
          );
        })}
      </ol>
      <div className="dec-row q-foot qf-foot">
        <DecisionButton quiet option={false} data-testid="question-form-leave" onClick={() => onAnswer(allToAgent(questions))}>
          Let the agent choose all
        </DecisionButton>
        <DecisionSpacer />
        <DecisionButton primary option={false} className="q-send" data-testid="question-send" onClick={() => onAnswer(formAnswers(questions, picks))}>
          Send
        </DecisionButton>
      </div>
    </Decision>
  );
}
