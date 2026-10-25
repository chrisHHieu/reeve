// An HTML file, run as the page it is (workspace panel redesign, 2026-10-11).
//
// The old view put the file's text in a sealed box: no folder behind it, so a link to the next
// page, a stylesheet beside it, `localStorage` and a form all failed, and it behaved nothing
// like the same file opened in a browser (owner-hit). The page is now served by the preview
// door from its own session folder, at an address that is not the app's and not the API's.
//
// Two frames, measured in Chromium (scratch probe, 2026-10-11): the outer one is ours and
// carries `frame-src`, so the page cannot navigate itself to another site (a navigation is not
// a fetch, and the page's own policy cannot stop it); the inner one is the page, sandboxed
// without leave to move its parent or the app's window, or to open a window of its own.
//
// The internet is off until the person turns it on for this page: the door sends the page with
// a policy that names only its own address. What the page tried to reach is counted and named.
// The frames and the page's reports are `usePageHost`'s; a design page has its own tab with its
// own bar around the same host (`DesignView`). Files shows a page as a page and nothing
// more: the design tools (point, comment, retype, tweaks, versions, variants) were here too, so a
// flow drawn as HTML that nobody asked to have designed came with all of them (owner, 2026-10-20).
// A page under `design/` opened here has a way to its design tab.
import { useEffect, useRef, useState } from "react";
import { revealArtifact } from "../../api";
import { Icon } from "../Icon";
import { openArtifact } from "../Markdown";
import { isDesignPath } from "./tabs";
import { holderDocument, InternetAsk, PAGE_SANDBOX, usePageHost } from "./usePageHost";

export { holderDocument, PAGE_SANDBOX };

/** The widths a page can be seen at: as wide as the panel, or one of the widths a design is checked
 *  at. A width wider than the panel is drawn at that width and scaled down to fit, so a
 *  phone layout is a phone layout and a desktop one is the desktop's, however wide the panel is. */
export const PAGE_WIDTHS = [
  { id: "fit", label: "As wide as the panel", icon: "expand" },
  { id: 1440, label: "Desktop, 1440 px", icon: "monitor" },
  { id: 834, label: "Tablet, 834 px", icon: "tablet" },
  { id: 390, label: "Phone, 390 px", icon: "smartphone" },
] as const;
/** As wide as the panel, or a width in px: one of PAGE_WIDTHS, or one dragged to. */
export type PageWidth = "fit" | number;

/** Where and how big the page is drawn for a width, in a stage `box` wide and tall. */
export function pageBox(width: PageWidth, box: { w: number; h: number }) {
  if (width === "fit" || box.w <= 0) return null;
  const scale = Math.min(1, box.w / width);
  return {
    scale,
    width,
    height: box.h > 0 ? Math.round(box.h / scale) : 0,
    left: Math.max(0, Math.round((box.w - width * scale) / 2)),
  };
}

/** The stage's size, measured while the page is on show. */
export function useStageBox(ready: boolean) {
  const stage = useRef<HTMLDivElement | null>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const element = stage.current;
    if (!ready || !element) return;
    const measure = () => setBox({ w: element.clientWidth, h: element.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ready]);
  return { stage, box };
}

export function PageFrame({
  sessionId,
  path,
  name,
  unsaved,
}: {
  sessionId: string;
  path: string;
  name: string;
  /** The editor holds text that is not saved: the page on show is the saved file. */
  unsaved?: boolean;
}) {
  const host = usePageHost(sessionId, path);
  const { grant } = host;
  const [width, setWidth] = useState<PageWidth>("fit");
  const { stage, box } = useStageBox(host.ready);
  const laidOut = pageBox(width, box);

  if (!grant) return <div className="fv-media-wait" role="status" aria-label={`Opening ${name}`} />;
  if (!grant.ok || !grant.url || !grant.origin || !grant.key) {
    return (
      <div className="artifact-open-prompt" data-testid="page-unavailable">
        <p>{grant.error || "This page could not be opened here."}</p>
        <button type="button" className="btn sm" onClick={() => revealArtifact(sessionId, path, "open")}>
          Open in browser
        </button>
      </div>
    );
  }

  const where = host.page || host.first;
  const sized = laidOut;
  return (
    <div className="page-frame" data-testid="page-frame">
      <div className="page-bar">
        <button type="button" className="artifact-icon-btn" aria-label="Back to the previous page" data-testid="page-back" onClick={() => host.tell("back")}>
          <Icon name="arrowLeft" size={14} />
        </button>
        <button type="button" className="artifact-icon-btn" aria-label="Reload the page" data-testid="page-reload" onClick={() => host.tell("reload")}>
          <Icon name="refresh" size={14} />
        </button>
        <span className="page-where" data-testid="page-where" title={where}>
          {where}
        </span>
        <span className="fv-fill" />
        {sized && sized.scale < 1 && (
          <span className="page-scale" data-testid="page-scale">
            {Math.round(sized.scale * 100)}%
          </span>
        )}
        <div className="seg page-widths" role="group" aria-label="Width">
          {PAGE_WIDTHS.map((option) => (
            <button
              key={option.id}
              type="button"
              className={width === option.id ? "on" : undefined}
              aria-label={option.label}
              aria-pressed={width === option.id}
              title={option.label}
              data-testid={`page-width-${option.id}`}
              onClick={() => setWidth(option.id)}
            >
              <Icon name={option.icon} size={14} />
            </button>
          ))}
        </div>
        {isDesignPath(path) && (
          <button type="button" className="fv-note-btn" data-testid="page-open-design" onClick={() => openArtifact(path)}>
            Open as design
          </button>
        )}
        {host.internet ? (
          <>
            <span className="page-net is-on" data-testid="page-net">
              <Icon name="globe" size={13} />
              Internet on for this page
            </span>
            <button type="button" className="fv-note-btn" data-testid="page-net-off" onClick={host.turnOff}>
              Turn off
            </button>
          </>
        ) : (
          <>
            <span className="page-net" data-testid="page-net">
              Internet off{host.blocked.length ? ` · ${host.blocked.length} blocked` : ""}
            </span>
            <button type="button" className="fv-note-btn" data-testid="page-net-allow" onClick={host.ask}>
              Allow…
            </button>
          </>
        )}
      </div>
      {unsaved && (
        <div className="fv-note" data-testid="page-unsaved">
          This is the saved file. Save to see your edits here.
        </div>
      )}
      <div className={sized ? "page-stage is-sized" : "page-stage"} ref={stage}>
        <iframe
          key={`${grant.key}-${host.mounts}`}
          ref={host.holder}
          className="page-holder"
          data-testid="artifact-frame"
          title={name}
          sandbox={PAGE_SANDBOX}
          referrerPolicy="no-referrer"
          srcDoc={holderDocument(grant.origin, host.start, name, host.internet)}
          style={
            sized
              ? {
                  width: `${sized.width}px`,
                  height: sized.height ? `${sized.height}px` : "100%",
                  left: `${sized.left}px`,
                  transform: `scale(${sized.scale})`,
                }
              : undefined
          }
        />
      </div>
      <InternetAsk host={host} name={name} />
    </div>
  );
}
