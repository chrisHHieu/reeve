import { useRef, useState } from "react";
import type { InboxItem } from "../api";
import { approvalTitle } from "../humanize";
import type { QuestionOption } from "../types";
import { approvalActionLabels, PreviewBlock, SaveSkillPreview, scopeNote, TitleText } from "./ApprovalCard";
import { Decision, DecisionActions, DecisionBody, DecisionButton, DecisionPager, DecisionSpacer } from "./decision/Decision";
import type { IconName } from "./Icon";
import { ScrollFade } from "./primitives/ScrollFade";
import { useDecisionKeyboard } from "./primitives/decisionKeys";
import { QuestionForm } from "./QuestionForm";
import { FORM_FROM, LEFT_TO_AGENT } from "./questionAnswers";

// Something waiting on the person, answered in its own conversation: a question the agent asked
// (ask_user), or something the server parked while no window was open (an approval, a folder, a
// plan). A question may offer answers to pick, one or several, and can always be answered in the
// person's own words. An answer may carry a description, a mark as the recommended one and a
// preview; questions can come as a group, gone through one at a time with "1 of N" in the
// head. A question with plain strings for answers, or a single one, is drawn by the same card.

export { LEFT_TO_AGENT } from "./questionAnswers";

type Resolve = (id: string, resolution: string) => void;

interface Answer {
  label: string;
  description: string;
  recommended: boolean;
  preview: string;
}

interface Question {
  question: string;
  header: string;
  options: Answer[];
  allowText: boolean;
  multi: boolean;
}

const answerOf = (option: QuestionOption): Answer =>
  typeof option === "string"
    ? { label: option, description: "", recommended: false, preview: "" }
    : { label: option.label || "", description: option.description || "", recommended: !!option.recommended, preview: option.preview || "" };

/** The item's questions: its grouped `questions`, or its own fields as one question. */
function questionsOf(item: InboxItem): Question[] {
  const group = item.questions || [];
  if (group.length) {
    return group.map((q) => ({ question: q.question, header: q.header || "", options: (q.options || []).map(answerOf), allowText: q.allow_text !== false, multi: !!q.multi }));
  }
  return [{ question: item.title, header: item.header || "", options: (item.options || []).map(answerOf), allowText: item.allow_text !== false, multi: !!item.multi }];
}

