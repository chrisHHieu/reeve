import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Attachment, ReviewComment, SentCommand, SessionMetrics, SessionUsage } from "../types";
import { unreadableNotices } from "../modelInputs";
import { mergeCapped, MAX_ATTACHMENTS, MAX_PAYLOAD_CHARS, pastedFiles, readFileChecked } from "../attach";
import { ProjectBindMenu } from "./ProjectBindMenu";
import { QueuedPrompts, type QueuedPrompt } from "./QueuedPrompts";
import {
  compactNow,
  sessionFileIndex,
  formatRemoteEnvironmentReadout,
  getSessionGoal,
  goalAction,
  readArtifact,
  sessionSkills,
  type RemoteEnvironmentSummary,
  type SessionSkillRow,
  type SubagentAgent,
} from "../api";
import {
  GOAL_USAGE,
  goalCommandInput,
  goalStatusBlock,
  parseGoalCommand,
} from "../goalCommand";
import {
  PROMPT_COMMANDS,
  promptCommandFor,
  promptCommandInput,
  resolvePromptTemplate,
  type PromptCommand,
} from "../promptCommands";
import { totalTokens } from "../usage";
import { getCommand, listCommands, type CommandRow } from "../api/plugins";
import { ModelPicker } from "./ModelPicker";
import { BranchChip } from "./BranchChip";
import { Icon } from "./Icon";
import { atMenuRows, folderOf as folderOfPath, matchIn, nameOf as nameOfPath, pointersIn, type AtRow, type Touched } from "./composer/atMenu";
import { FileIcon } from "./FileIcon";
import { AttachmentCard, AttachmentTile } from "./attachments/AttachmentCard";
import { attachedTotal } from "./attachments/attachmentFacts";
import { ImageOverlay } from "./markdown/MarkdownImage";
import { CommentChip } from "./composer/CommentChip";
import { ModeMenu } from "./composer/ModeMenu";
import { UsageChip } from "./composer/UsageChip";
import { CommandMark } from "./composer/CommandMark";
import { isDesktop } from "../desktop";
import { overPages, overSize, pdfLimitsFor } from "./composer/pdfLimits";
import { useDictation, WAVE_BARS } from "./composer/useDictation";

// -- per-conversation drafts -----------------------------------------------------------------
// What was left in the composer when its conversation was left, by conversation. In memory for
// the page's lifetime: attachments can be large (a pasted image) and are not worth a storage
// quota, and a draft that outlives a reload is a different feature.
type ComposerDraft = {
  text: string;
  attachments: Attachment[];
  pendingSkill: SessionSkillRow | null;
};
const DRAFTS = new Map<string, ComposerDraft>();
const DRAFTS_KEPT = 40;

function rememberDraft(key: string, draft: ComposerDraft): void {
  DRAFTS.delete(key);
  // An empty box is not a draft: nothing to put back.
  if (!draft.text.trim() && draft.attachments.length === 0 && !draft.pendingSkill) return;
  DRAFTS.set(key, draft);
  while (DRAFTS.size > DRAFTS_KEPT) DRAFTS.delete(DRAFTS.keys().next().value as string);
}

function takeDraft(key: string): ComposerDraft | undefined {
  const draft = DRAFTS.get(key);
  DRAFTS.delete(key);
  return draft;
}

const mergeAttachments = (cur: Attachment[], add: Attachment[]): Attachment[] => mergeCapped(cur, add).merged;

// There is no model list built in: until the server sends one (a few seconds after a cold start)
// the picker is a disabled "Loading models…" chip. A list baked in goes stale and offers ids the
// server never confirmed (caught 2026-07-21).
//
// Every `on…` that is left out takes its control with it.
interface Props {
  mode: string;
  model: string;
  models?: string[];
  /** Display names; a model without one shows its id. */
  modelLabels?: Record<string, string>;
  running: boolean;
  connected: boolean;
  /** False when the default model's provider has no key: the composer says so, and a send goes to
   *  setting one up, the draft kept, rather than nowhere. */
  modelReady?: boolean;
  onConnectModel?: () => void;
  onConfigureVoiceInput?: () => void;
  onSend: (text: string, attachments?: Attachment[], skill?: string, mentions?: string[], command?: SentCommand) => void;
  /** Hold the draft until the running turn ends. Without it a send during a turn is refused. */
  onQueue?: (text: string, attachments?: Attachment[], skill?: string, mentions?: string[], command?: SentCommand) => void;
  /** Send the draft into the running turn, at its next step. Plain text only: attachments, a skill
   *  or an @mention make a new turn, so the button never offers what the server would refuse. */
  onSteer?: (text: string) => void;
  /** Prompts waiting for the running turn, oldest first. */
  queued?: QueuedPrompt[];
  onUnqueue?: (id: string) => void;
  /** The session, for the "/" menu's skills and the rest that asks the
   *  server; without one the menu never opens. */
  sessionId?: string;
  onInterrupt: () => void;
  onModeChange: (mode: string) => void;
  /** Plan, a switch beside the presets: on, the agent explores read-only and proposes a plan, and
   *  the server refuses anything that changes things until the plan is approved. */
  plan?: boolean;
  onPlanChange?: (on: boolean) => void;
  onModelChange: (model: string) => void;
  /** The thinking level (see ModelPicker), on surfaces that offer it. */
  thinking?: string;
  onThinkingChange?: (level: string) => void;
  /** The picker added a catalogue model that was not ticked yet: the surface reads its settings
   *  and the picker's list again. */
  onModelsChanged?: () => void;
  /** Where the session works; with it the mode menu is shown. Folder access is the session's
   *  standing settings, not the composer's. */
  workspace?: string;
  /** The folder's checked-out branch and its name (null for a chat or a temporary folder, as the
   *  top bar has it): chips above the card on a new session, `folder / branch` in the footer on
   *  one with history. */
  gitBranch?: string | null;
  /** The person switched the folder's branch from the chip. */
  onBranchSwitched?: (branch: string) => void;
  /** Before the first message: the session should start in this new worktree instead. */
  onBranchWorktree?: (path: string, branch: string) => void;
  workspaceName?: string | null;
  /** The SSH environment the session is bound to and its folder there: the readout is
   *  then `user@host:path`, and the caller leaves out the local folder and branch, so nothing says
   *  the files are on this computer. */
  environment?: RemoteEnvironmentSummary | null;
  remoteWorkspace?: string | null;
  /** Line comments waiting to go with the next message, as chips over the box, so what will go is
   *  seen and can be taken off first. */
  comments?: ReviewComment[];
  onRemoveComment?: (id: string) => void;
  onEditComment?: (id: string, text: string) => void;
  /** "View & edit…" in the project-memory menu. */
  onOpenMemory?: () => void;
  /** Words and files put in the box from elsewhere (a start-page task, a design pointer), to be
   *  read and sent by the person. A new `nonce` applies even the same prefill again. `keepText`:
   *  what is typed stays, and `text` goes in only when there is none. */
  prefill?: { text: string; attachments?: Attachment[]; nonce: number; keepText?: boolean };
  /** A new session's setup chips (folder, machine), in the row above the card with the branch.
   *  Gone after the first message, and the row with it. */
  setupSlot?: ReactNode;
  /** Changes with the conversation on screen; the draft is kept for the one left. */
  resetKey?: string;
  /** The empty box's hint, per surface. */
  placeholder?: string;
  /** The session's tokens; without any the usage chip is not drawn. */
  usage?: SessionUsage;
  /** The active model's context window, when known; without it the chip counts and has no ring. */
  contextWindow?: number;
  /** Where the session compacts, when the server has said: the ring fills toward it. */
  compactAt?: number;
  /** Settings: the ring instead of the session's total (off by default). */
  contextBar?: boolean;
  /** The server's count over the session's events (retries, failures, latency, tools), asked for
   *  the first time the usage panel opens. */
  loadMetrics?: () => Promise<SessionMetrics | null>;
  /** A compaction is running now: the ring turns. */
  compacting?: boolean;
  // Busy without a turn (a `/compact` between turns, or Reeve still starting): Enter queues, and
  // there is no Stop.
  holding?: boolean;
  /** What a held message waits for, said in the queue row and the empty box ("compacting is done"). */
  holdFor?: string;
  /** The conversation on screen is a stand-in until the real one opens (Reeve starting): what is
   *  typed goes with the box to the conversation that opens, instead of staying behind. */
  provisional?: boolean;
  /** The files the agent read or changed in this conversation, newest first: "@" offers
   *  them before the folder. */
  touchedFiles?: Touched[];
  // `/compact` reports its phase upward: the busy row belongs IN the transcript (where the
  // marker lands, and where the person is reading), and a failure/refusal goes to a toast.
  onCompactStatus?: (busy: boolean, problem?: string, tone?: "info" | "error") => void;
  // `/goal` (the harness's goal state): the command's status, result and
  // refusal lines land in the transcript as one quiet info block — never an alert — and
  // the GoalDock moves on its own from the route's `goal` / `goal_activation` frames.
  // `title` is the block heading ("Goal created"); absent for a bare status/refusal.
  onGoalNotice?: (text: string, tone?: "info" | "warn", title?: string) => void;
  // P4 @-mentions: the `task` agent definitions (`GET /v1/subagents`). The `@` popup lists
  // `mode !== "primary" && !hidden`; absent → no popup.
  agents?: SubagentAgent[];
  // Built-in session commands: each row runs the same
  // handler the rest of the app already uses — a command is a second doorway, not a second
  // implementation. Absent handler → the row is disabled with a reason.
  /** `/new` — App's `startNewSession`, behaves like clicking New session. */
  onNewSession?: () => void;
  /** `/export` — export the transcript as JSON (App owns the toast). */
  onExportSession?: () => void;
  /** `/fork` — App's `forkConversation` (whole-prefix fork). */
  onForkSession?: () => void;
  /** `/mcp` — navigate to the Connectors surface (MCP list). */
  onOpenConnectors?: () => void;
  /** Whether the session has any user message yet. */
  hasMessages?: boolean;
  /** `/undo` — App's `undoConversation` (message-level revert). */
  onUndoSession?: () => void;
  /** `/reload-plugins` — rebuild this conversation with the plugins as they are now. */
  onReloadPlugins?: () => void;
  /** `/redo` — App's `redoConversation` (walk the revert boundary forward / clear). */
  onRedoSession?: () => void;
  /** Whether a visible user message exists to undo. */
  canUndo?: boolean;
  /** Whether a revert marker is staged. */
  canRedo?: boolean;
  /** `/open` — open the session's file picker. */
  onOpenFile?: () => void;
  /** `/terminal` — toggle the session's user-shell panel (A second doorway: the App owns the panel). */
  onToggleTerminal?: () => void;
  /** `/workspace` — toggle the project's git-worktrees section. Disabled without a folder. */
  onToggleWorktrees?: () => void;
  /** `/share` — upload the session to the person's `[share] endpoint` and copy the URL
   *  (there is no hosted cloud here). Disabled without a configured endpoint. */
  onShareSession?: () => void;
  canShare?: boolean;
  shareReason?: string;
  /** `/unshare` — revoke the stored share; only when the endpoint supports revoke. */
  onUnshareSession?: () => void;
  canUnshare?: boolean;
  unshareReason?: string;
}

