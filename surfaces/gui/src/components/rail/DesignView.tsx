// A design page in its own tab: the page on a ground, at a width the person picks or
// drags to, with one bar of tools over it. Left, how it is seen: reload, the page or its code, and
// the width. Right, what a click on it does and what the page carries: point, pin, retype, its
// tweaks, its variants, its versions, its comments. Under it, what Reeve's last check of it said.
//
// Comment drops a pin and opens a box right where it was dropped; the comments are kept in a dock
// beside the page until they go into a message. Mark draws over the page (box, pen, words) and
// sends a picture of the area with the ink on it, taken by Reeve's own browser, with the parts
// under it.
//
// The bar is laid out like OpenDesign's file viewer (read 2026-10-19): view controls left, tools as
// icons right. What the page can do outside the preview (Present, History, Export, Share, open in an
// editor) sits in the tab row beside its tab (`DesignActions`, drawn into the row's slot). The page itself is served exactly as in Files (`usePageHost`), with the same design
// bridge (`useDesignTools`), so a point or a pin says the same thing from either.
import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import { getDesignShot, readArtifact, revealArtifact, revealDownload } from "../../api";
import { DesignActions, type DesignOutcome } from "./DesignActions";
import { CommentPopover, CommentsDock } from "./DesignComments";
import { attachToComposer, markAttachment } from "./designAsks";
import { inkOver, markBounds, MarkLayer, type MarkShape } from "./MarkLayer";
import { Dropdown } from "../Dropdown";
import { Icon, type IconName } from "../Icon";
import { useDesignTools, MODES } from "./DesignTools";
import { CodeView } from "./fileViewers";
import { pageBox, useStageBox, type PageWidth } from "./PageFrame";
import { holderDocument, InternetAsk, PAGE_SANDBOX, usePageHost } from "./usePageHost";
import { useVariants, Variants } from "./Variants";

/** The narrowest and widest a page can be dragged to, in px. */
export const DRAG_MIN = 320;
export const DRAG_MAX = 2560;

const widthIcon = (width: PageWidth): IconName =>
  width === "fit" || width >= 1100 ? "monitor" : width >= 700 ? "tablet" : "smartphone";

/** The three a person picks from, as OpenDesign names them: Desktop is the page as wide as the
 *  panel, at full size (the foot says how many px that is); Tablet and Mobile are two of the
 *  widths a design is checked at. */
const DEVICES: { id: PageWidth; label: string }[] = [
  { id: "fit", label: "Desktop" },
  { id: 834, label: "Tablet" },
  { id: 390, label: "Mobile" },
];

/** The width menu: Desktop, Tablet, Mobile, and a width dragged to. */
export function widthOptions(width: PageWidth) {
  const options = DEVICES.map((device) => ({ value: String(device.id), label: device.label, icon: widthIcon(device.id) }));
  if (!DEVICES.some((device) => device.id === width)) {
    options.push({ value: String(width), label: `${width} px`, icon: widthIcon(width) });
  }
  return options;
}

/** A width a drag ends at: the start, moved by twice the pointer (the page is centred, so its edge
 *  moves half as far as it grows), in the page's own px, within DRAG_MIN..DRAG_MAX. */
export function draggedWidth(start: number, dx: number, scale: number): number {
  const grown = start + (2 * dx) / (scale > 0 ? scale : 1);
  return Math.round(Math.min(DRAG_MAX, Math.max(DRAG_MIN, grown)));
}

