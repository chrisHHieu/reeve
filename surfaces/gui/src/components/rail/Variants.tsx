// A design's variants side by side (phase 3c): `pricing.a.html`, `pricing.b.html`… drawn
// next to one another at the width the preview is set to, each in its own frame at the preview door,
// as a single page is. "Keep this one" asks the agent to make it the page; the files are the agent's
// to change, so the person reads the request before it is sent.
import { useEffect, useRef, useState } from "react";
import { readArtifact } from "../../api";
import { usePreviewGrant } from "./usePreviewGrant";
import { pageBox, type PageWidth } from "./PageFrame";
import { holderDocument, PAGE_SANDBOX } from "./usePageHost";
import { attachToComposer, keepVariantText, variantsOf } from "./designAsks";

export interface Variant {
  letter: string;
  name: string;
  path: string;
}

function VariantFrame({ sessionId, variant, width, onKeep }: { sessionId: string; variant: Variant; width: PageWidth; onKeep?: () => void }) {
  const grant = usePreviewGrant(sessionId, variant.path);
  const stage = useRef<HTMLDivElement | null>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const measure = () => setBox({ w: element.clientWidth, h: element.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const sized = pageBox(width, box);
  return (
    <section
      className="page-variant"
      aria-label={variant.letter ? `Variant ${variant.letter.toUpperCase()}` : "The page now"}
      data-testid={`page-variant-${variant.letter || "base"}`}
    >
      <header className="page-variant-head">
        <b>{variant.letter ? variant.letter.toUpperCase() : "Now"}</b>
        <span className="page-variant-name" title={variant.name}>
          {variant.name}
        </span>
      </header>
      <div className={sized ? "page-stage is-sized" : "page-stage"} ref={stage}>
        {grant?.ok && grant.url && grant.origin ? (
          <iframe
            className="page-holder"
            title={variant.name}
            sandbox={PAGE_SANDBOX}
            referrerPolicy="no-referrer"
            srcDoc={holderDocument(grant.origin, grant.url, variant.name, false)}
            style={
              sized
                ? { width: `${sized.width}px`, height: sized.height ? `${sized.height}px` : "100%", left: `${sized.left}px`, transform: `scale(${sized.scale})` }
                : undefined
            }
          />
        ) : (
          <div className="fv-media-wait" role="status" aria-label={`Opening ${variant.name}`} />
        )}
      </div>
      {onKeep ? (
        <button type="button" className="btn sm" data-testid={`page-variant-keep-${variant.letter}`} onClick={onKeep}>
          Keep this one
        </button>
      ) : (
        <span className="page-variant-now">The page as it is</span>
      )}
    </section>
  );
}

export function Variants({
  sessionId,
  variants,
  base,
  width,
  onKeep,
}: {
  sessionId: string;
  variants: Variant[];
  /** The page the variants are of, drawn first to compare against, when it exists. */
  base?: Variant | null;
  width: PageWidth;
  onKeep: (variant: Variant) => void;
}) {
  const columns = (base ? 1 : 0) + variants.length;
  return (
    <div className="page-variants" data-testid="page-variants" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
      {base && <VariantFrame sessionId={sessionId} variant={base} width={width} />}
      {variants.map((variant) => (
        <VariantFrame key={variant.path} sessionId={sessionId} variant={variant} width={width} onKeep={() => onKeep(variant)} />
      ))}
    </div>
  );
}

/** A page's variants, read from its folder again whenever `seen` changes (the page reporting where
 *  it is, which it does after the agent writes it), and keeping one as a request to the agent. */
export function useVariants(sessionId: string, path: string, name: string, seen: unknown) {
  const [siblings, setSiblings] = useState<string[]>([]);
  const [show, setShow] = useState(false);
  useEffect(() => {
    let alive = true;
    const folder = path.replace(/[\\/][^\\/]*$/, "");
    if (!folder || folder === path) {
      setSiblings([]);
      return;
    }
    readArtifact(sessionId, folder)
      .then((read) => {
        if (alive) setSiblings(read?.kind === "folder" && Array.isArray(read.entries) ? read.entries.filter((e) => !e.dir).map((e) => e.name) : []);
      })
      .catch(() => alive && setSiblings([]));
    return () => {
      alive = false;
    };
  }, [sessionId, path, seen]);
  const fileName = path.split(/[\\/]/).pop() || name;
  const folderOf = path.slice(0, path.length - fileName.length);
  const found = variantsOf(fileName, siblings);
  const variants: Variant[] = found.map((v) => ({ letter: v.letter, name: v.name, path: folderOf + v.name }));
  const keep = (variant: Variant) => {
    const base = found[0]?.base ?? fileName;
    attachToComposer([], keepVariantText(base, variant.name, variants.filter((v) => v.path !== variant.path).map((v) => v.name)));
    setShow(false);
  };
  // The page the variants are of, when it is beside them: the first column of a comparison.
  const baseName = found[0]?.base;
  const base: Variant | null = baseName && siblings.includes(baseName) ? { letter: "", name: baseName, path: folderOf + baseName } : null;
  return { variants, base, show: show && variants.length >= 2, setShow, keep };
}
