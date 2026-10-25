// Talking about a previewed page by touching it (phase 3): point at an element, leave
// numbered comments on it, retype text in place, mark an area. The page's design bridge does the
// touching (`preview_design.py`); this is the app's half: the modes, the comments, and what each
// becomes.
//
// Nothing here writes a design but a retype, and that only where the old text is exactly once in
// the file, guarded by the file's mtime like any save from the Files tab. A point, the comments and
// a mark go to the composer for the agent, each with the scope rule: the agent changes the page, and
// its check runs as after any write, saying what changed outside what was named.
//
// Comments work as OpenDesign's do (read 2026-10-19): a click drops a numbered pin and opens a box
// to type in where it was dropped; a comment is kept on the page until it is put in a message, one
// at a time or several together; a comment whose element the page no longer has says so.
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { getDesignVersions, readArtifact, writeSessionEntry, type DesignVersion } from "../../api";
import { DesignVersions } from "./DesignVersions";
import { Icon, type IconName } from "../Icon";
import { Disclosure } from "../../ui/Disclosure";
import {
  attachToComposer,
  elementLabel,
  pinsAttachment,
  placeRetype,
  pointedAttachment,
  retypeAttachment,
  asLevers,
  isColor,
  tweaksAttachment,
  type ElementStyle,
  type Lever,
  type DesignPin,
  type PageElement,
} from "./designAsks";

/** What a click on the page does. `mark` draws over the frame in the app; the page is left alone. */
export type DesignMode = "look" | "point" | "pin" | "text" | "mark";

/** The modes the page's own bridge does, in the order the bars show them. */
export const MODES: { id: Exclude<DesignMode, "mark">; label: string; hint: string; icon: IconName }[] = [
  { id: "look", label: "Look", hint: "Use the page as it is", icon: "eye" },
  { id: "point", label: "Point", hint: "Click a part of the page to put it in your message", icon: "pointer" },
  { id: "pin", label: "Comment", hint: "Click a part of the page to leave a comment on it", icon: "messageSquarePlus" },
  { id: "text", label: "Text", hint: "Click a piece of text to retype it", icon: "type" },
];

type Said = {
  type?: string;
  el?: unknown;
  fx?: unknown;
  fy?: unknown;
  cx?: unknown;
  cy?: unknown;
  before?: unknown;
  after?: unknown;
  lost?: unknown;
  id?: unknown;
  page?: unknown;
  under?: unknown;
};

const num = (x: unknown) => (typeof x === "number" && Number.isFinite(x) ? Math.round(x) : undefined);
const str = (x: unknown, max: number) => (typeof x === "string" ? x.slice(0, max) : undefined);

function asStyle(value: unknown): ElementStyle | undefined {
  if (!value || typeof value !== "object") return undefined;
  const v = value as Record<string, unknown>;
  return { font: str(v.font, 40), size: num(v.size), weight: str(v.weight, 4), color: str(v.color, 24), bg: str(v.bg, 24), radius: str(v.radius, 20) };
}

/** An element the page reported, as text and numbers only; anything else in it is dropped. */
export function asElement(value: unknown): PageElement | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  if (typeof v.selector !== "string" || typeof v.tag !== "string") return null;
  return {
    selector: v.selector.slice(0, 400),
    tag: v.tag.slice(0, 20),
    cls: str(v.cls, 80),
    text: str(v.text, 200),
    html: str(v.html, 500),
    w: num(v.w),
    h: num(v.h),
    x: num(v.x),
    y: num(v.y),
    style: asStyle(v.style),
  };
}

/** An area the page told back for a mark: where it is on the page, and what is under it. */
export interface MarkedArea {
  page: { x: number; y: number; w: number; h: number };
  width: number;
  under: PageElement[];
}

const unit = (x: unknown) => (typeof x === "number" && Number.isFinite(x) ? Math.max(0, Math.min(1, x)) : 0.5);

