// Designs: every design page of the conversation on one board, the way OpenDesign's
// Design Files tab lays out a project's pages (read 2026-10-19). A card is a page: the picture
// Reeve's last check of it took, its name, when it changed and what the check said. A page's
// variants (`pricing.a.html`, …) follow its card, each named as a variant of it. The project's
// DESIGN.md has a card of its own with its colours. A card opens the page in its own tab; a page
// with variants has Compare, which opens it with them side by side.
//
// The list is the server's (`/design/files`): pages under a `design/` folder, and any page a
// check recorded elsewhere. Read again when a new check lands, so a page the agent just wrote
// appears without a refresh to hunt for.
import { useEffect, useMemo, useState } from "react";
import { getDesignFiles, revealArtifact, type DesignFile, type DesignFiles } from "../../api";
import { Icon } from "../Icon";
import { openArtifact } from "../Markdown";
import { PagePeek } from "./PagePeek";

const ago = (ms: number) => {
  if (!ms) return "";
  const seconds = Math.max(0, (Date.now() - ms) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(ms).toLocaleDateString();
};

/** A page and the variants that hang on it. A variant whose page is not there is a card itself. */
export interface DesignCard {
  file: DesignFile;
  variants: DesignFile[];
}

export function groupDesigns(files: DesignFile[]): DesignCard[] {
  const byPath = new Map(files.map((file) => [file.path, file] as const));
  const cards = new Map<string, DesignCard>();
  for (const file of files) {
    if (file.variant_of && byPath.has(file.variant_of)) continue;
    cards.set(file.path, { file, variants: [] });
  }
  for (const file of files) {
    if (file.variant_of && cards.has(file.variant_of)) cards.get(file.variant_of)!.variants.push(file);
  }
  for (const card of cards.values()) card.variants.sort((a, b) => (a.letter ?? "").localeCompare(b.letter ?? ""));
  return [...cards.values()];
}

export function DesignsView({
  sessionId,
  seen,
  onOpen,
  onCompare,
}: {
  sessionId: string;
  /** Changes when a design check lands in the conversation: the list is read again. */
  seen: unknown;
  onOpen: (path: string) => void;
  /** Open a page with its variants side by side. */
  onCompare?: (path: string) => void;
}) {
  const [found, setFound] = useState<DesignFiles | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    getDesignFiles(sessionId)
      .then((list) => alive && setFound(list))
      .catch(() => alive && setFound(null));
    return () => {
      alive = false;
    };
  }, [sessionId, seen]);
  const cards = useMemo(() => groupDesigns(found?.files ?? []), [found]);
  // Every page has its card, a page's variants right after it (they are pages to look at, and a
  // board that showed them only as letters read as one page, owner 2026-10-19).
  const tiles = useMemo(
    () => cards.flatMap((card) => [{ ...card, of: null as DesignFile | null }, ...card.variants.map((file) => ({ file, variants: [], of: card.file }))]),
    [cards],
  );

  if (found === undefined) return <div className="fv-media-wait" role="status" aria-label="Reading the designs" />;
  const designMd = found?.design_md ?? null;
  return (
    <div className="designs-view" data-testid="designs-view">
      <div className="designs-head">
        <span className="designs-title">Designs</span>
        <span className="designs-count">
          {tiles.length} page{tiles.length === 1 ? "" : "s"}
        </span>
      </div>
      {cards.length === 0 && !designMd ? (
        <div className="designs-empty" data-testid="designs-empty">
          <p>Nothing drawn yet.</p>
          <p className="designs-empty-hint">
            Ask for a page in the chat. Every page Reeve writes is checked at three widths and appears here; it keeps
            them in a <span className="mono">design/</span> folder.
          </p>
        </div>
      ) : (
        <div className="designs-grid">
          {tiles.map(({ file, variants, of }) => (
            <article
              key={file.path}
              className={"design-card" + (of ? " is-variant" : "")}
              data-testid={`design-card-${file.path}`}
            >
              <button type="button" className="design-card-shot" aria-label={`Open ${file.path}`} onClick={() => onOpen(file.path)}>
                {file.thumb ? (
                  <img src={file.thumb} alt="" />
                ) : (
                  // No picture from a check (it has not run, or could not open the page): the
                  // app draws the page itself.
                  <PagePeek
                    sessionId={sessionId}
                    path={file.path}
                    title={file.name}
                    fallback={
                      <span className="design-card-noshot">
                        <Icon name="appWindow" size={22} />
                      </span>
                    }
                  />
                )}
              </button>
              <div className="design-card-meta">
                <button type="button" className="design-card-name" title={file.path} onClick={() => onOpen(file.path)}>
                  {file.name}
                </button>
                <span className="design-card-sub">
                  {[
                    of ? `Variant ${(file.letter ?? "").toUpperCase()} of ${of.name}` : "",
                    // The version the app is built from, said in words like the rest.
                    file.lock?.locked ? (file.lock.changed ? "Changed since locked" : "Locked") : "",
                    ago(file.mtime_ms),
                    variants.length ? `${variants.length} variant${variants.length === 1 ? "" : "s"}` : "",
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
                <span className="design-card-check" data-issues={file.issues || undefined}>
                  {file.checks ? file.summary : "Not checked by Reeve"}
                </span>
                {variants.length >= 2 && onCompare && (
                  <button
                    type="button"
                    className="btn sm design-card-compare"
                    data-testid={`design-card-compare-${file.path}`}
                    onClick={() => onCompare(file.path)}
                  >
                    <Icon name="layoutGrid" size={13} />
                    Compare {variants.length + 1}
                  </button>
                )}
              </div>
              <button
                type="button"
                className="artifact-icon-btn design-card-reveal"
                aria-label={`Show ${file.name} in its folder`}
                title="Show in folder"
                onClick={() => revealArtifact(sessionId, file.path, "reveal")}
              >
                <Icon name="folderOpen" size={14} />
              </button>
            </article>
          ))}
          {designMd && (
            <article className="design-card design-card-md" data-testid="design-card-design-md">
              <button
                type="button"
                className="design-card-shot"
                aria-label={`Open ${designMd.path}`}
                onClick={() => openArtifact(designMd.path)}
              >
                <span className="design-card-swatches">
                  {designMd.colors.length ? (
                    designMd.colors.map((color) => <span key={color} className="design-card-swatch" style={{ background: color }} title={color} />)
                  ) : (
                    <Icon name="palette" size={22} />
                  )}
                </span>
              </button>
              <div className="design-card-meta">
                <span className="design-card-name">DESIGN.md</span>
                <span className="design-card-sub">
                  {designMd.colors.length ? `${designMd.colors.length} colour${designMd.colors.length === 1 ? "" : "s"}` : "No colours named"}
                </span>
                <span className="design-card-check">What every design here follows</span>
              </div>
            </article>
          )}
        </div>
      )}
    </div>
  );
}