// One question's answers: compact rows (number, label, one line of description) in a box that
// scrolls when there are many, and ONE Send on the card (the owner, 2026-09-28: a card where several
// could be picked had a Send under the answers and another by the text box). With one to pick, a
// row answers when clicked or its number pressed; with several, a row is ticked, and Send sends the
// ticks and whatever was typed.
function QuestionBlock({ spec, onAnswer }: { spec: Question; onAnswer: (answer: string) => void }) {
  const [ticked, setTicked] = useState<string[]>([]);
  const [typedText, setTypedText] = useState("");
  const [pointed, setPointed] = useState<number | null>(null);
  const keys = useRef<HTMLDivElement>(null);
  const { options, multi } = spec;
  useDecisionKeyboard(keys, { enabled: options.length > 0 });
  const typed = typedText.trim();
  const ownWords = spec.allowText || options.length === 0;
  const canSend = typed !== "" || (multi && ticked.length > 0);
  const send = () => {
    if (canSend) onAnswer(multi ? [...ticked, ...(typed ? [typed] : [])].join(", ") : typed);
  };
  const choose = (answer: Answer) => {
    if (!multi) return onAnswer(answer.label);
    setTicked((was) => (was.includes(answer.label) ? was.filter((label) => label !== answer.label) : [...was, answer.label]));
  };

  // The preview follows the pointer or focus, else the ticked answer's, else the first there is.
  const firstTicked = options.findIndex((o) => ticked.includes(o.label));
  const previewAt = pointed ?? (firstTicked >= 0 && options[firstTicked].preview ? firstTicked : options.findIndex((o) => o.preview));
  const preview = previewAt >= 0 ? options[previewAt].preview : "";

  const rows = (
    <ScrollFade className="q-opts scroll-fade" data-testid="question-options">
      {options.map((answer, i) => {
        const on = ticked.includes(answer.label);
        return (
          <button
            key={answer.label + i}
            type="button"
            className="q-opt"
            data-option
            data-on={on || undefined}
            aria-pressed={multi ? on : undefined}
            onMouseEnter={() => setPointed(i)}
            onMouseLeave={() => setPointed(null)}
            onFocus={() => setPointed(i)}
            onBlur={() => setPointed(null)}
            onClick={() => choose(answer)}
          >
            <span className={multi ? "q-opt-key check" : "q-opt-key"} aria-hidden>
              {multi ? (on ? "✓" : "") : i + 1}
            </span>
            <span className="q-opt-main">
              <span className="q-opt-label">
                <span className="q-opt-text">{answer.label}</span>
                {answer.recommended && <span className="q-opt-rec">Recommended</span>}
              </span>
              {answer.description && <span className="q-opt-desc">{answer.description}</span>}
            </span>
          </button>
        );
      })}
    </ScrollFade>
  );

  return (
    <div ref={keys} className="q-block">
      {options.length > 0 &&
        (options.some((o) => o.preview) ? (
          // The answers on the left and the preview on the right, one above the other when narrow.
          <div className="q-panes">
            <div className="q-panes-opts">{rows}</div>
            <pre data-testid="question-preview" className="dec-b code q-preview">
              {preview}
            </pre>
          </div>
        ) : (
          rows
        ))}
      <div className="dec-row q-foot">
        {ownWords ? (
          <input
            className="dec-field q-input"
            placeholder={options.length ? "Something else? Type it here…" : "Your answer…"}
            aria-label="Your own answer"
            value={typedText}
            onChange={(e) => setTypedText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && canSend) {
                e.preventDefault();
                send();
              }
            }}
          />
        ) : (
          <DecisionSpacer />
        )}
        {/* Quiet and at the end: a way out, not one of the answers. */}
        <DecisionButton quiet option={false} className="q-leave" data-testid="question-leave" onClick={() => onAnswer(LEFT_TO_AGENT)}>
          Let the agent choose
        </DecisionButton>
        {(ownWords || multi) && (
          <DecisionButton primary option={false} className="q-send" data-testid="question-send" disabled={!canSend} onClick={send}>
            Send{multi && ticked.length ? ` (${ticked.length + (typed ? 1 : 0)})` : ""}
          </DecisionButton>
        )}
      </div>
    </div>
  );
}

function QuestionCard({ item, onResolve }: { item: InboxItem; onResolve: Resolve }) {
  const questions = questionsOf(item);
  const grouped = (item.questions?.length ?? 0) > 0;
  if (grouped && questions.length >= FORM_FROM) return <QuestionForm questions={questions} onAnswer={(answer) => onResolve(item.id, answer)} />;
  return <QuestionSteps item={item} questions={questions} grouped={grouped} onResolve={onResolve} />;
}

function QuestionSteps({ item, questions, grouped, onResolve }: { item: InboxItem; questions: Question[]; grouped: boolean; onResolve: Resolve }) {
  const [step, setStep] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const spec = questions[Math.min(step, questions.length - 1)];

  const answer = (given: string) => {
    if (!grouped) return onResolve(item.id, given);
    // Keyed by header, else by the question itself: the key answer_result() on the server gives
    // the agent.
    const all = { ...answers, [spec.header || spec.question]: given };
    setAnswers(all);
    if (step + 1 < questions.length) setStep(step + 1);
    else onResolve(item.id, JSON.stringify(all));
  };

  return (
    <Decision
      icon="chat"
      title={spec.question}
      // The header is what the question is about in a word or two ("Priority"), as the agent wrote
      // it. It used to be an uppercase label above the question.
      context={spec.header}
      pager={
        grouped ? (
          <span className="q-stepper" data-testid="question-stepper">
            <span className="q-stepper-name">{spec.header || `Question ${step + 1}`}</span>
            {/* Only back: a question is passed by answering it. */}
            <DecisionPager index={step} count={questions.length} onPrev={() => setStep(step - 1)} prevLabel="Previous question" />
          </span>
        ) : undefined
      }
      className="question-card"
      testId="question-card"
      label="Question"
      // The next question has its own number of answers; the card glides to its height.
      fluid
    >
      {item.body ? <p className="dec-note q-body">{item.body}</p> : null}
      {/* A new key per step starts the next question with nothing ticked, typed or pointed at. */}
      <div key={step} className={step > 0 || Object.keys(answers).length > 0 ? "ui-fade" : undefined}>
        <QuestionBlock spec={spec} onAnswer={answer} />
      </div>
    </Decision>
  );
}