export function useDesignTools({
  sessionId,
  path,
  label,
  post,
  viewWidth,
  reload,
  extra,
  inline = false,
}: {
  sessionId: string;
  path: string;
  /** How the file is named to the agent and the person: its path inside its folder. */
  label: string;
  /** Say something to the page in the frame. */
  post: (message: Record<string, unknown>) => void;
  /** The window width the page is laid out at now, for what a point says. */
  viewWidth: number;
  reload: () => void;
  /** More controls for the row, drawn before the comment count (the variants switch). */
  extra?: ReactNode;
  /** A comment's words are typed where it was dropped (the design tab), not in the list. */
  inline?: boolean;
}) {
  const [mode, setMode] = useState<DesignMode>("look");
  const [pins, setPins] = useState<DesignPin[]>([]);
  const [pinsOpen, setPinsOpen] = useState(false);
  // The comment just dropped, whose words are being typed where it was dropped.
  const [draft, setDraft] = useState<number | null>(null);
  const [notice, setNotice] = useState("");
  // The levers the page declared (`#reeve-tweaks`) and what the person moved them to. The choice
  // outlives a reload of the page: it is applied again when the page reports its levers.
  const [levers, setLevers] = useState<Lever[]>([]);
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [tweaksOpen, setTweaksOpen] = useState(false);
  // The page's versions as the checks after its writes recorded them (phase 3d), read
  // again when the page reloads, which it does after the agent writes it.
  const [versions, setVersions] = useState<DesignVersion[]>([]);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [versionsAsk, setVersionsAsk] = useState(0);
  useEffect(() => {
    let alive = true;
    getDesignVersions(sessionId, path)
      .then((found) => alive && setVersions(found))
      .catch(() => alive && setVersions([]));
    return () => {
      alive = false;
    };
  }, [sessionId, path, versionsAsk]);
  const chosenRef = useRef(chosen);
  chosenRef.current = chosen;
  const latest = useRef({ mode, pins, viewWidth });
  latest.current = { mode, pins, viewWidth };
  const focusPin = useRef<number | null>(null);
  // Areas asked of the page for a mark, answered by id.
  const areas = useRef(new Map<string, (area: MarkedArea | null) => void>());

  useEffect(() => {
    setMode("look");
    setPins([]);
    setPinsOpen(false);
    setDraft(null);
    setNotice("");
    setLevers([]);
    setChosen({});
    setTweaksOpen(false);
    setVersionsOpen(false);
  }, [sessionId, path]);

  const sync = useCallback(() => {
    const { mode: m, pins: p } = latest.current;
    post({ reevePreviewDo: "design", mode: m === "look" || m === "mark" ? null : m });
    post({ reevePreviewDo: "pins", pins: p.map((pin) => ({ n: pin.n, selector: pin.el.selector, fx: pin.fx, fy: pin.fy, sent: !!pin.sent })) });
  }, [post]);

  useEffect(() => sync(), [mode, pins, sync]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const retype = useCallback(
    async (el: PageElement, before: string, after: string) => {
      const read = await readArtifact(sessionId, path).catch(() => null);
      const placed = read?.ok && typeof read.content === "string" && !read.truncated ? placeRetype(read.content, before, after) : null;
      if (read && placed?.ok) {
        const wrote = await writeSessionEntry(sessionId, path, placed.content, read.mtime_ms, read.encoding).catch(() => null);
        if (wrote?.ok) {
          attachToComposer([retypeAttachment(label, el, before, after, true)]);
          setNotice("Saved. The change is in your next message too.");
          reload();
          return;
        }
        setNotice(wrote?.conflict ? "The file changed on disk; the change went to your message instead." : "Could not save; the change went to your message instead.");
      } else {
        setNotice(
          placed && !placed.ok && placed.reason === "ambiguous"
            ? "That text is in the file more than once; the change went to your message instead."
            : "Could not find that text in the file; the change went to your message instead.",
        );
      }
      attachToComposer([retypeAttachment(label, el, before, after, false)]);
    },
    [label, path, reload, sessionId],
  );

  /** A message from the page. True when it was the design bridge's. */
  const handle = useCallback(
    (said: Said): boolean => {
      if (said.type === "page") {
        sync();
        setVersionsAsk((n) => n + 1);
        return false;
      }
      if (said.type === "anchors" && Array.isArray(said.lost)) {
        const lost = new Set(said.lost.filter((n): n is number => typeof n === "number"));
        setPins((current) => (current.some((pin) => !!pin.lost !== lost.has(pin.n)) ? current.map((pin) => ({ ...pin, lost: lost.has(pin.n) })) : current));
        return true;
      }
      if (said.type === "area") {
        const id = typeof said.id === "string" ? said.id : "";
        const resolve = areas.current.get(id);
        if (!resolve) return true;
        areas.current.delete(id);
        const page = said.page as Record<string, unknown> | undefined;
        const box = page ? { x: num(page.x), y: num(page.y), w: num(page.w), h: num(page.h) } : null;
        if (!box || box.x === undefined || box.y === undefined || !box.w || !box.h) {
          resolve(null);
          return true;
        }
        const under = (Array.isArray(said.under) ? said.under : []).map(asElement).filter((el): el is PageElement => !!el).slice(0, 4);
        resolve({ page: { x: box.x, y: box.y, w: box.w, h: box.h }, width: num((said as Record<string, unknown>).vw) || latest.current.viewWidth, under });
        return true;
      }
      const el = asElement(said.el);
      if (said.type === "picked" && el) {
        attachToComposer([pointedAttachment(label, el, latest.current.viewWidth)]);
        setNotice(`Added ${elementLabel(el)} to your message.`);
        return true;
      }
      if (said.type === "pinned" && el) {
        setPins((current) => {
          if (current.length >= 30) return current;
          const n = (current.reduce((top, pin) => Math.max(top, pin.n), 0) || 0) + 1;
          focusPin.current = n;
          if (inline) setDraft(n);
          else setPinsOpen(true);
          return [...current, { n, el, fx: unit(said.fx), fy: unit(said.fy), cx: num(said.cx), cy: num(said.cy), comment: "" }];
        });
        return true;
      }
      if (said.type === "retyped" && el && typeof said.before === "string" && typeof said.after === "string") {
        void retype(el, said.before.slice(0, 2000), said.after.slice(0, 2000));
        return true;
      }
      if (said.type === "tweaks") {
        const found = asLevers((said as { levers?: unknown }).levers);
        setLevers(found);
        for (const lever of found) {
          const value = chosenRef.current[lever.name];
          if (value && lever.options.includes(value)) post({ reevePreviewDo: "tweak", name: lever.name, value });
        }
        return true;
      }
      if (said.type === "notext" && el) {
        setNotice("Pick a piece of text alone: that part holds other parts.");
        return true;
      }
      return false;
    },
    [inline, label, post, retype, sync],
  );

  /** Ask the page where an area of its window is on the page and what is under it (a mark). */
  const askArea = useCallback(
    (rect: { x: number; y: number; w: number; h: number }) =>
      new Promise<MarkedArea | null>((resolve) => {
        const id = `a${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
        const timer = window.setTimeout(() => {
          areas.current.delete(id);
          resolve(null);
        }, 2000);
        areas.current.set(id, (area) => {
          window.clearTimeout(timer);
          resolve(area);
        });
        post({ reevePreviewDo: "area", id, ...rect });
      }),
    [post],
  );

  /** Put comments in the message: the ones named, or every one with words not yet sent. */
  const sendComments = (only?: number[]) => {
    const chosenPins = pins.filter((pin) => !pin.sent && pin.comment.trim() && (!only || only.includes(pin.n)));
    const attachment = pinsAttachment(label, chosenPins, latest.current.viewWidth);
    if (!attachment) return;
    attachToComposer([attachment], `Comments on ${label}`);
    const sent = new Set(chosenPins.map((pin) => pin.n));
    setPins((current) => current.map((pin) => (sent.has(pin.n) ? { ...pin, sent: true } : pin)));
    setNotice(sent.size === 1 ? "The comment is in your message." : "The comments are in your message.");
  };
  const send = () => sendComments();
  const setComment = (n: number, value: string) =>
    setPins((current) => current.map((p) => (p.n === n ? { ...p, comment: value.slice(0, 2000), sent: false } : p)));
  const removePin = (n: number) => {
    setPins((current) => current.filter((p) => p.n !== n));
    setDraft((current) => (current === n ? null : current));
  };
  /** Close the box a comment was typed in: kept when it has words, gone when it has none. */
  const closeDraft = () => {
    const n = draft;
    setDraft(null);
    if (n !== null && !pins.find((pin) => pin.n === n)?.comment.trim()) removePin(n);
  };

  const waiting = pins.filter((pin) => !pin.sent && pin.comment.trim()).length;

  const bar: ReactNode = (
    <div className="page-design" data-testid="page-design">
      <div className="seg seg-words page-modes" role="group" aria-label="What a click does">
        {MODES.map((option) => (
          <button
            key={option.id}
            type="button"
            className={mode === option.id ? "on" : undefined}
            aria-pressed={mode === option.id}
            title={option.hint}
            data-testid={`page-mode-${option.id}`}
            onClick={() => setMode(option.id)}
          >
            <Icon name={option.icon} size={13} />
            <span>{option.label}</span>
          </button>
        ))}
      </div>
      <span className="page-design-notice" role="status" data-testid="page-design-notice">
        {notice}
      </span>
      {extra}
      {versions.length >= 2 && (
        <button
          type="button"
          className="fv-note-btn"
          aria-expanded={versionsOpen}
          data-testid="page-versions-toggle"
          onClick={() => setVersionsOpen((open) => !open)}
        >
          {versions.length} versions
        </button>
      )}
      {levers.length > 0 && (
        <button
          type="button"
          className="fv-note-btn"
          aria-expanded={tweaksOpen}
          data-testid="page-tweaks-toggle"
          onClick={() => setTweaksOpen((open) => !open)}
        >
          Tweaks
        </button>
      )}
      {pins.length > 0 && (
        <button
          type="button"
          className="fv-note-btn"
          aria-expanded={pinsOpen}
          data-testid="page-pins-toggle"
          onClick={() => setPinsOpen((open) => !open)}
        >
          {pins.length} comment{pins.length === 1 ? "" : "s"}
        </button>
      )}
    </div>
  );

  const pick = (lever: Lever, value: string) => {
    setChosen((current) => ({ ...current, [lever.name]: value }));
    post({ reevePreviewDo: "tweak", name: lever.name, value });
  };
  const reset = () => {
    for (const lever of levers) post({ reevePreviewDo: "tweak", name: lever.name, value: lever.current });
    setChosen({});
  };
  const keep = () => {
    const attachment = tweaksAttachment(label, levers, chosen);
    if (!attachment) return;
    attachToComposer([attachment], `Keep these tweaks in ${label}`);
    setNotice("The tweaks are in your message.");
  };
  const moved = levers.some((lever) => chosen[lever.name] && chosen[lever.name] !== lever.current);

  const tweaks: ReactNode = (
    <Disclosure open={tweaksOpen && levers.length > 0} className="fold-fade" testId="page-tweaks">
      <div className="page-tweaks">
        {levers.map((lever) => {
          const now = chosen[lever.name] ?? lever.current;
          const swatches = lever.options.every(isColor);
          return (
            <div key={lever.name} className="page-tweak" role="group" aria-label={lever.label}>
              <span className="page-tweak-label">{lever.label}</span>
              {swatches ? (
                <div className="page-swatches">
                  {lever.options.map((option) => (
                    <button
                      key={option}
                      type="button"
                      className={now === option ? "page-swatch on" : "page-swatch"}
                      aria-label={option}
                      aria-pressed={now === option}
                      title={option}
                      style={{ background: option }}
                      data-testid={`page-tweak-${lever.name}-${option}`}
                      onClick={() => pick(lever, option)}
                    />
                  ))}
                </div>
              ) : (
                <div className="seg seg-words">
                  {lever.options.map((option) => (
                    <button
                      key={option}
                      type="button"
                      className={now === option ? "on" : undefined}
                      aria-pressed={now === option}
                      data-testid={`page-tweak-${lever.name}-${option}`}
                      onClick={() => pick(lever, option)}
                    >
                      {option}
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
        <div className="page-pins-foot">
          <button type="button" className="fv-note-btn" disabled={!moved} onClick={reset}>
            Back to the file's
          </button>
          <button type="button" className="btn sm primary" disabled={!moved} data-testid="page-tweaks-keep" onClick={keep}>
            Ask the agent to keep them
          </button>
        </div>
      </div>
    </Disclosure>
  );

  const panel: ReactNode = (
    <Disclosure open={pinsOpen && pins.length > 0} className="fold-fade" testId="page-pins">
      <div className="page-pins">
        <ol className="page-pins-list">
          {pins.map((pin) => (
            <li key={pin.n} className={pin.sent ? "is-sent" : undefined}>
              <span className="page-pin-n" aria-hidden="true">
                {pin.n}
              </span>
              <div className="page-pin-body">
                <span className="page-pin-on" title={pin.el.selector}>
                  {elementLabel(pin.el)}
                  {pin.sent ? " · sent" : ""}
                  {pin.lost ? " · no longer on the page" : ""}
                </span>
                <textarea
                  rows={1}
                  aria-label={`Comment ${pin.n}`}
                  placeholder="What should change here?"
                  value={pin.comment}
                  data-testid={`page-pin-comment-${pin.n}`}
                  ref={(node) => {
                    if (node && focusPin.current === pin.n && !inline) {
                      focusPin.current = null;
                      node.focus();
                    }
                  }}
                  onChange={(event) => setComment(pin.n, event.target.value)}
                />
              </div>
              <button type="button" className="artifact-icon-btn" aria-label={`Remove comment ${pin.n}`} onClick={() => removePin(pin.n)}>
                <Icon name="x" size={13} />
              </button>
            </li>
          ))}
        </ol>
        <div className="page-pins-foot">
          <button type="button" className="fv-note-btn" onClick={() => setPins([])}>
            Clear
          </button>
          <button type="button" className="btn sm primary" disabled={waiting === 0} data-testid="page-pins-send" onClick={send}>
            {waiting > 0
              ? `Put ${waiting} comment${waiting === 1 ? "" : "s"} in your message`
              : pins.some((pin) => pin.sent)
                ? "In your message"
                : "Write a comment to send"}
          </button>
        </div>
      </div>
    </Disclosure>
  );

  const history: ReactNode = (
    <Disclosure open={versionsOpen && versions.length >= 2} className="fold-fade" testId="page-versions-panel">
      <DesignVersions versions={versions} />
    </Disclosure>
  );

  return {
    mode,
    setMode,
    notice,
    setNotice,
    pins,
    setPins,
    pinsOpen,
    setPinsOpen,
    draft,
    setDraft,
    closeDraft,
    setComment,
    removePin,
    sendComments,
    waiting,
    askArea,
    levers,
    tweaksOpen,
    setTweaksOpen,
    versions,
    versionsOpen,
    setVersionsOpen,
    handle,
    bar,
    panel,
    tweaks,
    history,
  };
}
