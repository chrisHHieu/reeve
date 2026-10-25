// Locking a design: the version the app is built from. The button sits last in the
// design's tab row; it opens a panel under it that has Reeve's browser open the page at the three
// widths and say what it measured (colors, type, spacing, parts, states, motion) and what the check
// still finds, which locking accepts. Lock writes the lock and the handoff (the server's, measured,
// never a model's) and puts the request to build from it in the message box, for the person to
// send. A session with no project folder locks the page and stops there: there is no app yet.
//
// Laid out from the approved mockup (the "Chốt" board, 2026-10-20).
import { useCallback, useEffect, useRef, useState } from "react";
import { getDesignLock, lockDesign, type DesignBuildTarget, type DesignLockState, type DesignLockSummary } from "../../api";
import type { Attachment } from "../../types";
import { DURATION } from "../../ui/motion";
import { Icon } from "../Icon";
import { attachToComposer } from "./designAsks";

const when = (ms?: number | null) =>
  ms ? new Date(ms).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";

/** What the agent is asked when a locked design is handed off: read the app, propose, wait. */
export function handoffAttachment(page: string, handoff: string, target: DesignBuildTarget): Attachment {
  const name = page.split(/[\\/]/).pop() || page;
  return {
    kind: "text",
    name: `${name} · handoff`,
    text: [
      `The person locked ${page} and wants it built into ${target.name}. Reeve measured the locked page into ${handoff}; build from that file, not from memory of the page.`,
      "First read the app (framework, routes, components, where its theme lives) and tell the person: which part of the page becomes which existing component, which parts need a new one, where the new colors and fonts go, and the steps. Wait for their go before writing code.",
    ].join("\n"),
  };
}

/** The App row: what the project's app is, and what of the design is new to it. */
export function appLine(app: NonNullable<DesignLockSummary["app"]>): string {
  if (!app.found) return "No app found in this project's files";
  const parts = [app.stack || "a web app"];
  if (app.theme?.length) parts.push(`theme in ${app.theme[0]}`);
  const fresh: string[] = [];
  if (app.colors_new) fresh.push(`${app.colors_new} ${app.colors_new === 1 ? "color" : "colors"}`);
  if (app.fonts_new?.length) fresh.push(`${app.fonts_new.length} ${app.fonts_new.length === 1 ? "font" : "fonts"}`);
  parts.push(fresh.length ? `${fresh.join(" and ")} new to it` : "every color and font already in it");
  if (app.components) parts.push(`${app.components} components`);
  return parts.join(" · ");
}

/** The button's words for a lock state. */
export function lockLabel(state: DesignLockState | null): { text: string; title: string } {
  if (!state?.locked) return { text: "Lock", title: "Lock this version: the one the app is built from" };
  if (state.changed) return { text: "Changed", title: `Locked ${when(state.locked_at)}; the page has changed since` };
  return { text: "Locked", title: `Locked ${when(state.locked_at)}` };
}

type Phase = "closed" | "open" | "closing";

