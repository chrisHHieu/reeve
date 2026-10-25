// A design page drawn small by the app itself. The card a write leaves in the chat
// showed the picture Reeve's check took, so a page the check could not open (its browser did not
// start, 2026-10-20) was a bare "Open" with nothing to see. The app can draw the page on its own:
// in the same sandboxed frames as its tab, at a desktop width, scaled to the card, out of reach of
// the pointer and of Tab. It is asked for only once the card is on screen, so a long conversation
// does not open every page it ever wrote.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { usePreviewGrant } from "./usePreviewGrant";
import { holderDocument, PAGE_SANDBOX } from "./usePageHost";

/** The width the page is laid out at before it is scaled down: a desktop window. */
export const PEEK_WIDTH = 1280;

export function PagePeek({
  sessionId,
  path,
  title,
  fallback,
}: {
  sessionId: string;
  path: string;
  title: string;
  /** What shows until the page is drawn, and instead of it when it cannot be. */
  fallback: ReactNode;
}) {
  const box = useRef<HTMLDivElement | null>(null);
  const [onScreen, setOnScreen] = useState(false);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = box.current;
    if (!element) return;
    setWidth(element.clientWidth);
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => setWidth(element.clientWidth));
    resize?.observe(element);
    if (typeof IntersectionObserver === "undefined") {
      setOnScreen(true);
      return () => resize?.disconnect();
    }
    const seen = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setOnScreen(true);
          seen.disconnect();
        }
      },
      { rootMargin: "200px 0px" },
    );
    seen.observe(element);
    return () => {
      seen.disconnect();
      resize?.disconnect();
    };
  }, []);
  return (
    <div ref={box} className="page-peek" data-testid="page-peek">
      {onScreen && width > 0 ? <PeekFrame sessionId={sessionId} path={path} title={title} width={width} fallback={fallback} /> : fallback}
    </div>
  );
}

function PeekFrame({
  sessionId,
  path,
  title,
  width,
  fallback,
}: {
  sessionId: string;
  path: string;
  title: string;
  width: number;
  fallback: ReactNode;
}) {
  const grant = usePreviewGrant(sessionId, path);
  const [drawn, setDrawn] = useState(false);
  if (!grant?.ok || !grant.url || !grant.origin) return <>{fallback}</>;
  const scale = width / PEEK_WIDTH;
  return (
    <>
      {!drawn && fallback}
      <iframe
        className={"page-peek-frame" + (drawn ? " is-drawn" : "")}
        title={title}
        aria-hidden="true"
        tabIndex={-1}
        sandbox={PAGE_SANDBOX}
        referrerPolicy="no-referrer"
        srcDoc={holderDocument(grant.origin, grant.url, title, false)}
        onLoad={() => setDrawn(true)}
        style={{ width: `${PEEK_WIDTH}px`, height: `${100 / scale}%`, transform: `scale(${scale})` }}
      />
    </>
  );
}
