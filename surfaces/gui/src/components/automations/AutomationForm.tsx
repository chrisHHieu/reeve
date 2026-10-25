import { useEffect, useId, useRef, useState } from "react";
import {
  announceAutomationsChanged,
  createAutomation,
  getConnectors,
  getRecentChannels,
  getSettings,
  updateAutomation,
  type Automation,
  type Connector,
  type RecentChannel,
} from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { pickFolder } from "../../desktop";
import { displayModelFull } from "../../modelName";
import { Button, ConfirmDialog, PageShell } from "../../ui";
import { PERMISSION_OPTIONS, modeValue } from "../composer/modes";
import { Icon } from "../Icon";
import { SelectMenu } from "../SelectMenu";
import { ChannelPicker } from "../ChannelPicker";
import { AddConnectionModal } from "../connectors/AddConnectionModal";
import { DAY_OPTIONS, fromCron, toCron } from "./schedule";
import { TEMPLATES, type Template } from "./templates";

// The one form an automation is made and changed in. A template is a way to fill it, not a
// second form: picking one writes the name, the schedule and the instructions, and adds the
// fields those instructions are written from. There is one button that creates, and the
// reason it cannot be pressed yet is said beside it.

const BROWSE = "__browse__";
const folderName = (path: string) => path.split(/[\\/]/).filter(Boolean).pop() || path;

interface Props {
  /** Given: the form changes this automation. Absent: it makes a new one. */
  task?: Automation;
  /** A template picked before the form opened (the empty list's suggestions). */
  startTemplate?: string;
  onDone: (task: Automation) => void;
  onBack: () => void;
}