export function DesignLock({ sessionId, path, version }: { sessionId: string; path: string; version: number }) {
  const name = path.split(/[\\/]/).pop() || path;
  const [state, setState] = useState<DesignLockState | null>(null);
  const [phase, setPhase] = useState<Phase>("closed");
  const [summary, setSummary] = useState<DesignLockSummary | null>(null);
  const [target, setTarget] = useState<DesignBuildTarget | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"measure" | "lock" | null>(null);
  const [showAccepted, setShowAccepted] = useState(false);
  const wrap = useRef<HTMLDivElement | null>(null);
  const asked = useRef(0);

  // The lock state follows the page: asked again when it was written (a new check) or locked.
  useEffect(() => {
    let alive = true;
    void getDesignLock(sessionId, path)
      .then((answer) => {
        if (!alive || !answer.ok) return;
        setState(answer.state ?? null);
        setTarget(answer.target ?? null);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [sessionId, path, version]);

  const close = useCallback(() => {
    setPhase((now) => (now === "open" ? "closing" : now));
  }, []);
  useEffect(() => {
    if (phase !== "closing") return;
    const timer = window.setTimeout(() => setPhase("closed"), DURATION.base);
    return () => window.clearTimeout(timer);
  }, [phase]);
  useEffect(() => {
    if (phase !== "open") return;
    const outside = (event: MouseEvent) => {
      if (!wrap.current?.contains(event.target as Node)) close();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("mousedown", outside);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("mousedown", outside);
      window.removeEventListener("keydown", escape);
    };
  }, [phase, close]);

  const open = () => {
    if (phase === "open") {
      close();
      return;
    }
    setPhase("open");
    setError("");
    setSummary(null);
    setShowAccepted(false);
    setBusy("measure");
    const ask = ++asked.current;
    void lockDesign(sessionId, path, true)
      .catch(() => ({ ok: false, error: "the server did not answer" }) as const)
      .then((answer) => {
        if (ask !== asked.current) return;
        setBusy(null);
        if (!answer.ok || !("summary" in answer) || !answer.summary) {
          setError(answer.error || "the page could not be measured");
          return;
        }
        setSummary(answer.summary);
        setState(answer.state ?? null);
        setTarget(answer.target ?? null);
      });
  };

  const lock = async () => {
    setBusy("lock");
    const answer = await lockDesign(sessionId, path, false).catch(() => ({ ok: false, error: "the server did not answer" }) as const);
    setBusy(null);
    if (!answer.ok || !("state" in answer)) {
      setError(answer.error || "the lock could not be written");
      return;
    }
    setState(answer.state ?? null);
    const goal = answer.target ?? null;
    if (goal && answer.handoff) {
      attachToComposer([handoffAttachment(summary?.page || path, answer.handoff, goal)], `Build ${name} into ${goal.name}.`);
    }
    close();
  };

  const fixFirst = () => {
    if (!summary) return;
    attachToComposer(
      [
        {
          kind: "text",
          name: `${name} · before locking`,
          text: [`Before the person locks ${summary.page}, fix what Reeve's check still finds on it:`, ...summary.accepted.map((line) => `- ${line}`)].join(
            "\n",
          ),
        },
      ],
      `Fix these on ${name} first.`,
    );
    close();
  };

  const label = lockLabel(state);
  const shown = phase !== "closed";
  return (
    <div className="am dl" ref={wrap}>
      <button
        type="button"
        className={"am-btn dl-btn" + (state?.locked ? (state.changed ? " is-changed" : " is-locked") : "") + (phase === "open" ? " on" : "")}
        aria-haspopup="dialog"
        aria-expanded={phase === "open"}
        title={label.title}
        data-testid="design-lock"
        onClick={open}
      >
        <Icon name={state?.locked && !state.changed ? "check" : "lock"} size={14} />
        <span className="am-label">{label.text}</span>
      </button>
      {shown && (
        <div
          className={"dl-pop" + (phase === "closing" ? " is-closing" : "")}
          role="dialog"
          aria-label={`Lock ${name}`}
          data-testid="design-lock-dialog"
        >
          <div className="dl-head">
            <h2>Lock {name}</h2>
            <p>
              {state?.locked
                ? state.changed
                  ? `Locked ${when(state.locked_at)}; the page has changed since. Locking again makes this version the one the app is built from.`
                  : `Locked ${when(state.locked_at)}. Locking again measures it anew.`
                : "This version becomes the one the app is built from. Reeve opens it at three widths and writes down what it measures; no model writes this part."}
            </p>
          </div>
          {busy === "measure" && (
            <div className="dl-wait" data-testid="design-lock-measuring">
              <Icon name="loader" size={14} running />
              Opening it at 1440, 834 and 390 px…
            </div>
          )}
          {error && (
            <div className="dl-error" role="alert">
              {error}
            </div>
          )}
          {summary && (
            <>
              <dl className="dl-rows" data-testid="design-lock-summary">
                <div>
                  <dt>Colors</dt>
                  <dd>
                    <span className="dl-swatches" aria-hidden="true">
                      {summary.colors.slice(0, 8).map((color) => (
                        <i key={color} style={{ background: color }} />
                      ))}
                    </span>
                    {summary.colors_count} {summary.colors_count === 1 ? "color" : "colors"}
                    {summary.design_md && summary.colors_unnamed.length > 0 ? `, ${summary.colors_unnamed.length} not in ${summary.design_md}` : ""}
                  </dd>
                </div>
                <div>
                  <dt>Type</dt>
                  <dd>
                    {summary.fonts.join(", ") || "the browser's own"} · {summary.sizes.length} {summary.sizes.length === 1 ? "size" : "sizes"}
                  </dd>
                </div>
                {summary.spacing.length > 0 && (
                  <div>
                    <dt>Spacing</dt>
                    <dd className="mono">{[...summary.spacing].sort((a, b) => a - b).join(" · ")}</dd>
                  </div>
                )}
                <div>
                  <dt>Parts</dt>
                  <dd>
                    {summary.parts_count}
                    {summary.parts.length ? `: ${summary.parts.join(", ")}` : ""}
                  </dd>
                </div>
                <div>
                  <dt>Clicked</dt>
                  <dd>
                    {summary.states
                      ? `${summary.states} ${summary.states === 1 ? "control" : "controls"}${summary.opens ? `, ${summary.opens} open something` : ""}`
                      : "nothing to click"}
                  </dd>
                </div>
                <div>
                  <dt>Motion</dt>
                  <dd>{summary.motion.join(" · ") || "none"}</dd>
                </div>
                {summary.app && (
                  <div data-testid="design-lock-app">
                    <dt>App</dt>
                    <dd>{appLine(summary.app)}</dd>
                  </div>
                )}
              </dl>
              {summary.accepted.length > 0 && (
                <div className="dl-warn" data-testid="design-lock-accepted">
                  <Icon name="warning" size={15} />
                  <div>
                    The check still finds {summary.accepted.length} {summary.accepted.length === 1 ? "thing" : "things"}. Locking anyway keeps{" "}
                    {summary.accepted.length === 1 ? "it" : "them"} as accepted by you.{" "}
                    <button type="button" className="dl-link" aria-expanded={showAccepted} onClick={() => setShowAccepted((v) => !v)}>
                      {showAccepted ? "Hide" : "Show"}
                    </button>
                    {showAccepted && (
                      <ul>
                        {summary.accepted.map((line) => (
                          <li key={line}>{line}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              )}
              <p className="dl-target" data-testid="design-lock-target">
                {target ? (
                  <>
                    Built into <b>{target.name}</b>: the request goes into the message box for you to send.
                  </>
                ) : (
                  "No project folder in this session: the lock and its handoff are kept, and there is no app to build into yet."
                )}
              </p>
            </>
          )}
          <div className="dl-actions">
            <button type="button" className="dl-primary" disabled={!summary || busy !== null} data-testid="design-lock-confirm" onClick={() => void lock()}>
              {busy === "lock" && <Icon name="loader" size={14} running />}
              {target ? (state?.locked ? "Lock again and hand off" : "Lock and hand off") : state?.locked ? "Lock again" : "Lock"}
            </button>
            {summary && summary.accepted.length > 0 && (
              <button type="button" className="dl-secondary" onClick={fixFirst}>
                Fix those first
              </button>
            )}
            <button type="button" className="dl-quiet" onClick={close}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
