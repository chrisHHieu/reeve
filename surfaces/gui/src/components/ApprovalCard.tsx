import { useRef, useState } from "react";
import type { ApprovalDecision, ApprovalItem } from "../types";
import { approvalTitle, type ApprovalSubject } from "../humanize";
import { Disclosure } from "../ui/Disclosure";
import { EXTERNAL, FILE_WRITES, grantHost, scopeNote, shortArgs } from "./approval/scope";
import { MessagePreview, PreviewBlock, SaveSkillPreview, TitleText } from "./approval/previews";
import { Decision, DecisionActions, DecisionBody, DecisionButton, DecisionRisk, DecisionSpacer } from "./decision/Decision";
import { Icon, type IconName } from "./Icon";
import { useDecisionKeyboard } from "./primitives/decisionKeys";

// The approval card: what is asked, where it acts, a preview of the action itself, and the answers.
// Where it acts and the previews live in ./approval; they are exported from here too, where the
// parked card (InboxItemCard) and humanize.ts have always found them.
export { grantHost, scopeNote, shortArgs, type ScopeNote } from "./approval/scope";
export { PreviewBlock, SaveSkillPreview, TitleText } from "./approval/previews";

// The verbs of the grant lines; the card's own title comes from humanize.ts.
const TOOL_VERBS: Record<string, string> = {
  write_file: "Write a file",
  replace_in_file: "Edit a file",
  apply_patch: "Apply a patch",
  apply_unified_diff: "Apply a patch",
  run_shell: "Run a command",
  send_message: "Send a message",
  send_file: "Send a file",
};

// The tools whose preview is drawn for them; any other gets its own detail line or its arguments.
const OWN_PREVIEW = new Set(["run_shell", "send_message", "send_file", "save_skill"]);

/** What the two answers say, per tool: proposing a skill is adding one, not allowing a call.
 *  The parked card uses the same, so both speak alike. */
export function approvalActionLabels(name?: string): { allow: string; deny: string } {
  return name === "save_skill" ? { allow: "Add to my skills", deny: "Not now" } : { allow: "Allow once", deny: "Deny" };
}

// A create_scheduled_task card's `permissions`: a read is shown so the person knows; a write
// is a standing grant the approval creates.
interface PermissionLine {
  tool: string;
  target: string;
  access: string;
}

function permissionLines(args: any): PermissionLine[] {
  const proposed = args?.permissions;
  if (!Array.isArray(proposed)) return [];
  return proposed.filter((p) => p && typeof p === "object" && p.tool && p.target).map((p) => ({ tool: String(p.tool), target: String(p.target), access: String(p.access || "read") }));
}

/** Whether a card offers no "always" for the whole session. Each case has its narrower grant or none:
 *  inside an automation run the grant for that task replaces it (the run's session does not last,
 *  and two "always" buttons side by side would blur the difference between them); a connector and
 *  a skill never get one (every skill is reviewed on its own); a command gets the
 *  grant for that command; a request out is granted by its destination (one tool-wide would
 *  cover every destination to come); a read outside the session is granted by adding the folder,
 *  which no tool-wide grant would reach anyway. */
function sessionGrantWithheld(item: ApprovalItem, standing: boolean): boolean {
  return standing || item.category === "connector" || ["run_shell", "save_skill", "web_fetch", "web_search"].includes(item.name) || !!item.reach?.length;
}

