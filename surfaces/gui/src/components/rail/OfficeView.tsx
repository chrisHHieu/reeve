// A Word or PowerPoint file, drawn from its own bytes (workspace panel redesign, 2026-10-11).
//
// Word is drawn by docx-preview as its pages, PowerPoint by pptx-preview as its slides. Both
// libraries are loaded when the first such file is opened, never with the app. Tried on real
// files before this was written: a .docx with heading styles, a coloured run, bullets, a
// bordered table, a picture and Vietnamese text came out as its pages.
//
// The drawing goes into a frame of its own that may not run scripts. A document is somebody
// else's content: whatever a link or a shape in it carries, nothing in it can run, and its
// styles cannot reach the app's. The bytes come from the preview door, granted for
// that one file.
import { useEffect, useRef, useState } from "react";
import { revealArtifact } from "../../api";
import { openExternal } from "../../desktop";
import { Icon } from "../Icon";
import { usePreviewGrant } from "./usePreviewGrant";

type Kind = "word" | "slides";

/** Which library draws a file, or null for the formats neither reads (.doc, .ppt). */
export function officeKind(name: string): Kind | null {
  if (/\.docx$/i.test(name)) return "word";
  if (/\.pptx$/i.test(name)) return "slides";
  return null;
}

const SLIDE_WIDTH = 960;
const SLIDE_HEIGHT = 540;
const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2];
// Ours, inside the frame: a quiet ground, pages with a hairline, slides stacked with room.
const FRAME_STYLE =
  "html,body{margin:0;padding:0;background:transparent}" +
  "#stage{padding:16px 0 40px}" +
  ".docx-wrapper{background:transparent!important;padding:0!important;display:flex;flex-direction:column;align-items:center;gap:16px}" +
  ".docx-wrapper>section.docx{box-shadow:0 1px 3px rgba(0,0,0,.12),0 0 0 1px rgba(0,0,0,.06)!important;margin:0!important}" +
  // A slide that names no font is drawn in PowerPoint's own default, not the browser's serif.
  ".pptx-preview-wrapper{background:transparent!important;height:auto!important;overflow:visible!important;margin:0 auto;font-family:Calibri,'Segoe UI',Arial,sans-serif}" +
  ".pptx-preview-slide-wrapper{box-shadow:0 1px 3px rgba(0,0,0,.12),0 0 0 1px rgba(0,0,0,.06);margin:0 auto 16px!important}" +
  "a{cursor:pointer}";