export function DesignView({
  sessionId,
  path,
  compareRequest,
  actionsSlot,
}: {
  sessionId: string;
  path: string;
  /** Bumped to open the page with its variants side by side (Designs' Compare). */
  compareRequest?: number;
  /** The tab row's place for this tab's own actions (Present, History, Export, Share, open in). */
  actionsSlot?: HTMLElement | null;
}) {
  const name = path.split(/[\\/]/).pop() || path;
  const host = usePageHost(sessionId, path);
  const { grant } = host;
  const [view, setView] = useState<"preview" | "code">("preview");
  const [width, setWidth] = useState<PageWidth>("fit");
  const { stage, box } = useStageBox(host.ready && view === "preview");
  const laidOut = pageBox(width, box);
  const variants = useVariants(sessionId, path, name, host.page);
  const design = useDesignTools({
    sessionId,
    path,
    label: host.first || name,
    post: host.post,
    viewWidth: laidOut ? laidOut.width : box.w,
    reload: host.reload,
    inline: true,
  });
  host.onSaid.current = design.handle;
  // A mark being turned into a picture and words, and the layer's own key (a fresh one clears it).
  const [marking, setMarking] = useState(false);
  const [markRound, setMarkRound] = useState(0);
  const [shooting, setShooting] = useState(false);
  // Present in the tab: the page alone, no bar and no foot, until Escape or Exit.
  const [presenting, setPresenting] = useState(false);
  const body = useRef<HTMLDivElement | null>(null);
  // What the tab row last did (a file saved, a picture copied), for a few seconds.
  const [outcome, setOutcome] = useState<DesignOutcome | null>(null);
  useEffect(() => {
    if (!outcome) return;
    const timer = window.setTimeout(() => setOutcome(null), outcome.saved ? 12000 : 6000);
    return () => window.clearTimeout(timer);
  }, [outcome]);
  useEffect(() => {
    if (!presenting) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPresenting(false);
    };
    // Leaving full screen (the window's own Escape) ends the presentation with it.
    const left = () => {
      if (!document.fullscreenElement) setPresenting(false);
    };
    window.addEventListener("keydown", escape);
    document.addEventListener("fullscreenchange", left);
    return () => {
      window.removeEventListener("keydown", escape);
      document.removeEventListener("fullscreenchange", left);
    };
  }, [presenting]);

  // The code, read when it is asked for and again when the page reloads after a write.
  const [code, setCode] = useState<string | null>(null);
  useEffect(() => {
    if (view !== "code") return;
    let alive = true;
    readArtifact(sessionId, path)
      .then((read) => alive && setCode(read?.ok && typeof read.content === "string" ? read.content : ""))
      .catch(() => alive && setCode(""));
    return () => {
      alive = false;
    };
  }, [view, sessionId, path, host.page]);

  useEffect(() => {
    setView("preview");
    setWidth("fit");
    setCode(null);
  }, [sessionId, path]);

  // Compare from Designs: the variants side by side, once they are read from the folder.
  const variantCount = variants.variants.length;
  const { setShow: showVariants } = variants;
  useEffect(() => {
    if (compareRequest && variantCount >= 2) {
      setView("preview");
      showVariants(true);
    }
  }, [compareRequest, variantCount, showVariants]);

  const startDrag = (event: ReactPointerEvent) => {
    event.preventDefault();
    const startX = event.clientX;
    const start = laidOut ? laidOut.width : Math.max(DRAG_MIN, box.w);
    const scale = laidOut?.scale ?? 1;
    const move = (ev: PointerEvent) => setWidth(draggedWidth(start, ev.clientX - startX, scale));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const nudge = (event: ReactKeyboardEvent) => {
    const step = event.key === "ArrowRight" ? 10 : event.key === "ArrowLeft" ? -10 : 0;
    if (!step) return;
    event.preventDefault();
    const now = laidOut ? laidOut.width : Math.max(DRAG_MIN, box.w);
    setWidth(Math.min(DRAG_MAX, Math.max(DRAG_MIN, now + step)));
  };

  if (!grant) return <div className="fv-media-wait" role="status" aria-label={`Opening ${name}`} />;
  if (!grant.ok || !grant.url || !grant.origin || !grant.key) {
    return (
      <div className="artifact-open-prompt" data-testid="design-unavailable">
        <p>{grant.error || "This design could not be opened here."}</p>
        <button type="button" className="btn sm" onClick={() => revealArtifact(sessionId, path, "open")}>
          Open in browser
        </button>
      </div>
    );
  }

  const sized = laidOut;
  const last = design.versions[design.versions.length - 1];
  const drawnWidth = sized ? sized.width : box.w;
  const handleLeft = sized ? sized.left + sized.width * sized.scale : box.w;
  const tool = (id: (typeof MODES)[number]["id"]) => MODES.find((mode) => mode.id === id)!;
  const pinsWaiting = design.pins.filter((pin) => !pin.sent).length;
  // Where the frame is drawn in the stage, in screen px, and its scale: what the comment box and
  // the mark layer are placed over.
  const frameScale = sized ? sized.scale : 1;
  const frameLeft = sized ? sized.left : 0;
  const frameWidth = sized ? sized.width * sized.scale : box.w;
  const frameHeight = box.h;
  const draftPin = design.draft !== null ? design.pins.find((pin) => pin.n === design.draft) ?? null : null;
  const label = host.first || name;

  /** A mark into the message: where it is and what is under it (asked of the page), a picture of
   *  the area from Reeve's browser with the ink drawn on it, and the note. */
  const sendMark = async (shapes: MarkShape[], note: string) => {
    const rect = markBounds(shapes);
    if (!rect) return;
    setMarking(true);
    try {
      const area = await design.askArea(rect);
      const layout = sized ? sized.width : box.w;
      const page = area?.page ?? rect;
      const png = await getDesignShot(sessionId, path, area?.width || layout, page).catch(() => null);
      const inked = png ? await inkOver(png, shapes, rect) : null;
      const picture = inked ?? png;
      attachToComposer(
        [
          ...(picture ? [{ kind: "image" as const, name: `${label} · marked area`, mime: "image/png", data_url: picture }] : []),
          markAttachment(label, {
            note,
            kinds: Array.from(new Set(shapes.map((shape) => shape.kind))),
            area: page,
            width: area?.width || layout,
            under: area?.under ?? [],
            pictured: inked ? "inked" : png ? "plain" : "none",
          }),
        ],
        note.trim() || `Look at the mark on ${label}`,
      );
      design.setNotice(picture ? "The mark is in your message, with its picture." : "The mark is in your message; its picture could not be taken.");
      setMarkRound((n) => n + 1);
    } finally {
      setMarking(false);
    }
  };

  /** What the person sees of the page, as a picture from Reeve's browser, into the message. */
  const shootView = async () => {
    const layout = sized ? sized.width : box.w;
    if (!layout) return;
    setShooting(true);
    try {
      const seen = { x: 0, y: 0, w: layout, h: Math.round(frameHeight / frameScale) };
      const area = await design.askArea(seen);
      const width = area?.width || layout;
      const png = await getDesignShot(sessionId, path, width, area?.page ?? seen).catch(() => null);
      if (png) {
        attachToComposer([{ kind: "image", name: `${label} · ${width} px`, mime: "image/png", data_url: png }]);
        design.setNotice("A picture of what you see is in your message.");
      } else {
        design.setNotice("The picture could not be taken.");
      }
    } finally {
      setShooting(false);
    }
  };

  /** Present the page: alone in the tab, or the tab full screen (the tab alone when the window
   *  cannot go full screen). */
  const present = (where: "tab" | "full") => {
    setView("preview");
    variants.setShow(false);
    design.setVersionsOpen(false);
    design.setTweaksOpen(false);
    design.setPinsOpen(false);
    design.setMode("look");
    setPresenting(true);
    if (where === "full" && body.current?.requestFullscreen) {
      body.current.requestFullscreen().catch(() => undefined);
    }
  };
  const stopPresenting = () => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
    setPresenting(false);
  };

  return (
    <div className={"design-view" + (presenting ? " is-presenting" : "")} data-testid="design-view" ref={body}>
      {presenting && (
        <button type="button" className="dv-present-exit" data-testid="design-present-exit" onClick={stopPresenting}>
          <Icon name="x" size={13} />
          Exit
          <kbd className="mono">Esc</kbd>
        </button>
      )}
      <div className="dv-bar" role="toolbar" aria-label={`${name} tools`}>
        <button type="button" className="artifact-icon-btn" aria-label="Reload the page" title="Reload" data-testid="design-reload" onClick={host.reload}>
          <Icon name="refresh" size={14} />
        </button>
        <div className="dv-switch" role="group" aria-label="Show">
          <button type="button" className={view === "preview" ? "on" : undefined} aria-pressed={view === "preview"} data-testid="design-view-preview" onClick={() => setView("preview")}>
            <Icon name="eye" size={13} />
            <span>Preview</span>
          </button>
          <button type="button" className={view === "code" ? "on" : undefined} aria-pressed={view === "code"} data-testid="design-view-code" onClick={() => setView("code")}>
            <Icon name="codeXml" size={13} />
            <span>Code</span>
          </button>
        </div>
        {view === "preview" && (
          <Dropdown
            value={String(width)}
            options={widthOptions(width)}
            icon={widthIcon(width)}
            className="dv-pill"
            onChange={(value) => setWidth(value === "fit" ? "fit" : Number(value))}
          />
        )}
        <span className="fv-fill" />
        {view === "preview" && (
          <div className="dv-tools">
            <button
              type="button"
              className="artifact-icon-btn"
              aria-label="Put a picture of what you see in your message"
              title="Picture of what you see"
              data-testid="design-shoot"
              disabled={shooting}
              onClick={() => void shootView()}
            >
              <Icon name="camera" size={15} />
            </button>
            {(["point", "pin", "text"] as const).map((id) => {
              const option = tool(id);
              const on = design.mode === id;
              return (
                <button
                  key={id}
                  type="button"
                  className={"artifact-icon-btn" + (on ? " on" : "")}
                  aria-pressed={on}
                  aria-label={option.hint}
                  title={option.hint}
                  data-testid={`design-mode-${id}`}
                  onClick={() => design.setMode(on ? "look" : id)}
                >
                  <Icon name={option.icon} size={15} />
                </button>
              );
            })}
            <button
              type="button"
              className={"artifact-icon-btn" + (design.mode === "mark" ? " on" : "")}
              aria-pressed={design.mode === "mark"}
              aria-label="Mark: draw on the page and send a picture of it"
              title="Mark: draw on the page"
              data-testid="design-mode-mark"
              onClick={() => design.setMode(design.mode === "mark" ? "look" : "mark")}
            >
              <Icon name="pencilLine" size={15} />
            </button>
            <span className="dv-tools-rule" aria-hidden="true" />
            {design.levers.length > 0 && (
              <button
                type="button"
                className={"artifact-icon-btn" + (design.tweaksOpen ? " on" : "")}
                aria-expanded={design.tweaksOpen}
                aria-label="Tweaks this page offers"
                title="Tweaks"
                data-testid="design-tweaks-toggle"
                onClick={() => design.setTweaksOpen((open) => !open)}
              >
                <Icon name="sliders" size={15} />
              </button>
            )}
            {variants.variants.length >= 2 && (
              <button
                type="button"
                className={"dv-count" + (variants.show ? " on" : "")}
                aria-pressed={variants.show}
                title={variants.show ? "Back to one page" : "See the variants side by side"}
                data-testid="design-variants-toggle"
                onClick={() => variants.setShow((on) => !on)}
              >
                <Icon name="layoutGrid" size={14} />
                {variants.variants.length + (variants.base ? 1 : 0)}
              </button>
            )}
            <button
              type="button"
              className={"dv-count" + (design.pinsOpen ? " on" : "")}
              aria-expanded={design.pinsOpen}
              aria-label={`${design.pins.length} comments, ${pinsWaiting} not sent`}
              title="Comments"
              data-testid="design-pins-toggle"
              onClick={() => design.setPinsOpen((open) => !open)}
            >
              <Icon name="messageSquare" size={14} />
              {design.pins.length}
            </button>
            <button
              type="button"
              className={"artifact-icon-btn" + (host.internet ? " on" : "")}
              aria-label={host.internet ? "Internet on for this page. Turn it off" : `Internet off${host.blocked.length ? `, ${host.blocked.length} blocked` : ""}. Allow…`}
              title={host.internet ? "Internet on · turn off" : `Internet off${host.blocked.length ? ` · ${host.blocked.length} blocked` : ""}`}
              data-testid="design-net"
              onClick={host.internet ? host.turnOff : host.ask}
            >
              <Icon name="globe" size={15} />
            </button>
            <span className="page-scale dv-zoom" title="How large the page is drawn" data-testid="design-scale">
              {Math.round(frameScale * 100)}%
            </span>
          </div>
        )}
      </div>
      {design.notice && (
        <div className="dv-notice" role="status" data-testid="design-notice">
          {design.notice}
        </div>
      )}
      {outcome && (
        <div className="dv-notice dv-outcome" role="status" data-testid="design-outcome">
          <span>{outcome.text}</span>
          {outcome.saved && (
            <button type="button" className="fv-note-btn" data-testid="design-outcome-reveal" onClick={() => void revealDownload(outcome.saved!)}>
              Show in folder
            </button>
          )}
        </div>
      )}
      {view === "preview" && (
        <>
          {design.tweaks}
          {design.history}
        </>
      )}
      {view === "code" ? (
        <div className="dv-code" data-testid="design-code">
          {code === null ? <div className="fv-media-wait" role="status" aria-label="Reading the code" /> : <CodeView text={code} lang="html" path={path} />}
        </div>
      ) : variants.show ? (
        <Variants sessionId={sessionId} variants={variants.variants} base={variants.base} width={width} onKeep={variants.keep} />
      ) : (
        <div className="dv-body">
        <div className={sized ? "dv-ground" : "dv-ground is-fill"}>
          <div className={sized ? "page-stage dv-stage is-sized" : "page-stage dv-stage"} ref={stage}>
            <iframe
              key={`${grant.key}-${host.mounts}`}
              ref={host.holder}
              className="page-holder"
              data-testid="design-frame"
              title={name}
              sandbox={PAGE_SANDBOX}
              referrerPolicy="no-referrer"
              srcDoc={holderDocument(grant.origin, host.start, name, host.internet)}
              style={
                sized
                  ? { width: `${sized.width}px`, height: sized.height ? `${sized.height}px` : "100%", left: `${sized.left}px`, transform: `scale(${sized.scale})` }
                  : undefined
              }
            />
            <div
              className="dv-handle"
              role="separator"
              aria-orientation="vertical"
              aria-label="Page width"
              aria-valuenow={drawnWidth || undefined}
              aria-valuemin={DRAG_MIN}
              aria-valuemax={DRAG_MAX}
              tabIndex={0}
              title="Drag to change the width"
              data-testid="design-width-handle"
              style={{ left: `${Math.max(0, handleLeft)}px` }}
              onPointerDown={startDrag}
              onDoubleClick={() => setWidth("fit")}
              onKeyDown={nudge}
            />
            {design.mode === "mark" && (
              <MarkLayer
                key={markRound}
                left={frameLeft}
                width={frameWidth}
                height={frameHeight}
                scale={frameScale}
                busy={marking}
                onSend={(shapes, note) => void sendMark(shapes, note)}
              />
            )}
            {draftPin && (
              <CommentPopover
                pin={draftPin}
                left={Math.max(4, Math.min(frameLeft + (draftPin.cx ?? 0) * frameScale + 12, box.w - 292))}
                top={Math.max(4, Math.min((draftPin.cy ?? 0) * frameScale - 8, box.h - 190))}
                onChange={(value) => design.setComment(draftPin.n, value)}
                onSend={() => {
                  design.sendComments([draftPin.n]);
                  design.setDraft(null);
                }}
                onKeep={() => {
                  design.setDraft(null);
                  design.setPinsOpen(true);
                }}
                onCancel={design.closeDraft}
              />
            )}
          </div>
        </div>
        {design.pinsOpen && (
          <CommentsDock
            pins={design.pins}
            onChange={design.setComment}
            onRemove={design.removePin}
            onSend={design.sendComments}
            onClose={() => design.setPinsOpen(false)}
          />
        )}
        </div>
      )}
      <div className="dv-foot" data-testid="design-foot">
        {view === "preview" && drawnWidth > 0 && (
          <span className="mono">
            {drawnWidth} px{frameScale < 1 ? ` · ${Math.round(frameScale * 100)}%` : ""}
          </span>
        )}
        {last ? <span>{last.summary}</span> : <span>Not checked yet: Reeve checks a design when it writes one.</span>}
      </div>
      <InternetAsk host={host} name={name} />
      {actionsSlot &&
        createPortal(
          <DesignActions
            sessionId={sessionId}
            path={path}
            width={drawnWidth}
            internet={host.internet}
            versions={design.versions.length}
            versionsOpen={design.versionsOpen}
            onToggleVersions={() => {
              setView("preview");
              design.setVersionsOpen((open) => !open);
            }}
            onPresent={present}
            onOutcome={setOutcome}
          />,
          actionsSlot,
        )}
    </div>
  );
}