function Buttons({
  item,
  onApprove,
  runTask,
  primaryLabel,
  denyLabel = "Deny",
}: {
  item: ApprovalItem;
  onApprove: (decision: ApprovalDecision, edited?: Record<string, unknown>) => void;
  runTask?: { id: string; title: string } | null;
  primaryLabel: string;
  denyLabel?: string;
}) {
  // "Edit": the person corrects the call (a wrong path, a command with one flag too many) and the
  // ENGINE checks the edited arguments again, as a new call, before they run. The editor is JSON
  // because the arguments are; a form per tool would be a second schema to keep in step.
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [editError, setEditError] = useState("");
  const startEditing = () => {
    setDraft(JSON.stringify(item.args ?? {}, null, 2));
    setEditError("");
    setEditing(true);
  };
  const stopEditing = () => {
    setEditing(false);
    setEditError("");
  };
  const runEdited = () => {
    let edited: unknown;
    try {
      edited = JSON.parse(draft);
    } catch {
      return setEditError("That is not valid JSON — fix it, or cancel.");
    }
    if (!edited || typeof edited !== "object" || Array.isArray(edited)) return setEditError("The arguments must be a JSON object.");
    onApprove("modify", edited as Record<string, unknown>);
  };

  const standing = !!(runTask && item.standingTarget);
  // A search goes to one fixed place (the configured provider), so a grant for the tool is a grant
  // for that provider, and the button names what it grants: searches. A fetch is granted by domain.
  const fetchHost = item.name === "web_fetch" ? grantHost(item.args?.url) : "";

  // The editor opens above the answers by growing, and closes the same way; the row of answers under
  // it changes where it is (it used to replace the whole row in one frame).
  const editor = (
    <Disclosure open={editing} className="fold-fade" testId="approval-edit">
      <textarea className="dec-field code" value={draft} spellCheck={false} aria-label="Edit the arguments before running" data-testid="approval-edit-text" onChange={(e) => setDraft(e.target.value)} />
      {editError && (
        <p className="dec-error" role="alert" data-testid="approval-edit-error">
          {editError}
        </p>
      )}
    </Disclosure>
  );

  if (editing) {
    return (
      <>
        {editor}
        <DecisionActions>
          <DecisionButton primary option={false} onClick={runEdited} data-testid="approval-edit-run">
            Run edited
          </DecisionButton>
          <DecisionSpacer />
          <DecisionButton quiet option={false} onClick={stopEditing}>
            Cancel
          </DecisionButton>
        </DecisionActions>
      </>
    );
  }

  return (
    <>
      {editor}
      <DecisionActions>
        <DecisionButton primary onClick={() => onApprove("once")}>
          {primaryLabel}
        </DecisionButton>
        {standing && (
          <DecisionButton
            title={`Always allow ${item.name} → ${item.standingTarget} for “${runTask?.title || "this automation"}” — revoke any time on its Automations page`}
            onClick={() => onApprove("always_task")}
          >
            Allow every time
          </DecisionButton>
        )}
        {!sessionGrantWithheld(item, standing) && (
          <DecisionButton title={`Always allow ${TOOL_VERBS[item.name]?.toLowerCase() || item.name} for this session`} onClick={() => onApprove("always_tool")}>
            Always allow
          </DecisionButton>
        )}
        {!standing && item.name === "web_fetch" && fetchHost && (
          <DecisionButton title={`Every fetch to ${fetchHost} (and its subdomains) runs without asking for the rest of this session`} onClick={() => onApprove("always_domain")}>
            Always allow {fetchHost} this session
          </DecisionButton>
        )}
        {!standing && item.name === "web_search" && (
          <DecisionButton title="Every web search runs without asking for the rest of this session — the grant ends if you change the search provider" onClick={() => onApprove("always_tool")}>
            Always allow searches this session
          </DecisionButton>
        )}
        {item.name === "run_shell" && <DecisionButton onClick={() => onApprove("always_command")}>Always allow this command</DecisionButton>}
        <DecisionSpacer />
        {/* One way to refuse here, since the other already exists. Deny is "not like this" and the
            turn goes on; the composer's Stop, pressed while this card is up, refuses the action AND
            ends the turn. A second button for that would be two ways of saying one thing, which to
            someone who does not write code is a question, not a choice. Stop only lacked the words,
            and those were fixed in the engine (USER_STOP_MESSAGE), not with a button. */}
        <DecisionButton quiet option={false} title="Change the arguments, then run — the edited call is checked again before it runs" onClick={startEditing} data-testid="approval-edit-open">
          Edit
        </DecisionButton>
        <DecisionButton quiet className="deny" title="Don't do this — the agent may still find a different way to help" onClick={() => onApprove("deny")}>
          {denyLabel}
        </DecisionButton>
      </DecisionActions>
    </>
  );
}