// One row of the "/" popup. A SKILL inserts its name inline and rides the next send as a
// field; a built-in COMMAND runs on the spot — there is no message to
// send, so nothing is inserted and the draft is cleared; a PROMPT row is the reference design's
// server-command shape — selecting it INSERTs `/name ` into the draft ()
// and submitting resolves the template with the typed arguments.
type SlashRow =
  | {
      kind: "command";
      name: string;
      description: string;
      run: () => void;
      disabled?: boolean;
      reason?: string;
    }
  // A prompt template: a built-in one, or a command a person, a project or a plugin
  // defined (`server`) whose body is fetched when it is picked.
  | { kind: "prompt"; command: PromptCommand; server?: CommandRow }
  | { kind: "skill"; skill: SessionSkillRow }
  // An agent the message can be handed to (it was offered by "@" until 2026-10-13: "@" is
  // context, handing work over is an act). Picking it puts `@name` in the draft, which rides
  // the send as the `mentions` field, as before.
  | { kind: "agent"; agent: { name: string; description?: string; default?: boolean } };

const rowName = (row: SlashRow): string =>
  row.kind === "command"
    ? row.name
    : row.kind === "prompt"
      ? row.command.name
      : row.kind === "agent"
        ? row.agent.name
        : row.skill.name;

const rowDisabled = (row: SlashRow): boolean =>
  row.kind === "command" ? !!row.disabled : false;

// What the row's second column reads: a disabled command shows WHY it cannot run in place
// of its description.
const rowDescription = (row: SlashRow): string =>
  row.kind === "command"
    ? row.disabled
      ? row.reason || row.description
      : row.description
    : row.kind === "prompt"
      ? row.command.description +
        (row.server?.argument_hint ? ` — ${row.server.argument_hint}` : "")
      : row.kind === "agent"
        ? row.agent.description || "Subagent"
        : // A skill's own line for people where it has one; its description is written for
          // the model ("Use this skill whenever…").
          row.skill.summary || row.skill.description;

// Where a defined command comes from, in the pill a built-in template shows "prompt" in.
const SERVER_SOURCE_WORDS: Record<CommandRow["source"], string> = {
  plugin: "plugin",
  user: "yours",
  project: "project",
  compat: "shared",
};

const firstEnabledIndex = (rows: SlashRow[]): number => {
  const index = rows.findIndex((row) => !rowDisabled(row));
  return index < 0 ? 0 : index;
};

// While the "/" popup is still a bare query, the Skills section previews this many names;
// the rest are one keystroke away ("type to filter"). With many compat skills installed the
// uncapped list used to grow past the window (owner screenshot 2026-09-18).
const SKILL_PREVIEW_LIMIT = 8;

/** The `@token` the caret is in: at the start or after whitespace, to the end of the draft.
 * It may hold a path, so "/" and "\\" are part of it. */
const MENTION_TOKEN = /(^|\s)@([A-Za-z0-9._/\\-]*)$/;
/** The text and code files the "+" menu's file window offers, beside images and PDFs. */
const TEXT_FILES = "text/*,.md,.csv,.json,.yaml,.yml,.log,.py,.ts,.tsx,.js,.rs,.go,.toml";

/** Where a skill comes from, said in a word on its row (it was a pill with a use count). */
const SKILL_SCOPE_WORDS: Record<string, string> = {
  project: "this project",
  global: "yours",
  compat: "shared",
  plugin: "a plugin's",
};

/** What Enter does on a row of the "/" list, said on the row the keyboard is on. */
const slashDoes = (row: SlashRow): string =>
  row.kind === "command"
    ? "Enter runs it now."
    : row.kind === "prompt"
      ? "Enter types it into the box."
      : row.kind === "agent"
        ? "Enter hands this message to it."
        : "Enter adds it to this message. The agent loads it when you send.";

/** The name with what was typed marked, for a row of either list. */
function Marked({ name, typed }: { name: string; typed: string }) {
  const parts = matchIn(name, typed);
  if (!parts) return <>{name}</>;
  return (
    <>
      {parts[0]}
      <mark className="menu-match">{parts[1]}</mark>
      {parts[2]}
    </>
  );
}

// The room a panel of the composer has to open into, written on the wrap for the stylesheet
// (`--pop-up*`, `--pop-down*`): from the top bar down to the card, to the row of controls and
// to the strip, and from the card and the strip down to the foot of the window. A panel is
// never taller than its room and scrolls inside, so on a short window (a laptop) nothing is
// cut off at the window's edge. The card moves without changing size when the first message
// is sent, so the room is measured again before anything can open: on a press or a key in
// the composer, as well as when the window or the wrap changes size.
const PANEL_MARGIN = 8;
const PANEL_FLOOR = 120;
function usePanelRoom(wrapRef: { current: HTMLDivElement | null }) {
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const measure = () => {
      const card = wrap.querySelector<HTMLElement>(".composer");
      if (!card) return;
      const column = wrap.closest<HTMLElement>(".main-chat");
      const ceiling =
        (column ? column.getBoundingClientRect().top + (parseFloat(getComputedStyle(column).paddingTop) || 0) : 0) +
        PANEL_MARGIN;
      const floor = window.innerHeight - PANEL_MARGIN;
      const c = card.getBoundingClientRect();
      const row = card.querySelector(".composer-footer")?.getBoundingClientRect() ?? c;
      const tray = wrap.querySelector(".composer-tray")?.getBoundingClientRect() ?? c;
      const set = (name: string, px: number) =>
        wrap.style.setProperty(name, `${Math.max(PANEL_FLOOR, Math.floor(px))}px`);
      set("--pop-up", c.top - 8 - ceiling);
      set("--pop-up-row", row.top - ceiling);
      // The strip's controls are 40px above its lower edge (its top is tucked under the card).
      set("--pop-up-tray", tray.bottom - 40 - ceiling);
      set("--pop-down", floor - c.bottom - 6);
      set("--pop-down-tray", floor - tray.bottom);
    };
    measure();
    wrap.addEventListener("pointerdown", measure, true);
    wrap.addEventListener("keydown", measure, true);
    window.addEventListener("resize", measure);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(wrap);
    return () => {
      wrap.removeEventListener("pointerdown", measure, true);
      wrap.removeEventListener("keydown", measure, true);
      window.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, [wrapRef]);
}