// -- a parked item that is not a question ---------------------------------------------------------
//
// A parked approval that carries its tool is drawn as the live ApprovalCard (App builds the same
// item from it). What comes here is the rest: an old row with no tool data, a folder or a plan asked
// for while no window was open, a notice to dismiss.

const PARKED_ICONS: Partial<Record<InboxItem["kind"], IconName>> = { directory: "folderPlus", plan: "checklist", approval: "shield" };

function ParkedAnswers({ item, tool, onResolve }: { item: InboxItem; tool: string; onResolve: Resolve }) {
  const say = (resolution: unknown) => () => onResolve(item.id, typeof resolution === "string" ? resolution : JSON.stringify(resolution));
  const refuse = (label: string, resolution: unknown) => (
    <>
      <DecisionSpacer />
      <DecisionButton quiet className="deny" onClick={say(resolution)}>
        {label}
      </DecisionButton>
    </>
  );
  const data = item.data;
  if (item.kind === "approval") {
    const labels = tool ? approvalActionLabels(tool) : { allow: "Approve", deny: "Deny" };
    return (
      <DecisionActions>
        <DecisionButton primary onClick={say("allow")}>
          {labels.allow}
        </DecisionButton>
        {/* The grant for the automation: only when it came up inside a run AND the call can be
            bound to a tool and a target. */}
        {data?.task_id && data?.standing_target && (
          <DecisionButton title={`Always allow against ${data.standing_target} for “${data.task_title || "this automation"}” — revoke any time on its Automations page`} onClick={say("always_task")}>
            Allow every time
          </DecisionButton>
        )}
        {refuse(labels.deny, "deny")}
      </DecisionActions>
    );
  }
  if (item.kind === "directory") {
    const path = data?.path || "";
    return (
      <DecisionActions>
        <DecisionButton primary disabled={!path} title={path || "No folder was suggested"} onClick={say({ granted: true, path, writable: data?.primary ? true : !!data?.writable })}>
          {path ? (data?.primary ? "Make workspace" : "Grant") : "Grant (no folder)"}
        </DecisionButton>
        {refuse("Deny", { granted: false })}
      </DecisionActions>
    );
  }
  if (item.kind === "plan") {
    return (
      <DecisionActions>
        <DecisionButton primary onClick={say({ approved: true, mode: "interactive" })}>
          Approve
        </DecisionButton>
        {refuse("Reject", { approved: false, feedback: "" })}
      </DecisionActions>
    );
  }
  return (
    <DecisionActions>
      <DecisionButton primary onClick={say("seen")}>
        Dismiss
      </DecisionButton>
    </DecisionActions>
  );
}

function ParkedCard({ item, onResolve }: { item: InboxItem; onResolve: Resolve }) {
  const keys = useRef<HTMLDivElement>(null);
  useDecisionKeyboard(keys);
  const tool = item.kind === "approval" && item.data?.tool ? String(item.data.tool) : "";
  const args = item.data?.arguments;
  // One subject for this card and the live one, from everything the server parked, the category
  // and the tool's own presentation with it: working it out from the tool and its arguments alone
  // is what made the two cards disagree.
  const subject = tool ? { name: tool, args, category: item.data?.category, presented: item.data?.presentation } : null;
  const scope = subject ? scopeNote(subject) : null;

  let shown = null;
  if (tool === "save_skill") shown = <SaveSkillPreview args={args} />; // the live card's review
  else if (tool && typeof args?.content === "string") shown = <PreviewBlock text={args.content} />;
  else if (tool && typeof args?.command === "string") shown = <PreviewBlock text={args.command} />;
  else if (item.body) shown = <DecisionBody className="parked-body">{item.body}</DecisionBody>;

  return (
    <Decision
      icon={PARKED_ICONS[item.kind] ?? "info"}
      title={subject ? <TitleText line={approvalTitle(subject)} /> : item.title}
      context={scope?.text}
      warn={!!scope && (scope.external || scope.unknown)}
      className="parked-card"
      testId="parked-card"
      innerRef={keys}
      label="Waiting on you"
    >
      {shown}
      <ParkedAnswers item={item} tool={tool} onResolve={onResolve} />
    </Decision>
  );
}

export function InboxItemCard({ item, onResolve }: { item: InboxItem; onResolve: Resolve }) {
  return item.kind === "question" ? <QuestionCard item={item} onResolve={onResolve} /> : <ParkedCard item={item} onResolve={onResolve} />;
}