/** The answers about a call that may already have happened. Checking first is what most
 *  people answer, so it is the filled one: running again can do the work twice, and a check costs a
 *  step. No Edit and no lasting grant: the question is about the past. Refusing is "Check first"
 *  itself; the composer's Stop still ends the turn. */
function UncertainButtons({ onApprove }: { onApprove: (decision: ApprovalDecision) => void }) {
  return (
    <DecisionActions>
      <DecisionButton primary data-testid="approval-check-first" title="Don't run it again — the agent checks whether it already happened" onClick={() => onApprove("deny")}>
        Check first
      </DecisionButton>
      <DecisionButton data-testid="approval-run-again" onClick={() => onApprove("once")}>
        Run it again
      </DecisionButton>
    </DecisionActions>
  );
}

/** The head's icon, by what is asked: the glyphs the transcript's steps wear. */
function approvalIcon(name: string): IconName {
  if (name === "run_shell") return "terminal";
  if (FILE_WRITES.has(name)) return "pencilLine";
  if (EXTERNAL.has(name)) return "chat";
  const glyphs: Record<string, IconName> = { create_scheduled_task: "clock", save_skill: "book", web_fetch: "globe", web_search: "globe" };
  return glyphs[name] ?? "shield";
}

/** Why this asks, as a line of its own (D4). "Out of reach" is the one most worth recognising: no
 *  button on the card changes that outcome (plan mode, a write outside the session's folders, Reeve's
 *  own settings). An older server never sends it. */
function WhyItAsks({ item }: { item: ApprovalItem }) {
  if (item.uncertain) return <DecisionRisk testId="approval-may-have-run">It may have finished before Reeve stopped. Running it twice can do its work twice.</DecisionRisk>;
  if (item.humanOnly) {
    return (
      <DecisionRisk testId="approval-always-asks" title="This needs a person's OK — saved grants do not cover it; only Full access does">
        Always asks
      </DecisionRisk>
    );
  }
  if (item.axis === "boundary") {
    return (
      <DecisionRisk testId="approval-out-of-reach" title="Outside this session's reach — no approval can change this">
        Out of reach
      </DecisionRisk>
    );
  }
  if (item.axis === "consent") {
    return (
      <p className="dec-note" title="Not dangerous by reach — it needs a person's OK">
        Needs your OK
      </p>
    );
  }
  return null;
}

function SendFilePreview({ args }: { args: any }) {
  return (
    <>
      <span className="approval-filechip">
        <span className="ico">
          <Icon name="file" size={13} />
        </span>
        {String(args?.path ?? "").split("/").pop() || "file"}
        {args?.as_screenshot ? " · as a PNG screenshot" : ""}
      </span>
      {args?.comment && <MessagePreview text={String(args.comment)} label="With the message" />}
    </>
  );
}