export function AutomationForm({ task, startTemplate, onDone, onBack }: Props) {
  const editing = !!task;
  const ids = useId();
  const first = editing ? null : TEMPLATES.find((t) => t.key === startTemplate) || null;
  const said = task ? fromCron(task.schedule_raw?.cron) : null;

  const [tplKey, setTplKey] = useState<string | null>(first?.key ?? null);
  const tpl = TEMPLATES.find((t) => t.key === tplKey) || null;
  const [name, setName] = useState(task?.title ?? first?.title ?? "");
  // The instructions are the template's, written from the fields above them, until the
  // person types in the box: from then on they are the person's.
  const [typed, setTyped] = useState(task?.instructions ?? "");
  const [ownWords, setOwnWords] = useState(editing);
  const [day, setDay] = useState(said?.day ?? first?.day ?? "daily");
  const [time, setTime] = useState(said?.time ?? first?.time ?? "09:00");
  // A schedule the form cannot say (the agent wrote "every 15 minutes") is shown as written
  // and kept, until the person asks to set a new one.
  const [keepSchedule, setKeepSchedule] = useState(editing && !said);
  const [workspace, setWorkspace] = useState(task?.workspace ?? "");
  const [mode, setMode] = useState(modeValue(task?.mode || "workspace-write"));
  const [model, setModel] = useState(task?.model ?? "");
  const [repo, setRepo] = useState("");
  const [channel, setChannel] = useState("");
  const [deliver, setDeliver] = useState<"app" | "slack">("app");
  const [consent, setConsent] = useState(true);
  const [touched, setTouched] = useState<Record<string, boolean>>({});

  const [models, setModels] = useState<string[]>([]);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [recent, setRecent] = useState<RecentChannel[]>([]);
  const [channelNames, setChannelNames] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    getSettings()
      .then((s) => {
        setModels(s.models || []);
        setLabels(s.model_labels || {});
      })
      .catch(() => {});
  }, []);

  // What is connected decides whether a template can be created, and it is drawn on each
  // template's card (its connections' marks, in colour once connected), so it is read when the
  // form opens. A connect finishes in the browser, out of this page's sight, so the list is
  // read again while a template that needs one is picked.
  const loadConnectors = () => {
    getConnectors().then(setConnectors).catch(() => {});
  };
  useEffect(loadConnectors, []);
  const needsConnections = !!tpl && tpl.conns.length > 0;
  useEffect(() => {
    if (!needsConnections) return;
    loadConnectors();
    const timer = setInterval(loadConnectors, 3000);
    return () => clearInterval(timer);
  }, [needsConnections, tplKey]);
  const needsChannel = !!tpl?.needsChannel;
  useEffect(() => {
    if (needsChannel) getRecentChannels().then(setRecent).catch(() => {});
  }, [needsChannel]);

  const connector = (connName: string) => connectors.find((c) => c.name === connName);
  const missing = tpl ? tpl.conns.filter((c) => !connector(c.name)?.connected) : [];
  const instructions = !ownWords && tpl ? tpl.instructions({ repo, channel, deliver }) : typed;
  const channelName = channelNames[channel] || recent.find((c) => c.channel === channel)?.name;
  const channelLabel = channelName ? `#${channelName}` : channel;

  const reason = missing.length
    ? `Connect ${missing.map((c) => c.label).join(" and ")} to create this.`
    : needsChannel && !channel
      ? "Pick a channel to post to."
      : !name.trim()
        ? "Give it a name."
        : !instructions.trim()
          ? "Say what it should do."
          : "";

  // What the form holds, as one string: it differs from the one it opened with once the
  // person has changed something, and leaving then asks first.
  const held = JSON.stringify([tplKey, name, instructions, day, time, keepSchedule, workspace, mode, model, repo, channel, deliver, consent]);
  const opened = useRef(held);
  const dirty = held !== opened.current;
  const leave = () => (dirty ? setLeaving(true) : onBack());

  const pick = (t: Template) => {
    if (tplKey === t.key) {
      // Pressing the picked template again lets go of it. What the person wrote stays.
      setTplKey(null);
      if (name === t.title) setName("");
      return;
    }
    if (!name.trim() || name === tpl?.title) setName(t.title);
    setTplKey(t.key);
    setDay(t.day);
    setTime(t.time);
    setOwnWords(false);
    setConsent(true);
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      if (task) {
        // Only what changed is sent: a field left alone is not written again.
        const changes: Record<string, unknown> = {};
        if (name.trim() !== task.title) changes.title = name.trim();
        if (instructions.trim() !== task.instructions) changes.instructions = instructions.trim();
        if (!keepSchedule && toCron(day, time) !== (task.schedule_raw?.cron || "")) changes.cron = toCron(day, time);
        if (workspace !== (task.workspace ?? "")) changes.workspace = workspace;
        if (model !== (task.model ?? "")) changes.model = model;
        if (mode !== modeValue(task.mode || "workspace-write")) changes.mode = mode;
        const res = await updateAutomation(task.id, changes);
        if (!res?.ok || !res.task) throw new Error(res?.error || "The changes could not be saved.");
        announceAutomationsChanged();
        onDone(res.task);
      } else {
        const res = await createAutomation({
          title: name.trim(),
          instructions: instructions.trim(),
          cron: toCron(day, time),
          workspace,
          model,
          mode,
          permissions:
            tpl?.consent && consent && channel ? [{ tool: "send_message", target: channel, access: "write" }] : [],
        });
        if (!res.ok || !res.task) throw new Error(res.error || "The automation could not be created.");
        announceAutomationsChanged();
        onDone(res.task);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "That did not work. Try again.");
      setBusy(false);
    }
  };

  const folderOptions = [
    { value: "", label: "Scratch folder", sub: "A folder Reeve keeps for this automation" },
    ...(workspace ? [{ value: workspace, label: folderName(workspace), sub: workspace }] : []),
    { value: BROWSE, label: "Choose a folder…", sub: "Work inside a project you already have" },
  ];
  const modelOptions = [
    { value: "", label: "Server default", sub: "The model set in Settings when it runs" },
    ...models.map((m) => ({ value: m, label: displayModelFull(m, labels) })),
  ];
  const nameBad = touched.name && !name.trim();
  const channelBad = touched.channel && needsChannel && !channel && missing.length === 0;
  const instructionsBad = touched.instructions && !instructions.trim();
  const id = (field: string) => `${ids}-${field}`;

  return (
    <PageShell testId="automation-form">
      <div className="auto-view">
        <button type="button" className="auto-back" onClick={leave}>
          <Icon name="arrowLeft" size={13} />
          Automations
        </button>
        <h1 className="page-title">{editing ? "Edit automation" : "New automation"}</h1>
        <p className="page-sub">
          {editing
            ? "Change when it runs, what it does, and how it works."
            : "Say when it runs and what it does. A template fills the form in for you."}
        </p>

        {!editing && (
          <>
            <h2 className="auto-h2">Start from a template</h2>
            <div className="auto-templates">
              {TEMPLATES.map((t) => (
                <TemplateCard key={t.key} template={t} connectors={connectors} picked={t.key === tplKey} onPick={() => pick(t)} />
              ))}
            </div>
          </>
        )}

        {needsConnections && tpl && (
          <>
            <h2 className="auto-h2">What it needs</h2>
            <div className="auto-card" data-testid="qs-needs">
              {tpl.conns.map((c) => {
                const live = connector(c.name);
                return (
                  <div className="auto-need" key={c.name}>
                    {live && <ConnectorBadge connector={live} size={26} title={live.title} />}
                    <div className="auto-fill">
                      <div className="auto-need-name">{live?.title || c.label}</div>
                      <div className="auto-need-why">{c.why}</div>
                    </div>
                    {live?.connected ? (
                      <span className="auto-ok">
                        <Icon name="check" size={13} />
                        Connected
                      </span>
                    ) : (
                      <Button size="sm" onClick={() => setConnecting(c.name)} data-testid={`ob-connect-${c.name}`}>
                        Connect
                      </Button>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}
        {connecting && connector(connecting) && (
          <AddConnectionModal c={connector(connecting)!} onClose={() => setConnecting(null)} onChanged={loadConnectors} />
        )}

        <div className="auto-form" data-testid="automation-editor">
          <div className="auto-grid2">
            <div>
              <label className="auto-label" htmlFor={id("name")}>
                Name
              </label>
              <input
                id={id("name")}
                className="auto-field"
                value={name}
                aria-invalid={nameBad || undefined}
                aria-describedby={nameBad ? id("name-e") : undefined}
                onChange={(e) => setName(e.target.value)}
                onBlur={() => setTouched((t) => ({ ...t, name: true }))}
              />
              {nameBad && (
                <p className="auto-error" id={id("name-e")}>
                  Give it a name.
                </p>
              )}
            </div>
            {tpl?.needsRepo && (
              <div>
                <label className="auto-label" htmlFor={id("repo")}>
                  Repository
                </label>
                <input
                  id={id("repo")}
                  className="auto-field"
                  placeholder="owner/repo"
                  value={repo}
                  onChange={(e) => setRepo(e.target.value)}
                  data-testid="ob-repo"
                />
              </div>
            )}
          </div>

          {keepSchedule ? (
            <div className="auto-kept" data-testid="schedule-kept">
              <div className="auto-fill">
                <div className="auto-label">Runs</div>
                <div>{task?.schedule}</div>
                <p className="auto-hint">This schedule was not set in this form, and it is kept as it is.</p>
              </div>
              <Button onClick={() => setKeepSchedule(false)}>Set a new schedule</Button>
            </div>
          ) : (
            <div className="auto-grid2">
              <div>
                <span className="auto-label">Repeats</span>
                <SelectMenu ariaLabel="Repeats" value={day} options={DAY_OPTIONS} onChange={setDay} />
              </div>
              <div>
                <label className="auto-label" htmlFor={id("at")}>
                  At
                </label>
                <input
                  id={id("at")}
                  type="time"
                  className="auto-field auto-num"
                  value={time}
                  aria-describedby={id("at-h")}
                  onChange={(e) => setTime(e.target.value)}
                />
                <p className="auto-hint" id={id("at-h")}>
                  Your computer's time.
                </p>
              </div>
            </div>
          )}

          {needsChannel && (
            <div>
              <span className="auto-label">Post to channel</span>
              <div
                data-testid="ob-channel"
                data-bad={channelBad || undefined}
                onBlur={() => setTouched((t) => ({ ...t, channel: true }))}
              >
                <ChannelPicker
                  value={channel}
                  onChange={setChannel}
                  recent={recent}
                  onPickName={(address, picked) => setChannelNames((m) => ({ ...m, [address]: picked }))}
                />
              </div>
              {channelBad && (
                <p className="auto-error" role="alert">
                  Pick the channel it posts to.
                </p>
              )}
              <p className="auto-hint">Reeve has to be a member of the channel. Invite @Reeve in Slack if it is not.</p>
            </div>
          )}

          {tpl?.deliver && (
            <div>
              <span className="auto-label">Deliver to</span>
              <SelectMenu
                ariaLabel="Deliver to"
                value={deliver}
                options={[
                  { value: "app", label: "In the app" },
                  { value: "slack", label: "Slack DM", sub: "Needs Slack connected" },
                ]}
                onChange={(v) => setDeliver(v as "app" | "slack")}
              />
            </div>
          )}

          <div>
            <label className="auto-label" htmlFor={id("ins")}>
              Instructions
            </label>
            <textarea
              id={id("ins")}
              className="auto-field auto-textarea"
              placeholder="What it should do each time, written the way you would ask in a conversation."
              value={instructions}
              aria-invalid={instructionsBad || undefined}
              aria-describedby={instructionsBad ? id("ins-e") : undefined}
              onChange={(e) => {
                setTyped(e.target.value);
                setOwnWords(true);
              }}
              onBlur={() => setTouched((t) => ({ ...t, instructions: true }))}
            />
            {instructionsBad && (
              <p className="auto-error" id={id("ins-e")}>
                Say what it should do.
              </p>
            )}
          </div>

          <div className="auto-grid3" data-testid="automation-control-row">
            <div>
              <span className="auto-label">Works in</span>
              <SelectMenu
                ariaLabel="Works in"
                value={workspace}
                options={folderOptions}
                onChange={(v) => {
                  if (v !== BROWSE) return setWorkspace(v);
                  pickFolder()
                    .then((dir) => dir && setWorkspace(dir))
                    .catch(() => {});
                }}
              />
            </div>
            <div>
              <span className="auto-label">Permissions</span>
              <SelectMenu
                ariaLabel="Permissions"
                value={mode}
                options={PERMISSION_OPTIONS.map((o) => ({ value: o.value, label: o.label, sub: o.description }))}
                onChange={setMode}
              />
            </div>
            <div>
              <span className="auto-label">Model</span>
              <SelectMenu ariaLabel="Model" value={model} options={modelOptions} onChange={setModel} />
            </div>
          </div>

          {tpl?.consent ? (
            <label className="auto-consent">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} data-testid="ob-consent" />
              <span>
                Let this automation post its digest to <b title={channel || undefined}>{channelLabel || "the channel"}</b> without
                asking each time. Anything else still asks first.
              </span>
            </label>
          ) : needsConnections ? (
            <p className="auto-hint">This automation only reads. Reading never needs your approval.</p>
          ) : null}
          <p className="auto-hint">
            A run that needs your approval waits for you. Its conversation shows under “Needs you” in the sidebar.
          </p>
        </div>

        <div className="auto-bar">
          <span className={"auto-fill " + (error ? "auto-error" : "auto-reason")} role={error ? "alert" : "status"} data-testid="ob-create-hint">
            {error || reason}
          </span>
          <Button onClick={leave} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} disabled={!!reason || (editing && !dirty)} loading={busy} data-testid="ob-create">
            {editing ? "Save" : "Create automation"}
          </Button>
        </div>
      </div>

      {leaving && (
        <ConfirmDialog
          title="Discard your changes?"
          body={editing ? `You changed “${task!.title}” and have not saved.` : "This automation has not been created yet."}
          confirmLabel="Discard"
          onConfirm={onBack}
          onCancel={() => setLeaving(false)}
        />
      )}
    </PageShell>
  );
}

export function TemplateCard({
  template,
  connectors,
  picked,
  onPick,
}: {
  template: Template;
  /** What is connected, when it is known: a connection's mark is in colour once it is. */
  connectors?: Connector[];
  picked?: boolean;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      className="auto-template"
      aria-pressed={picked === undefined ? undefined : picked}
      onClick={onPick}
      data-testid={`qs-template-${template.key}`}
    >
      <b>{template.title}</b>
      <span>{template.blurb}</span>
      <i>
        {/* The connections a template needs are shown by their marks, as everywhere else in the
            app, not spelled out: in colour when connected, grey when not yet. */}
        {template.conns.map((c) => {
          const live = connectors?.find((x) => x.name === c.name);
          const state = `${live?.title || c.label}: ${live?.connected ? "connected" : "not connected yet"}`;
          return (
            <span
              key={c.name}
              className={"auto-template-mark" + (live?.connected ? "" : " off")}
              data-testid={`qs-mark-${c.name}`}
              data-connected={live?.connected ? "true" : "false"}
              title={state}
            >
              {live ? <ConnectorBadge connector={live} size={18} title={state} /> : <span className="auto-template-blank" role="img" aria-label={state} />}
            </span>
          );
        })}
        <span>{template.conns.length ? template.cadence : `No connection needed · ${template.cadence.toLowerCase()}`}</span>
      </i>
    </button>
  );
}