/** After a library has drawn: links go to the person's browser, and only web links go. */
function settle(doc: Document) {
  doc.querySelectorAll("script").forEach((node) => node.remove());
  doc.querySelectorAll("a[href]").forEach((link) => {
    const href = link.getAttribute("href") || "";
    if (!/^https?:\/\//i.test(href)) link.removeAttribute("href");
  });
  doc.addEventListener("click", (event) => {
    const link = (event.target as Element | null)?.closest?.("a[href]");
    if (!link) return;
    event.preventDefault();
    void openExternal(link.getAttribute("href") || "");
  });
}

/**
 * Charts on a slide are redrawn by the library from the file's numbers. Seen on a real deck
 * (2026-10-11): a series that names no colour of its own (PowerPoint takes it from the theme)
 * was drawn with `fill="none"` and no outline, so the bars had the right heights and could not
 * be seen, and an untitled chart got the library's own placeholder title in Chinese. A mark
 * that would be invisible is given Office's default series colour, and that placeholder is
 * taken out. Returns how many charts the deck has, so the view can say they are redrawn.
 */
export function mendCharts(stage: HTMLElement): number {
  const charts = stage.querySelectorAll("[_echarts_instance_]");
  charts.forEach((chart) => {
    chart.querySelectorAll("svg text").forEach((text) => {
      if ((text.textContent || "").trim() === "图表标题") text.remove();
      // The same missing colour, on a label (the legend's): unseen until it has one.
      else if (text.getAttribute("fill") === "none") text.setAttribute("fill", "#444444");
    });
    chart.querySelectorAll("svg path").forEach((mark) => {
      const closed = /z\s*$/i.test(mark.getAttribute("d") || "");
      if (closed && mark.getAttribute("fill") === "none" && !mark.getAttribute("stroke")) mark.setAttribute("fill", "#4472C4");
    });
  });
  return charts.length;
}

export function OfficeView({
  sessionId,
  path,
  name,
}: {
  sessionId: string;
  path: string;
  name: string;
}) {
  const kind = officeKind(name);
  const grant = usePreviewGrant(sessionId, kind ? path : "", { single: true });
  const frame = useRef<HTMLIFrameElement | null>(null);
  const [phase, setPhase] = useState<"drawing" | "drawn" | { error: string }>("drawing");
  const [count, setCount] = useState(0);
  const [charts, setCharts] = useState(0);
  // null: fit the panel's width. A number: the person's own choice.
  const [zoom, setZoom] = useState<number | null>(null);
  const [fit, setFit] = useState(1);
  const natural = useRef(0);

  useEffect(() => {
    if (!kind || !grant?.ok || !grant.url) return;
    const url = grant.url;
    let alive = true;
    let undo: (() => void) | undefined;
    setPhase("drawing");
    setCount(0);
    setCharts(0);
    natural.current = 0;
    (async () => {
      const response = await globalThis.fetch(url);
      if (!response.ok) throw new Error("the file could not be read");
      const bytes = await response.arrayBuffer();
      const doc = frame.current?.contentDocument;
      if (!alive || !doc) return;
      doc.open();
      doc.write(`<!doctype html><html><head><meta charset="utf-8"><style>${FRAME_STYLE}</style></head><body><div id="styles"></div><div id="stage"></div></body></html>`);
      doc.close();
      const stage = doc.getElementById("stage") as HTMLElement;
      let drawn = 0;
      if (kind === "word") {
        const library = await import("docx-preview");
        // Its styles go in a box of their own: the library empties the box it is given, and
        // given the head it emptied ours with it (seen on the dev app: a grey ground).
        await library.renderAsync(bytes, stage, doc.getElementById("styles") as HTMLElement, {
          inWrapper: true,
          breakPages: true,
          ignoreLastRenderedPageBreak: true,
          // Pictures as data, not as blob addresses that outlive the view.
          useBase64URL: true,
        });
        drawn = stage.querySelectorAll("section.docx").length;
      } else {
        const library = await import("pptx-preview");
        const previewer = library.init(stage, { width: SLIDE_WIDTH, height: SLIDE_HEIGHT, mode: "list" });
        // A chart is drawn after `preview` has answered (and again when it redraws), so the
        // mending follows the drawing instead of running once.
        const mend = () => {
          if (alive) setCharts(mendCharts(stage));
        };
        const watcher = new MutationObserver(mend);
        watcher.observe(stage, { childList: true, subtree: true });
        undo = () => {
          watcher.disconnect();
          previewer.destroy();
        };
        await previewer.preview(bytes);
        drawn = previewer.slideCount;
        mend();
      }
      if (!alive) return;
      settle(doc);
      const page = stage.querySelector<HTMLElement>("section.docx, .pptx-preview-slide-wrapper");
      natural.current = (page?.offsetWidth || (kind === "slides" ? SLIDE_WIDTH : 816)) + 32;
      setCount(drawn);
      setPhase("drawn");
    })().catch((error: unknown) => {
      if (alive) setPhase({ error: error instanceof Error ? error.message : String(error) });
    });
    return () => {
      alive = false;
      try {
        undo?.();
      } catch {
        /* the frame is already gone */
      }
    };
  }, [kind, grant?.ok, grant?.url]);

  // The page is as wide as the document says; the panel is as wide as the person made it.
  useEffect(() => {
    const element = frame.current;
    if (phase !== "drawn" || !element) return;
    const measure = () => {
      const width = element.clientWidth;
      if (width > 0 && natural.current > 0) setFit(Math.min(1, width / natural.current));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [phase]);

  const scale = zoom ?? fit;
  useEffect(() => {
    const body = frame.current?.contentDocument?.body;
    if (phase === "drawn" && body) body.style.setProperty("zoom", String(scale));
  }, [scale, phase]);

  const app = kind === "slides" || /\.pptx?$/i.test(name) ? "PowerPoint" : "Word";
  const outside = (
    <button type="button" className="btn sm" onClick={() => revealArtifact(sessionId, path, "open")}>
      Open in {app}
    </button>
  );
  if (!kind) {
    return (
      <div className="artifact-open-prompt" data-testid="office-older">
        <p>This is an older {app} format that cannot be drawn here.</p>
        {outside}
      </div>
    );
  }
  const refusal = grant && (!grant.ok || !grant.url) ? grant.error || "This file could not be opened." : typeof phase === "object" ? `This document could not be drawn here: ${phase.error}` : null;
  if (refusal !== null) {
    return (
      <div className="artifact-open-prompt" data-testid="office-undrawn">
        <p>{refusal}</p>
        {outside}
      </div>
    );
  }
  const step = (by: 1 | -1) => {
    const now = zoom ?? fit;
    const next = by > 0 ? ZOOMS.find((z) => z > now + 0.01) : [...ZOOMS].reverse().find((z) => z < now - 0.01);
    if (next !== undefined) setZoom(next);
  };
  const unit = kind === "slides" ? "slide" : "page";
  return (
    <div className="page-frame office-view" data-testid="office-view" aria-busy={phase === "drawing" || undefined}>
      <div className="page-bar">
        <span className="page-where" data-testid="office-count" role="status">
          {phase === "drawn" ? `${count} ${unit}${count === 1 ? "" : "s"}` : "Opening…"}
        </span>
        <span className="fv-fill" />
        <button type="button" className="artifact-icon-btn" aria-label="Zoom out" data-testid="office-zoom-out" disabled={phase !== "drawn"} onClick={() => step(-1)}>
          <span aria-hidden="true">−</span>
        </button>
        <button
          type="button"
          className="fv-note-btn office-zoom"
          data-testid="office-zoom"
          title="Fit the panel's width"
          disabled={phase !== "drawn"}
          onClick={() => setZoom(null)}
        >
          {Math.round(scale * 100)}%
        </button>
        <button type="button" className="artifact-icon-btn" aria-label="Zoom in" data-testid="office-zoom-in" disabled={phase !== "drawn"} onClick={() => step(1)}>
          <Icon name="plus" size={13} />
        </button>
      </div>
      {phase === "drawn" && kind === "slides" && (
        <div className="fv-note" data-testid="office-note">
          Drawn from the file: positions and text are the deck's own; fonts, bullets and effects can differ from
          PowerPoint.{charts > 0 ? " Charts are redrawn from their numbers, so their colours can differ too." : ""}
        </div>
      )}
      {phase === "drawing" && (
        <div className="office-wait" data-testid="office-wait" role="status" aria-label={`Opening ${name}`}>
          <div className="office-wait-page">
            <span style={{ width: "58%", height: 12 }} />
            <span style={{ width: "100%", marginTop: 8 }} />
            <span style={{ width: "94%" }} />
            <span style={{ width: "97%" }} />
            <span style={{ width: "62%" }} />
          </div>
        </div>
      )}
      {/* May not run scripts: the document is drawn INTO it by the app, and stays inert. */}
      <iframe
        ref={frame}
        className={"page-holder office-frame" + (phase === "drawn" ? "" : " is-hidden")}
        data-testid="office-frame"
        title={name}
        sandbox="allow-same-origin"
      />
    </div>
  );
}