export function ApprovalCard({
  item,
  onApprove,
  runTask,
}: {
  item: ApprovalItem;
  onApprove: (decision: ApprovalDecision) => void;
  // Given when the approval came up inside an automation run: it offers "Allow every time" for that
  // task (in the app only).
  runTask?: { id: string; title: string } | null;
}) {
  const subject: ApprovalSubject = { name: item.name, args: item.args, category: item.category, presented: item.presentation };
  const scope = scopeNote(subject);
  const cautious = scope.external || scope.unknown;
  const grants = item.name === "create_scheduled_task" ? permissionLines(item.args) : [];
  // "requires approval" is the engine's default wording; only a real reason is shown.
  const reason = item.reason && item.reason !== "requires approval" ? item.reason : "";
  const fileWrite = FILE_WRITES.has(item.name);
  const content = typeof item.args?.content === "string" ? item.args.content : "";
  const labels = approvalActionLabels(item.name);
  const fallbackDetail = !fileWrite && !OWN_PREVIEW.has(item.name) && !grants.length ? item.presentation?.detail || shortArgs(item.args) : "";
  const uncertainTitle = item.name === "run_shell" ? "This command had started when Reeve stopped. Run it again?" : "This had started when Reeve stopped. Run it again?";

  // Answered from the keyboard: 1..N picks an option, ↑↓ moves, Enter confirms, Escape denies, the
  // last only while there is a Deny to fall back on (an answered card is no longer a decision).
  const dock = useRef<HTMLDivElement>(null);
  useDecisionKeyboard(dock, { onDismiss: item.resolved ? undefined : () => onApprove("deny") });

  return (
    <Decision
      icon={approvalIcon(item.name)}
      title={item.uncertain ? uncertainTitle : <TitleText line={approvalTitle(subject)} />}
      context={scope.text}
      warn={cautious}
      className={cautious ? "approval-dec approval-external" : "approval-dec"}
      // A file write was a one-line row of its own until 2026-10-12; it keeps that id.
      testId={fileWrite ? "approval-row" : "approval-card"}
      innerRef={dock}
      label="Approval"
    >
      {/* The action itself, shaped by its tool, not an arguments dump. A file write shows its content
          at once: it used to be behind a "preview" toggle, and approving a write nobody looked at is
          what this card exists to prevent. */}
      {item.name === "run_shell" && item.args?.command && <PreviewBlock text={String(item.args.command)} />}
      {fileWrite && content && <PreviewBlock text={content} />}
      {item.name === "send_file" && <SendFilePreview args={item.args} />}
      {item.name === "send_message" && item.args?.text && <MessagePreview text={String(item.args.text)} />}
      {/* A skill's arguments are what it is reviewed by. */}
      {item.name === "save_skill" && <SaveSkillPreview args={item.args} />}
      {/* A search names where it goes as the setting stands now: "currently", never "default". */}
      {item.name === "web_search" && <p className="dec-note">Queries go to your configured search provider{item.searchProvider ? ` (currently: ${item.searchProvider})` : ""}.</p>}

      {grants.length > 0 && (
        <DecisionBody>
          <div className="dec-grants" data-testid="approval-grants">
            {grants.map((grant, i) => (
              <div className="dec-grant" key={i} data-access={grant.access}>
                <Icon name={grant.access === "write" ? "check" : "eye"} size={13} />
                <span className="grant-line">
                  <b>{TOOL_VERBS[grant.tool] || grant.tool}</b> <code>{grant.target}</code>
                  <span className="grant-note">{grant.access === "write" ? " — always allowed once you approve" : " — read-only"}</span>
                </span>
              </div>
            ))}
          </div>
        </DecisionBody>
      )}
      {/* Any other tool: the detail it declared, and only without one its arguments as key=value,
          which a reader falls back to and is never given first. */}
      {fallbackDetail && <DecisionBody code>{fallbackDetail}</DecisionBody>}
      {/* A command's text cannot say the agent wrote this file a moment ago. */}
      {item.provenance && <DecisionRisk>{item.provenance}</DecisionRisk>}
      <WhyItAsks item={item} />
      {/* What approving changes beyond this call: the project joins the session, so the
          next read there does not ask, and a write after a read asks once more instead of being
          refused. It is said here because the answer outlasts the call. The uncertain card's
          reason only repeats its title; its risk line says the rest. */}
      {item.reach?.length ? (
        <p className="dec-note" data-testid="approval-reach">
          Allowing lets this session {item.reach.some((r) => r.writable) ? "read and write" : "read"} {item.reach.map((r) => r.path).join(", ")} from now on.
        </p>
      ) : (
        reason && !item.uncertain && <p className="dec-note">{reason}</p>
      )}

      {item.resolved ? (
        <div className="dec-resolved resolved">Approved: {item.resolved.replace("_", " ")}</div>
      ) : item.uncertain ? (
        <UncertainButtons onApprove={onApprove} />
      ) : (
        <Buttons item={item} onApprove={onApprove} runTask={runTask} primaryLabel={fileWrite ? "Allow" : labels.allow} denyLabel={labels.deny} />
      )}
    </Decision>
  );
}