export function Composer(props: Props) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  // The strip under the card goes with the first message; it is kept for its leaving motion
  // only when that message was sent from here. A conversation whose history arrives after it
  // is opened also goes from no messages to some, and its strip must not flash and slide away
  // (seen on the real app, 2026-10-13).
  const started = props.hasMessages === true;
  const [trayLeaving, setTrayLeaving] = useState(false);
  const trayBefore = useRef({ session: props.sessionId, started });
  const sentFrom = useRef<string | null>(null);
  useLayoutEffect(() => {
    const before = trayBefore.current;
    trayBefore.current = { session: props.sessionId, started };
    const sentHere = sentFrom.current === (props.sessionId ?? "");
    if (before.session !== props.sessionId || before.started || !started || !sentHere) {
      setTrayLeaving(false);
      return;
    }
    setTrayLeaving(true);
    const timer = window.setTimeout(() => setTrayLeaving(false), 220);
    return () => window.clearTimeout(timer);
  }, [props.sessionId, started]);
  // The "/" menu follows the text: it is open while the box holds "/" and a word
  // with no space after it, and no skill is picked. A pick puts "/name " in the box, where it is
  // the state itself; the person types on after it, and the send takes the prefix off and names
  // the skill in a field of its own. Editing the prefix out drops the skill.
  const [pendingSkill, setPendingSkill] = useState<SessionSkillRow | null>(null);
  const [slashSkills, setSlashSkills] = useState<SessionSkillRow[] | null>(null);
  // Commands people, projects and plugins define. The list rides the popup;
  // a body is fetched once its command is picked (or submitted typed), then kept.
  const [serverCommands, setServerCommands] = useState<CommandRow[] | null>(null);
  const knownCommands = useRef<Set<string>>(new Set());
  const commandBodies = useRef<Record<string, string>>({});
  const [slashIndex, setSlashIndex] = useState(0);
  // The popup's scroll container: keyboard walking scrolls the selected row into view so
  // arrows never move the highlight off-screen (the rule the model menu already uses).
  const slashScrollRef = useRef<HTMLDivElement | null>(null);
  // The `/model` row opens the composer's own popover in place (no modal): the same state
  // the control toggles, so opening from either doorway draws the same panel.
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const prefixIntact = pendingSkill !== null && (text === `/${pendingSkill.name}` || text.startsWith(`/${pendingSkill.name} `));
  useEffect(() => {
    if (pendingSkill && !prefixIntact) setPendingSkill(null);
  }, [pendingSkill, prefixIntact]);
  const slashQuery = !prefixIntact && props.sessionId && text.startsWith("/") && !/\s/.test(text.slice(1)) ? text.slice(1).toLowerCase() : null;
  // Each opening reads the session's commands and skills again; closing forgets them.
  useEffect(() => {
    if (slashQuery === null) {
      setSlashSkills(null);
      setServerCommands(null);
      setSlashIndex(0);
      return;
    }
    if (serverCommands === null && props.sessionId) {
      listCommands({ session: props.sessionId, workspace: props.workspace })
        .then((rows) => {
          knownCommands.current = new Set(rows.map((c) => c.name));
          setServerCommands(rows);
        })
        .catch(() => setServerCommands([]));
    }
    if (slashSkills === null && props.sessionId) {
      // Not one turned off here, and not one only the agent may run (`user-invocable: false`, P1-7).
      sessionSkills(props.sessionId, props.workspace)
        .then((all) => setSlashSkills(all.filter((s) => s.enabled && s.user_invocable !== false)))
        .catch(() => setSlashSkills([]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slashQuery === null]);
  const pickSkill = (skill: SessionSkillRow) => {
    setPendingSkill(skill);
    setText(`/${skill.name} `);
    textareaRef.current?.focus();
  };
  const pickRow = (row: SlashRow) => {
    if (row.kind === "command") {
      if (row.disabled) return;
      setText("");
      setPendingSkill(null);
      row.run();
      return;
    }
    if (row.kind === "agent") {
      setPendingSkill(null);
      setText("");
      pickMention(row.agent.name);
      return;
    }
    if (row.kind === "prompt") {
      // Server commands INSERT `/name ` on select; the
      // template expands on submit, with whatever arguments were typed after it.
      setPendingSkill(null);
      setText(`/${row.command.name} `);
      textareaRef.current?.focus();
      if (row.server && commandBodies.current[row.server.name] === undefined) {
        const name = row.server.name;
        void getCommand(name, { session: props.sessionId, workspace: props.workspace }).then((c) => {
          if (c) commandBodies.current[name] = c.body;
        });
      }
      return;
    }
    pickSkill(row.skill);
  };

  const [dragging, setDragging] = useState(false);
  const [dragCount, setDragCount] = useState(0);
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  // Which "This session" submenu is open (bindings live server-side).
  const [bindMenu, setBindMenu] = useState<"memory" | null>(null);
  // Bindings need a session and a workspace surface (Chat has neither).
  const sessionRows = Boolean(props.sessionId && props.workspace !== undefined);
  const bindRow = (icon: "book", label: string, kind: "memory", says: string) => (
    <button
      className={"plus-item" + (bindMenu === kind ? " on" : "")}
      onClick={() => setBindMenu(bindMenu === kind ? null : kind)}
    >
      <Icon name={icon} size={15} className="plus-item-ico" />
      <span className="plus-item-text">
        <span className="plus-item-name">{label}</span>
        <span className="plus-item-says">{says}</span>
      </span>
      <Icon name="chevronRight" size={12} className="plus-item-key" />
    </button>
  );
  // "@" and "/" from the "+" menu: the same lists the keys open, said here once so a person
  // who never read the placeholder finds them.
  const openAtList = () => {
    setAttachMenuOpen(false);
    setMentionClosed(false);
    setText((cur) => `${cur}${cur && !/\s$/.test(cur) ? " " : ""}@`);
    textareaRef.current?.focus();
  };
  const openSlashList = () => {
    setAttachMenuOpen(false);
    setText("/");
    textareaRef.current?.focus();
  };
  const [attachNotice, setAttachNotice] = useState<string | null>(null);
  // Files still being read (their cards say so, and Send waits for them), and files that will not
  // go, each a card saying why until it is taken off or the message is sent.
  const [reading, setReading] = useState<string[]>([]);
  // The picture in the box opened large (its index), or none.
  const [viewing, setViewing] = useState<number | null>(null);
  // How far the box's text has scrolled, for the highlight drawn over its first line.
  const [inputScroll, setInputScroll] = useState(0);
  const [refused, setRefused] = useState<{ name: string; why: string }[]>([]);
  // `/compact`: the phase (busy / refused / failed) is reported upward — the transcript
  // shows the busy row and the marker, a toast carries the problems. Nothing renders on
  // the prompt bar (design review 2026-09-16).
  const compactRunning = useRef(false);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  usePanelRoom(wrapRef);
  const noticeTimer = useRef<number | null>(null);
  // Spoken words are added after what is typed.
  const { dictation, dictationBusy, dictationError, levels, recordingTime, voiceReady, toggleDictation } = useDictation({
    onWords: (words) => setText((draft) => (draft.trim() ? `${draft.trimEnd()} ${words}` : words)),
    onConfigure: props.onConfigureVoiceInput,
    focus: () => textareaRef.current?.focus(),
  });

  // Why an attachment did not go. "<name> skipped — <why>" is about one file and becomes its card,
  // kept until it is taken off, not a line gone before it is read; anything else is a line for 8s.
  const showAttachNotice = (message: string) => {
    const skipped = /^(.+?) skipped — (.+)$/s.exec(message);
    if (skipped) {
      const [, name, why] = skipped;
      setRefused((cur) => [...cur.filter((r) => r.name !== name), { name, why: why.charAt(0).toUpperCase() + why.slice(1) }]);
      return;
    }
    setAttachNotice(message);
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setAttachNotice(null), 8000);
  };

  // `/compact` (alias `/summarize`): the server refuses mid-turn (the running loop reads the
  // history per iteration), so the command says why instead of firing into a rejection.
  const runCompact = async () => {
    if (!props.sessionId || compactRunning.current) return;
    if (props.running) {
      props.onCompactStatus?.(false, "Wait for the current task to finish.", "info");
      return;
    }
    compactRunning.current = true;
    props.onCompactStatus?.(true);
    try {
      const out = await compactNow(props.sessionId);
      if (out.ok || out.announced) props.onCompactStatus?.(false);
      else props.onCompactStatus?.(false, out.error || "Could not compact.", "error");
    } catch {
      props.onCompactStatus?.(false, "Could not compact.", "error");
    } finally {
      compactRunning.current = false;
    }
  };

  // `/goal`. The transport lives here, like
  // `/compact`'s; the App decides where the line lands. The READ is the same GET the dock
  // reloads from, and every mutation POSTs the route whose `goal` / `goal_activation`
  // broadcast moves the dock. The grammar and wording live in `goalCommand.ts`, with
  // the route's refusal text surfaced verbatim, so "use /goal edit …" reads the same in
  // the transcript as it would in the reference design.
  const goalRunning = useRef(false);
  const runGoal = async (input: string) => {
    const notice = (text: string, tone: "info" | "warn" = "info", title?: string) =>
      props.onGoalNotice?.(text, tone, title);
    const command = parseGoalCommand(input);
    if (command.kind === "invalid-edit") {
      notice(`Goal editing requires a replacement objective.\n${GOAL_USAGE}`, "warn");
      return;
    }
    if (!props.sessionId || goalRunning.current) return;
    goalRunning.current = true;
    try {
      if (command.kind === "show") {
        const goal = await getSessionGoal(props.sessionId);
        if (goal === null) {
          notice(`No goal is currently set.\n${GOAL_USAGE}`);
          return;
        }
        const block = goalStatusBlock("Goal", goal);
        notice(block.text, "info", block.title);
        return;
      }
      // Every other verb reads first: the bare-state refusals ("no goal to pause") and
      // replacement rule are decided on the CURRENT goal, and the mutation still
      // re-validates server-side (the revision fence).
      const current = await getSessionGoal(props.sessionId).catch(() => null);
      if (command.kind === "clear") {
        if (current === null) {
          notice("No goal to clear.");
          return;
        }
        const res = await goalAction(props.sessionId, "clear");
        if (!res.ok) notice(res.error || "Could not clear the goal.", "warn");
        else notice("Goal cleared.");
        return;
      }
      if (command.kind === "pause" || command.kind === "resume") {
        if (current === null) {
          notice(
            `No goal is currently set; /goal ${command.kind} requires one.\n${GOAL_USAGE}`,
            "warn",
          );
          return;
        }
        const res = await goalAction(props.sessionId, command.kind);
        if (!res.ok || !res.goal) {
          notice(res.error || "The goal command is not valid for the current state. Run /goal to view available commands.", "warn");
          return;
        }
        const block = goalStatusBlock(
          command.kind === "pause" ? "Goal paused" : "Goal resumed",
          res.goal,
        );
        notice(block.text, "info", block.title);
        return;
      }
      if (command.kind === "edit" && current === null) {
        notice(`No goal is currently set; /goal edit requires one.\n${GOAL_USAGE}`, "warn");
        return;
      }
      if (command.kind === "create" && current !== null && current.phase !== "complete") {
        // The refusal, verbatim: a silent overwrite would drop work the agent was told
        // it pursues. A COMPLETE goal is replaceable — that is how a new one starts.
        notice(
          `A goal is already ${current.phase}. Use /goal edit <objective> to change it or /goal clear before replacing it.`,
          "warn",
        );
        return;
      }
      // A complete goal is replaced with `set`.
      const replace = current !== null && current.phase !== "complete";
      const res = await goalAction(props.sessionId, replace ? "edit" : "set", {
        objective: command.objective,
      });
      if (!res.ok || !res.goal) {
        notice(res.error || "Could not record the goal.", "warn");
        return;
      }
      const block = goalStatusBlock(replace ? "Goal updated" : "Goal created", res.goal);
      notice(block.text, "info", block.title);
    } catch {
      notice("Could not reach the server for the goal command.", "warn");
    } finally {
      goalRunning.current = false;
    }
  };

  // P4 @-mentions. The composer has no rich-text parts, so the mention is a highlighted
  // TOKEN in the draft: typing `@` opens a popup over the agents the
  // prompt-input offers (`!hidden && mode !== "primary"`);
  // picking one inserts `@name ` inline (the slash-popup pattern this file already
  // uses). On send, the names are re-scanned off the draft — so editing the token away
  // drops the mention — and ride as their own field, exactly like `skill`. The server
  // appends the synthetic instruction and keys the `task` ask-skip on the turn.
  // Declared before the command rows: `/agent` needs to know whether the picker has
  // anything to show.
  const mentionable = useMemo(
    () =>
      (props.agents ?? []).filter(
        (a) =>
          typeof a?.name === "string" &&
          a.name.trim() !== "" &&
          a.mode !== "primary" &&
          a.hidden !== true,
      ),
    [props.agents],
  );

  // Built-in commands lead the menu, then the prompt rows (`/init`, `/review`
  // server-command shape), then the skills. `/compact` (alias `/summarize`) is the slash
  // form of the usage popover's button, and a person who has the muscle memory should not
  // have to find a gauge first. `/goal` is the human command — the row runs the bare
  // status (usage and all), and a typed `/goal <objective|verb>` draft is intercepted on
  // send, so the command never rides out as a chat message. The new session commands reuse
  // the app's existing handlers (`startNewSession`, `forkConversation`, the Connectors
  // navigation): a row is a second doorway, never a second implementation. The row keeps
  // the CANONICAL name even when it was matched through the alias. Muted skills never
  // appear; commands are not session data, so they are offered even while the skills
  // fetch is in flight.
  const modelsLoaded = !!(props.models && props.models.length);
  const needsModel = props.modelReady === false;
  const commands = [
    {
      name: "compact",
      aliases: ["summarize"],
      description: "Condense the history now to free context",
      run: () => void runCompact(),
    },
    {
      name: "goal",
      aliases: [],
      description:
        "Set or view the goal for a long-running task — /goal <objective> | clear | edit <objective> | pause | resume",
      run: () => void runGoal(""),
    },
    {
      // `/plan`: the bare row turns the Plan overlay on; the typed
      // forms `/plan off` and `/plan <objective>` are intercepted on submit. Disabled
      // while already on — a row that would do nothing never pretends it would.
      name: "plan",
      aliases: [],
      description:
        "Plan first (read-only) — /plan off to leave, /plan <objective> to start on one",
      run: () => props.onPlanChange?.(true),
      disabled: props.plan === true,
      reason: "Plan is already on",
    },
    {
      // Stage a
      // message-level revert at the last user message. Disabled when no user message is
      // left visible.
      name: "undo",
      aliases: [],
      description: "Undo the last message",
      run: () => void props.onUndoSession?.(),
      disabled: !props.onUndoSession || props.canUndo === false,
      reason: "No messages to undo",
    },
    {
      // Re-stage the
      // next reverted message, or clear the marker. Disabled with nothing staged.
      name: "redo",
      aliases: [],
      description: "Redo the last undone message",
      run: () => void props.onRedoSession?.(),
      disabled: !props.onRedoSession || props.canRedo === false,
      reason: "Nothing to redo",
    },
    {
      // Search the session's roots and open the chosen
      // file in the existing RightRail viewer.
      name: "open",
      aliases: [],
      description: "Open file",
      run: () => props.onOpenFile?.(),
      disabled: !props.onOpenFile,
      reason: "Unavailable in this surface",
    },
    {
      // Toggles the session's user-shell panel (also ⌃`). The
      // panel is a second doorway onto the same shell across reopens, never a new one.
      name: "terminal",
      aliases: [],
      description: "Toggle the terminal panel",
      run: () => props.onToggleTerminal?.(),
      disabled: !props.onToggleTerminal,
      reason: "Unavailable in this surface",
    },
    {
      // The row cannot know git-ness synchronously,
      // so the gate is "has a folder at all"; the section itself says "Not a git
      // repository." when that is the truth (the visible-reason rule).
      name: "workspace",
      aliases: [],
      description: "Show this project's git worktrees",
      run: () => props.onToggleWorktrees?.(),
      disabled: !props.onToggleWorktrees || !props.workspace,
      reason: "No project folder",
    },
    {
      // Behaves
      // exactly like clicking New session.
      name: "new",
      aliases: [],
      description: "New session",
      run: () => props.onNewSession?.(),
      disabled: !props.onNewSession,
      reason: "Unavailable in this surface",
    },
    {
      // Plugins are fixed when a conversation's engine is built, so one
      // turned on or installed later reaches an open conversation only through this.
      name: "reload-plugins",
      aliases: [],
      description:
        "Load plugins changed since this conversation opened — the next reply re-reads the whole history, and commands the agent left running stop",
      run: () => props.onReloadPlugins?.(),
      disabled: !props.onReloadPlugins || props.running,
      reason: props.running ? "Wait for this turn to end" : "Unavailable in this surface",
    },
    {
      // The transcript as JSON.
      name: "export",
      aliases: [],
      description: "Export the full session transcript as JSON",
      run: () => props.onExportSession?.(),
      disabled: !props.onExportSession,
      reason: "Unavailable in this surface",
    },
    {
      // `/share`: NOT the cloud share — the harness uploads to the
      // person's configured `[share] endpoint` and the row copies the returned URL. A
      // second pick with a stored URL just copies it; the transcript is not re-uploaded.
      name: "share",
      aliases: [],
      description: "Share this session and copy the link",
      run: () => props.onShareSession?.(),
      disabled: !props.onShareSession || props.canShare === false,
      reason: props.shareReason || "Unavailable in this surface",
    },
    {
      // `/unshare`: appears only when the endpoint supports revoke — a row that could
      // not revoke anything would be a lie about the infrastructure.
      name: "unshare",
      aliases: [],
      description: "Revoke this session's share link",
      run: () => props.onUnshareSession?.(),
      disabled: !props.onUnshareSession || props.canUnshare === false,
      reason: props.unshareReason || "Not shared",
    },
    {
      // opens its model dialog;
      // ours opens the composer's own ModelPicker popover in place.
      name: "model",
      aliases: [],
      description: "Select a different model",
      run: () => {
        if (modelsLoaded) setModelPickerOpen(true);
        else props.onConnectModel?.();
      },
      disabled: !modelsLoaded && !needsModel,
      reason: "Model list is still loading",
    },
    {
      // opens a per-message
      // picker. Our fork branches the whole conversation, so no dialog; the gate is the
      // same — a session with no user messages has nothing to fork (dialog.fork.empty).
      name: "fork",
      aliases: [],
      description: "Create a new session seeded with this conversation's history",
      run: () => props.onForkSession?.(),
      disabled: !props.onForkSession || props.hasMessages === false,
      reason: props.hasMessages === false ? "No messages to fork from" : "Unavailable in this surface",
    },
    {
      // opens a per-session MCP
      // toggle dialog; ours navigates to the Connectors surface (no per-session toggling).
      name: "mcp",
      aliases: [],
      description: "Manage MCP connectors",
      run: () => props.onOpenConnectors?.(),
      disabled: !props.onOpenConnectors,
      reason: "Unavailable in this surface",
    },
  ];
  const commandRows: SlashRow[] =
    slashQuery === null || !props.sessionId
      ? []
      : [
          ...commands
            .filter(
              (c) =>
                c.name.includes(slashQuery) ||
                c.aliases.some((a) => a.includes(slashQuery)),
            )
            .map((c) => ({
              kind: "command" as const,
              name: c.name,
              description: c.description,
              run: c.run,
              disabled: c.disabled,
              reason: c.reason,
            })),
          // The prompt templates: a name already carried by a
          // builtin/skill stays with the earlier entry, like a registry
          // (`command/index.ts:135`).
          ...PROMPT_COMMANDS.filter(
            (c) =>
              c.name.includes(slashQuery) &&
              !commands.some((builtin) => builtin.name === c.name),
          ).map((c) => ({ kind: "prompt" as const, command: c })),
          // Defined commands after the app's own: a built-in name stays the built-in's.
          ...(serverCommands ?? [])
            .filter(
              (c) =>
                c.name.includes(slashQuery) &&
                !commands.some((builtin) => builtin.name === c.name) &&
                !PROMPT_COMMANDS.some((p) => p.name === c.name),
            )
            .map((c) => ({
              kind: "prompt" as const,
              command: { name: c.name, description: c.description, template: "" },
              server: c,
            })),
        ];
  // The Skills section leads the popup and ranks by use - how often the worker loaded each
  // one - then by name (GUI UX review: nine built-in commands filled the first screen and a
  // person's own skills sat below it; a bare "/" + Enter also ran /compact). While the box
  // is still a bare "/" it is a PREVIEW: the first SKILL_PREVIEW_LIMIT names, with the
  // rest one keystroke away. A non-empty query shows every match. The panel itself is
  // capped and scrolls (same fix as the model menu): with many compat skills installed the
  // uncapped list grew past the window and could not be scrolled.
  const skillRows: SlashRow[] = (slashSkills ?? [])
    .filter(
      (s) =>
        s.name.toLowerCase().includes(slashQuery ?? "") &&
        !PROMPT_COMMANDS.some((c) => c.name === s.name),
    )
    .map((s) => ({ kind: "skill" as const, skill: s }))
    .sort(
      (a, b) =>
        ((b.kind === "skill" ? b.skill.uses : 0) ?? 0) - ((a.kind === "skill" ? a.skill.uses : 0) ?? 0) ||
        rowName(a).localeCompare(rowName(b), undefined, { sensitivity: "base" }),
    );
  const visibleSkillRows =
    slashQuery === "" ? skillRows.slice(0, SKILL_PREVIEW_LIMIT) : skillRows;
  const hiddenSkillCount = skillRows.length - visibleSkillRows.length;
  const agentRows: SlashRow[] =
    slashQuery === null || !props.sessionId
      ? []
      : mentionable
          .filter((a) => a.name.toLowerCase().includes(slashQuery))
          .map((agent) => ({ kind: "agent" as const, agent }));
  const slashRows: SlashRow[] = [...visibleSkillRows, ...commandRows, ...agentRows];
  // Arrow keys step over disabled rows (a visible-but-unrunnable row must never be the
  // landing spot of a keyboard walk); wrap-around matches `moveActive`.
  const stepSlash = (dir: 1 | -1) => {
    const count = slashRows.length;
    if (count === 0) return;
    let next = slashIndex;
    for (let step = 0; step < count; step++) {
      next = (next + dir + count) % count;
      if (!rowDisabled(slashRows[next])) {
        setSlashIndex(next);
        return;
      }
    }
  };
  // A filter change can shrink the list under the highlight; land on the first row that
  // can actually run rather than on a stale index.
  useEffect(() => {
    if (slashQuery === null) return;
    setSlashIndex((current) =>
      current < slashRows.length && !rowDisabled(slashRows[current])
        ? current
        : firstEnabledIndex(slashRows),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slashQuery]);
  // Keep the keyboard highlight in the panel's viewport: the list scrolls (capped height),
  // so an arrow walk across sections must carry the selection with it — never off-screen.
  useEffect(() => {
    if (slashQuery === null) return;
    slashScrollRef.current
      ?.querySelector<HTMLElement>(`[data-slash-index="${slashIndex}"]`)
      ?.scrollIntoView?.({ block: "nearest" });
  }, [slashIndex, slashQuery]);
  // The trailing `@token` the caret is in (a token has no whitespace; a leading `@` or
  // one after whitespace counts, a mid-word `a@b` does not). A token may hold a path
  // (`@src/scenes/Hook`). "@" is context: the session's files and folders (agents are "/").
  const mentionMatch = MENTION_TOKEN.exec(text);
  const [mentionClosed, setMentionClosed] = useState(false);
  // What was typed after "@", as typed: a path keeps its case.
  const mentionTyped = mentionMatch !== null && !mentionClosed ? mentionMatch[2] : null;
  const mentionQuery = mentionTyped === null ? null : mentionTyped.toLowerCase();
  // The session's files, read once each time the menu opens and filtered here as it is typed
  // (owner, 2026-10-13: it asked the server for a search per keystroke and was not live). A
  // session on a remote host answers why it cannot list them.
  const [fileIndex, setFileIndex] = useState<{ files: string[]; error?: string } | null>(null);
  const mentionSession = props.sessionId;
  const mentionOpen = mentionQuery !== null;
  useEffect(() => {
    setFileIndex(null);
  }, [mentionSession]);
  useEffect(() => {
    if (!mentionOpen || !mentionSession) return;
    let current = true;
    sessionFileIndex(mentionSession)
      .then((found) => {
        if (!current) return;
        setFileIndex(found.ok ? { files: found.files } : { files: [], error: found.error || "Files cannot be listed here." });
      })
      .catch(() => {
        if (current) setFileIndex((previous) => previous ?? { files: [] });
      });
    return () => {
      current = false;
    };
  }, [mentionOpen, mentionSession]);
  const mentionRows = useMemo<AtRow[]>(
    () => (mentionTyped === null ? [] : atMenuRows(fileIndex?.files ?? [], mentionTyped, props.touchedFiles ?? [])),
    [mentionTyped, fileIndex, props.touchedFiles],
  );
  const [mentionIndex, setMentionIndex] = useState(0);
  useEffect(() => {
    setMentionIndex(0);
  }, [mentionQuery]);
  const mentionScrollRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (mentionQuery === null) return;
    mentionScrollRef.current
      ?.querySelector<HTMLElement>(`[data-at-index="${mentionIndex}"]`)
      ?.scrollIntoView?.({ block: "nearest" });
  }, [mentionIndex, mentionQuery]);
  // An agent picked from "/": `@name` goes into the draft, and rides the send as a mention.
  const pickMention = (name: string) => {
    setText((cur) => {
      const replaced = cur.replace(MENTION_TOKEN, (_match, lead: string) => `${lead}@${name} `);
      if (replaced !== cur) return replaced;
      if (!cur) return `@${name} `;
      return `${cur}${/\s$/.test(cur) ? "" : " "}@${name} `;
    });
    textareaRef.current?.focus();
  };
  // A file or folder picked from "@" is pointed at, not copied: `@docs/API.md` stays in the
  // sentence, and the agent reads it where it is when the message is sent, as it is then. It
  // used to be read into the message at the pick, cut at a size limit, and stale if the file
  // changed before the send. `into` opens a folder in the list instead of adding it.
  const pointAt = (path: string, into = false) => {
    setText((cur) => cur.replace(MENTION_TOKEN, (_match, lead: string) => `${lead}@${path}${into ? "" : " "}`));
    textareaRef.current?.focus();
  };
  // A folder row opens the folder, as Enter does in a file picker: adding it with a space closed the
  // list, and the person had to delete the space to see what was inside (owner, 2026-10-21). The
  // folder is added whole from its "Add all of" row, at the top of the list inside it.
  const opensFolder = (row: AtRow) => row.kind === "dir" || (row.kind === "touched" && row.path.endsWith("/"));
  const pickMentionRow = (row: AtRow) => pointAt(row.path, opensFolder(row));
  const pointers = useMemo(() => pointersIn(text, fileIndex?.files ?? []), [text, fileIndex]);
  const dropPointer = (path: string) =>
    setText((cur) =>
      cur
        .split(/(\s+)/)
        .filter((part) => part !== `@${path}`)
        .join("")
        .replace(/\s{2,}/g, " ")
        .trim(),
    );
  // The mentions a draft actually carries: every `@name` token that names a suggested
  // agent, deduplicated, in reading order. Re-derived on send rather than remembered,
  // so deleting the token really un-mentions.
  const mentionsIn = (value: string): string[] => {
    const known = new Map(mentionable.map((a) => [a.name.toLowerCase(), a.name]));
    const out: string[] = [];
    for (const match of value.matchAll(/(^|\s)@([A-Za-z0-9._-]+)/g)) {
      const name = known.get(match[2].toLowerCase());
      if (name && !out.includes(name)) out.push(name);
    }
    return out;
  };
  const activeMentions = useMemo(() => mentionsIn(text), [text, mentionable]);
  const dropMention = (name: string) => {
    setText((cur) =>
      cur
        .replace(new RegExp(`(^|\\s)@${name}(?=\\s|$)`, "g"), "$1")
        .replace(/\s{2,}/g, " ")
        .trim(),
    );
    textareaRef.current?.focus();
  };

  // The box grows with its text from two lines to six, then scrolls. Two, because one line read
  // as a search bar (owner, 2026-09-10); six, because team briefs outgrew four. The limits count
  // the padding, as scrollHeight does: without it the box stopped short and scrolled its first
  // line out of sight while typing.
  useLayoutEffect(() => {
    const box = textareaRef.current;
    if (!box) return;
    box.style.height = "auto";
    const style = getComputedStyle(box);
    const padding = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
    const line = parseFloat(style.lineHeight) || 22;
    const tallest = line * 6 + padding;
    box.style.height = `${Math.max(Math.min(box.scrollHeight, tallest), line * 2 + padding)}px`;
    box.style.overflowY = box.scrollHeight > tallest ? "auto" : "hidden";
  }, [text]);

  // A draft belongs to the conversation it was typed in. Leaving one keeps what was in the box
  // (text, files, a picked skill) under that conversation, and opening one puts back what was
  // left there — so glancing at another conversation never costs a half-written message, and
  // one conversation's draft never bleeds into another. Declared BEFORE the prefill effect:
  // when both fire in the same render (the Skills doorway starts a new session AND prefills
  // it), effects run in declaration order — restore first, then the prefill lands on top.
  const draftNow = useRef<ComposerDraft>({ text: "", attachments: [], pendingSkill: null });
  draftNow.current = { text, attachments, pendingSkill };
  const draftKey = useRef(props.resetKey);
  // Whether the conversation the box was typed in was a stand-in, read as of the render before a
  // change: React may hand over the real conversation and end "provisional" in one render.
  const wasProvisional = useRef(!!props.provisional);
  useEffect(() => {
    const left = draftKey.current;
    draftKey.current = props.resetKey;
    // Leaving a stand-in (Reeve opening the last conversation while the person typed): what was
    // typed goes with the box. It is the opened conversation's own draft only if the box is empty.
    const carried = wasProvisional.current && left !== props.resetKey;
    if (carried) {
      const kept = props.resetKey === undefined ? undefined : takeDraft(props.resetKey);
      const typed = draftNow.current;
      const empty = !typed.text.trim() && typed.attachments.length === 0 && !typed.pendingSkill;
      if (empty && kept) {
        setText(kept.text);
        setAttachments(kept.attachments);
        setPendingSkill(kept.pendingSkill);
      }
      return;
    }
    if (left !== undefined && left !== props.resetKey) rememberDraft(left, draftNow.current);
    const kept = props.resetKey === undefined ? undefined : takeDraft(props.resetKey);
    setText(kept?.text ?? "");
    setAttachments(kept?.attachments ?? []);
    setPendingSkill(kept?.pendingSkill ?? null);
    setModelPickerOpen(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.resetKey]);
  useEffect(() => {
    wasProvisional.current = !!props.provisional;
  });
  // While Reeve starts, only the words and the send are the person's: the mode, the model and the
  // rest of the row belong to the conversation that opens, which sets them when it does (a mode
  // picked before it opened was undone by it). Every render, so a control drawn later is held too.
  useEffect(() => {
    const row = wrapRef.current?.querySelectorAll(".composer-footer > :not(.composer-send-slot)") ?? [];
    for (const el of Array.from(row)) {
      if (props.provisional) el.setAttribute("inert", "");
      else el.removeAttribute("inert");
    }
  });

  // A prefill goes in once for its nonce (a second render, or StrictMode's, does not apply it
  // again), its files merged so none lands twice, and the box takes the focus.
  const appliedNonce = useRef<number>(-1);
  useEffect(() => {
    const fill = props.prefill;
    if (!fill || fill.nonce === appliedNonce.current) return;
    appliedNonce.current = fill.nonce;
    setText((cur) => (fill.keepText && cur.trim() ? cur : fill.text));
    if (fill.attachments?.length) setAttachments((cur) => mergeAttachments(cur, fill.attachments!));
    textareaRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.prefill?.nonce]);

  // Files picked, dropped or pasted: each is read, or a card says why not (a PDF over the person's
  // limits, a file that could not be read); none is dropped without a word.
  const addFiles = async (files: FileList | File[]) => {
    const list = Array.from(files);
    const limits = await pdfLimitsFor(list);
    const accepted = list.filter((file) => {
      const why = overSize(file, limits);
      if (why) showAttachNotice(why);
      return !why;
    });
    setReading((cur) => [...cur, ...accepted.map((f) => f.name || "file")]);
    const outcomes = await Promise.all(accepted.map(readFileChecked)).finally(() =>
      setReading((cur) => {
        const left = cur.slice();
        for (const f of accepted) {
          const at = left.indexOf(f.name || "file");
          if (at >= 0) left.splice(at, 1);
        }
        return left;
      }),
    );
    const kept: Attachment[] = [];
    for (const outcome of outcomes) {
      if (!outcome.ok) {
        showAttachNotice(outcome.reason);
        continue;
      }
      const why = await overPages(outcome.attachment, limits);
      if (why) showAttachNotice(why);
      else kept.push(outcome.attachment);
    }
    if (kept.length) addAttachments(kept);
  };

  // Every way in shares the per-message limit, and going over it is said aloud.
  const addAttachments = (add: Attachment[]) => {
    // The picker said what this model takes; a file it cannot read is called out now, while another
    // model can still be chosen.
    void unreadableNotices(props.model, add).then((notices) => {
      if (notices.length) showAttachNotice(notices.join(" "));
    });
    setAttachments((cur) => {
      const { merged, dropped } = mergeCapped(cur, add);
      if (dropped > 0) {
        showAttachNotice(
          `${dropped} file${dropped === 1 ? "" : "s"} not added — a message carries at most ${MAX_ATTACHMENTS} files and about ${Math.round(MAX_PAYLOAD_CHARS / 1.37 / 1024 / 1024)} MB in all`,
        );
      }
      return merged;
    });
  };

  /**
   * Attach a file that already lives in the session's workspace.
   *
   * The rail's file tree hands us a PATH, not a File — the bytes are on the server, and the
   * honest way in is the artifact reader: text arrives as text, an image/PDF as a data URL.
   * A binary the reader cannot render is refused with a notice rather than attached empty.
   */
  const addArtifactPath = async (path: string, named?: string) => {
    if (!props.sessionId || !path) return;
    const name = named || path.split(/[\\/]/).pop() || path;
    try {
      const file = await readArtifact(props.sessionId, path);
      if (!file.ok) {
        showAttachNotice(`${name} skipped — ${file.error || "could not read it"}`);
        return;
      }
      if (typeof file.content === "string" && file.kind !== "image") {
        addAttachments([{ kind: "text", name, text: file.content! }]);
        if (file.truncated) {
          showAttachNotice(`${name} was read only up to the size limit`);
        }
        return;
      }
      if (file.data_url) {
        const kind = file.kind === "image" ? "image" : "pdf";
        addAttachments([{ kind, name, data_url: file.data_url }]);
        return;
      }
      showAttachNotice(`${name} skipped — nothing here can attach that kind of file`);
    } catch {
      showAttachNotice(`${name} skipped — could not read it`);
    }
  };
  // The rail's tree dispatches this on drop; the event carries the path only, so the
  // composer stays the one place that knows how to shape an attachment.
  useEffect(() => {
    const onAttach = (event: Event) => {
      const path = String((event as CustomEvent).detail?.path || "");
      if (path) void addArtifactPath(path);
    };
    window.addEventListener("reeve-attach-file", onAttach);
    return () => window.removeEventListener("reeve-attach-file", onAttach);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.sessionId]);

  // Another surface hands the person words to send (a terminal's "Ask Reeve about this"): they
  // go into the box after what is already typed, and the person reads, edits and sends them.
  // Nothing is sent on their behalf.
  useEffect(() => {
    const onInsert = (event: Event) => {
      const words = String((event as CustomEvent).detail?.text || "");
      if (!words) return;
      setText((current) => (current.trim() ? `${current.replace(/\s+$/, "")}\n\n${words}` : words));
      window.requestAnimationFrame(() => {
        const box = textareaRef.current;
        if (!box) return;
        box.focus();
        box.setSelectionRange(box.value.length, box.value.length);
      });
    };
    window.addEventListener("reeve-insert-text", onInsert);
    return () => window.removeEventListener("reeve-insert-text", onInsert);
  }, []);

  // The system's file window, from the "+" menu, filtered to what can be attached.
  const pickFiles = (accept: string) => {
    setAttachMenuOpen(false);
    const input = fileInput.current;
    if (!input) return;
    input.accept = accept;
    input.click();
  };

  const submit = () => {
    // With the "/" menu open the text is a search in it, not a message.
    if (slashQuery !== null) return;
    // A file still being read is part of this message: it goes when it is read, not without it.
    if (reading.length > 0) return;
    // The visible "/name " prefix is UI state, not message text — strip it for the send;
    // the skill rides as its own field.
    const skill = prefixIntact ? pendingSkill!.name : undefined;
    let t = (skill ? text.slice(skill.length + 1) : text).trim();
    // `/goal …` is a command, not a message: the parser owns its
    // grammar, it runs in place, and nothing is sent. A bare `/goal` picked from the
    // popover already ran the status; this path catches the typed form with arguments.
    const goalInput = goalCommandInput(t);
    if (goalInput !== null) {
      setText("");
      setPendingSkill(null);
      void runGoal(goalInput);
      return;
    }
    // `/plan`: bare turns the Plan overlay on, `off` turns it off,
    // and any other argument is the objective — the overlay turns on AND the text rides out
    // as a normal message. A command, not a message: `/plan …` never reaches the model.
    const planMatch = /^\/plan(?:\s+([\s\S]*))?$/.exec(t);
    if (planMatch !== null) {
      const objective = (planMatch[1] ?? "").trim();
      setText("");
      setPendingSkill(null);
      if (objective === "off") {
        props.onPlanChange?.(false);
        return;
      }
      props.onPlanChange?.(true);
      if (objective === "") return;
      t = objective;
    }
    // `/init …` / `/review …` are server commands: the draft inserted by
    // the popup row IS the command, and submitting expands the template with the typed
    // arguments before it rides out as one normal message (promptCommands.ts).
    // What the message was sent with, for the transcript to draw: the model reads
    // the expanded text, the person sees the command and what they typed after it.
    let command: SentCommand | undefined = skill
      ? {
          kind: "skill",
          name: skill,
          ...(t ? { args: t } : {}),
          ...(pendingSkill?.description ? { description: pendingSkill.description } : {}),
        }
      : undefined;
    const promptInput = promptCommandInput(t);
    if (promptInput !== null) {
      command = {
        kind: "prompt",
        name: promptInput.command.name,
        ...(promptInput.args ? { args: promptInput.args } : {}),
        description: promptInput.command.description,
      };
      t = resolvePromptTemplate(promptInput.command.template, promptInput.args);
    }
    // A defined command expands the same way, from its fetched body. Typed
    // without the popup, its body is not here yet: fetch it, then submit again — the
    // draft is still in the box.
    const defined = /^\/(\S+)(?:\s+([\s\S]*))?$/.exec(t);
    if (promptInput === null && defined !== null && knownCommands.current.has(defined[1])) {
      const body = commandBodies.current[defined[1]];
      if (body === undefined) {
        const name = defined[1];
        void getCommand(name, { session: props.sessionId, workspace: props.workspace }).then((c) => {
          if (!c) return;
          commandBodies.current[name] = c.body;
          submitRef.current();
        });
        return;
      }
      const args = (defined[2] ?? "").trim();
      const row = serverCommands?.find((c) => c.name === defined[1]);
      command = {
        kind: "prompt",
        name: defined[1],
        ...(args ? { args } : {}),
        ...(row?.description ? { description: row.description } : {}),
        ...(row ? { source: SERVER_SOURCE_WORDS[row.source] ?? row.source } : {}),
      };
      t = resolvePromptTemplate(body, args);
    }
    if ((!t && attachments.length === 0 && !skill) || dictation?.recording || dictationBusy) return;
    // With no model to answer, the draft stays and the send goes to setting one up.
    if (needsModel) return void props.onConnectModel?.();
    // The @agent names the DRAFT carries (re-scanned, so an edited-away token is gone
    // and the bubble stays the literal text the person typed).
    const mentions = mentionsIn(t);
    // Mid-turn, the engine is not reading input — but the sentence is still wanted. It goes
    // to the queue instead of being refused; the surface sends it when the turn ends. Without
    // a queue handler (tests, surfaces that do not offer one) the old refusal stands.
    // The mentions field is appended ONLY when the draft carries one, so the call arity
    // stays (text, attachments, skill) for every old caller.
    if (props.running || props.holding) {
      if (props.onQueue === undefined) return;
      if (command) props.onQueue(t, attachments, skill, mentions.length ? mentions : undefined, command);
      else if (mentions.length) props.onQueue(t, attachments, skill, mentions);
      else props.onQueue(t, attachments, skill);
    } else {
      sentFrom.current = props.sessionId ?? "";
      if (command) props.onSend(t, attachments, skill, mentions.length ? mentions : undefined, command);
      else if (mentions.length) props.onSend(t, attachments, skill, mentions);
      else props.onSend(t, attachments, skill);
    }
    setText("");
    setAttachments([]);
    setRefused([]);
    setPendingSkill(null);
  };

  const submitRef = useRef(submit);
  submitRef.current = submit;

  // Steering is offered only where the server accepts it: plain text, no
  // attachments, no pinned skill, no @mentions (those frame a NEW turn, or carry
  // turn-scoped bypasses, and cannot ride a splice). The Steer button and the
  // Ctrl/Cmd+Enter shortcut share this one gate, so neither can promise a splice the
  // server would refuse.
  const steerVisible =
    props.running &&
    props.onSteer !== undefined &&
    text.trim().length > 0 &&
    attachments.length === 0 &&
    !pendingSkill &&
    activeMentions.length === 0;

  // Send the draft into the RUNNING turn instead of parking it. The
  // composer clears it like a send; the bubble appears when the server acks
  // `steering_queued`, so a refused steer leaves no ghost message on screen.
  const submitSteer = () => {
    const steer = props.onSteer;
    if (!steerVisible || steer === undefined) return;
    steer(text.trim());
    setText("");
  };

  // The keys of the "/" menu while it is open: the arrows walk it, Escape empties the box (the
  // menu is the text), and Enter picks the row the keyboard is on.
  const slashKey = (e: React.KeyboardEvent): boolean => {
    const act: Record<string, () => void> = {
      ArrowDown: () => stepSlash(1),
      ArrowUp: () => stepSlash(-1),
      Escape: () => setText(""),
      Enter: () => {
        const chosen = slashRows[slashIndex];
        if (chosen && !rowDisabled(chosen)) pickRow(chosen);
      },
    };
    const run = act[e.key];
    if (!run || (e.key === "Enter" && e.shiftKey)) return false;
    e.preventDefault();
    run();
    return true;
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (slashQuery !== null && slashKey(e)) return;
    // The "@" popup owns the keys while it is open — Enter commits a mention, never sends
    // a half-typed `@`; Escape closes it and leaves the token as plain text.
    if (mentionQuery !== null && mentionRows.length > 0) {
      // A folder opens to the right and closes to the left, as in a file tree.
      const active = mentionRows[mentionIndex];
      if (e.key === "ArrowRight" && active && opensFolder(active)) {
        e.preventDefault();
        pointAt(active.path, true);
        return;
      }
      if (e.key === "ArrowLeft" && mentionTyped?.endsWith("/")) {
        e.preventDefault();
        pointAt(folderOfPath(mentionTyped), true);
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMentionIndex((i) => Math.min(i + 1, mentionRows.length - 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMentionIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMentionClosed(true);
        return;
      }
      if ((e.key === "Enter" && !e.shiftKey) || e.key === "Tab") {
        e.preventDefault();
        const chosen = mentionRows[mentionIndex];
        if (chosen) pickMentionRow(chosen);
        return;
      }
    }
    // Ctrl/Cmd+Enter steers the draft into the running turn — the Queue BUTTON retired,
    // so Enter keeps one meaning (park for later) and the modifier is the fast path to
    // the other one. Only where steer is legal; an ineligible draft falls through to
    // the ordinary Enter path, which queues it.
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && steerVisible) {
      e.preventDefault();
      submitSteer();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  // A paste that brings files attaches them instead of putting anything in the text.
  const onPaste = (e: React.ClipboardEvent) => {
    const files = pastedFiles(e.clipboardData.items);
    if (!files.length) return;
    e.preventDefault();
    void addFiles(files);
  };

  const iconBtn = "w-6 h-6 p-0.5 grid place-items-center rounded-md text-muted hover:text-ink shrink-0";

  // Send wears the accent only when there is something to send, so the composer does not carry a
  // coloured dot at all times. A picked skill is something to send on its own: with the arrow grey
  // after a pick, the composer read as stuck (tester, 2026-07-26).
  const hasContent = text.trim().length > 0 || attachments.length > 0 || !!pendingSkill;
  // A command at the start of the box (a skill picked, `/init`, a command a project defines) is
  // highlighted where it stands, with its line beside it until something is typed after it.
  const marked = useMemo(() => {
    const match = /^\/(\S+)(?:\s|$)/.exec(text);
    if (!match || slashQuery !== null) return null;
    const name = match[1];
    if (prefixIntact && pendingSkill?.name === name) return { name, description: pendingSkill.description };
    const prompt = promptCommandFor(name);
    if (prompt) return { name, description: prompt.description };
    const defined = serverCommands?.find((c) => c.name === name);
    return defined ? { name, description: defined.description } : null;
  }, [text, slashQuery, prefixIntact, pendingSkill, serverCommands]);
  // Mid-turn, the Steer control shares the input's bottom-right corner with Stop — so
  // the textarea reserves the wider gutter while it is up (the circle never moves).
  // Queue has no button: Enter/submit IS the queue path, exactly as it always was.

  // One row of the "/" popup, shared by both sections so the badge/disabled/reason rules
  // cannot drift between Commands and Skills. `i` is the flat visible index the keyboard
  // walk uses; data-slash-index is what the auto-scroll effect looks up.
  const renderSlashRow = (row: SlashRow, i: number) => {
    const disabled = rowDisabled(row);
    const on = i === slashIndex;
    // Where it comes from, in a quiet word: a skill's scope, a defined command's source.
    const where =
      row.kind === "skill"
        ? SKILL_SCOPE_WORDS[row.skill.scope] ?? row.skill.scope
        : row.kind === "prompt"
          ? row.server
            ? SERVER_SOURCE_WORDS[row.server.source] ?? row.server.source
            : "template"
          : "";
    return (
      <button
        key={`${row.kind}-${rowName(row)}`}
        role="option"
        aria-selected={on}
        aria-disabled={disabled}
        disabled={disabled}
        data-slash-index={i}
        data-testid={
          row.kind === "skill"
            ? `slash-skill-${rowName(row)}`
            : row.kind === "agent"
              ? `slash-agent-${rowName(row)}`
              : `slash-command-${rowName(row)}`
        }
        className={"menu-row slash-row" + (on ? " on" : "") + (disabled ? " off" : "")}
        onMouseEnter={() => {
          if (!disabled) setSlashIndex(i);
        }}
        onClick={() => pickRow(row)}
      >
        <span className="menu-row-name slash-row-name">
          /<Marked name={rowName(row)} typed={slashQuery ?? ""} />
        </span>
        <span className="menu-row-desc">{rowDescription(row)}</span>
        <span className="menu-row-where">{where}</span>
        {/* The row the keyboard is on says what Enter will do with it. */}
        {on && !disabled && <span className="slash-row-does">{slashDoes(row)}</span>}
      </button>
    );
  };

  return (
    <div className="composer-wrap pb-5 pt-4" ref={wrapRef} data-provisional={props.provisional ? "" : undefined}>

      {/* Why the microphone did not work. */}
      {dictationError && (
        <div className="max-w-[var(--thread-w)] mx-auto mb-2 px-1 text-[12px] text-red-600" role="alert">
          {dictationError}
        </div>
      )}

      {/* The card takes files dropped on it: from the computer, or from the panel's file tree. */}
      <div
        className={"composer relative max-w-[var(--thread-w)] mx-auto border border-line bg-panel" + (dragging ? " dragging" : "")}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
          // How many are coming, said on the box; a browser may hide the count until the drop.
          setDragCount(e.dataTransfer.items?.length ?? 0);
        }}
        onDragLeave={(e) => {
          // Leaving the box for one of its own children is not leaving it.
          if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
          setDragging(false);
        }}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (e.dataTransfer.files.length) return void addFiles(e.dataTransfer.files);
          // From the tree, a path in the folder rather than a file of the browser's.
          const path = e.dataTransfer.getData("application/x-reeve-file") || e.dataTransfer.getData("text/plain");
          if (path && !path.includes("\n")) void addArtifactPath(path.trim());
        }}
      >
        {dragging && (
          <div className="composer-drop" data-testid="composer-drop" aria-hidden>
            <Icon name="download" size={18} />
            <span className="composer-drop-title">
              {dragCount > 1 ? `Drop to attach ${dragCount} files` : "Drop to attach"}
            </span>
            <span className="composer-drop-sub">Pictures, PDFs, text and code</span>
          </div>
        )}
        {/* The rows of the card above the text: what is queued, each with a way to hand it to
            the running turn now and a way to take it back. (A lost connection is said by the
            line above the composer, `ConnectionNote`, which also offers Try again.) */}
        <QueuedPrompts
          items={props.queued ?? []}
          when={props.holding && !props.running ? `when ${props.holdFor ?? "compacting is done"}` : undefined}
          onRemove={props.onUnqueue}
          onSendNow={
            props.running && props.onSteer !== undefined
              ? (item) => {
                  props.onSteer?.(item.text);
                  props.onUnqueue?.(item.id);
                }
              : undefined
          }
        />
        {/* "@" popup: floating above the card like the "/" list (it was in the card's flow and
            pushed the message box down). What the conversation touched, then the folder; a
            folder opens with → and adds with Enter. */}
        {mentionQuery !== null && props.sessionId && (mentionRows.length > 0 || fileIndex === null || fileIndex.error) && (
          <div
            className="composer-pop at-pop absolute z-40 bottom-full left-0 right-0 mb-2 flex [--pop-cap:340px] flex-col rounded-xl border border-line bg-panel shadow-2xl"
            data-testid="mention-popup"
            role="listbox"
            aria-label="Files and folders"
          >
            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1.5" ref={mentionScrollRef}>
              {fileIndex === null && <div className="menu-note">Reading the folder…</div>}
              {fileIndex?.error && (
                <div className="menu-note" data-testid="mention-remote">
                  Files on the remote host cannot be listed here yet. Type a path and the agent will read it there.
                </div>
              )}
              {mentionRows.map((row, i) => {
                const heading =
                  row.kind === "touched"
                    ? "In this conversation"
                    : mentionTyped === ""
                      ? props.workspace
                        ? props.workspace.split(/[\\/]/).filter(Boolean).pop() || "This folder"
                        : "This folder"
                      : mentionTyped?.endsWith("/") && row.kind !== "here"
                        ? null
                        : row.kind === "here"
                          ? mentionTyped
                          : "Files and folders";
                const previous = mentionRows[i - 1];
                const showHeading =
                  heading !== null &&
                  (i === 0 || (previous?.kind === "touched") !== (row.kind === "touched") || previous?.kind === "here");
                const isDir = row.kind === "dir" || row.kind === "here" || row.path.endsWith("/");
                const name = row.kind === "here" ? `Add all of ${row.path}` : nameOfPath(row.path);
                const where = row.kind === "here" ? "" : folderOfPath(row.path);
                return (
                  <Fragment key={`${row.kind}-${row.path}`}>
                    {showHeading && heading && (
                      <div className="menu-head" data-testid={`mention-section-${row.kind === "touched" ? "touched" : "files"}`}>
                        {heading}
                      </div>
                    )}
                    <button
                      role="option"
                      aria-selected={i === mentionIndex}
                      data-at-index={i}
                      data-testid={isDir ? "mention-dir" : "mention-file"}
                      data-path={row.path}
                      title={row.path}
                      className={"menu-row at-row" + (i === mentionIndex ? " on" : "")}
                      onMouseEnter={() => setMentionIndex(i)}
                      onClick={() => pickMentionRow(row)}
                    >
                      {/* The row the keyboard is on wears the file's own coloured logo. */}
                      {i === mentionIndex ? (
                        <FileIcon path={row.path} type={isDir ? "directory" : "file"} size={14} />
                      ) : (
                        <Icon name={isDir ? "folder" : "file"} size={14} className="menu-row-ico" />
                      )}
                      <span className="menu-row-name">
                        {row.kind === "here" ? name : <Marked name={name} typed={mentionTyped?.split("/").pop() ?? ""} />}
                      </span>
                      <span className="menu-row-desc">{where}</span>
                      <span className="menu-row-where">
                        {row.kind === "touched"
                          ? row.what
                          : row.kind === "dir" || row.kind === "here"
                            ? `${row.count} file${row.count === 1 ? "" : "s"}`
                            : ""}
                      </span>
                    </button>
                  </Fragment>
                );
              })}
            </div>
            <div className="menu-keys" aria-hidden>
              <span>↑ ↓ move</span>
              <span>Enter open a folder, add a file</span>
              <span>Esc close</span>
            </div>
          </div>
        )}
        {/* "/" popup — anchored ABOVE the card (never in flow, so the page layout and the
            last transcript lines stay put), height-capped with its own scroller. Built-in
            commands first (they RUN on pick), then the prompt templates (`/init`, `/review`
            — they INSERT and resolve on submit), then the session's effective skill menu
            (muted skills never appear). While the box is a bare "/" the Skills section is a
            preview; the rest are one keystroke away. A row that cannot run is VISIBLY
            disabled and says why (the reference design hides those; the visible reason is our rule). */}
        {slashQuery !== null && (
          <div
            className="composer-pop absolute z-40 bottom-full left-0 right-0 mb-2 flex [--pop-cap:340px] flex-col rounded-xl border border-line bg-panel shadow-2xl"
            data-testid="skill-popup"
            role="listbox"
            aria-label="Commands and skills"
          >
            <div
              className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1.5"
              data-testid="skill-popup-scroll"
              ref={slashScrollRef}
            >
              {slashSkills === null && (
                <div className="px-2.5 py-1.5 text-[12px] text-faint">Loading skills…</div>
              )}
              {slashSkills !== null && visibleSkillRows.length > 0 && (
                <>
                  <div className="menu-head">
                    <span>Skills</span>
                    <span className="menu-head-count">
                      {hiddenSkillCount > 0
                        ? `${visibleSkillRows.length} of ${skillRows.length}, most used first`
                        : skillRows.length}
                    </span>
                  </div>
                  {visibleSkillRows.map((row, j) => renderSlashRow(row, j))}
                  {hiddenSkillCount > 0 && (
                    <div className="menu-note" data-testid="skill-popup-more">
                      {hiddenSkillCount} more skill{hiddenSkillCount === 1 ? "" : "s"}. Keep typing to find one.
                    </div>
                  )}
                </>
              )}
              {commandRows.length > 0 && (
                <>
                  <div className="menu-head">
                    <span>Commands</span>
                    <span className="menu-head-count">{commandRows.length}</span>
                  </div>
                  {commandRows.map((row, i) => renderSlashRow(row, visibleSkillRows.length + i))}
                </>
              )}
              {agentRows.length > 0 && (
                <>
                  <div className="menu-head" data-testid="slash-section-agents">
                    <span>Agents</span>
                    <span className="menu-head-count">hand this message to one</span>
                  </div>
                  {agentRows.map((row, i) => renderSlashRow(row, visibleSkillRows.length + commandRows.length + i))}
                </>
              )}
              {slashSkills !== null && slashRows.length === 0 && (
                <div className="menu-note">Nothing is called “/{slashQuery}”.</div>
              )}
            </div>
            <div className="menu-keys" aria-hidden>
              <span>↑ ↓ move</span>
              <span>Enter choose</span>
              <span>Esc close</span>
            </div>
          </div>
        )}
        {/* Mention tokens the draft carries — the "highlighted token" stand-in for a
            rich-text part: each detected `@name` is shown and removable, so what will
            ride the send is visible before it goes. */}
        {pointers.length > 0 && (
          <div className="composer-attach" data-testid="composer-pointers">
            {pointers.map((path) => (
              <span key={`pointer-${path}`} className="pointer-chip" data-testid={`pointer-chip-${path}`} title={`The agent reads ${path} where it is when you send`}>
                <Icon name={path.endsWith("/") ? "folder" : "file"} size={12} />
                {nameOfPath(path)}
                {folderOfPath(path) && <span className="pointer-chip-where">{folderOfPath(path)}</span>}
                <button
                  className="pointer-chip-x"
                  title={`Remove ${path}`}
                  aria-label={`Remove ${path}`}
                  onClick={() => dropPointer(path)}
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
        )}
        {activeMentions.length > 0 && (
          <div className="composer-attach" data-testid="composer-mentions">
            {activeMentions.map((name) => (
              <span
                key={`mention-chip-${name}`}
                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[12px] bg-accentSoft text-accent"
                data-testid={`mention-chip-${name}`}
              >
                @{name}
                <button
                  className="opacity-60 hover:opacity-100"
                  title={`Remove @${name}`}
                  aria-label={`Remove @${name}`}
                  onClick={() => dropMention(name)}
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
        )}
        {/* Attachments preview — INSIDE the card, above the text. Pasted images used to sit
            in a strip outside the frame with the remove ✕ hanging off the chip corner, which
            read as a broken thumbnail floating over the composer; framed inside, the
            attachment is unambiguously part of the message being written. */}
        {(attachments.length > 0 || reading.length > 0 || refused.length > 0) && (
          <div className="composer-attach" data-testid="composer-attachments">
            {/* What will not go leads: it is the card that asks for something. */}
            {refused.map((r) => (
              <AttachmentCard
                key={`refused-${r.name}`}
                a={{ kind: "file", name: r.name }}
                state="refused"
                reason={r.why}
                onRemove={() => setRefused((cur) => cur.filter((x) => x.name !== r.name))}
              />
            ))}
            {attachments.map((a, i) => {
              const remove = () => setAttachments((all) => all.filter((_, j) => j !== i));
              return a.kind === "image" && a.data_url ? (
                <AttachmentTile key={i} a={a} className="small" onOpen={() => setViewing(i)} onRemove={remove} />
              ) : (
                <AttachmentCard key={i} a={a} onRemove={remove} />
              );
            })}
            {reading.map((name, i) => (
              <AttachmentCard key={`reading-${i}`} a={{ kind: "file", name }} state="reading" />
            ))}
          </div>
        )}
        {viewing !== null && attachments[viewing]?.data_url && (
          <ImageOverlay
            images={[{ src: attachments[viewing].data_url!, alt: attachments[viewing].name }]}
            start={0}
            onClose={() => setViewing(null)}
          />
        )}
        {/* An attachment that was refused (a PDF over the Token-savings limits) is said
            here, with the attachments it would have joined, not in a banner over the card. */}
        {attachNotice && (
          <div className="composer-attach-error" role="alert" data-testid="attach-notice">
            <span className="composer-row-text">{attachNotice}</span>
            <button type="button" className="queued-prompt-x" aria-label="Dismiss" title="Dismiss" onClick={() => setAttachNotice(null)}>
              <Icon name="x" size={12} />
            </button>
          </div>
        )}
        {/* Pending line comments ride the next message as comment notes. A chip
            per comment keeps the pending set visible (and removable) right where it leaves. */}
        {!!props.comments?.length && (
          <div className="composer-comments" data-testid="composer-comments">
            {props.comments.map((c) => (
              <CommentChip
                key={c.id}
                comment={c}
                onRemove={props.onRemoveComment}
                onEdit={props.onEditComment}
              />
            ))}
          </div>
        )}
        {/* The input zone: the text owns the top of the card, edge to edge. Send and Stop
            are at the end of the row of controls under it (2026-10-12), not in its corner. */}
        <div className="composer-input">
          <textarea
            ref={textareaRef}
            aria-label="Message"
            className={
              "w-full block px-4 pt-3.5 pb-1.5"
            }
            placeholder={
              props.running && props.onQueue !== undefined
                  ? props.onSteer !== undefined
                    ? "Enter queues it, Ctrl+Enter sends it now"
                    : "Enter queues it for after this turn"
                  : props.holding && props.onQueue !== undefined
                    ? `Enter queues it until ${props.holdFor ?? "compacting is done"}`
                    : props.placeholder || "Ask anything, / for commands, @ for files"
            }
            value={text}
            onChange={(e) => {
              // Editing the draft re-arms the "@" popup a previous Escape dismissed.
              setMentionClosed(false);
              setText(e.target.value);
            }}
            onKeyDown={onKey}
            onPaste={onPaste}
            onScroll={(e) => setInputScroll(e.currentTarget.scrollTop)}
            rows={1}
          />
          {marked && (
            <CommandMark
              textarea={textareaRef.current}
              name={marked.name}
              hint={text.trim() === `/${marked.name}` ? marked.description : undefined}
              scrollTop={inputScroll}
            />
          )}

        </div>

        {/* The row of controls. Left: what is added and what is allowed (attach,
            permissions). Right: the model, the context ring, the mic, and Send or Stop. */}
        <div className="composer-footer">
          {/* "+": what can go with the message, and what is set for the whole session. */}
          <div className="relative">
            <button className={iconBtn + (attachMenuOpen ? " bg-paper text-ink" : "")} title="Attach" aria-label="Attach" aria-haspopup="menu" aria-expanded={attachMenuOpen} onClick={() => setAttachMenuOpen(!attachMenuOpen)}>
              <Icon name="plus" size={15} />
            </button>
            {attachMenuOpen && (
              <>
                <div
                  className="fixed inset-0 z-30"
                  onClick={() => {
                    setAttachMenuOpen(false);
                    setBindMenu(null);
                  }}
                />
                {/* One door for files (it was three, "Photo or image", "PDF" and "Other files",
                    each the same file window with a filter: the composer works the kind out
                    itself), then the two lists "@" and "/" open, then what is set for the whole
                    session (owner, 2026-10-13). */}
                <div className="composer-pop plus-menu absolute z-40 bottom-full mb-1 left-0 w-[300px] max-w-[calc(100vw-24px)] rounded-xl border border-line bg-panel shadow-2xl py-1.5" data-testid="plus-menu">
                  <div className="plus-head">Add to this message</div>
                  <button
                    className="plus-item"
                    data-testid="plus-files"
                    onClick={() => pickFiles(`image/*,application/pdf,.pdf,${TEXT_FILES}`)}
                  >
                    <Icon name="paperclip" size={15} className="plus-item-ico" />
                    <span className="plus-item-text">
                      <span className="plus-item-name">Files from this computer</span>
                      <span className="plus-item-says">Images, PDFs, text and code. Or paste, or drop them here.</span>
                    </span>
                  </button>
                  {props.sessionId && (
                    <button className="plus-item" data-testid="plus-at" onClick={openAtList}>
                      <Icon name="file" size={15} className="plus-item-ico" />
                      <span className="plus-item-text">
                        <span className="plus-item-name">A file from this folder</span>
                        <span className="plus-item-says">The agent reads it where it is.</span>
                      </span>
                      <kbd className="plus-item-key">@</kbd>
                    </button>
                  )}
                  {props.sessionId && (
                    <button
                      className="plus-item"
                      data-testid="plus-slash"
                      disabled={text.trim().length > 0}
                      onClick={openSlashList}
                    >
                      <Icon name="book" size={15} className="plus-item-ico" />
                      <span className="plus-item-text">
                        <span className="plus-item-name">A skill, a command or an agent</span>
                        <span className="plus-item-says">
                          {text.trim().length > 0
                            ? "It starts a message: empty the box first."
                            : "What the agent already knows how to do."}
                        </span>
                      </span>
                      <kbd className="plus-item-key">/</kbd>
                    </button>
                  )}
                  {sessionRows && (
                    <>
                      <div className="plus-rule" />
                      <div className="plus-head">For this whole session</div>
                      {bindRow("book", "Project memory", "memory", "Which project's notes this session uses.")}
                    </>
                  )}
                </div>
                {bindMenu && props.sessionId && (
                  <ProjectBindMenu
                    sessionId={props.sessionId}
                    kind={bindMenu}
                    onClose={() => {
                      setBindMenu(null);
                      setAttachMenuOpen(false);
                    }}
                    onOpenMemory={props.onOpenMemory}
                  />
                )}
              </>
            )}
          </div>
          {/* The system's file window, opened by "+"; emptied after each pick, so the same file
              can be picked again. */}
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              if (e.target.files) void addFiles(e.target.files);
              e.target.value = "";
            }}
          />

          {/* While recording, the quiet controls give way to the wave (the microphone's
              loudness, scrolling left) and the time. */}
          {dictation?.recording ? (
            <div className="voice-wave-row flex-1 flex items-center gap-2 ml-1" aria-hidden="true">
              <span className="voice-wave-line" />
              <span className="voice-wave-bars">
                {Array.from({ length: WAVE_BARS }, (_, index) => (
                  <i key={index} style={{ height: Math.round(4 + (levels[levels.length - WAVE_BARS + index] ?? 0) * 24) }} />
                ))}
              </span>
              <span className="text-[12px] text-muted tabular-nums">{recordingTime}</span>
            </div>
          ) : props.workspace !== undefined ? (
            <ModeMenu
              mode={props.mode}
              onModeChange={props.onModeChange}
              plan={props.plan}
              onPlanChange={props.onPlanChange}
              sessionId={props.sessionId}
            />
          ) : null}

          {dictationBusy === "Transcribing…" && <span className="text-[12px] text-accent">Transcribing…</span>}

          {/* How many and how much in all, quietly, so a big send is seen before it goes; and
              how many will not go. */}
          {!dictation?.recording && (attachments.length > 0 || refused.length > 0) && (
            // One line that gives way first when the row is short (it is cut, the controls are not).
            <span className="composer-attach-facts">
              {attachments.length > 0 && (
                <span data-testid="composer-attach-total">{attachedTotal(attachments)}</span>
              )}
              {attachments.length > 0 && refused.length > 0 && " · "}
              {refused.length > 0 && (
                <span className="composer-attach-refused" data-testid="composer-attach-refused">
                  {refused.length} file{refused.length === 1 ? "" : "s"} will not be sent
                </span>
              )}
            </span>
          )}

          <span className="ml-auto" />

          {/* The right of the row: the model (its one control names the model and its
              thinking level), the context ring, the mic, then Send or Stop. The left is
              what is added and what is allowed: attach, permissions. */}
          {!dictation?.recording && (needsModel ? (
            <button
              className="pill model-warn chip"
              onClick={() => props.onConnectModel?.()}
              title="Connect a model"
              aria-label="No model connected — connect a model"
            >
              <span className="pill-label">No model</span>
              <span className="model-warn-ico" aria-hidden>⚠</span>
            </button>
          ) : modelsLoaded ? (
            <ModelPicker
              value={props.model}
              models={props.models || []}
              modelLabels={props.modelLabels}
              onChange={props.onModelChange}
              onModelsChanged={props.onModelsChanged}
              thinking={props.thinking}
              onThinkingChange={props.onThinkingChange}
              open={modelPickerOpen}
              onOpenChange={setModelPickerOpen}
              // A conversation change retires the provider filter with the draft: the new
              // session's model decides it (see ModelPicker), never the old session's pick.
              sessionKey={props.resetKey}
            />
          ) : (
            <button
              className="pill chip text-faint cursor-default"
              disabled
              data-testid="models-loading"
              title="Fetching the model list from the server"
            >
              <span className="pill-label">Loading models…</span>
            </button>
          ))}

          {/* No working indicator here: the transcript header already says "Working for Ns"
              and the send slot becomes Stop, so a third "Working" was noise (owner call). */}

          {/* token usage — a quiet chip; hidden until the server reports usage.
              Shows the context-window fill bar alone (the session total lives in the
              popover), or the session total when there's no window / the bar is off. */}
          {!dictation?.recording && props.usage && totalTokens(props.usage) > 0 && (
            <UsageChip
              usage={props.usage}
              contextWindow={props.contextWindow}
              compactAt={props.compactAt}
              contextBar={props.contextBar}
              compacting={props.compacting}
              model={props.model}
              modelLabels={props.modelLabels}
              loadMetrics={props.loadMetrics}
            />
          )}

          {/* The microphone, the row's last control, since Send sits in the box's corner (owner,
              it belongs at the bar's right end). */}
          {isDesktop() && (
            <button
              className={
                iconBtn +
                (dictation?.recording ? " bg-red-50 text-red-600 hover:bg-red-100" : "") +
                (dictationBusy ? " opacity-60" : "") +
                (!voiceReady && !dictation?.recording ? " opacity-40" : "")
              }
              onClick={() => void toggleDictation()}
              disabled={!!dictationBusy}
              title={
                dictationBusy ||
                (dictation?.recording
                  ? "Stop recording and transcribe"
                  : voiceReady
                    ? "Start local voice dictation"
                    : "Configure Voice Input in Settings")
              }
              aria-label={dictation?.recording ? "Stop dictation" : voiceReady ? "Start dictation" : "Configure Voice Input in Settings"}
              aria-disabled={!voiceReady && !dictation?.recording}
            >
              {/* Listening: the bars of a voice move until it stops (owner mockup 2026-10-12). */}
              <Icon name={dictation?.recording ? "audioLines" : "mic"} size={16} running={!!dictation?.recording} />
            </button>
          )}

          {/* Send / Stop: the last thing in the row. A pending gate re-opens Send: the
              reply resolves it. Mid-turn the circle is Stop and nothing else: Enter queues
              what was typed, Ctrl/Cmd+Enter hands it to the running turn (the placeholder
              says both), and a queued row has "Send now". */}
          <div className="composer-send-slot">
            {props.running ? (
              <button
                className="w-8 h-8 rounded-full grid place-items-center shrink-0 bg-accent text-white hover:brightness-105 transition-colors"
                onClick={props.onInterrupt}
                title="Stop (Esc)"
                aria-label="Stop"
                data-testid="composer-stop"
              >
                <span className="w-2.5 h-2.5 rounded-[3px] bg-white" aria-hidden="true" />
              </button>
            ) : (
              <button
                className={
                  "w-8 h-8 rounded-full grid place-items-center shrink-0 transition-colors " +
                  (hasContent && (props.connected || props.holding) && !dictation?.recording && !dictationBusy
                    ? "bg-accent text-white hover:brightness-105"
                    : "bg-paper border border-line text-faint")
                }
                onClick={submit}
                disabled={(!props.connected && !props.holding) || !!dictation?.recording || !!dictationBusy}
                title={needsModel ? "Connect a model to send" : undefined}
                aria-label="Send"
              >
                <Icon name="arrowUp" size={15} />
              </button>
            )}
          </div>
        </div>
      </div>
      {/* Where this session works, in a strip tucked under the card: the folder and the
          branch on the left, the machine it runs on at the right. These are the pickers that
          set the session up (`setupSlot`, and the branch picker, which can start a worktree),
          so the strip is there until the first message. After it the strip stated them again,
          a row at the foot of the page for something nobody chooses any more; the top bar's
          crumb says it now (session/WhereCrumb.tsx; owner, 2026-10-13). When the first message
          goes, the strip slides up under the card and fades. A remote session shows
          `user@host:path`: the local folder and branch describe the wrong machine. */}
      {(props.hasMessages !== true || trayLeaving) &&
        (props.setupSlot || props.environment || props.workspaceName || props.gitBranch) && (
        <div
          className={"composer-tray" + (props.hasMessages === true ? "" : " setup")}
          data-testid="composer-session"
          data-state={props.hasMessages === true ? "session" : "setup"}
          data-leaving={trayLeaving || undefined}
          aria-hidden={trayLeaving || undefined}
        >
          {props.hasMessages !== true && props.setupSlot ? (
            props.setupSlot
          ) : props.environment ? (
            <span
              className="composer-tray-part"
              data-testid="composer-environment"
              title={formatRemoteEnvironmentReadout(props.environment, props.remoteWorkspace)}
            >
              <Icon name="server" size={13} />
              <span className="composer-tray-label">
                {formatRemoteEnvironmentReadout(props.environment, props.remoteWorkspace)}
              </span>
            </span>
          ) : (
            props.workspaceName && (
              <span className="composer-tray-part" data-testid="composer-folder" title={props.workspace || props.workspaceName}>
                <Icon name="folder" size={13} />
                <span className="composer-tray-label">{props.workspaceName}</span>
              </span>
            )
          )}
          {!props.environment && props.gitBranch && (
            <>
              {(props.workspaceName || (props.hasMessages !== true && props.setupSlot)) && (
                <span className="composer-tray-slash">/</span>
              )}
              {props.onBranchSwitched && props.workspace ? (
                <BranchChip
                  branch={props.gitBranch}
                  path={props.workspace}
                  className="composer-tray-part"
                  iconSize={13}
                  allowWorktree={props.hasMessages !== true && !!props.onBranchWorktree}
                  onSwitched={props.onBranchSwitched}
                  onWorktree={props.hasMessages !== true ? props.onBranchWorktree : undefined}
                />
              ) : (
                <span className="composer-tray-part" title={`Checked out: ${props.gitBranch}`}>
                  <Icon name="branch" size={13} />
                  <span className="composer-tray-label">{props.gitBranch}</span>
                </span>
              )}
            </>
          )}
          {/* The machine, at the right end. The pickers carry their own (the environment
              picker); a conversation on this computer states it. */}
          {!(props.hasMessages !== true && props.setupSlot) && !props.environment && (
            <span className="composer-tray-part env" data-testid="composer-local" title="Runs on this computer">
              <Icon name="laptop" size={13} />
              <span className="composer-tray-label">Local</span>
            </span>
          )}
        </div>
      )}
      <span className="sr-only" role="status" aria-live="polite">
        {dictation?.recording ? `Listening, ${recordingTime}` : dictationBusy || ""}
      </span>
    </div>
  );
}
